"""Balance and period calculations over a user's own transactions.

Definitions (also in docs/ARCHITECTURE.md):

* **Account balance** = opening balance + income + refunds + incoming transfers
  - expenses - outgoing transfers, counting only transactions dated on or before the
  as-of date. Future-dated transactions are treated as scheduled, not as money moved.
* **Income / expenses for a period** exclude transfers. Expenses are *net* of refunds.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import Account, Transaction

ZERO = Decimal("0.00")
OUTFLOW_KINDS = (Transaction.Kind.EXPENSE, Transaction.Kind.TRANSFER)


def local_today() -> date:
    return timezone.localdate()


def account_balances(user, as_of: date | None = None) -> dict[int, Decimal]:
    """Balance of every account of ``user`` as of ``as_of`` (default: today)."""
    as_of = as_of or local_today()
    balances = {a.id: a.opening_balance for a in Account.objects.filter(user=user)}
    txns = Transaction.objects.filter(user=user, date__lte=as_of)

    for row in txns.values("account_id", "kind").annotate(total=Sum("amount")):
        sign = -1 if row["kind"] in OUTFLOW_KINDS else 1
        balances[row["account_id"]] += sign * row["total"]
    for row in txns.filter(kind=Transaction.Kind.TRANSFER).values("to_account_id").annotate(total=Sum("amount")):
        balances[row["to_account_id"]] += row["total"]
    return balances


@dataclass(frozen=True)
class PeriodTotals:
    income: Decimal
    expenses: Decimal  # net of refunds
    savings: Decimal
    savings_rate: Decimal | None  # percent of income; None when there is no income


def _sum(qs) -> Decimal:
    return qs.aggregate(total=Sum("amount"))["total"] or ZERO


def period_totals(user, start: date, end: date) -> PeriodTotals:
    txns = Transaction.objects.filter(user=user, date__gte=start, date__lte=end)
    income = _sum(txns.filter(kind=Transaction.Kind.INCOME))
    expenses = _sum(txns.filter(kind=Transaction.Kind.EXPENSE)) - _sum(txns.filter(kind=Transaction.Kind.REFUND))
    savings = income - expenses
    rate = (savings * 100 / income).quantize(Decimal("0.1")) if income > 0 else None
    return PeriodTotals(income, expenses, savings, rate)


def category_spend(user, start: date, end: date) -> dict[int | None, Decimal]:
    """Net spending per category id (``None`` = uncategorised) for the period."""
    spend: dict[int | None, Decimal] = {}
    rows = (
        Transaction.objects.filter(
            user=user, date__gte=start, date__lte=end, kind__in=[Transaction.Kind.EXPENSE, Transaction.Kind.REFUND]
        )
        .values("category_id", "kind")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        sign = 1 if row["kind"] == Transaction.Kind.EXPENSE else -1
        spend[row["category_id"]] = spend.get(row["category_id"], ZERO) + sign * row["total"]
    return spend
