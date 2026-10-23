import calendar as pycalendar
import csv
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.formatting import fmt_date
from core.views_util import delete_page, form_page
from ledger.importing import safe_cell
from ledger.models import Account
from ledger.services import local_today

from . import debt as debt_math
from . import reports as report_data
from .budgets import budget_statuses, essential_split
from .forecast import HORIZONS, Event
from .forms import (
    BillForm,
    BudgetForm,
    ContributionForm,
    DebtForm,
    DebtPaymentForm,
    GoalForm,
    IncomeForm,
    PurchaseForm,
    RecordForm,
)
from .goals import project_goal
from .models import Budget, Debt, Goal, GoalContribution, Occurrence, ScheduledItem
from .recurrence import PER_YEAR
from .services import (
    SCENARIOS,
    AlreadyRecorded,
    _emi_dates,
    data_gaps,
    pending_occurrences,
    record_debt_payment,
    record_occurrence,
    run_forecast,
    undo_occurrence,
)

ZERO = Decimal("0.00")


# ---- cash flow ------------------------------------------------------------------------


@login_required
def cashflow(request):
    try:
        days = int(request.GET.get("days", 30))
    except ValueError:
        days = 30
    days = days if days in HORIZONS else 30
    scenario = request.GET.get("scenario", "conservative")
    scenario = scenario if scenario in SCENARIOS else "conservative"
    account = Account.objects.filter(user=request.user, pk=request.GET.get("account") or 0).first()
    result = run_forecast(request.user, days, scenario, account.id if account else None)
    other = "expected" if scenario == "conservative" else "conservative"
    other_end = run_forecast(request.user, days, other, account.id if account else None).forecast.end_balance
    rows = [(p.date, e, p.balance) for p in result.forecast.points for e in p.events]
    return render(
        request,
        "planner/cashflow.html",
        {
            "result": result,
            "f": result.forecast,
            "days": days,
            "horizons": HORIZONS,
            "scenario": scenario,
            "scenarios": SCENARIOS,
            "other_scenario": other,
            "other_end": other_end,
            "rows": rows,
            "account": account,
            "accounts": Account.objects.filter(user=request.user, is_archived=False),
            "gaps": data_gaps(request.user),
        },
    )


# ---- budgets --------------------------------------------------------------------------


@login_required
def budget_list(request):
    today = local_today()
    statuses = budget_statuses(request.user, today)
    start = today.replace(day=1)
    return render(
        request,
        "planner/budget_list.html",
        {
            "statuses": statuses,
            "split": essential_split(request.user, start, today),
            "month_start": start,
            "today": today,
        },
    )


@login_required
def budget_create(request):
    return form_page(
        request,
        BudgetForm,
        title="Add budget",
        back="budget_list",
        success_message="Budget added.",
        intro="Budgets compare planned and actual spending. Transfers are never counted as spending.",
    )


@login_required
def budget_update(request, pk):
    obj = get_object_or_404(Budget, pk=pk, user=request.user)
    return form_page(
        request, BudgetForm, instance=obj, title="Edit budget", back="budget_list", success_message="Budget updated."
    )


@login_required
def budget_delete(request, pk):
    obj = get_object_or_404(Budget, pk=pk, user=request.user)
    return delete_page(
        request,
        obj,
        title="Delete this budget?",
        back="budget_list",
        warning="Past transactions are not affected.",
        success_message="Budget deleted.",
    )


# ---- bills and income -----------------------------------------------------------------


@login_required
def bill_list(request):
    today = local_today()
    pending = pending_occurrences(request.user, today - timedelta(days=60), today + timedelta(days=45), today=today)
    groups = {"Overdue": [], "Next 7 days": [], "Later": []}
    for p in pending:
        key = "Overdue" if p.state == "overdue" else "Next 7 days" if (p.due_date - today).days <= 7 else "Later"
        groups[key].append(p)
    items = ScheduledItem.objects.filter(user=request.user).select_related("account")
    bills = [i for i in items if i.flow == "bill"]
    incomes = [i for i in items if i.flow == "income"]
    recorded = Occurrence.objects.filter(item__user=request.user).select_related("item")[:12]
    annual = sum(
        (i.amount * PER_YEAR[i.frequency] for i in bills if i.status == "active" and not i.settles_account_id),
        ZERO,
    )
    return render(
        request,
        "planner/bill_list.html",
        {
            "groups": groups,
            "bills": bills,
            "incomes": incomes,
            "recorded": recorded,
            "annual_total": annual,
            "today": today,
            "accounts": Account.objects.filter(user=request.user, is_archived=False),
            "any_pending": bool(pending),
        },
    )


@login_required
def bill_create(request):
    return form_page(
        request,
        BillForm,
        title="Add bill or subscription",
        back="bill_list",
        success_message="Bill added.",
        initial={"start_date": timezone.localdate()},
        intro="Use the first due date. Monthly bills keep their day of the month and move to the last day "
        "in shorter months.",
    )


@login_required
def income_create(request):
    return form_page(
        request,
        IncomeForm,
        title="Add expected income",
        back="bill_list",
        success_message="Income schedule added.",
        initial={"start_date": timezone.localdate()},
        intro="Only income marked as confirmed is counted in the conservative forecast.",
    )


@login_required
def schedule_update(request, pk):
    item = get_object_or_404(ScheduledItem, pk=pk, user=request.user)
    form_class = BillForm if item.flow == "bill" else IncomeForm
    return form_page(
        request,
        form_class,
        instance=item,
        title=f"Edit {item.name}",
        back="bill_list",
        success_message="Saved. Payments already recorded are unchanged.",
    )


@login_required
def schedule_delete(request, pk):
    item = get_object_or_404(ScheduledItem, pk=pk, user=request.user)
    return delete_page(
        request,
        item,
        title=f"Delete {item.name}?",
        back="bill_list",
        warning="Transactions already created from it stay in your records. To stop it without "
        "deleting history, set its status to cancelled instead.",
        success_message="Deleted.",
    )


@login_required
@require_POST
def schedule_record(request):
    item = get_object_or_404(ScheduledItem, pk=request.POST.get("item") or 0, user=request.user)
    try:
        due = date.fromisoformat(request.POST.get("due_date", ""))
    except ValueError:
        messages.error(request, "That due date is not valid.")
        return redirect("bill_list")
    action = request.POST.get("action")

    if action == "undo":
        occ = Occurrence.objects.filter(item=item, due_date=due).first()
        if occ:
            undo_occurrence(occ)
            messages.success(request, f"{item.name} due {fmt_date(due)} is pending again.")
        return redirect("bill_list")

    form = RecordForm(request.POST, user=request.user)
    outcome = Occurrence.Outcome.SKIPPED if action == "skip" else Occurrence.Outcome.DONE
    if outcome == Occurrence.Outcome.DONE and not form.is_valid():
        messages.error(request, "Check the date and amount: " + "; ".join(sum(form.errors.values(), [])))
        return redirect("bill_list")
    data = form.cleaned_data if form.is_valid() else {}
    try:
        record_occurrence(
            item, due, outcome, paid_on=data.get("paid_on"), amount=data.get("amount"), account=data.get("account")
        )
    except AlreadyRecorded:
        messages.info(request, "That due date was already recorded.")
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        verb = "Skipped" if outcome == Occurrence.Outcome.SKIPPED else ("Received" if item.flow == "income" else "Paid")
        messages.success(request, f"{verb}: {item.name}, due {fmt_date(due)}.")
    return redirect("bill_list")


# ---- debt -----------------------------------------------------------------------------


def _debt_summary(debt: Debt, today: date, extra: Decimal = ZERO) -> dict:
    plan = debt_math.amortize(debt.outstanding, debt.annual_rate, debt.emi_amount, extra)
    return {"debt": debt, "plan": plan, "payoff": plan.payoff_date(today)}


@login_required
def debt_list(request):
    today = local_today()
    debts = list(Debt.objects.filter(user=request.user))
    active = [d for d in debts if d.status == "active"]
    rows = [_debt_summary(d, today) for d in debts]
    return render(
        request,
        "planner/debt_list.html",
        {
            "rows": rows,
            "total_outstanding": sum((d.outstanding for d in active), ZERO),
            "total_emi": sum((d.emi_amount for d in active), ZERO),
        },
    )


@login_required
def debt_create(request):
    return form_page(
        request,
        DebtForm,
        title="Add loan or EMI",
        back="debt_list",
        success_message="Debt added.",
        intro="Outstanding balance, rate and EMI drive every estimate. Replace them with your lender's "
        "figures when you have them.",
    )


@login_required
def debt_update(request, pk):
    debt = get_object_or_404(Debt, pk=pk, user=request.user)
    return form_page(
        request, DebtForm, instance=debt, title=f"Edit {debt.name}", back="debt_list", success_message="Debt updated."
    )


@login_required
def debt_delete(request, pk):
    debt = get_object_or_404(Debt, pk=pk, user=request.user)
    return delete_page(
        request,
        debt,
        title=f"Delete {debt.name}?",
        back="debt_list",
        warning="Payment history for this debt is deleted. Expense transactions stay in your records.",
        success_message="Debt deleted.",
    )


@login_required
def debt_detail(request, pk):
    debt = get_object_or_404(Debt, pk=pk, user=request.user)
    today = local_today()
    form = DebtPaymentForm(
        request.POST or None,
        user=request.user,
        initial={"date": today, "amount": debt.emi_amount, "account": debt.account},
    )
    if request.method == "POST" and form.is_valid():
        try:
            record_debt_payment(
                debt, form.cleaned_data["date"], form.cleaned_data["amount"], form.cleaned_data["account"]
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Payment recorded.")
            return redirect("debt_detail", pk=debt.pk)
    try:
        extra = max(ZERO, Decimal(request.GET.get("extra") or 0))
    except Exception:
        extra = ZERO
    return render(
        request,
        "planner/debt_detail.html",
        {
            "debt": debt,
            "base": _debt_summary(debt, today),
            "scenario": _debt_summary(debt, today, extra),
            "extra": extra,
            "form": form,
            "payments": debt.payments.all()[:20],
            "interest_this_month": debt_math.monthly_interest(debt.outstanding, debt.annual_rate),
        },
    )


@login_required
def debt_compare(request):
    today = local_today()
    try:
        extra = max(ZERO, Decimal(request.GET.get("extra") or 0))
    except Exception:
        extra = ZERO
    inputs = [
        debt_math.DebtInput(d.name, d.outstanding, d.annual_rate, d.emi_amount)
        for d in Debt.objects.filter(user=request.user, status="active")
    ]
    plans = [debt_math.plan_payoff(inputs, extra, s) for s in ("avalanche", "snowball")] if inputs else []
    from .recurrence import add_months

    rows = [{"plan": p, "payoff": add_months(today, p.months) if p.months else None} for p in plans]
    return render(request, "planner/debt_compare.html", {"rows": rows, "extra": extra, "count": len(inputs)})


# ---- goals ----------------------------------------------------------------------------


@login_required
def goal_list(request):
    today = local_today()
    rows = []
    for goal in Goal.objects.filter(user=request.user):
        contributions = list(goal.contributions.values_list("date", "amount"))
        saved = sum((a for _, a in contributions), ZERO)
        rows.append(
            {
                "goal": goal,
                "p": project_goal(goal.target_amount, saved, today, goal.target_date, goal.monthly_plan, contributions),
                "form": ContributionForm(initial={"date": today}),
            }
        )
    return render(request, "planner/goal_list.html", {"rows": rows})


@login_required
def goal_create(request):
    return form_page(
        request,
        GoalForm,
        title="Add savings goal",
        back="goal_list",
        success_message="Goal added.",
        intro="Contributions are tracked on the goal. They do not move money between your accounts.",
    )


@login_required
def goal_update(request, pk):
    goal = get_object_or_404(Goal, pk=pk, user=request.user)
    return form_page(
        request, GoalForm, instance=goal, title=f"Edit {goal.name}", back="goal_list", success_message="Goal updated."
    )


@login_required
def goal_delete(request, pk):
    goal = get_object_or_404(Goal, pk=pk, user=request.user)
    return delete_page(
        request,
        goal,
        title=f"Delete {goal.name}?",
        back="goal_list",
        warning="Its contribution history is deleted.",
        success_message="Goal deleted.",
    )


@login_required
@require_POST
def goal_contribute(request, pk):
    goal = get_object_or_404(Goal, pk=pk, user=request.user)
    form = ContributionForm(request.POST)
    if form.is_valid():
        GoalContribution.objects.create(goal=goal, **form.cleaned_data)
        messages.success(request, f"Added to {goal.name}.")
    else:
        messages.error(request, "Enter a valid date and an amount greater than zero.")
    return redirect("goal_list")


# ---- purchase planner -----------------------------------------------------------------

MAX_PURCHASE_LEAD_DAYS = 180


def _scenario(user, label, amount, when, today):
    horizon = max(90, (when - today).days + 30)
    when = max(when, today)
    event = Event(when, -amount, label, "purchase")
    result = run_forecast(user, horizon, "conservative", extra_events=(event,))
    return result.forecast


@login_required
def purchase(request):
    today = local_today()
    form = PurchaseForm(request.GET or None, initial={"date": today, "postpone_days": 30})
    context = {"form": form, "gaps": data_gaps(request.user), "today": today}
    if request.GET and form.is_valid():
        d = form.cleaned_data
        if d["date"] > today + timedelta(days=MAX_PURCHASE_LEAD_DAYS):
            form.add_error("date", f"Pick a date within {MAX_PURCHASE_LEAD_DAYS} days.")
        else:
            later = d["date"] + timedelta(days=d["postpone_days"])
            context.update(
                {
                    "d": d,
                    "later": later,
                    "baseline": run_forecast(request.user, 90, "conservative").forecast,
                    "now": _scenario(request.user, d["name"], d["amount"], d["date"], today),
                    "postponed": _scenario(request.user, d["name"], d["amount"], later, today),
                }
            )
    return render(request, "planner/purchase.html", context)


# ---- reports --------------------------------------------------------------------------

PERIODS = {"month": "This month", "30": "Last 30 days", "90": "Last 90 days", "year": "This year"}


def _period(key: str, today: date) -> tuple[date, date]:
    if key == "30":
        return today - timedelta(days=29), today
    if key == "90":
        return today - timedelta(days=89), today
    if key == "year":
        return today.replace(month=1, day=1), today
    return today.replace(day=1), today


@login_required
def reports(request):
    today = local_today()
    key = request.GET.get("period", "month")
    key = key if key in PERIODS else "month"
    start, end = _period(key, today)
    goals = []
    for goal in Goal.objects.filter(user=request.user):
        saved = goal.contributions.aggregate(t=Sum("amount"))["t"] or ZERO
        goals.append({"goal": goal, "saved": saved, "percent": min(100, int(saved * 100 / goal.target_amount))})
    debts = Debt.objects.filter(user=request.user)
    debt_rows = [
        {"debt": d, "paid_percent": int((d.principal - d.outstanding) * 100 / d.principal) if d.principal else 0}
        for d in debts
    ]
    return render(
        request,
        "planner/reports.html",
        {
            "periods": PERIODS,
            "key": key,
            "start": start,
            "end": end,
            "today": today,
            "cmp": report_data.compare_periods(request.user, start, end),
            "categories": report_data.category_breakdown(request.user, start, end),
            "merchants": report_data.top_merchants(request.user, start, end),
            "series": report_data.monthly_series(request.user, today),
            "recurring": report_data.recurring_summary(request.user),
            "budgets": budget_statuses(request.user, today),
            "goals": goals,
            "debts": debt_rows,
            "insights": report_data.insights(request.user, today),
        },
    )


@login_required
def reports_export(request):
    today = local_today()
    key = request.GET.get("period", "month")
    start, end = _period(key if key in PERIODS else "month", today)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="fintrackr-report-{end}.csv"'
    w = csv.writer(response)
    w.writerow(["Spending by category", f"{start} to {end}"])
    w.writerow(["Category", "Amount", "Share %"])
    for r in report_data.category_breakdown(request.user, start, end):
        w.writerow([safe_cell(r["name"]), r["amount"], r["percent"]])
    w.writerow([])
    w.writerow(["Monthly income and spending"])
    w.writerow(["Month", "Income", "Spending", "Saved"])
    for s in report_data.monthly_series(request.user, today):
        w.writerow([s["label"], s["income"], s["expenses"], s["savings"]])
    return response


# ---- calendar -------------------------------------------------------------------------


@login_required
def calendar_view(request):
    today = local_today()
    try:
        year, month = (int(x) for x in request.GET.get("month", f"{today.year}-{today.month}").split("-"))
        first = date(year, month, 1)
    except (ValueError, TypeError):
        first = today.replace(day=1)
    last = first.replace(day=pycalendar.monthrange(first.year, first.month)[1])

    entries = []  # (date, label, amount, kind, status, link)
    for p in pending_occurrences(request.user, first, last, today=today):
        status = "overdue" if p.state == "overdue" else "due"
        entries.append((p.due_date, p.item.name, p.amount, p.item.flow, status, "bill_list"))
    for occ in Occurrence.objects.filter(
        item__user=request.user, due_date__gte=first, due_date__lte=last
    ).select_related("item"):
        entries.append(
            (occ.due_date, occ.item.name, occ.amount or occ.item.amount, occ.item.flow, occ.outcome, "bill_list")
        )
    for debt in Debt.objects.filter(user=request.user, status="active", emi_amount__gt=0):
        for due in _emi_dates(debt, first, last, set()):
            entries.append(
                (due, f"{debt.name} EMI", debt.emi_amount, "emi", "due" if due >= today else "past", "debt_list")
            )
    for goal in Goal.objects.filter(user=request.user, target_date__gte=first, target_date__lte=last):
        entries.append((goal.target_date, f"{goal.name} target", goal.target_amount, "goal", "due", "goal_list"))
    entries.sort(key=lambda e: (e[0], e[1]))

    by_day = defaultdict(list)
    for e in entries:
        by_day[e[0]].append(e)
    weeks = pycalendar.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month)
    prev_month = (first - timedelta(days=1)).replace(day=1)
    next_month = last + timedelta(days=1)
    return render(
        request,
        "planner/calendar.html",
        {
            "first": first,
            "weeks": [[(d, by_day.get(d, []), d.month == first.month) for d in w] for w in weeks],
            "entries": entries,
            "view": "agenda" if request.GET.get("view") == "agenda" else "month",
            "today": today,
            "prev": f"{prev_month.year}-{prev_month.month}",
            "next": f"{next_month.year}-{next_month.month}",
        },
    )
