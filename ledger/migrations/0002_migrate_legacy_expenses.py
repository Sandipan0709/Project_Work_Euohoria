"""Copy rows from the pre-ledger ``core.Expense`` table into ledger transactions.

For every user who had expenses this creates one account, "Cash (migrated)", whose
opening balance equals the sum of the migrated expenses so today's balance starts at
zero rather than at an invented negative figure. The user is expected to edit the
opening balance to match reality. Rows are tagged ``legacy-expense-<id>`` so the
migration is idempotent and reversible. The legacy table is left untouched.
"""
from decimal import Decimal

from django.db import migrations

DEFAULTS = [
    ("Rent & housing", "expense", True), ("Groceries", "expense", True), ("Food & dining", "expense", False),
    ("Transport", "expense", True), ("Utilities & bills", "expense", True), ("Education", "expense", True),
    ("Health", "expense", True), ("EMI & loans", "expense", True), ("Subscriptions", "expense", False),
    ("Entertainment", "expense", False), ("Shopping", "expense", False), ("Family", "expense", True),
    ("Other", "expense", False), ("Salary", "income", False), ("Other income", "income", False),
]
LEGACY_MAP = {
    "food": "Food & dining", "transport": "Transport", "bills": "Utilities & bills",
    "entertainment": "Entertainment", "other": "Other",
}
PREFIX = "legacy-expense-"


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Expense = apps.get_model("core", "Expense")
    Category = apps.get_model("ledger", "Category")
    Account = apps.get_model("ledger", "Account")
    Transaction = apps.get_model("ledger", "Transaction")

    for user in User.objects.all():
        for name, flow, essential in DEFAULTS:
            Category.objects.get_or_create(user=user, name=name, defaults={"flow": flow, "is_essential": essential})

        legacy = [e for e in Expense.objects.filter(user=user).order_by("id") if e.amount > 0]
        if not legacy:
            continue
        total = sum((e.amount for e in legacy), Decimal("0.00"))
        account, _ = Account.objects.get_or_create(
            user=user, name="Cash (migrated)",
            defaults={"kind": "cash", "opening_balance": total,
                      "notes": "Created from your earlier expenses. Edit the opening balance to match reality."},
        )
        categories = {c.name: c for c in Category.objects.filter(user=user)}
        for e in legacy:
            Transaction.objects.get_or_create(
                user=user, fingerprint=f"{PREFIX}{e.id}",
                defaults={
                    "kind": "expense", "account": account, "amount": e.amount, "date": e.date,
                    "category": categories.get(LEGACY_MAP.get(e.category, "Other")),
                    "notes": e.notes or "",
                },
            )


def backwards(apps, schema_editor):
    Transaction = apps.get_model("ledger", "Transaction")
    Account = apps.get_model("ledger", "Account")
    Transaction.objects.filter(fingerprint__startswith=PREFIX).delete()
    Account.objects.filter(name="Cash (migrated)", transactions__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_profile_min_buffer_profile_notify_bills_and_more"),
        ("ledger", "0001_initial"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
