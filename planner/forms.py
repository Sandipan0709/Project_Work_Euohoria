from decimal import Decimal

from django import forms

from core.forms import UserScopedModelForm
from ledger.forms import DATE_INPUT
from ledger.models import Account, Category

from .models import Budget, Debt, Goal, ScheduledItem

MONEY_INPUT = forms.NumberInput(attrs={"step": "0.01", "min": "0.01"})


class BudgetForm(UserScopedModelForm):
    class Meta:
        model = Budget
        fields = ["category", "period", "amount", "warn_percent"]
        widgets = {"amount": MONEY_INPUT}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope("category", Category.objects.filter(flow="expense").select_related("parent"))
        self.fields["category"].required = False
        self.fields["category"].empty_label = "Everything (overall limit)"

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        clash = Budget.objects.filter(
            user=self.user, category=cleaned.get("category"), period=cleaned.get("period")
        ).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(
                "You already have a budget for this category and period. Edit that one instead."
            )
        return cleaned

    def clean_warn_percent(self):
        value = self.cleaned_data["warn_percent"]
        if not 1 <= value <= 100:
            raise forms.ValidationError("Enter a percentage between 1 and 100.")
        return value


class _ScheduleBase(UserScopedModelForm):
    FLOW = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope("account", Account.objects.filter(is_archived=False))
        self.scope(
            "category",
            Category.objects.filter(flow="expense" if self.FLOW == "bill" else "income").select_related("parent"),
        )
        self.instance.flow = self.FLOW

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The end date cannot be before the first due date.")
        return cleaned


class BillForm(_ScheduleBase):
    FLOW = "bill"

    class Meta:
        model = ScheduledItem
        fields = [
            "name",
            "amount",
            "frequency",
            "start_date",
            "end_date",
            "status",
            "account",
            "category",
            "settles_account",
            "is_variable",
            "is_subscription",
            "remind_days_before",
            "notes",
        ]
        widgets = {"start_date": DATE_INPUT, "end_date": DATE_INPUT, "amount": MONEY_INPUT}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope("settles_account", Account.objects.filter(kind="credit_card"))
        self.fields["settles_account"].label = "Pays off credit card"


class IncomeForm(_ScheduleBase):
    FLOW = "income"

    class Meta:
        model = ScheduledItem
        fields = [
            "name",
            "amount",
            "frequency",
            "start_date",
            "end_date",
            "status",
            "account",
            "category",
            "is_confirmed",
            "notes",
        ]
        widgets = {"start_date": DATE_INPUT, "end_date": DATE_INPUT, "amount": MONEY_INPUT}
        labels = {"start_date": "Next expected date", "is_confirmed": "Confirmed (e.g. fixed salary)"}


class RecordForm(forms.Form):
    """Mark one due date as paid/received."""

    paid_on = forms.DateField(widget=DATE_INPUT, label="Date")
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"), required=False)
    account = forms.ModelChoiceField(queryset=Account.objects.none(), required=False)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(user=user, is_archived=False)


class DebtForm(UserScopedModelForm):
    class Meta:
        model = Debt
        fields = [
            "name",
            "lender",
            "principal",
            "outstanding",
            "annual_rate",
            "emi_amount",
            "emi_day",
            "account",
            "status",
        ]
        widgets = {"emi_day": forms.NumberInput(attrs={"min": 1, "max": 31})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope("account", Account.objects.filter(is_archived=False, kind__in=Account.LIQUID_KINDS))

    def clean(self):
        cleaned = super().clean()
        principal, outstanding = cleaned.get("principal"), cleaned.get("outstanding")
        if principal is not None and principal <= 0:
            self.add_error("principal", "Enter an amount greater than zero.")
        if outstanding is not None and outstanding < 0:
            self.add_error("outstanding", "The balance cannot be negative.")
        day = cleaned.get("emi_day")
        if day is not None and not 1 <= day <= 31:
            self.add_error("emi_day", "Enter a day between 1 and 31.")
        return cleaned


class DebtPaymentForm(forms.Form):
    date = forms.DateField(widget=DATE_INPUT)
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    account = forms.ModelChoiceField(queryset=Account.objects.none(), required=False)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(user=user, is_archived=False)


class GoalForm(UserScopedModelForm):
    class Meta:
        model = Goal
        fields = ["name", "target_amount", "target_date", "priority", "monthly_plan"]
        widgets = {"target_date": DATE_INPUT, "target_amount": MONEY_INPUT}

    def clean_target_amount(self):
        value = self.cleaned_data["target_amount"]
        if value <= 0:
            raise forms.ValidationError("Enter an amount greater than zero.")
        return value


class ContributionForm(forms.Form):
    date = forms.DateField(widget=DATE_INPUT)
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))


class PurchaseForm(forms.Form):
    name = forms.CharField(max_length=80, label="What are you buying?")
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    date = forms.DateField(widget=DATE_INPUT, label="Planned date")
    postpone_days = forms.IntegerField(
        min_value=1, max_value=365, initial=30, label="Compare with postponing by (days)"
    )
