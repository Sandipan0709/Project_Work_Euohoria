"""Generate in-app notifications. Idempotent: each event has a stable key, so running this on
every page load (there is no background worker) never creates duplicates."""

from datetime import date, timedelta

from django.db.models import Sum
from django.utils import timezone

from planner.budgets import budget_statuses
from planner.models import Goal, GoalContribution, ScheduledItem
from planner.services import pending_occurrences, run_forecast

from .formatting import fmt_date, inr
from .models import Notification


def _notify(user, kind: str, key: str, message: str, link_name: str = "") -> bool:
    _, created = Notification.objects.get_or_create(
        user=user, key=key, defaults={"kind": kind, "message": message[:300], "link_name": link_name}
    )
    return created


def refresh_notifications(user, today: date | None = None) -> int:
    today = today or timezone.localdate()
    profile = user.profile
    created = 0

    if profile.notify_bills:
        horizon = today + timedelta(days=30)
        for p in pending_occurrences(user, today - timedelta(days=30), horizon, ScheduledItem.Flow.BILL, today):
            if p.state == "overdue":
                created += _notify(
                    user,
                    Notification.Kind.BILL_OVERDUE,
                    f"bill-overdue:{p.item.id}:{p.due_date}",
                    f"{p.item.name} ({inr(p.amount)}) was due on {fmt_date(p.due_date)}.",
                    "bill_list",
                )
            elif (p.due_date - today).days <= p.item.remind_days_before:
                created += _notify(
                    user,
                    Notification.Kind.BILL_DUE,
                    f"bill-due:{p.item.id}:{p.due_date}",
                    f"{p.item.name} ({inr(p.amount)}) is due on {fmt_date(p.due_date)}.",
                    "bill_list",
                )

    if profile.notify_budgets:
        for s in budget_statuses(user, today):
            if s.state in ("warn", "over"):
                name = s.budget.category or "Overall"
                verb = "has gone over its limit" if s.state == "over" else f"has used {s.percent_used}% of its limit"
                created += _notify(
                    user,
                    Notification.Kind.BUDGET,
                    f"budget:{s.budget.id}:{s.start}:{s.state}",
                    f"{name} budget {verb} ({inr(s.spent)} of {inr(s.budget.amount)}).",
                    "budget_list",
                )

    if profile.notify_low_balance:
        f = run_forecast(user, 30, today=today).forecast
        if f.first_shortfall:
            created += _notify(
                user,
                Notification.Kind.LOW_BALANCE,
                f"low-balance:shortfall:{f.first_shortfall}",
                f"Projected balance goes below zero on {fmt_date(f.first_shortfall)}.",
                "cashflow",
            )
        elif f.first_below_buffer:
            created += _notify(
                user,
                Notification.Kind.LOW_BALANCE,
                f"low-balance:buffer:{f.first_below_buffer}",
                f"Projected balance falls below your {inr(f.buffer, 0)} buffer on {fmt_date(f.first_below_buffer)}.",
                "cashflow",
            )

    if profile.notify_goals:
        for goal in Goal.objects.filter(user=user):
            saved = GoalContribution.objects.filter(goal=goal).aggregate(t=Sum("amount"))["t"] or 0
            percent = int(saved * 100 / goal.target_amount)
            for milestone in (50, 100):
                if percent >= milestone:
                    text = "reached its target" if milestone == 100 else "is halfway to its target"
                    created += _notify(
                        user,
                        Notification.Kind.GOAL,
                        f"goal:{goal.id}:{milestone}",
                        f"{goal.name} {text} ({inr(saved)} of {inr(goal.target_amount)}).",
                        "goal_list",
                    )
    return created
