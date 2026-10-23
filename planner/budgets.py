"""Budget status: planned versus actual spending and spending pace."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from ledger.models import Category
from ledger.services import category_spend, period_totals

from .models import Budget

ZERO = Decimal("0.00")
MIN_DAYS_FOR_PROJECTION = {"monthly": 3, "weekly": 2}


def period_bounds(period: str, today: date) -> tuple[date, date]:
    if period == Budget.Period.WEEKLY:
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    return today.replace(day=1), today.replace(day=monthrange(today.year, today.month)[1])


def previous_bounds(period: str, start: date) -> tuple[date, date]:
    return period_bounds(period, start - timedelta(days=1))


@dataclass(frozen=True)
class BudgetStatus:
    budget: Budget
    start: date
    end: date
    spent: Decimal
    remaining: Decimal
    percent_used: int
    elapsed_percent: int
    state: str  # "ok", "warn", "over"
    projected_spend: Decimal | None
    previous_spent: Decimal


def _spent(user, budget: Budget, start: date, end: date, cache: dict, children: dict) -> Decimal:
    if budget.category_id is None:
        return period_totals(user, start, end).expenses
    spend = cache.setdefault((start, end), category_spend(user, start, end))
    ids = [budget.category_id, *children.get(budget.category_id, [])]
    return sum((spend.get(i, ZERO) for i in ids), ZERO)


def budget_statuses(user, today: date) -> list[BudgetStatus]:
    children: dict[int, list[int]] = {}
    for cid, parent in Category.objects.filter(user=user, parent__isnull=False).values_list("id", "parent_id"):
        children.setdefault(parent, []).append(cid)

    cache: dict = {}
    statuses = []
    for budget in Budget.objects.filter(user=user).select_related("category"):
        start, end = period_bounds(budget.period, today)
        spent = _spent(user, budget, start, end, cache, children)
        total_days = (end - start).days + 1
        elapsed = (today - start).days + 1
        percent = int(spent * 100 / budget.amount) if budget.amount else 0
        state = "over" if percent >= 100 else "warn" if percent >= budget.warn_percent else "ok"
        projected = None
        if elapsed >= MIN_DAYS_FOR_PROJECTION[budget.period] and spent > 0:
            projected = (spent * total_days / elapsed).quantize(Decimal("0.01"))
        prev_start, prev_end = previous_bounds(budget.period, start)
        statuses.append(
            BudgetStatus(
                budget,
                start,
                end,
                spent,
                budget.amount - spent,
                percent,
                int(elapsed * 100 / total_days),
                state,
                projected,
                _spent(user, budget, prev_start, prev_end, cache, children),
            )
        )
    statuses.sort(key=lambda s: (s.budget.category_id is not None, -s.percent_used))
    return statuses


def essential_split(user, start: date, end: date) -> dict[str, Decimal]:
    """Spending split into essential, discretionary and uncategorised."""
    spend = category_spend(user, start, end)
    essential = {c.id: c.is_essential for c in Category.objects.filter(user=user)}
    split = {"Essential": ZERO, "Discretionary": ZERO, "Uncategorised": ZERO}
    for category_id, amount in spend.items():
        if category_id is None:
            split["Uncategorised"] += amount
        elif essential.get(category_id):
            split["Essential"] += amount
        else:
            split["Discretionary"] += amount
    return split
