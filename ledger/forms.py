from django import forms
from django.conf import settings

from core.forms import UserScopedModelForm

from .importing import DATE_FORMAT_LABELS
from .models import Account, Category, Transaction

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class AccountForm(UserScopedModelForm):
    class Meta:
        model = Account
        fields = ["name", "kind", "opening_balance", "notes"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = Account.objects.filter(user=self.user, name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("You already have an account with this name.")
        return name


class CategoryForm(UserScopedModelForm):
    class Meta:
        model = Category
        fields = ["name", "parent", "flow", "is_essential"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        parents = Category.objects.filter(user=self.user, parent__isnull=True).exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = parents
        self.fields["parent"].required = False

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = Category.objects.filter(user=self.user, name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("You already have a category with this name.")
        return name


class TransactionForm(UserScopedModelForm):
    class Meta:
        model = Transaction
        fields = [
            "kind",
            "amount",
            "date",
            "account",
            "to_account",
            "category",
            "payee",
            "payment_method",
            "tags",
            "notes",
        ]
        widgets = {
            "kind": forms.RadioSelect,
            "date": DATE_INPUT,
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {"to_account": "Transfer to", "account": "Account"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        accounts = Account.objects.filter(is_archived=False)
        self.scope("account", accounts)
        self.scope("to_account", accounts)
        self.scope("category", Category.objects.select_related("parent"))
        self.fields["to_account"].required = False
        self.fields["payment_method"].required = False

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("kind") != Transaction.Kind.TRANSFER:
            cleaned["to_account"] = None
            self.instance.to_account = None
        return cleaned


class TransactionFilterForm(forms.Form):
    q = forms.CharField(
        required=False, label="Search", widget=forms.TextInput(attrs={"placeholder": "Payee, note or tag"})
    )
    kind = forms.ChoiceField(required=False, choices=[("", "All types"), *Transaction.Kind.choices])
    account = forms.ModelChoiceField(required=False, queryset=Account.objects.none(), empty_label="All accounts")
    category = forms.ModelChoiceField(required=False, queryset=Category.objects.none(), empty_label="All categories")
    date_from = forms.DateField(required=False, widget=DATE_INPUT, label="From")
    date_to = forms.DateField(required=False, widget=DATE_INPUT, label="To")
    sort = forms.ChoiceField(
        required=False,
        choices=[
            ("-date", "Newest first"),
            ("date", "Oldest first"),
            ("-amount", "Largest first"),
            ("amount", "Smallest first"),
        ],
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(user=user)
        self.fields["category"].queryset = Category.objects.filter(user=user).select_related("parent")


class ImportUploadForm(forms.Form):
    account = forms.ModelChoiceField(
        queryset=Account.objects.none(), help_text="Transactions are added to this account."
    )
    file = forms.FileField(label="CSV file")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(user=user, is_archived=False)

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.name.lower().endswith((".csv", ".txt")):
            raise forms.ValidationError("Upload a .csv file.")
        if upload.size > settings.MAX_IMPORT_BYTES:
            raise forms.ValidationError(f"The file is larger than {settings.MAX_IMPORT_BYTES // (1024 * 1024)} MB.")
        return upload


class ImportMapForm(forms.Form):
    date = forms.ChoiceField(label="Date column")
    amount = forms.ChoiceField(required=False, label="Amount column")
    debit = forms.ChoiceField(required=False, label="Debit (money out) column")
    credit = forms.ChoiceField(required=False, label="Credit (money in) column")
    payee = forms.ChoiceField(required=False, label="Payee / description column")
    category = forms.ChoiceField(required=False, label="Category column")
    notes = forms.ChoiceField(required=False, label="Notes column")
    date_format = forms.ChoiceField(choices=list(DATE_FORMAT_LABELS.items()), label="Date format")
    amount_mode = forms.ChoiceField(
        choices=[
            ("signed", "Negative amounts are spending, positive are income"),
            ("all_expense", "Every row is spending"),
        ],
        label="How amounts are signed",
    )

    def __init__(self, *args, headers, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "— none —"), *[(h, h) for h in headers]]
        for name in ("date", "amount", "debit", "credit", "payee", "category", "notes"):
            self.fields[name].choices = choices
