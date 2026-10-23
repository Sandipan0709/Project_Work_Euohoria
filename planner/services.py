"""Database-aware planning logic: pending bills, forecast inputs, and recording outcomes.

Double-counting rules for the forecast (documented in docs/ARCHITECTURE.md):

1. The opening balance counts every transaction dated today or earlier.
2. A bill/income due date that has an ``Occurrence`` row (paid, received or skipped) is
   *not* projected again, because its transaction is already in the opening balance.
3. Transactions dated in the future are projected once, as scheduled transactions.
4. EMIs are projected from the debt record, not from bills. A month with a recorded
   debt payment is not projected again.
5. Income due before today that was never marked received is not projected.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum

from ledger.models import Account, Category, Transaction
from ledger.services import account_balances, local_today

from . import debt as debt_math
from .forecast import ZERO, Event, Forecast, project
from .models import Debt, DebtPayment, Occurrence, ScheduledItem
from .recurrence import clamp_day, occurrence_dates

OVERDUE_LOOKBACK_DAYS = 60
SCENARIOS = {
    "conservative": "Conservative: only income you marked as confirmed is included.",
    "expected": "Expected: all expected income is included, confirmed or not.",
}


class AlreadyRecorded(Exception):
    """The due date already has a recorded outcome."""


@dataclass(frozen=True)
class Pending:
    item: ScheduledItem
    due_date: date
    state: str  # "upcoming" or "overdue"

    @property
    def amount(self) -> Decimal:
        return self.item.amount


@dataclass(frozen=True)
class Position:
    liquid: Decimal
    card_balance: Decimal  # signed; negative means money owed on cards
    debts: Decimal
    net_worth: Decimal
    accounts: tuple[tuple[Account, Decimal], ...]


def financial_position(user, today: date | None = None) -> Position:
    """Assets, liabilities and net worth. See docs/ARCHITECTURE.md for the definitions."""
    balances = account_balances(user, today)
    accounts = list(Account.objects.filter(user=user, is_archived=False))
    liquid = sum((balances[a.id] for a in accounts if a.is_liquid), ZERO)
    cards = sum((balances[a.id] for a in accounts if not a.is_liquid), ZERO)
    debts = Debt.objects.filter(user=user, status=Debt.Status.ACTIVE).aggregate(t=Sum("outstanding"))["t"] or ZERO
    return Position(liquid, cards, debts, liquid + cards - debts, tuple((a, balances[a.id]) for a in accounts))


def pending_occurrences(
    user, window_start: date, window_end: date, flow: str | None = None, today: date | None = None
) -> list[Pending]:
    """Due dates in the window that have no recorded outcome, soonest first."""
    today = today or local_today()
    items = ScheduledItem.objects.filter(user=user, status=ScheduledItem.Status.ACTIVE)
    if flow:
        items = items.filter(flow=flow)
    items = list(items)
    recorded = set(
        Occurrence.objects.filter(item__in=items, due_date__gte=window_start, due_date__lte=window_end).values_list(
            "item_id", "due_date"
        )
    )
    pending = []
    for item in items:
        for due in occurrence_dates(item.start_date, item.frequency, window_start, window_end, item.end_date):
            if (item.id, due) not in recorded:
                pending.append(Pending(item, due, "overdue" if due < today else "upcoming"))
    return sorted(pending, key=lambda p: (p.due_date, p.item.name))


def _emi_dates(debt: Debt, start: date, end: date, paid_months: set[tuple[int, int]]) -> list[date]:
    months_left = debt_math.amortize(debt.outstanding, debt.annual_rate, debt.emi_amount).months
    dates, year, month = [], start.year, start.month
    while date(year, month, 1) <= end and (months_left is None or len(dates) < months_left):
        due = clamp_day(year, month, debt.emi_day)
        if start <= due <= end and (year, month) not in paid_months:
            dates.append(due)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return dates


def build_events(
    user,
    start: date,
    end: date,
    scenario: str = "conservative",
    account_id: int | None = None,
    today: date | None = None,
) -> tuple[list[Event], list[str]]:
    """Dated cash events for the forecast, plus plain-language notes on what was left out."""
    today = today or local_today()
    liquid_ids = set(
        Account.objects.filter(user=user, is_archived=False, kind__in=Account.LIQUID_KINDS).values_list("id", flat=True)
    )
    scope = {account_id} if account_id else liquid_ids
    overall = account_id is None
    notes: list[str] = []

    def in_scope(item_account_id: int | None) -> bool:
        return item_account_id in scope or (overall and item_account_id is None)

    events: list[Event] = []
    excluded_income = ZERO
    window_start = min(start, today) - timedelta(days=OVERDUE_LOOKBACK_DAYS)
    for p in pending_occurrences(user, window_start, end, today=today):
        if not in_scope(p.item.account_id):
            continue
        item = p.item
        if item.flow == ScheduledItem.Flow.BILL:
            overdue = p.due_date < start
            label = f"{item.name} (overdue)" if overdue else item.name
            events.append(Event(max(p.due_date, start), -item.amount, label, "bill"))
        else:
            if p.due_date < today:
                continue  # rule 5
            if scenario == "conservative" and not item.is_confirmed:
                excluded_income += item.amount
                continue
            events.append(Event(p.due_date, item.amount, item.name, "income", item.is_confirmed))
    if excluded_income:
        notes.append(f"Excludes ₹{excluded_income:,.2f} of unconfirmed expected income in this scenario.")

    for debt in Debt.objects.filter(user=user, status=Debt.Status.ACTIVE, emi_amount__gt=0):
        if not in_scope(debt.account_id):
            continue
        paid = {
            (d.year, d.month)
            for d in DebtPayment.objects.filter(debt=debt, date__gte=today.replace(day=1)).values_list(
                "date", flat=True
            )
        }
        for due in _emi_dates(debt, max(start, today), end, paid):
            label = debt.name if "emi" in debt.name.lower() else f"{debt.name} EMI"
            events.append(Event(due, -debt.emi_amount, label, "emi"))

    future = Transaction.objects.filter(user=user, date__gt=today, date__lte=end)
    for t in future:
        if t.kind == Transaction.Kind.TRANSFER:
            if t.account_id in scope:
                events.append(Event(t.date, -t.amount, f"Transfer out: {t.payee or 'scheduled'}", "scheduled"))
            if t.to_account_id in scope:
                events.append(Event(t.date, t.amount, f"Transfer in: {t.payee or 'scheduled'}", "scheduled"))
        elif t.account_id in scope:
            sign = -1 if t.kind == Transaction.Kind.EXPENSE else 1
            events.append(Event(t.date, sign * t.amount, t.payee or t.get_kind_display(), "scheduled"))
    return events, notes


@dataclass(frozen=True)
class ForecastResult:
    forecast: Forecast
    events: tuple[Event, ...]
    notes: tuple[str, ...]
    scenario: str
    account: Account | None


def run_forecast(
    user,
    days: int = 30,
    scenario: str = "conservative",
    account_id: int | None = None,
    extra_events: tuple[Event, ...] = (),
    today: date | None = None,
) -> ForecastResult:
    today = today or local_today()
    account = Account.objects.filter(user=user, pk=account_id).first() if account_id else None
    balances = account_balances(user, today)
    if account:
        opening = balances[account.id]
        scope_label = f"account {account.name}"
    else:
        liquid = Account.objects.filter(user=user, is_archived=False, kind__in=Account.LIQUID_KINDS)
        opening = sum((balances[a.id] for a in liquid), ZERO)
        scope_label = f"{liquid.count()} cash, bank and wallet account(s)"

    events, notes = build_events(
        user, today, today + timedelta(days=days), scenario, account.id if account else None, today
    )
    events = events + list(extra_events)
    buffer = user.profile.min_buffer
    assumptions = (
        f"Opening balance ₹{opening:,.2f} across {scope_label}, counting transactions up to today.",
        SCENARIOS[scenario],
        "Bills already marked paid or skipped are not counted again.",
        "Variable bills use their expected amount. Overdue bills are assumed paid today.",
        *notes,
    )
    forecast = project(opening, events, today, days, buffer, assumptions)
    return ForecastResult(forecast, tuple(events), tuple(notes), scenario, account)


def data_gaps(user) -> list[str]:
    """Reasons a forecast or affordability answer may be incomplete."""
    gaps = []
    if not Account.objects.filter(user=user, is_archived=False, kind__in=Account.LIQUID_KINDS).exists():
        gaps.append("No cash, bank or wallet account is set up.")
    if not ScheduledItem.objects.filter(user=user, flow=ScheduledItem.Flow.INCOME, status="active").exists():
        gaps.append("No expected income is recorded, so the forecast assumes none.")
    if (
        not ScheduledItem.objects.filter(user=user, flow=ScheduledItem.Flow.BILL, status="active").exists()
        and not Debt.objects.filter(user=user, status=Debt.Status.ACTIVE).exists()
    ):
        gaps.append("No bills or EMIs are recorded, so upcoming obligations may be missing.")
    return gaps


def obligations_through(user, end: date, today: date | None = None) -> Decimal:
    """Unpaid bills and EMIs due from today (and overdue) through ``end``."""
    today = today or local_today()
    events, _ = build_events(user, today, end, "conservative", None, today)
    return -sum((e.amount for e in events if e.source in ("bill", "emi")), ZERO)


# ---- recording outcomes -------------------------------------------------------------


def _category_by_name(user, name: str):
    return Category.objects.filter(user=user, name=name).first()


@transaction.atomic
def record_occurrence(
    item: ScheduledItem,
    due_date: date,
    outcome: str,
    *,
    paid_on: date | None = None,
    amount: Decimal | None = None,
    account: Account | None = None,
) -> Occurrence:
    """Mark one due date paid/received (creating its transaction) or skipped.

    Idempotent per (item, due_date): a second call raises ``AlreadyRecorded`` and writes nothing.
    """
    if not occurrence_dates(item.start_date, item.frequency, due_date, due_date, item.end_date):
        raise ValueError("That is not a due date of this schedule.")
    amount = amount if amount is not None else item.amount
    try:
        with transaction.atomic():
            occurrence = Occurrence.objects.create(
                item=item,
                due_date=due_date,
                outcome=outcome,
                amount=amount if outcome == Occurrence.Outcome.DONE else ZERO,
            )
    except IntegrityError as exc:
        raise AlreadyRecorded from exc

    if outcome == Occurrence.Outcome.DONE:
        account = account or item.account
        if account is None:
            raise ValueError("Choose the account this was paid from or received into.")
        if item.flow == ScheduledItem.Flow.INCOME:
            kind, to_account = Transaction.Kind.INCOME, None
        elif item.settles_account_id:
            kind, to_account = Transaction.Kind.TRANSFER, item.settles_account
        else:
            kind, to_account = Transaction.Kind.EXPENSE, None
        occurrence.transaction = Transaction.objects.create(
            user=item.user,
            kind=kind,
            account=account,
            to_account=to_account,
            amount=amount,
            date=paid_on or due_date,
            category=item.category,
            payee=item.name,
            notes=f"{item.get_flow_display()} due {due_date.isoformat()}",
        )
        occurrence.save(update_fields=["transaction"])
    return occurrence


@transaction.atomic
def undo_occurrence(occurrence: Occurrence) -> None:
    """Remove a recorded outcome and the transaction it created."""
    txn = occurrence.transaction
    occurrence.delete()
    if txn:
        txn.delete()


@transaction.atomic
def record_debt_payment(debt: Debt, paid_on: date, amount: Decimal, account: Account | None = None) -> DebtPayment:
    """Record an EMI/extra payment: one expense transaction, one payment row, balance updated."""
    account = account or debt.account
    if account is None:
        raise ValueError("Choose the account the payment was made from.")
    principal = debt_math.estimate_principal_part(debt.outstanding, debt.annual_rate, amount)
    txn = Transaction.objects.create(
        user=debt.user,
        kind=Transaction.Kind.EXPENSE,
        account=account,
        amount=amount,
        date=paid_on,
        category=_category_by_name(debt.user, "EMI & loans"),
        payee=debt.name,
        notes="Debt payment",
    )
    payment = DebtPayment.objects.create(
        debt=debt, date=paid_on, amount=amount, principal_part=principal, transaction=txn
    )
    debt.outstanding = max(ZERO, debt.outstanding - principal)
    if debt.outstanding == 0:
        debt.status = Debt.Status.CLOSED
    debt.save(update_fields=["outstanding", "status"])
    return payment


@transaction.atomic
def delete_transaction(txn: Transaction) -> None:
    """Delete a transaction and undo anything that was derived from it.

    * A bill/income occurrence recorded with this transaction becomes pending again.
    * A debt payment recorded with it is reversed (the balance is restored).
    """
    Occurrence.objects.filter(transaction=txn).delete()
    for payment in DebtPayment.objects.filter(transaction=txn).select_related("debt"):
        debt = payment.debt
        debt.outstanding += payment.principal_part
        debt.status = Debt.Status.ACTIVE
        debt.save(update_fields=["outstanding", "status"])
        payment.delete()
    txn.delete()
