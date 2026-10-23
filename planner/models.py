"""Planning domain: budgets, scheduled bills/income, debts and savings goals."""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from ledger.models import MONEY, Account, Category, Transaction


class Budget(models.Model):
    """A spending limit. ``category=None`` means the overall limit for all spending."""

    class Period(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        WEEKLY = "weekly", "Weekly (Mon–Sun)"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="budgets")
    category = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="budgets",
        help_text="Leave empty for an overall limit.",
    )
    period = models.CharField(max_length=10, choices=Period.choices, default=Period.MONTHLY)
    amount = models.DecimalField(**MONEY)
    warn_percent = models.PositiveSmallIntegerField(default=80, help_text="Warn when this share is used.")

    class Meta:
        ordering = ["category__name"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="budget_amount_positive"),
            models.CheckConstraint(condition=Q(warn_percent__gte=1, warn_percent__lte=100), name="budget_warn_range"),
            models.UniqueConstraint(
                fields=["user", "category", "period"],
                condition=Q(category__isnull=False),
                name="uniq_budget_category_period",
            ),
            models.UniqueConstraint(
                fields=["user", "period"], condition=Q(category__isnull=True), name="uniq_budget_overall_period"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.category or 'Overall'} ({self.period})"


class ScheduledItem(models.Model):
    """A recurring (or one-off) bill or expected income."""

    class Flow(models.TextChoices):
        BILL = "bill", "Bill"
        INCOME = "income", "Expected income"

    class Frequency(models.TextChoices):
        ONCE = "once", "One time"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Every 3 months"
        YEARLY = "yearly", "Yearly"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scheduled_items")
    flow = models.CharField(max_length=10, choices=Flow.choices, default=Flow.BILL)
    name = models.CharField(max_length=100)
    amount = models.DecimalField(**MONEY, help_text="Expected amount. Variable bills use this as the estimate.")
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.MONTHLY)
    start_date = models.DateField(help_text="First due date. The day of month is kept for monthly schedules.")
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    account = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="scheduled_items",
        help_text="Account the money moves through.",
    )
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    settles_account = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="settled_by",
        help_text="Credit card this bill pays off. Payment is recorded as a transfer, not an expense.",
    )
    is_variable = models.BooleanField(default=False, help_text="Amount changes from one bill to the next.")
    is_subscription = models.BooleanField(default=False)
    is_confirmed = models.BooleanField(
        default=True, help_text="Income only: confirmed income is included in the conservative forecast."
    )
    remind_days_before = models.PositiveSmallIntegerField(default=3)
    notes = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="schedule_amount_positive")]

    def __str__(self) -> str:
        return self.name


class Occurrence(models.Model):
    """The recorded outcome of one due date. Dates with no row are still pending."""

    class Outcome(models.TextChoices):
        DONE = "done", "Paid / received"
        SKIPPED = "skipped", "Skipped"

    item = models.ForeignKey(ScheduledItem, on_delete=models.CASCADE, related_name="occurrences")
    due_date = models.DateField()
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    amount = models.DecimalField(**MONEY, default=Decimal("0.00"))
    transaction = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-due_date"]
        constraints = [models.UniqueConstraint(fields=["item", "due_date"], name="uniq_occurrence_per_due_date")]

    def __str__(self) -> str:
        return f"{self.item} due {self.due_date} ({self.outcome})"


class Debt(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="debts")
    name = models.CharField(max_length=100)
    lender = models.CharField(max_length=100, blank=True)
    principal = models.DecimalField("Original principal", **MONEY)
    outstanding = models.DecimalField(
        "Outstanding balance", **MONEY, help_text="Use the figure from your lender's statement when you have it."
    )
    annual_rate = models.DecimalField("Interest rate (% per year)", max_digits=5, decimal_places=2)
    emi_amount = models.DecimalField("EMI / monthly instalment", **MONEY)
    emi_day = models.PositiveSmallIntegerField("EMI day of month", default=5)
    account = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="debts",
        help_text="Account the EMI is paid from.",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["status", "name"]
        constraints = [
            models.CheckConstraint(condition=Q(emi_day__gte=1, emi_day__lte=31), name="debt_emi_day_range"),
            models.CheckConstraint(condition=Q(annual_rate__gte=0), name="debt_rate_nonnegative"),
            models.CheckConstraint(condition=Q(outstanding__gte=0), name="debt_outstanding_nonnegative"),
        ]

    def __str__(self) -> str:
        return self.name


class DebtPayment(models.Model):
    debt = models.ForeignKey(Debt, on_delete=models.CASCADE, related_name="payments")
    date = models.DateField()
    amount = models.DecimalField(**MONEY)
    principal_part = models.DecimalField(**MONEY, help_text="Estimated share that reduced the balance.")
    transaction = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="debtpay_amount_positive")]

    def __str__(self) -> str:
        return f"{self.debt} payment {self.amount} on {self.date}"


class Goal(models.Model):
    class Priority(models.IntegerChoices):
        HIGH = 1, "High"
        MEDIUM = 2, "Medium"
        LOW = 3, "Low"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="goals")
    name = models.CharField(max_length=100)
    target_amount = models.DecimalField(**MONEY)
    target_date = models.DateField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(choices=Priority.choices, default=Priority.MEDIUM)
    monthly_plan = models.DecimalField("Planned monthly contribution", **MONEY, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["priority", "target_date", "name"]
        constraints = [models.CheckConstraint(condition=Q(target_amount__gt=0), name="goal_target_positive")]

    def __str__(self) -> str:
        return self.name


class GoalContribution(models.Model):
    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name="contributions")
    date = models.DateField()
    amount = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="goalcontrib_amount_positive")]

    def __str__(self) -> str:
        return f"{self.goal} +{self.amount} on {self.date}"
