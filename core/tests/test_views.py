import json
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Notification
from ledger.models import Account, Category, ImportBatch, Transaction
from ledger.tests.factories import category, make_account, make_txn, make_user
from planner.models import Budget, Debt, Goal, GoalContribution, Occurrence, ScheduledItem

PASSWORD = "a-long-test-password-1"
TODAY = timezone.localdate()


def populated(username="alice"):
    """A user with one of everything, dated relative to today."""
    user = make_user(username)
    bank = make_account(user, "Bank", opening="50000")
    card = make_account(user, "Card", Account.Kind.CREDIT_CARD, "-2000")
    food = category(user, "Food & dining")
    make_txn(user, bank, "income", 60000, TODAY - timedelta(days=3), category=category(user, "Salary"))
    make_txn(user, bank, "expense", 450, TODAY - timedelta(days=2), category=food, payee="Swiggy")
    make_txn(user, bank, "expense", 900, TODAY - timedelta(days=1), payee="Uncategorised shop")
    make_txn(user, bank, "transfer", 1000, TODAY - timedelta(days=1), to_account=card)
    Budget.objects.create(user=user, category=None, amount=D("5000"))
    Budget.objects.create(user=user, category=food, amount=D("500"))
    ScheduledItem.objects.create(
        user=user, name="Rent", amount=D("12000"), start_date=TODAY + timedelta(days=3), account=bank
    )
    ScheduledItem.objects.create(
        user=user,
        name="Old bill",
        amount=D("300"),
        start_date=TODAY - timedelta(days=5),
        frequency="once",
        account=bank,
    )
    ScheduledItem.objects.create(
        user=user, flow="income", name="Salary", amount=D("60000"), start_date=TODAY + timedelta(days=9), account=bank
    )
    ScheduledItem.objects.create(
        user=user,
        name="Netflix",
        amount=D("649"),
        start_date=TODAY + timedelta(days=5),
        is_subscription=True,
        account=bank,
    )
    Debt.objects.create(
        user=user,
        name="Car loan",
        principal=D("400000"),
        outstanding=D("250000"),
        annual_rate=D("9"),
        emi_amount=D("9000"),
        account=bank,
    )
    Debt.objects.create(
        user=user,
        name="Phone",
        principal=D("30000"),
        outstanding=D("12000"),
        annual_rate=D("14"),
        emi_amount=D("2000"),
        account=bank,
    )
    goal = Goal.objects.create(
        user=user, name="Laptop", target_amount=D("80000"), target_date=TODAY + timedelta(days=200)
    )
    GoalContribution.objects.create(goal=goal, date=TODAY - timedelta(days=10), amount=D("45000"))
    return user


class PageRenderTests(TestCase):
    PAGES = [
        "dashboard",
        "cashflow",
        "budget_list",
        "bill_list",
        "goal_list",
        "debt_list",
        "debt_compare",
        "purchase",
        "transaction_list",
        "account_list",
        "category_list",
        "import_upload",
        "import_history",
        "reports",
        "calendar",
        "notification_list",
        "settings",
        "export_data",
        "export_csv",
        "reports_export",
        "transaction_create",
        "account_create",
        "category_create",
        "budget_create",
        "bill_create",
        "income_create",
        "debt_create",
        "goal_create",
    ]

    @classmethod
    def setUpTestData(cls):
        cls.user = populated()

    def setUp(self):
        self.client.login(username="alice", password=PASSWORD)

    def test_every_page_renders_for_a_populated_user(self):
        for name in self.PAGES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200, name)

    def test_calendar_agenda_and_month_navigation(self):
        for query in ("?view=agenda", "?month=2025-02", "?month=garbage", "?month=2025-13"):
            self.assertEqual(self.client.get(reverse("calendar") + query).status_code, 200, query)

    def test_cashflow_options_and_bad_params(self):
        acct = Account.objects.get(user=self.user, name="Bank")
        for query in (
            "?days=7",
            "?days=90&scenario=expected",
            f"?account={acct.id}",
            "?days=abc",
            "?scenario=nope",
            "?account=999999",
        ):
            self.assertEqual(self.client.get(reverse("cashflow") + query).status_code, 200, query)

    def test_overview_shows_traceable_figures(self):
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("Available to spend", html)
        self.assertIn("Estimate", html)
        self.assertIn("Actual", html)
        self.assertIn("₹", html)
        self.assertIn("Old bill", html + self.client.get(reverse("bill_list")).content.decode())

    def test_indian_number_format_in_pages(self):
        make_txn(self.user, Account.objects.get(user=self.user, name="Bank"), "income", 1234567, TODAY)
        self.assertIn("₹", self.client.get(reverse("transaction_list")).content.decode())
        self.assertContains(self.client.get(reverse("transaction_list")), "12,34,567.00")

    def test_reports_periods(self):
        for key in ("month", "30", "90", "year", "bogus"):
            self.assertEqual(self.client.get(reverse("reports"), {"period": key}).status_code, 200)

    def test_purchase_planner_results_and_validation(self):
        r = self.client.get(
            reverse("purchase"), {"name": "Laptop", "amount": "40000", "date": TODAY.isoformat(), "postpone_days": 30}
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Buy on")
        far = self.client.get(
            reverse("purchase"),
            {"name": "x", "amount": "1", "date": (TODAY + timedelta(days=400)).isoformat(), "postpone_days": 5},
        )
        self.assertContains(far, "Pick a date within")
        bad = self.client.get(
            reverse("purchase"), {"name": "x", "amount": "-5", "date": TODAY.isoformat(), "postpone_days": 5}
        )
        self.assertEqual(bad.status_code, 200)

    def test_debt_detail_and_extra_payment_scenario(self):
        debt = Debt.objects.get(user=self.user, name="Car loan")
        r = self.client.get(reverse("debt_detail", args=[debt.pk]), {"extra": "3000"})
        self.assertContains(r, "With ₹3,000 extra")
        self.assertEqual(self.client.get(reverse("debt_detail", args=[debt.pk]), {"extra": "abc"}).status_code, 200)


class AuthTests(TestCase):
    def test_login_required_everywhere(self):
        for name in PageRenderTests.PAGES + ["notification_read", "transaction_bulk", "schedule_record"]:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn(reverse("login"), response["Location"], name)

    def test_logout_requires_post(self):
        make_user()
        self.client.login(username="alice", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)

    def test_registration_validates_password_and_email(self):
        weak = self.client.post(
            reverse("register"), {"username": "n", "email": "n@x.com", "password1": "12345678", "password2": "12345678"}
        )
        self.assertEqual(weak.status_code, 200)
        self.assertFalse(User.objects.filter(username="n").exists())
        good = {
            "username": "newbie",
            "email": "N@x.com",
            "password1": "correct-horse-battery",
            "password2": "correct-horse-battery",
        }
        self.assertRedirects(self.client.post(reverse("register"), good), reverse("onboarding"))
        self.client.post(reverse("logout"))
        dup = {**good, "username": "newbie2", "email": "n@X.com"}
        self.assertContains(self.client.post(reverse("register"), dup), "already exists")

    def test_new_user_is_sent_through_onboarding_then_sees_empty_states(self):
        make_user()
        self.client.login(username="alice", password=PASSWORD)
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("onboarding"))
        r = self.client.post(
            reverse("onboarding"),
            {
                "account_name": "Main",
                "account_kind": "bank",
                "opening_balance": "2500",
                "min_buffer": "1000",
                "monthly_budget": "20000",
            },
        )
        self.assertRedirects(r, reverse("bill_list"))
        user = User.objects.get(username="alice")
        self.assertEqual(Account.objects.get(user=user).opening_balance, D("2500.00"))
        self.assertEqual(user.profile.min_buffer, D("1000.00"))
        self.assertEqual(Budget.objects.get(user=user).amount, D("20000.00"))
        for name in (
            "dashboard",
            "cashflow",
            "transaction_list",
            "budget_list",
            "bill_list",
            "goal_list",
            "debt_list",
            "reports",
            "calendar",
        ):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertContains(self.client.get(reverse("dashboard")), "may be incomplete")


class IsolationTests(TestCase):
    """Alice must never read or change Bob's records."""

    @classmethod
    def setUpTestData(cls):
        cls.alice = populated("alice")
        cls.bob = populated("bob")

    def setUp(self):
        self.client.login(username="alice", password=PASSWORD)

    def test_other_users_objects_are_404(self):
        bob_txn = Transaction.objects.filter(user=self.bob).first()
        bob_acct = Account.objects.filter(user=self.bob).first()
        bob_cat = Category.objects.filter(user=self.bob).first()
        bob_budget = Budget.objects.filter(user=self.bob).first()
        bob_item = ScheduledItem.objects.filter(user=self.bob).first()
        bob_debt = Debt.objects.filter(user=self.bob).first()
        bob_goal = Goal.objects.filter(user=self.bob).first()
        urls = [
            ("transaction_update", bob_txn),
            ("transaction_delete", bob_txn),
            ("account_update", bob_acct),
            ("account_delete", bob_acct),
            ("category_update", bob_cat),
            ("category_delete", bob_cat),
            ("budget_update", bob_budget),
            ("budget_delete", bob_budget),
            ("schedule_update", bob_item),
            ("schedule_delete", bob_item),
            ("debt_detail", bob_debt),
            ("debt_update", bob_debt),
            ("debt_delete", bob_debt),
            ("goal_update", bob_goal),
            ("goal_delete", bob_goal),
        ]
        for name, obj in urls:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name, args=[obj.pk])).status_code, 404)
                self.assertEqual(self.client.post(reverse(name, args=[obj.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("account_archive", args=[bob_acct.pk])).status_code, 404)
        self.assertEqual(
            self.client.post(
                reverse("goal_contribute", args=[bob_goal.pk]), {"date": TODAY.isoformat(), "amount": "5"}
            ).status_code,
            404,
        )
        self.assertTrue(Transaction.objects.filter(pk=bob_txn.pk).exists())
        self.assertFalse(Account.objects.get(pk=bob_acct.pk).is_archived)

    def test_lists_only_contain_own_data(self):
        response = self.client.get(reverse("transaction_list"), {"q": "Swiggy"})
        rows = list(response.context["page"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user, self.alice)
        self.assertEqual(Transaction.objects.filter(payee="Swiggy").count(), 2)  # Bob's exists but is not shown

    def test_bulk_actions_ignore_foreign_ids(self):
        bob_ids = list(Transaction.objects.filter(user=self.bob).values_list("pk", flat=True))
        self.client.post(reverse("transaction_bulk"), {"ids": bob_ids, "action": "delete", "confirmed": "yes"})
        self.assertEqual(Transaction.objects.filter(user=self.bob).count(), len(bob_ids))

    def test_form_choices_exclude_other_users_accounts_and_categories(self):
        bob_acct = Account.objects.filter(user=self.bob).first()
        bob_cat = Category.objects.filter(user=self.bob, name="Food & dining").first()
        before = Transaction.objects.count()
        r = self.client.post(
            reverse("transaction_create"),
            {
                "kind": "expense",
                "amount": "10",
                "date": TODAY.isoformat(),
                "account": bob_acct.pk,
                "category": bob_cat.pk,
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Transaction.objects.count(), before)

    def test_recording_foreign_bill_is_404(self):
        item = ScheduledItem.objects.filter(user=self.bob).first()
        r = self.client.post(
            reverse("schedule_record"), {"item": item.pk, "due_date": item.start_date.isoformat(), "action": "skip"}
        )
        self.assertEqual(r.status_code, 404)
        self.assertEqual(Occurrence.objects.count(), 0)

    def test_open_redirect_is_blocked(self):
        txn = Transaction.objects.filter(user=self.alice).first()
        r = self.client.post(reverse("transaction_delete", args=[txn.pk]), {"next": "https://evil.example/steal"})
        self.assertRedirects(r, reverse("transaction_list"), fetch_redirect_response=False)

    def test_import_session_cannot_target_foreign_account(self):
        bob_acct = Account.objects.filter(user=self.bob).first()
        session = self.client.session
        session["csv_import"] = {"text": "Date,Amount\n01/01/2025,-5\n", "filename": "x.csv", "account_id": bob_acct.pk}
        session.save()
        self.assertEqual(self.client.get(reverse("import_map")).status_code, 404)

    def test_export_contains_only_own_records(self):
        data = json.loads(self.client.get(reverse("export_data")).content)
        self.assertEqual({t["user_id"] for t in data["transactions"]}, {self.alice.pk})
        self.assertNotIn("password", json.dumps(data))
        csv_text = self.client.get(reverse("export_csv")).content.decode()
        self.assertEqual(csv_text.count("Swiggy"), 1)


class TransactionFlowTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.bank = make_account(self.user, "Bank", opening="1000")
        self.cash = make_account(self.user, "Cash", Account.Kind.CASH, "0")
        self.client.login(username="alice", password=PASSWORD)

    def post_txn(self, **overrides):
        data = {"kind": "expense", "amount": "100", "date": TODAY.isoformat(), "account": self.bank.pk}
        data.update(overrides)
        return self.client.post(reverse("transaction_create"), data)

    def test_create_edit_delete(self):
        self.assertRedirects(self.post_txn(payee="Chai"), reverse("transaction_list"))
        txn = Transaction.objects.get()
        self.assertEqual((txn.user, txn.payee, txn.amount), (self.user, "Chai", D("100.00")))
        self.client.post(
            reverse("transaction_update", args=[txn.pk]),
            {"kind": "expense", "amount": "120", "date": TODAY.isoformat(), "account": self.bank.pk, "payee": "Chai"},
        )
        txn.refresh_from_db()
        self.assertEqual(txn.amount, D("120.00"))
        self.client.post(reverse("transaction_delete", args=[txn.pk]))
        self.assertFalse(Transaction.objects.exists())

    def test_invalid_amounts_are_rejected_with_messages(self):
        for bad in ("0", "-5", "abc", ""):
            r = self.post_txn(amount=bad)
            self.assertEqual(r.status_code, 200, bad)
        self.assertFalse(Transaction.objects.exists())

    def test_transfer_requires_destination_and_ignores_it_otherwise(self):
        self.assertEqual(self.post_txn(kind="transfer").status_code, 200)
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(self.post_txn(kind="transfer", to_account=self.bank.pk).status_code, 200)
        self.post_txn(kind="transfer", to_account=self.cash.pk)
        self.post_txn(kind="expense", to_account=self.cash.pk)
        self.assertEqual(Transaction.objects.filter(kind="transfer").count(), 1)
        self.assertIsNone(Transaction.objects.get(kind="expense").to_account)

    def test_filters_search_sort_pagination(self):
        for i in range(30):
            make_txn(self.user, self.bank, "expense", i + 1, TODAY - timedelta(days=i), payee=f"Shop{i}")
        r = self.client.get(reverse("transaction_list"))
        self.assertEqual(len(r.context["page"]), 25)
        self.assertEqual(len(self.client.get(reverse("transaction_list"), {"page": 2}).context["page"]), 5)
        r = self.client.get(reverse("transaction_list"), {"q": "Shop7", "sort": "amount"})
        self.assertEqual([t.payee for t in r.context["page"]], ["Shop7"])
        r = self.client.get(reverse("transaction_list"), {"date_from": (TODAY - timedelta(days=2)).isoformat()})
        self.assertEqual(r.context["summary"]["count"], 3)
        self.assertContains(self.client.get(reverse("transaction_list"), {"q": "zzz"}), "No transactions match")

    def test_bulk_categorise_and_confirmed_delete(self):
        a = make_txn(self.user, self.bank, "expense", 10)
        b = make_txn(self.user, self.bank, "expense", 20)
        food = category(self.user, "Food & dining")
        self.client.post(
            reverse("transaction_bulk"), {"ids": [a.pk, b.pk], "action": "categorise", "category": food.pk}
        )
        self.assertEqual(Transaction.objects.filter(category=food).count(), 2)
        r = self.client.post(reverse("transaction_bulk"), {"ids": [a.pk, b.pk], "action": "delete"})
        self.assertContains(r, "Delete 2 transactions")  # confirmation step, nothing deleted yet
        self.assertEqual(Transaction.objects.count(), 2)
        self.client.post(reverse("transaction_bulk"), {"ids": [a.pk, b.pk], "action": "delete", "confirmed": "yes"})
        self.assertEqual(Transaction.objects.count(), 0)

    def test_account_with_transactions_cannot_be_deleted_only_archived(self):
        make_txn(self.user, self.bank, "expense", 5)
        self.client.post(reverse("account_delete", args=[self.bank.pk]))
        self.assertTrue(Account.objects.filter(pk=self.bank.pk).exists())
        self.client.post(reverse("account_archive", args=[self.bank.pk]))
        self.assertTrue(Account.objects.get(pk=self.bank.pk).is_archived)
        self.client.post(reverse("account_delete", args=[self.cash.pk]))
        self.assertFalse(Account.objects.filter(pk=self.cash.pk).exists())

    def test_duplicate_account_and_category_names_rejected(self):
        self.client.post(reverse("account_create"), {"name": "bank", "kind": "bank", "opening_balance": "0"})
        self.assertEqual(Account.objects.filter(user=self.user).count(), 2)
        self.client.post(reverse("category_create"), {"name": "groceries", "flow": "expense"})
        self.assertEqual(Category.objects.filter(user=self.user, name__iexact="groceries").count(), 1)

    def test_csv_export_neutralises_formulas(self):
        make_txn(self.user, self.bank, "expense", 5, payee='=HYPERLINK("http://x")')
        self.assertIn("'=HYPERLINK", self.client.get(reverse("export_csv")).content.decode())


class ImportFlowTests(TestCase):
    CSV = b"Date,Narration,Amount\n01/10/2025,Zomato,-300\n02/10/2025,Pay,5000\nbad,row,x\n"

    def setUp(self):
        self.user = make_user()
        self.bank = make_account(self.user, "Bank")
        self.client.login(username="alice", password=PASSWORD)

    def upload(self, content=None, name="stmt.csv"):
        return self.client.post(
            reverse("import_upload"),
            {"account": self.bank.pk, "file": SimpleUploadedFile(name, self.CSV if content is None else content)},
        )

    def map_payload(self, **extra):
        data = {"date": "Date", "payee": "Narration", "amount": "Amount", "date_format": "dmy", "amount_mode": "signed"}
        data.update(extra)
        return data

    def test_upload_preview_confirm_and_reimport(self):
        self.assertRedirects(self.upload(), reverse("import_map"))
        preview = self.client.post(reverse("import_map"), self.map_payload(preview="1"))
        self.assertContains(preview, "Will import")
        self.assertEqual(Transaction.objects.count(), 0)  # preview saves nothing
        self.assertRedirects(
            self.client.post(reverse("import_map"), self.map_payload(confirm="1")), reverse("import_history")
        )
        self.assertEqual(Transaction.objects.count(), 2)
        batch = ImportBatch.objects.get()
        self.assertEqual((batch.rows_imported, batch.rows_invalid), (2, 1))
        self.assertTrue(Notification.objects.filter(user=self.user, kind="import").exists())
        # same file again: nothing new
        self.upload()
        again = self.client.post(reverse("import_map"), self.map_payload(confirm="1"))
        self.assertContains(again, "no new rows")  # stays on the page, nothing written
        self.assertEqual(Transaction.objects.count(), 2)

    def test_rejects_wrong_extension_empty_and_oversized_files(self):
        self.assertEqual(self.upload(name="x.exe").status_code, 200)
        self.assertEqual(self.upload(content=b"").status_code, 200)
        big = b"Date,Amount\n" + b"01/01/2025,-1\n" * 200000
        self.assertEqual(self.upload(content=big).status_code, 200)
        self.assertNotIn("csv_import", self.client.session)

    def test_map_without_upload_redirects(self):
        self.assertRedirects(self.client.get(reverse("import_map")), reverse("import_upload"))

    def test_missing_amount_column_is_a_form_error(self):
        self.upload()
        r = self.client.post(
            reverse("import_map"), {"date": "Date", "date_format": "dmy", "amount_mode": "signed", "preview": "1"}
        )
        self.assertContains(r, "Choose a date column and an amount")


class NotificationTests(TestCase):
    def setUp(self):
        self.user = populated()
        self.client.login(username="alice", password=PASSWORD)

    def test_generation_is_idempotent(self):
        self.client.get(reverse("dashboard"))
        first = Notification.objects.filter(user=self.user).count()
        self.assertGreater(first, 0)
        for _ in range(3):
            self.client.get(reverse("dashboard"))
            self.client.get(reverse("notification_list"))
        self.assertEqual(Notification.objects.filter(user=self.user).count(), first)

    def test_expected_notification_kinds(self):
        self.client.get(reverse("dashboard"))
        kinds = set(Notification.objects.filter(user=self.user).values_list("kind", flat=True))
        self.assertIn("bill_overdue", kinds)  # "Old bill" due 5 days ago
        self.assertIn("bill_due", kinds)  # Rent due in 3 days, reminder window 3
        self.assertIn("budget", kinds)  # food 450 of 500 = 90%
        self.assertIn("goal", kinds)  # 45000 of 80000 passes 50%

    def test_preferences_disable_alerts(self):
        profile = self.user.profile
        profile.notify_bills = profile.notify_budgets = profile.notify_low_balance = profile.notify_goals = False
        profile.save()
        self.client.get(reverse("dashboard"))
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 0)

    def test_mark_read_only_affects_own_notifications(self):
        bob = populated("bob")
        self.client.get(reverse("dashboard"))
        other = Notification.objects.create(user=bob, kind="budget", key="k", message="bob's")
        self.client.post(reverse("notification_read"))
        other.refresh_from_db()
        self.assertIsNone(other.read_at)
        self.assertFalse(Notification.objects.filter(user=self.user, read_at__isnull=True).exists())


class BillFlowTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.bank = make_account(self.user, "Bank", opening="50000")
        self.client.login(username="alice", password=PASSWORD)
        self.item = ScheduledItem.objects.create(
            user=self.user, name="Rent", amount=D("12000"), start_date=TODAY, account=self.bank
        )

    def record(self, action, **extra):
        data = {"item": self.item.pk, "due_date": TODAY.isoformat(), "action": action, "paid_on": TODAY.isoformat()}
        data.update(extra)
        return self.client.post(reverse("schedule_record"), data, follow=True)

    def test_pay_twice_creates_one_transaction_and_undo_restores(self):
        self.record("pay")
        r = self.record("pay")
        self.assertContains(r, "already recorded")
        self.assertEqual(Transaction.objects.count(), 1)
        self.record("undo")
        self.assertEqual((Transaction.objects.count(), Occurrence.objects.count()), (0, 0))

    def test_deleting_the_payment_transaction_reopens_the_bill(self):
        self.record("pay")
        txn = Transaction.objects.get()
        self.client.post(reverse("transaction_delete", args=[txn.pk]))
        self.assertEqual(Occurrence.objects.count(), 0)
        self.assertEqual(len(self.client.get(reverse("bill_list")).context["groups"]["Next 7 days"]), 1)

    def test_variable_amount_and_skip(self):
        self.record("pay", amount="12500.50")
        self.assertEqual(Transaction.objects.get().amount, D("12500.50"))

    def test_skip_creates_no_transaction(self):
        self.record("skip")
        self.assertEqual((Transaction.objects.count(), Occurrence.objects.get().outcome), (0, "skipped"))

    def test_bad_inputs_do_not_500(self):
        self.assertEqual(
            self.client.post(
                reverse("schedule_record"), {"item": self.item.pk, "due_date": "nope", "action": "pay"}
            ).status_code,
            302,
        )
        self.assertEqual(self.record("pay", amount="-4").status_code, 200)
        self.assertEqual(Transaction.objects.count(), 0)


class DebtAndGoalFlowTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.bank = make_account(self.user, "Bank", opening="50000")
        self.client.login(username="alice", password=PASSWORD)

    def test_debt_payment_via_form_and_reversal_by_deleting_transaction(self):
        debt = Debt.objects.create(
            user=self.user,
            name="Loan",
            principal=D("100000"),
            outstanding=D("100000"),
            annual_rate=D("12"),
            emi_amount=D("5000"),
            account=self.bank,
        )
        self.client.post(reverse("debt_detail", args=[debt.pk]), {"date": TODAY.isoformat(), "amount": "5000"})
        debt.refresh_from_db()
        self.assertEqual(debt.outstanding, D("96000.00"))  # 5000 minus 1000 interest
        self.client.post(reverse("transaction_delete", args=[Transaction.objects.get().pk]))
        debt.refresh_from_db()
        self.assertEqual(debt.outstanding, D("100000.00"))

    def test_debt_validation(self):
        r = self.client.post(
            reverse("debt_create"),
            {
                "name": "x",
                "principal": "0",
                "outstanding": "-1",
                "annual_rate": "10",
                "emi_amount": "100",
                "emi_day": "40",
                "status": "active",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Debt.objects.exists())

    def test_goal_contribution_flow(self):
        self.client.post(reverse("goal_create"), {"name": "Trip", "target_amount": "10000", "priority": "2"})
        goal = Goal.objects.get()
        self.client.post(reverse("goal_contribute", args=[goal.pk]), {"date": TODAY.isoformat(), "amount": "2500"})
        self.client.post(reverse("goal_contribute", args=[goal.pk]), {"date": TODAY.isoformat(), "amount": "-1"})
        self.assertEqual(goal.contributions.count(), 1)
        self.assertContains(self.client.get(reverse("goal_list")), "25%")

    def test_budget_duplicate_rejected(self):
        self.client.post(reverse("budget_create"), {"period": "monthly", "amount": "100", "warn_percent": "80"})
        r = self.client.post(reverse("budget_create"), {"period": "monthly", "amount": "200", "warn_percent": "80"})
        self.assertContains(r, "already have a budget")
        self.assertEqual(Budget.objects.count(), 1)


class AccountDeletionTests(TestCase):
    def test_wrong_password_keeps_data_and_right_password_deletes_everything(self):
        user = populated()
        other = populated("bob")
        self.client.login(username="alice", password=PASSWORD)
        self.client.post(reverse("delete_account"), {"password": "wrong", "confirm": "on"})
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.client.post(reverse("delete_account"), {"password": PASSWORD, "confirm": "on"})
        self.assertFalse(User.objects.filter(pk=user.pk).exists())
        self.assertFalse(Transaction.objects.filter(user_id=user.pk).exists())
        self.assertTrue(Transaction.objects.filter(user=other).exists())


class SettingsTests(TestCase):
    def test_buffer_validation_and_save(self):
        user = make_user()
        self.client.login(username="alice", password=PASSWORD)
        self.client.post(reverse("settings"), {"min_buffer": "-5"})
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.min_buffer, D("5000.00"))
        self.client.post(reverse("settings"), {"min_buffer": "7500", "notify_bills": "on"})
        user.profile.refresh_from_db()
        self.assertEqual((user.profile.min_buffer, user.profile.notify_budgets), (D("7500.00"), False))
