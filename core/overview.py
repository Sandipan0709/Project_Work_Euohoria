"""Numbers and alerts for the Overview page. Every figure here is defined in docs/ARCHITECTURE.md."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from ledger.models import Transaction
from ledger.services import period_totals
from planner.budgets import budget_statuses
from planner.reports import month_end, shift_month
from planner.services import (
    data_gaps,
    financial_position,
    obligations_through,
    pending_occurrences,
    run_forecast,
)

from .formatting import fmt_date, inr


@dataclass
class Alert:
    level: str  # "danger", "warn", "info"
    text: str
    reason: str
    link_name: str = ""
    link_label: str = ""


@dataclass
class Overview:
    position: object
    month_totals: object
    previous_totals: object
    obligations: Decimal
    available: Decimal
    reserved_days: int
    month_end_balance: Decimal
    forecast: object
    upcoming: list
    budgets: list
    recent: list
    alerts: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    month_end: date | None = None


def build_overview(user, today: date) -> Overview:
    position = financial_position(user, today)
    start = today.replace(day=1)
    end = month_end(start)
    prev_start = shift_month(start, -1)
    obligations = obligations_through(user, end, today)

    forecast_result = run_forecast(user, 30, today=today)
    forecast = forecast_result.forecast
    days_left = (end - today).days
    month_end_balance = (
        run_forecast(user, days_left, today=today).forecast.end_balance
        if days_left >= 1
        else forecast.points[0].balance
    )

    horizon = today + timedelta(days=14)
    upcoming = [e for e in forecast_result.events if e.source in ("bill", "emi", "income") and e.date <= horizon]
    upcoming.sort(key=lambda e: (e.date, e.label))

    budgets = budget_statuses(user, today)
    overview = Overview(
        position=position,
        month_totals=period_totals(user, start, today),
        previous_totals=period_totals(user, prev_start, month_end(prev_start)),
        obligations=obligations,
        available=position.liquid - obligations,
        reserved_days=days_left,
        month_end_balance=month_end_balance,
        forecast=forecast,
        upcoming=upcoming[:8],
        budgets=budgets[:4],
        recent=list(Transaction.objects.filter(user=user).select_related("account", "category")[:8]),
        gaps=data_gaps(user),
        month_end=end,
    )
    overview.alerts = _alerts(user, today, forecast, budgets)
    return overview


def _alerts(user, today: date, forecast, budgets) -> list[Alert]:
    alerts: list[Alert] = []
    if forecast.first_shortfall:
        alerts.append(
            Alert(
                "danger",
                f"Projected balance falls below zero on {fmt_date(forecast.first_shortfall)}.",
                f"Lowest point in the next 30 days: {inr(forecast.min_balance)} on {fmt_date(forecast.min_date)}, "
                "counting only confirmed income.",
                "cashflow",
                "See cash flow",
            )
        )
    elif forecast.first_below_buffer:
        alerts.append(
            Alert(
                "warn",
                f"Projected balance falls below your {inr(forecast.buffer, 0)} buffer on "
                f"{fmt_date(forecast.first_below_buffer)}.",
                f"Lowest point in the next 30 days: {inr(forecast.min_balance)} on {fmt_date(forecast.min_date)}.",
                "cashflow",
                "See cash flow",
            )
        )

    overdue = pending_occurrences(user, today - timedelta(days=60), today - timedelta(days=1), "bill", today)
    if overdue:
        total = sum((p.amount for p in overdue), Decimal(0))
        alerts.append(
            Alert(
                "danger",
                f"{len(overdue)} bill(s) overdue, {inr(total)} in total.",
                "Due dates in the last 60 days with no payment or skip recorded.",
                "bill_list",
                "Review bills",
            )
        )

    for s in budgets:
        if s.state in ("over", "warn"):
            name = s.budget.category or "Overall"
            alerts.append(
                Alert(
                    "danger" if s.state == "over" else "warn",
                    f"{name} budget: {s.percent_used}% used.",
                    f"{inr(s.spent)} spent of {inr(s.budget.amount)} between {fmt_date(s.start, False)} and "
                    f"{fmt_date(s.end, False)}.",
                    "budget_list",
                    "Open budgets",
                )
            )

    uncategorised = Transaction.objects.filter(user=user, kind="expense", category__isnull=True).count()
    if uncategorised:
        alerts.append(
            Alert(
                "info",
                f"{uncategorised} expense(s) have no category.",
                "Categories make budgets and reports accurate.",
                "transaction_list",
                "Review",
            )
        )
    return alerts
