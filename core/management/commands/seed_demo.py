"""Create a clearly synthetic demo user. Nothing here is real financial data.

python manage.py seed_demo            # creates user "demo" with a random password (printed once)
python manage.py seed_demo --reset    # deletes and recreates it
"""

import random
import secrets
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ledger.models import Account, Category, Transaction
from planner.models import Budget, Debt, Goal, GoalContribution, ScheduledItem
from planner.recurrence import add_months

DEMO_USER = "demo"
TAG = "demo"
SPEND = [  # (category, payees, low, high, per month)
    ("Groceries", ["Demo Mart", "Sample Fresh", "Test Kirana"], 250, 1400, 9),
    ("Food & dining", ["Sample Cafe", "Demo Biryani House", "Test Tiffin"], 120, 700, 10),
    ("Transport", ["Demo Metro", "Sample Cab", "Test Fuel"], 40, 600, 11),
    ("Entertainment", ["Demo Cinemas", "Sample Games"], 150, 900, 2),
    ("Shopping", ["Demo Store", "Sample Fashion"], 400, 3200, 2),
]


class Command(BaseCommand):
    help = "Create a synthetic demo user with a few months of made-up data."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete the demo user first.")

    @transaction.atomic
    def handle(self, *args, **options):
        existing = User.objects.filter(username=DEMO_USER)
        if existing.exists():
            if not options["reset"]:
                raise CommandError("The demo user already exists. Use --reset to recreate it.")
            Transaction.objects.filter(user__username=DEMO_USER).delete()
            existing.delete()

        password = secrets.token_urlsafe(12)
        user = User.objects.create_user(DEMO_USER, "demo@example.invalid", password)
        user.profile.min_buffer = D("8000")
        user.profile.save()
        today = timezone.localdate()
        rng = random.Random(2026)

        bank = Account.objects.create(
            user=user, name="Demo Savings", kind="bank", opening_balance=D("42000"), notes="Synthetic demo data"
        )
        wallet = Account.objects.create(user=user, name="Demo Wallet", kind="wallet", opening_balance=D("1500"))
        card = Account.objects.create(user=user, name="Demo Credit Card", kind="credit_card", opening_balance=D("0"))
        cat = {c.name: c for c in Category.objects.filter(user=user)}

        def add(kind, account, amount, day, **kw):
            Transaction.objects.create(
                user=user, kind=kind, account=account, amount=D(amount), date=day, tags=TAG, **kw
            )

        month_start = today.replace(day=1)
        for back in range(3, -1, -1):
            first = add_months(month_start, -back)
            add("income", bank, "65000", first, category=cat["Salary"], payee="Demo Employer Pvt Ltd")
            for name, day, amount in (
                ("Demo Landlord", 5, "15000"),
                ("Demo Power Co", 9, str(rng.randint(1100, 1900))),
                ("Demo Broadband", 11, "799"),
            ):
                due = first.replace(day=day)
                if due <= today:
                    add(
                        "expense",
                        bank,
                        amount,
                        due,
                        category=cat["Utilities & bills" if day != 5 else "Rent & housing"],
                        payee=name,
                    )
            if first.replace(day=7) <= today:
                add("expense", bank, "9000", first.replace(day=7), category=cat["EMI & loans"], payee="Demo Car Loan")
            for category_name, payees, low, high, count in SPEND:
                for _ in range(count):
                    day = first + timedelta(days=rng.randint(0, 27))
                    if day > today:
                        continue
                    account = rng.choice([bank, wallet, card])
                    add(
                        "expense",
                        account,
                        str(rng.randint(low, high)),
                        day,
                        category=cat[category_name],
                        payee=rng.choice(payees),
                    )
            repay = first.replace(day=20)
            if repay <= today:
                add("transfer", bank, "6000", repay, to_account=card, payee="Card payment")

        def next_due(day: int):
            candidate = month_start.replace(day=day)
            return candidate if candidate > today else add_months(candidate, 1)

        def schedule(name, amount, day, flow="bill", **kw):
            return ScheduledItem.objects.create(
                user=user, flow=flow, name=name, amount=D(amount), start_date=next_due(day), account=bank, **kw
            )

        schedule("Demo Landlord", "15000", 5, category=cat["Rent & housing"])
        schedule("Demo Power Co", "1500", 9, is_variable=True, category=cat["Utilities & bills"])
        schedule("Demo Broadband", "799", 11, category=cat["Utilities & bills"])
        schedule("Demo Streaming", "649", 14, is_subscription=True, category=cat["Subscriptions"])
        schedule("Demo Credit Card bill", "6000", 20, settles_account=card)
        schedule("Demo Employer salary", "65000", 1, flow="income", category=cat["Salary"])
        schedule("Demo freelance project", "12000", 25, flow="income", is_confirmed=False)

        Debt.objects.create(
            user=user,
            name="Demo Car Loan",
            lender="Demo Bank",
            principal=D("600000"),
            outstanding=D("410000"),
            annual_rate=D("9.25"),
            emi_amount=D("9000"),
            emi_day=7,
            account=bank,
        )
        Debt.objects.create(
            user=user,
            name="Demo Phone EMI",
            lender="Demo Finance",
            principal=D("36000"),
            outstanding=D("14000"),
            annual_rate=D("14"),
            emi_amount=D("3000"),
            emi_day=12,
            account=bank,
        )

        goal = Goal.objects.create(
            user=user,
            name="Demo emergency fund",
            target_amount=D("150000"),
            target_date=add_months(today, 14),
            priority=1,
            monthly_plan=D("8000"),
        )
        for back in (3, 2, 1):
            GoalContribution.objects.create(
                goal=goal, date=add_months(month_start, -back).replace(day=26), amount=D("7000")
            )
        Goal.objects.create(
            user=user, name="Demo trip", target_amount=D("40000"), target_date=add_months(today, 6), priority=3
        )

        Budget.objects.create(user=user, category=None, amount=D("55000"))
        for name, amount in (("Groceries", "8000"), ("Food & dining", "5000"), ("Entertainment", "2500")):
            Budget.objects.create(user=user, category=cat[name], amount=D(amount))

        self.stdout.write(self.style.SUCCESS(f"Created demo user '{DEMO_USER}' (all data is synthetic)."))
        self.stdout.write(f"Password: {password}")
