from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from ledger.models import Account

from .models import Profile


class UserScopedModelForm(forms.ModelForm):
    """ModelForm that knows its owner, so model validation sees the user and querysets stay private."""

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.instance.user = user

    def scope(self, field: str, queryset) -> None:
        self.fields[field].queryset = queryset.filter(user=self.user)


class UserRegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ["username", "email", "password1", "password2"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["min_buffer", "notify_bills", "notify_budgets", "notify_low_balance", "notify_goals"]
        widgets = {"min_buffer": forms.NumberInput(attrs={"step": "100", "min": "0"})}

    def clean_min_buffer(self):
        value = self.cleaned_data["min_buffer"]
        if value < 0:
            raise forms.ValidationError("The buffer cannot be negative.")
        return value


class OnboardingForm(forms.Form):
    account_name = forms.CharField(max_length=80, initial="Main account")
    account_kind = forms.ChoiceField(choices=[c for c in Account.Kind.choices if c[0] != "credit_card"], initial="bank")
    opening_balance = forms.DecimalField(
        max_digits=14, decimal_places=2, initial=0, help_text="What this account holds right now."
    )
    min_buffer = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=0,
        initial=5000,
        label="Minimum balance buffer",
        help_text="You will be warned when the forecast drops below this.",
    )
    monthly_budget = forms.DecimalField(
        max_digits=14, decimal_places=2, min_value=1, required=False, label="Overall monthly spending limit (optional)"
    )


class DeleteAccountForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, label="Confirm with your password")
    confirm = forms.BooleanField(label="I understand this permanently deletes all my data")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not authenticate(username=self.user.get_username(), password=password):
            raise forms.ValidationError("That password is not correct.")
        return password
