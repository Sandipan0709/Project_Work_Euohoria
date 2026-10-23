"""Date arithmetic for recurring bills and income.

Every occurrence is computed from the *original* start date, never from the previous
occurrence, so a schedule that starts on 31 January yields 28/29 Feb, 31 Mar, 30 Apr...
instead of drifting to the 28th forever. A yearly schedule starting on 29 February
lands on 28 February in non-leap years and 29 February again in leap years.
"""

from calendar import monthrange
from datetime import date, timedelta

MONTH_STEP = {"monthly": 1, "quarterly": 3, "yearly": 12}
# Occurrences per year, used for annual cost summaries. One-off items are not recurring.
PER_YEAR = {"weekly": 52, "monthly": 12, "quarterly": 4, "yearly": 1, "once": 0}


def add_months(anchor: date, months: int) -> date:
    """Add whole months, clamping the day to the target month's length."""
    year_offset, month_index = divmod(anchor.month - 1 + months, 12)
    year, month = anchor.year + year_offset, month_index + 1
    return date(year, month, min(anchor.day, monthrange(year, month)[1]))


def occurrence_dates(
    start: date, frequency: str, window_start: date, window_end: date, end_date: date | None = None
) -> list[date]:
    """Due dates of a schedule that fall inside ``[window_start, window_end]``."""
    last = min(window_end, end_date) if end_date else window_end
    if last < window_start or last < start:
        return []

    dates: list[date] = []
    if frequency == "once":
        candidates = iter([start])
    elif frequency == "weekly":
        first_k = max(0, (window_start - start).days // 7)
        candidates = (start + timedelta(weeks=k) for k in range(first_k, first_k + 400))
    elif frequency in MONTH_STEP:
        step = MONTH_STEP[frequency]
        approx_months = (window_start.year - start.year) * 12 + window_start.month - start.month
        first_k = max(0, approx_months // step - 1)
        candidates = (add_months(start, k * step) for k in range(first_k, first_k + 400))
    else:
        raise ValueError(f"Unknown frequency: {frequency!r}")

    for candidate in candidates:
        if candidate > last:
            break
        if candidate >= window_start:
            dates.append(candidate)
    return dates


def clamp_day(year: int, month: int, day: int) -> date:
    """``day`` of the month, or the last day if the month is shorter (EMI on the 31st)."""
    return date(year, month, min(day, monthrange(year, month)[1]))
