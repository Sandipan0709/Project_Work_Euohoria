"""Ledger domain: accounts, categories, transactions and CSV import batches.

Money rules (see docs/ARCHITECTURE.md):

* Amounts are always stored as positive ``Decimal`` values; ``kind`` decides the
  direction.
* A *transfer* moves money between two of the user's own accounts. It is never
  income or spending.
* A credit card is an account whose balance is normally negative (what you owe).
  Card purchases are ordinary expenses on the card account; paying the card is a
  transfer from a bank account to the card account. Nothing is counted twice.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

MONEY = {"max_digits": 14, "decimal_places": 2}


class Account(models.Model):
    class Kind(models.TextChoices):
        CASH = "cash", "Cash"
        BANK = "bank", "Bank account"
        WALLET = "wallet", "Wallet / UPI balance"
        CREDIT_CARD = "credit_card", "Credit card"

    LIQUID_KINDS = (Kind.CASH, Kind.BANK, Kind.WALLET)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accounts")
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.BANK)
    opening_balance = models.DecimalField(
        **MONEY,
        default=Decimal("0.00"),
        help_text=(
            "Balance before your first recorded transaction. "
            "For a credit card, enter what you owe as a negative number."
        ),
    )
    is_archived = models.BooleanField(default=False)
    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["is_archived", "name"]
        constraints = [models.UniqueConstraint(fields=["user", "name"], name="uniq_account_name_per_user")]

    def __str__(self) -> str:
        return self.name

    @property
    def is_liquid(self) -> bool:
        return self.kind in self.LIQUID_KINDS


class Category(models.Model):
    class Flow(models.TextChoices):
        EXPENSE = "expense", "Expense"
        INCOME = "income", "Income"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=60)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    flow = models.CharField(max_length=10, choices=Flow.choices, default=Flow.EXPENSE)
    is_essential = models.BooleanField(
        default=False, help_text="Essential spending (rent, groceries, bills) versus discretionary."
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"
        constraints = [models.UniqueConstraint(fields=["user", "name"], name="uniq_category_name_per_user")]

    def __str__(self) -> str:
        return f"{self.parent.name} › {self.name}" if self.parent_id else self.name

    def clean(self):
        if self.parent_id and self.parent.parent_id:
            raise ValidationError({"parent": "Categories support one level of subcategories."})
        if self.parent_id and self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": "A category cannot be its own parent."})


class ImportBatch(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="import_batches")
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="import_batches")
    filename = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    rows_total = models.PositiveIntegerField(default=0)
    rows_imported = models.PositiveIntegerField(default=0)
    rows_duplicate = models.PositiveIntegerField(default=0)
    rows_invalid = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.filename} ({self.rows_imported} imported)"


class Transaction(models.Model):
    class Kind(models.TextChoices):
        EXPENSE = "expense", "Expense"
        INCOME = "income", "Income"
        REFUND = "refund", "Refund"
        TRANSFER = "transfer", "Transfer"

    class Method(models.TextChoices):
        UPI = "upi", "UPI"
        CARD = "card", "Card"
        CASH = "cash", "Cash"
        NETBANKING = "netbanking", "Net banking"
        AUTOPAY = "autopay", "Auto-debit"
        OTHER = "other", "Other"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.EXPENSE)
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="transactions",
        help_text="For transfers this is the account money leaves.",
    )
    to_account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="incoming_transfers"
    )
    amount = models.DecimalField(**MONEY)
    date = models.DateField()
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
    )
    payee = models.CharField("Merchant or payee", max_length=120, blank=True)
    notes = models.TextField(blank=True)
    tags = models.CharField(max_length=120, blank=True, help_text="Comma-separated.")
    payment_method = models.CharField(max_length=12, choices=Method.choices, blank=True)
    import_batch = models.ForeignKey(
        ImportBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
    )
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["user", "date"]),
            models.Index(fields=["user", "account", "date"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="txn_amount_positive"),
            models.CheckConstraint(
                condition=(Q(kind="transfer", to_account__isnull=False) & ~Q(to_account=F("account")))
                | (~Q(kind="transfer") & Q(to_account__isnull=True)),
                name="txn_transfer_shape",
            ),
            models.UniqueConstraint(
                fields=["user", "fingerprint"], condition=~Q(fingerprint=""), name="uniq_txn_fingerprint_per_user"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.kind} {self.amount}"

    def clean(self):
        errors = {}
        if self.amount is not None and self.amount <= 0:
            errors["amount"] = "Enter an amount greater than zero."
        if self.kind == self.Kind.TRANSFER:
            if not self.to_account_id:
                errors["to_account"] = "Choose the account the money goes to."
            elif self.to_account_id == self.account_id:
                errors["to_account"] = "A transfer needs two different accounts."
        elif self.to_account_id:
            errors["to_account"] = "Only transfers have a destination account."
        if self.account_id and self.user_id and self.account.user_id != self.user_id:
            errors["account"] = "Unknown account."
        if errors:
            raise ValidationError(errors)

    @property
    def tag_list(self) -> list[str]:
        return [t.strip() for t in self.tags.split(",") if t.strip()]
