"""Deterministic cash-flow projection.

This module is pure: it receives an opening balance and a list of dated events and
returns a day-by-day balance timeline. Deciding *which* events exist (and avoiding
double counting against recorded transactions) is the job of ``planner.services``.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
HORIZONS = (7, 14, 30, 90)


@dataclass(frozen=True)
class Event:
    date: date
    amount: Decimal  # signed: money in is positive, money out is negative
    label: str
    source: str  # "bill", "income", "emi", "scheduled", "purchase"
    certain: bool = True


@dataclass(frozen=True)
class DayPoint:
    date: date
    balance: Decimal
    events: tuple[Event, ...] = ()


@dataclass(frozen=True)
class Forecast:
    start: date
    days: int
    opening: Decimal
    buffer: Decimal
    points: tuple[DayPoint, ...]
    min_balance: Decimal
    min_date: date
    first_shortfall: date | None  # first day the balance is below zero
    first_below_buffer: date | None  # first day the balance is below the buffer
    end_balance: Decimal
    total_in: Decimal
    total_out: Decimal
    assumptions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def end(self) -> date:
        return self.start + timedelta(days=self.days)


def project(
    opening: Decimal,
    events: list[Event],
    start: date,
    days: int,
    buffer: Decimal = ZERO,
    assumptions: tuple[str, ...] = (),
) -> Forecast:
    """Project the balance for ``days`` days after ``start`` (the start day is day 0)."""
    if days < 1:
        raise ValueError("days must be at least 1")

    end = start + timedelta(days=days)
    by_day: dict[date, list[Event]] = {}
    for event in events:
        if start <= event.date <= end:
            by_day.setdefault(event.date, []).append(event)

    balance = opening
    points: list[DayPoint] = []
    total_in = total_out = ZERO
    for offset in range(days + 1):
        day = start + timedelta(days=offset)
        todays = tuple(by_day.get(day, ()))
        for event in todays:
            balance += event.amount
            if event.amount >= 0:
                total_in += event.amount
            else:
                total_out += -event.amount
        points.append(DayPoint(day, balance.quantize(CENT), todays))

    lowest = min(points, key=lambda p: (p.balance, p.date))
    first_shortfall = next((p.date for p in points if p.balance < 0), None)
    first_below_buffer = next((p.date for p in points if p.balance < buffer), None)
    return Forecast(
        start=start,
        days=days,
        opening=opening.quantize(CENT),
        buffer=buffer,
        points=tuple(points),
        min_balance=lowest.balance,
        min_date=lowest.date,
        first_shortfall=first_shortfall,
        first_below_buffer=first_below_buffer,
        end_balance=points[-1].balance,
        total_in=total_in.quantize(CENT),
        total_out=total_out.quantize(CENT),
        assumptions=assumptions,
    )
