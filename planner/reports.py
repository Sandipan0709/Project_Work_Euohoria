"""Report data and explainable insights. Every insight carries the figures it is based on."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum

from core.formatting import fmt_date, inr
from ledger.models import Category, Transaction
from ledger.services import category_spend, period_totals

from .models import ScheduledItem
from .recurrence import PER_YEAR

ZERO = Decimal("0.00")
INCREASE_MIN_PERCENT = 25
INCREASE_MIN_AMOUNT = Decimal("500")


def month_floor(d: date) -> date:
    return d.replace(day=1)


def shift_month(d: date, months: int) -> date:
    index = d.year * 12 + d.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def month_end(start: date) -> date:
    return shift_month(start, 1) - timedelta(days=1)


def monthly_series(user, today: date, months: int = 6) -> list[dict]:
    """Income and net spending for each of the last ``months`` calendar months, oldest first."""
    series = []
    for back in range(months - 1, -1, -1):
        start = shift_month(month_floor(today), -back)
        totals = period_totals(user, start, min(month_end(start), today))
        series.append(
            {
                "label": start.strftime("%b %y"),
                "start": start,
                "income": totals.income,
                "expenses": totals.expenses,
                "savings": totals.savings,
            }
        )
    return series


def category_breakdown(user, start: date, end: date) -> list[dict]:
    names = {c.id: str(c) for c in Category.objects.filter(user=user).select_related("parent")}
    spend = {k: v for k, v in category_spend(user, start, end).items() if v > 0}
    total = sum(spend.values(), ZERO)
    rows = [
        {
            "name": names.get(cid, "Uncategorised"),
            "amount": amount,
            "percent": int(amount * 100 / total) if total else 0,
        }
        for cid, amount in spend.items()
    ]
    return sorted(rows, key=lambda r: -r["amount"])


def top_merchants(user, start: date, end: date, limit: int = 8) -> list[dict]:
    rows = (
        Transaction.objects.filter(user=user, kind=Transaction.Kind.EXPENSE, date__gte=start, date__lte=end)
        .exclude(payee="")
        .values("payee")
        .annotate(total=Sum("amount"))
        .order_by("-total")[:limit]
    )
    return [{"payee": r["payee"], "amount": r["total"]} for r in rows]


def compare_periods(user, start: date, end: date) -> dict:
    """Current period versus the equally long period immediately before it."""
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return {
        "current": period_totals(user, start, end),
        "previous": period_totals(user, prev_start, prev_end),
        "prev_start": prev_start,
        "prev_end": prev_end,
    }


def recurring_summary(user) -> dict:
    """Active recurring bills with their yearly cost.

    One-off bills are not recurring, and credit-card payments are excluded because they settle purchases
    that are already counted as spending.
    """
    rows = []
    for item in ScheduledItem.objects.filter(
        user=user, flow="bill", status="active", settles_account__isnull=True
    ).exclude(frequency="once"):
        annual = item.amount * PER_YEAR[item.frequency]
        rows.append({"item": item, "annual": annual, "monthly": (annual / 12).quantize(Decimal("0.01"))})
    rows.sort(key=lambda r: -r["annual"])
    subs = [r for r in rows if r["item"].is_subscription]
    return {
        "rows": rows,
        "annual_total": sum((r["annual"] for r in rows), ZERO),
        "monthly_total": sum((r["monthly"] for r in rows), ZERO),
        "subscription_annual": sum((r["annual"] for r in subs), ZERO),
        "subscription_count": len(subs),
    }


@dataclass(frozen=True)
class Insight:
    text: str
    basis: str


def insights(user, today: date) -> list[Insight]:
    found: list[Insight] = []
    start = month_floor(today)

    totals = period_totals(user, start, today)
    basis = f"{fmt_date(start, False)}–{fmt_date(today)}: income {inr(totals.income)}, spending {inr(totals.expenses)}."
    if totals.income > 0 and totals.savings_rate is not None:
        found.append(Insight(f"You have kept {totals.savings_rate}% of this month's income.", basis))

    cur_start = today - timedelta(days=29)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=29)
    current = category_spend(user, cur_start, today)
    previous = category_spend(user, prev_start, prev_end)
    names = {c.id: str(c) for c in Category.objects.filter(user=user).select_related("parent")}
    biggest = None
    for cid, now in current.items():
        before = previous.get(cid, ZERO)
        rise = now - before
        if before > 0 and rise >= INCREASE_MIN_AMOUNT and rise * 100 / before >= INCREASE_MIN_PERCENT:
            if biggest is None or rise > biggest[0]:
                biggest = (rise, cid, before, now)
    if biggest:
        rise, cid, before, now = biggest
        pct = int(rise * 100 / before)
        found.append(
            Insight(
                f"Spending on {names.get(cid, 'uncategorised items')} is up {pct}% on the previous 30 days.",
                f"{fmt_date(cur_start, False)}–{fmt_date(today)}: {inr(now)} versus {inr(before)} in "
                f"{fmt_date(prev_start, False)}–{fmt_date(prev_end)}.",
            )
        )

    recurring = recurring_summary(user)
    if recurring["subscription_count"]:
        found.append(
            Insight(
                f"Your {recurring['subscription_count']} subscription(s) cost "
                f"{inr(recurring['subscription_annual'], 0)} a year.",
                "Sum of each active subscription's expected amount multiplied by how often it renews.",
            )
        )

    uncategorised = Transaction.objects.filter(
        user=user, kind=Transaction.Kind.EXPENSE, category__isnull=True, date__gte=cur_start, date__lte=today
    ).count()
    if uncategorised:
        found.append(
            Insight(
                f"Review {uncategorised} transaction(s) awaiting categorisation.",
                f"Expenses from {fmt_date(cur_start, False)} to {fmt_date(today)} with no category.",
            )
        )
    return found
