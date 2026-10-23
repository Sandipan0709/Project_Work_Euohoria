"""Savings-goal projection."""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .recurrence import add_months

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
DAYS_PER_MONTH = Decimal("30.4375")
RECENT_WINDOW_DAYS = 90


@dataclass(frozen=True)
class GoalProjection:
    saved: Decimal
    remaining: Decimal
    progress_percent: int
    achieved: bool
    required_monthly: Decimal | None  # needed to reach the target date; None if no date or date passed
    estimated_completion: date | None
    pace_basis: str  # how the completion estimate was derived
    on_track: bool | None  # None when it cannot be judged


def project_goal(
    target: Decimal,
    saved: Decimal,
    today: date,
    target_date: date | None,
    monthly_plan: Decimal | None,
    contributions: list[tuple[date, Decimal]],
) -> GoalProjection:
    remaining = max(ZERO, target - saved)
    progress = min(100, int(saved * 100 / target)) if target > 0 else 0
    if remaining == 0:
        return GoalProjection(saved, ZERO, 100, True, None, None, "Target reached.", True)

    required = None
    if target_date and target_date > today:
        months_left = max(1, math.ceil(Decimal((target_date - today).days) / DAYS_PER_MONTH))
        required = (remaining / months_left).quantize(CENT)

    rate, basis = _monthly_pace(today, monthly_plan, contributions)
    completion = None
    if rate and rate > 0:
        completion = add_months(today, math.ceil(remaining / rate))

    on_track = None
    if target_date and completion:
        on_track = completion <= target_date
    elif target_date and not completion:
        on_track = False if target_date <= today else None

    return GoalProjection(saved, remaining, progress, False, required, completion, basis, on_track)


def _monthly_pace(
    today: date, monthly_plan: Decimal | None, contributions: list[tuple[date, Decimal]]
) -> tuple[Decimal | None, str]:
    if monthly_plan and monthly_plan > 0:
        return monthly_plan, f"Based on your planned contribution of ₹{monthly_plan:,.2f} a month."
    cutoff = today - timedelta(days=RECENT_WINDOW_DAYS)
    recent = sum((amount for day, amount in contributions if day >= cutoff), ZERO)
    if recent > 0:
        return (recent / 3).quantize(CENT), "Based on your average monthly contributions over the last 90 days."
    return None, "Add a planned monthly amount or a contribution to get an estimate."
