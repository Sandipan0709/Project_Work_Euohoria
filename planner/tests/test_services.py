from datetime import date
from decimal import Decimal as D

from django.test import TestCase

from ledger.models import Account, Transaction
from ledger.tests.factories import category, make_account, make_txn, make_user
from planner.forecast import Event
from planner.models import Budget, Debt, DebtPayment, Occurrence, ScheduledItem
from planner.services import (
    AlreadyRecorded,
    build_events,
    data_gaps,
    financial_position,
    obligations_through,
    pending_occurrences,
    record_debt_payment,
    record_occurrence,
    run_forecast,
    undo_occurrence,
)

K = Transaction.Kind
TODAY = date(2025, 10, 20)


def bill(user, name="Rent", amount="10000", day=date(2025, 10, 25), **kw):
    return ScheduledItem.objects.create(user=user, name=name, amount=D(amount), start_date=day, **kw)


def income(user, name="Salary", amount="50000", day=date(2025, 10, 28), **kw):
    return ScheduledItem.objects.create(user=user, flow="income", name=name, amount=D(amount), start_date=day, **kw)


class PendingTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.acct = make_account(self.user, opening="20000")

    def test_overdue_and_upcoming_states(self):
        bill(self.user, "Old", day=date(2025, 10, 10), frequency="once")
        bill(self.user, "Soon", day=date(2025, 10, 22), frequency="once")
        pending = pending_occurrences(self.user, date(2025, 9, 1), date(2025, 11, 30), today=TODAY)
        self.assertEqual([(p.item.name, p.state) for p in pending], [("Old", "overdue"), ("Soon", "upcoming")])

    def test_recorded_outcomes_are_not_pending(self):
        item = bill(self.user, account=self.acct)
        record_occurrence(item, date(2025, 10, 25), "done")
        self.assertEqual(pending_occurrences(self.user, date(2025, 10, 1), date(2025, 10, 31), today=TODAY), [])

    def test_paused_and_cancelled_items_do_not_appear(self):
        bill(self.user, "P", status="paused")
        bill(self.user, "C", status="cancelled")
        self.assertEqual(pending_occurrences(self.user, date(2025, 10, 1), date(2025, 12, 31), today=TODAY), [])


class RecordOccurrenceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.acct = make_account(self.user, opening="20000")

    def test_paying_a_bill_creates_one_expense_and_is_idempotent(self):
        item = bill(self.user, account=self.acct, category=category(self.user, "Rent & housing"))
        record_occurrence(item, date(2025, 10, 25), "done", paid_on=date(2025, 10, 24))
        txn = Transaction.objects.get()
        self.assertEqual(
            (txn.kind, txn.amount, txn.date, txn.account), (K.EXPENSE, D("10000.00"), date(2025, 10, 24), self.acct)
        )
        with self.assertRaises(AlreadyRecorded):
            record_occurrence(item, date(2025, 10, 25), "done")
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(Occurrence.objects.count(), 1)

    def test_variable_bill_uses_actual_amount(self):
        item = bill(self.user, "Electricity", "1500", account=self.acct, is_variable=True)
        record_occurrence(item, date(2025, 10, 25), "done", amount=D("1873.50"))
        self.assertEqual(Transaction.objects.get().amount, D("1873.50"))

    def test_credit_card_bill_is_a_transfer_not_an_expense(self):
        card = make_account(self.user, "Card", Account.Kind.CREDIT_CARD, "-5000")
        item = bill(self.user, "Card bill", "5000", account=self.acct, settles_account=card)
        record_occurrence(item, date(2025, 10, 25), "done")
        txn = Transaction.objects.get()
        self.assertEqual((txn.kind, txn.account, txn.to_account), (K.TRANSFER, self.acct, card))

    def test_skip_creates_no_transaction(self):
        item = bill(self.user, account=self.acct)
        record_occurrence(item, date(2025, 10, 25), "skipped")
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(Occurrence.objects.get().outcome, "skipped")

    def test_not_a_real_due_date_is_rejected(self):
        item = bill(self.user, account=self.acct)
        with self.assertRaises(ValueError):
            record_occurrence(item, date(2025, 10, 26), "done")
        self.assertEqual(Occurrence.objects.count(), 0)

    def test_missing_account_rolls_everything_back(self):
        item = bill(self.user)
        with self.assertRaises(ValueError):
            record_occurrence(item, date(2025, 10, 25), "done")
        self.assertEqual((Occurrence.objects.count(), Transaction.objects.count()), (0, 0))

    def test_undo_removes_occurrence_and_transaction(self):
        item = bill(self.user, account=self.acct)
        occ = record_occurrence(item, date(2025, 10, 25), "done")
        undo_occurrence(occ)
        self.assertEqual((Occurrence.objects.count(), Transaction.objects.count()), (0, 0))

    def test_changing_recurrence_does_not_rewrite_history(self):
        item = bill(self.user, account=self.acct)
        record_occurrence(item, date(2025, 10, 25), "done")
        item.frequency, item.amount = "weekly", D("999")
        item.save()
        self.assertEqual(Transaction.objects.get().amount, D("10000.00"))
        self.assertEqual(Occurrence.objects.get().amount, D("10000.00"))

    def test_income_received_creates_income_transaction(self):
        item = income(self.user, account=self.acct)
        record_occurrence(item, date(2025, 10, 28), "done")
        self.assertEqual(Transaction.objects.get().kind, K.INCOME)


class ForecastBuildTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.acct = make_account(self.user, opening="20000")

    def run_fc(self, days=30, scenario="conservative", **kw):
        return run_forecast(self.user, days, scenario, today=TODAY, **kw)

    def test_no_data_is_a_flat_line_with_gaps_reported(self):
        r = self.run_fc()
        self.assertEqual(r.forecast.end_balance, D("20000.00"))
        self.assertEqual(len(data_gaps(self.user)), 2)

    def test_bills_reduce_and_confirmed_income_increases_balance(self):
        bill(self.user, account=self.acct)
        income(self.user, account=self.acct)
        r = self.run_fc()
        self.assertEqual(r.forecast.end_balance, D("60000.00"))
        self.assertEqual(r.forecast.points[5].balance, D("10000.00"))  # after rent on the 25th

    def test_unconfirmed_income_only_in_expected_scenario(self):
        income(self.user, account=self.acct, is_confirmed=False, amount="8000")
        self.assertEqual(self.run_fc().forecast.end_balance, D("20000.00"))
        self.assertTrue(any("unconfirmed" in n for n in self.run_fc().notes))
        self.assertEqual(self.run_fc(scenario="expected").forecast.end_balance, D("28000.00"))

    def test_paid_bill_is_not_counted_twice(self):
        item = bill(self.user, account=self.acct)
        record_occurrence(item, date(2025, 10, 25), "done", paid_on=date(2025, 10, 18))
        r = self.run_fc()
        self.assertEqual(r.forecast.opening, D("10000.00"))  # the payment is already in the balance
        self.assertEqual(r.forecast.end_balance, D("10000.00"))  # and is not projected again

    def test_overdue_bill_hits_today(self):
        bill(self.user, "Late", "2500", day=date(2025, 10, 15), frequency="once", account=self.acct)
        p = self.run_fc().forecast.points[0]
        self.assertEqual(p.balance, D("17500.00"))
        self.assertIn("overdue", p.events[0].label)

    def test_past_income_never_received_is_not_projected(self):
        income(self.user, day=date(2025, 10, 5), frequency="once", account=self.acct)
        self.assertEqual(self.run_fc().forecast.end_balance, D("20000.00"))

    def test_future_dated_transaction_counted_once(self):
        make_txn(self.user, self.acct, K.EXPENSE, 4000, day=date(2025, 10, 30))
        r = self.run_fc()
        self.assertEqual(r.forecast.opening, D("20000.00"))
        self.assertEqual(r.forecast.end_balance, D("16000.00"))

    def test_emi_projected_monthly_and_skipped_after_payment(self):
        debt = Debt.objects.create(
            user=self.user,
            name="Car",
            principal=D("300000"),
            outstanding=D("200000"),
            annual_rate=D("9"),
            emi_amount=D("8000"),
            emi_day=31,
            account=self.acct,
        )
        events, _ = build_events(self.user, TODAY, date(2025, 12, 31), today=TODAY)
        self.assertEqual(
            [e.date for e in events if e.source == "emi"], [date(2025, 10, 31), date(2025, 11, 30), date(2025, 12, 31)]
        )
        record_debt_payment(debt, TODAY, D("8000"))
        events, _ = build_events(self.user, TODAY, date(2025, 12, 31), today=TODAY)
        self.assertEqual([e.date for e in events if e.source == "emi"], [date(2025, 11, 30), date(2025, 12, 31)])

    def test_account_specific_forecast_only_uses_that_accounts_items(self):
        other = make_account(self.user, "Other", opening="1000")
        bill(self.user, "A", "100", account=self.acct)
        bill(self.user, "B", "400", account=other)
        bill(self.user, "Unassigned", "50")
        self.assertEqual(self.run_fc(account_id=other.id).forecast.end_balance, D("600.00"))
        self.assertEqual(self.run_fc().forecast.end_balance, D("21000.00") - D("550"))

    def test_credit_card_is_excluded_from_cash_but_its_bill_payment_is_included(self):
        card = make_account(self.user, "Card", Account.Kind.CREDIT_CARD, "-5000")
        make_txn(self.user, card, K.EXPENSE, 2000, day=date(2025, 10, 10))
        bill(self.user, "Card bill", "7000", account=self.acct, settles_account=card)
        r = self.run_fc()
        self.assertEqual(r.forecast.opening, D("20000.00"))
        self.assertEqual(r.forecast.end_balance, D("13000.00"))

    def test_purchase_scenario_event_and_buffer(self):
        r = self.run_fc(extra_events=(Event(TODAY, D("-18000"), "Laptop", "purchase"),))
        self.assertEqual(r.forecast.end_balance, D("2000.00"))
        self.assertEqual(r.forecast.first_below_buffer, TODAY)  # buffer default 5000
        self.assertIsNone(r.forecast.first_shortfall)

    def test_obligations_through_counts_bills_and_emis_not_income(self):
        bill(self.user, account=self.acct)
        income(self.user, account=self.acct)
        self.assertEqual(obligations_through(self.user, date(2025, 10, 31), TODAY), D("10000.00"))


class PositionTests(TestCase):
    def test_net_worth_definition(self):
        user = make_user()
        bank = make_account(user, opening="50000")
        make_account(user, "Card", Account.Kind.CREDIT_CARD, "-8000")
        Debt.objects.create(
            user=user,
            name="Loan",
            principal=D("100000"),
            outstanding=D("60000"),
            annual_rate=D("10"),
            emi_amount=D("5000"),
        )
        make_txn(user, bank, K.INCOME, 1000)
        p = financial_position(user, date(2025, 10, 31))
        self.assertEqual((p.liquid, p.card_balance, p.debts), (D("51000.00"), D("-8000.00"), D("60000.00")))
        self.assertEqual(p.net_worth, D("-17000.00"))

    def test_archived_accounts_excluded(self):
        user = make_user()
        make_account(user, opening="100")
        old = make_account(user, "Old", opening="900")
        old.is_archived = True
        old.save()
        self.assertEqual(financial_position(user, date(2025, 10, 31)).liquid, D("100.00"))


class DebtPaymentTests(TestCase):
    def test_payment_updates_balance_and_closes_debt(self):
        user = make_user()
        acct = make_account(user, opening="10000")
        debt = Debt.objects.create(
            user=user,
            name="Phone",
            principal=D("12000"),
            outstanding=D("3000"),
            annual_rate=D("0"),
            emi_amount=D("1500"),
            account=acct,
        )
        record_debt_payment(debt, TODAY, D("1500"))
        debt.refresh_from_db()
        self.assertEqual((debt.outstanding, debt.status), (D("1500.00"), "active"))
        record_debt_payment(debt, TODAY, D("1500"))
        debt.refresh_from_db()
        self.assertEqual((debt.outstanding, debt.status), (D("0.00"), "closed"))
        self.assertEqual(DebtPayment.objects.count(), 2)
        self.assertEqual(Transaction.objects.filter(kind=K.EXPENSE).count(), 2)

    def test_payment_without_account_is_rejected_and_atomic(self):
        user = make_user()
        debt = Debt.objects.create(
            user=user, name="X", principal=D("100"), outstanding=D("100"), annual_rate=D("0"), emi_amount=D("10")
        )
        with self.assertRaises(ValueError):
            record_debt_payment(debt, TODAY, D("10"))
        self.assertEqual(DebtPayment.objects.count(), 0)


class BudgetStatusTests(TestCase):
    def setUp(self):
        from planner.budgets import budget_statuses

        self.statuses = budget_statuses
        self.user = make_user()
        self.acct = make_account(self.user, opening="100000")
        self.food = category(self.user, "Food & dining")

    def test_warning_over_and_remaining(self):
        Budget.objects.create(user=self.user, category=self.food, amount=D("1000"), warn_percent=80)
        make_txn(self.user, self.acct, K.EXPENSE, 850, category=self.food, day=date(2025, 10, 5))
        s = self.statuses(self.user, date(2025, 10, 15))[0]
        self.assertEqual((s.spent, s.remaining, s.percent_used, s.state), (D("850.00"), D("150.00"), 85, "warn"))
        make_txn(self.user, self.acct, K.EXPENSE, 200, category=self.food, day=date(2025, 10, 6))
        self.assertEqual(self.statuses(self.user, date(2025, 10, 15))[0].state, "over")

    def test_subcategory_spend_rolls_up_into_parent(self):
        from ledger.models import Category

        sub = Category.objects.create(user=self.user, name="Coffee", parent=self.food)
        Budget.objects.create(user=self.user, category=self.food, amount=D("1000"))
        make_txn(self.user, self.acct, K.EXPENSE, 300, category=sub, day=date(2025, 10, 5))
        make_txn(self.user, self.acct, K.EXPENSE, 100, category=self.food, day=date(2025, 10, 5))
        self.assertEqual(self.statuses(self.user, date(2025, 10, 15))[0].spent, D("400.00"))

    def test_overall_budget_excludes_transfers_and_nets_refunds(self):
        other = make_account(self.user, "B")
        Budget.objects.create(user=self.user, category=None, amount=D("5000"))
        make_txn(self.user, self.acct, K.EXPENSE, 1000, day=date(2025, 10, 5))
        make_txn(self.user, self.acct, K.REFUND, 250, day=date(2025, 10, 6))
        make_txn(self.user, self.acct, K.TRANSFER, 4000, to_account=other, day=date(2025, 10, 7))
        self.assertEqual(self.statuses(self.user, date(2025, 10, 15))[0].spent, D("750.00"))

    def test_pace_projection_needs_enough_days(self):
        Budget.objects.create(user=self.user, category=None, amount=D("3000"))
        make_txn(self.user, self.acct, K.EXPENSE, 1000, day=date(2025, 10, 1))
        self.assertIsNone(self.statuses(self.user, date(2025, 10, 2))[0].projected_spend)
        s = self.statuses(self.user, date(2025, 10, 10))[0]
        self.assertEqual(s.projected_spend, D("3100.00"))  # 1000/10 days * 31

    def test_weekly_budget_uses_monday_to_sunday(self):
        Budget.objects.create(user=self.user, category=None, period="weekly", amount=D("500"))
        make_txn(self.user, self.acct, K.EXPENSE, 100, day=date(2025, 10, 19))  # Sunday of previous week
        make_txn(self.user, self.acct, K.EXPENSE, 200, day=date(2025, 10, 20))  # Monday
        s = self.statuses(self.user, date(2025, 10, 22))[0]
        self.assertEqual(
            (s.start, s.end, s.spent, s.previous_spent),
            (date(2025, 10, 20), date(2025, 10, 26), D("200.00"), D("100.00")),
        )


class RecurringSummaryTests(TestCase):
    def test_card_payments_are_not_counted_as_annual_cost(self):
        from planner.reports import recurring_summary

        user = make_user()
        bank = make_account(user, opening="1000")
        card = make_account(user, "Card", Account.Kind.CREDIT_CARD)
        bill(user, "Netflix", "649", account=bank, is_subscription=True)
        bill(user, "Card bill", "6000", account=bank, settles_account=card)
        bill(user, "Once", "999", frequency="once", account=bank)
        summary = recurring_summary(user)
        self.assertEqual([r["item"].name for r in summary["rows"]], ["Netflix"])
        self.assertEqual(summary["annual_total"], D("7788.00"))
        self.assertEqual((summary["subscription_count"], summary["subscription_annual"]), (1, D("7788.00")))
