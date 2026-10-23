from django.contrib.auth.models import User
from django.db import models


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    age = models.PositiveIntegerField(null=True, blank=True)
    income = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    min_buffer = models.DecimalField(
        "Minimum balance buffer",
        max_digits=14,
        decimal_places=2,
        default=5000,
        help_text="Forecasts warn when your projected balance falls below this.",
    )
    notify_bills = models.BooleanField("Bill reminders and overdue alerts", default=True)
    notify_budgets = models.BooleanField("Budget threshold alerts", default=True)
    notify_low_balance = models.BooleanField("Projected low-balance alerts", default=True)
    notify_goals = models.BooleanField("Savings milestones", default=True)

    def __str__(self):
        return f"{self.user.username} Profile"


class Budget(models.Model):
    """Legacy single monthly limit. Superseded by planner.Budget; kept so no data is lost."""

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    monthly_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.user.username} Budget: {self.monthly_limit}"


class Expense(models.Model):
    """Legacy expense row. Copied into ledger.Transaction by ledger migration 0002; kept read-only."""

    CATEGORY_CHOICES = [
        ("food", "Food"),
        ("transport", "Transport"),
        ("bills", "Bills"),
        ("entertainment", "Entertainment"),
        ("other", "Other"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    date = models.DateField()
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.category} - {self.amount}"


class Notification(models.Model):
    """In-app notification. ``key`` makes generation idempotent (one row per event)."""

    class Kind(models.TextChoices):
        BILL_DUE = "bill_due", "Bill due soon"
        BILL_OVERDUE = "bill_overdue", "Bill overdue"
        BUDGET = "budget", "Budget"
        LOW_BALANCE = "low_balance", "Low balance"
        GOAL = "goal", "Savings goal"
        IMPORT = "import", "Import"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    key = models.CharField(max_length=120)
    message = models.CharField(max_length=300)
    link_name = models.CharField(max_length=60, blank=True, help_text="URL name to open from the notification.")
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="uniq_notification_key_per_user")]

    def __str__(self) -> str:
        return self.message
