import json

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ledger.models import Account, Category, ImportBatch, Transaction
from planner.models import Budget, Debt, DebtPayment, Goal, GoalContribution, Occurrence, ScheduledItem

from .forms import DeleteAccountForm, OnboardingForm, ProfileForm, UserRegisterForm
from .models import Notification
from .notifications import refresh_notifications
from .overview import build_overview


def home(request):
    return redirect("dashboard" if request.user.is_authenticated else "login")


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = UserRegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Your account is ready. Start by adding the account you spend from.")
        return redirect("onboarding")
    return render(request, "core/register.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def dashboard(request):
    if not Account.objects.filter(user=request.user, is_archived=False).exists():
        return redirect("onboarding")
    today = timezone.localdate()
    refresh_notifications(request.user, today)
    return render(request, "core/dashboard.html", {"o": build_overview(request.user, today), "today": today})


@login_required
def onboarding(request):
    if Account.objects.filter(user=request.user).exists():
        return redirect("dashboard")
    form = OnboardingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            Account.objects.create(
                user=request.user,
                name=data["account_name"].strip(),
                kind=data["account_kind"],
                opening_balance=data["opening_balance"],
            )
            request.user.profile.min_buffer = data["min_buffer"]
            request.user.profile.save(update_fields=["min_buffer"])
            if data["monthly_budget"]:
                Budget.objects.create(user=request.user, category=None, amount=data["monthly_budget"])
        messages.success(
            request, "Set up. Add your regular bills and income next so the forecast has something to work with."
        )
        return redirect("bill_list")
    return render(request, "core/onboarding.html", {"form": form})


@login_required
def notification_list(request):
    refresh_notifications(request.user)
    items = Notification.objects.filter(user=request.user)[:100]
    return render(request, "core/notifications.html", {"items": items})


@login_required
@require_POST
def notification_read(request):
    qs = Notification.objects.filter(user=request.user, read_at__isnull=True)
    pk = request.POST.get("id")
    if pk:
        qs = qs.filter(pk=pk)
    qs.update(read_at=timezone.now())
    return redirect("notification_list")


@login_required
def settings_page(request):
    form = ProfileForm(request.POST or None, instance=request.user.profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Preferences saved.")
        return redirect("settings")
    return render(request, "core/settings.html", {"form": form})


@login_required
def export_data(request):
    """Everything the user owns, as one JSON file. Decimals are exported as strings."""
    u = request.user

    def rows(qs, *exclude):
        return [{k: v for k, v in r.items() if k not in exclude} for r in qs.values()]

    payload = {
        "exported_at": timezone.now().isoformat(),
        "profile": rows(type(u.profile).objects.filter(user=u), "id", "user_id", "income"),
        "accounts": rows(Account.objects.filter(user=u)),
        "categories": rows(Category.objects.filter(user=u)),
        "transactions": rows(Transaction.objects.filter(user=u)),
        "import_batches": rows(ImportBatch.objects.filter(user=u)),
        "budgets": rows(Budget.objects.filter(user=u)),
        "scheduled_items": rows(ScheduledItem.objects.filter(user=u)),
        "occurrences": rows(Occurrence.objects.filter(item__user=u)),
        "debts": rows(Debt.objects.filter(user=u)),
        "debt_payments": rows(DebtPayment.objects.filter(debt__user=u)),
        "goals": rows(Goal.objects.filter(user=u)),
        "goal_contributions": rows(GoalContribution.objects.filter(goal__user=u)),
    }
    response = HttpResponse(json.dumps(payload, cls=DjangoJSONEncoder, indent=2), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="fintrackr-export-{timezone.localdate()}.json"'
    return response


@login_required
def delete_account(request):
    form = DeleteAccountForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        user = request.user
        logout(request)
        with transaction.atomic():
            # Transactions protect their accounts, so they must go first.
            Transaction.objects.filter(user=user).delete()
            user.delete()
        messages.success(request, "Your account and all its data were deleted.")
        return redirect("login")
    return render(request, "core/delete_account.html", {"form": form})
