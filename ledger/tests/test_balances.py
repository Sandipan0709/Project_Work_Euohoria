from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from ledger.models import Account, Category, Transaction
from ledger.services import account_balances, category_spend, period_totals

from .factories import category, make_account, make_txn, make_user

K = Transaction.Kind
OCT = date(2025, 10, 31)


class BalanceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.bank = make_account(self.user, "Bank", opening="10000")
        self.cash = make_account(self.user, "Wallet", Account.Kind.CASH, "500")

    def test_opening_balance_only(self):
        b = account_balances(self.user, OCT)
        self.assertEqual((b[self.bank.id], b[self.cash.id]), (D("10000.00"), D("500.00")))

    def test_income_expense_refund(self):
        make_txn(self.user, self.bank, K.INCOME, 50000)
        make_txn(self.user, self.bank, K.EXPENSE, 1200)
        make_txn(self.user, self.bank, K.REFUND, 200)
        self.assertEqual(account_balances(self.user, OCT)[self.bank.id], D("59000.00"))

    def test_transfer_moves_money_without_changing_total(self):
        make_txn(self.user, self.bank, K.TRANSFER, 2000, to_account=self.cash)
        b = account_balances(self.user, OCT)
        self.assertEqual((b[self.bank.id], b[self.cash.id]), (D("8000.00"), D("2500.00")))
        self.assertEqual(sum(b.values()), D("10500.00"))

    def test_transfer_is_not_income_or_spending(self):
        make_txn(self.user, self.bank, K.TRANSFER, 2000, to_account=self.cash)
        t = period_totals(self.user, date(2025, 10, 1), OCT)
        self.assertEqual((t.income, t.expenses), (D("0.00"), D("0.00")))

    def test_credit_card_purchase_and_repayment_do_not_double_count(self):
        card = make_account(self.user, "Card", Account.Kind.CREDIT_CARD, "0")
        make_txn(self.user, card, K.EXPENSE, 3000, payee="Shop")  # spend on card
        self.assertEqual(period_totals(self.user, date(2025, 10, 1), OCT).expenses, D("3000.00"))
        make_txn(self.user, self.bank, K.TRANSFER, 3000, to_account=card)  # pay the card
        b = account_balances(self.user, OCT)
        self.assertEqual((b[card.id], b[self.bank.id]), (D("0.00"), D("7000.00")))
        # Spending is still 3000, not 6000.
        self.assertEqual(period_totals(self.user, date(2025, 10, 1), OCT).expenses, D("3000.00"))

    def test_future_dated_transactions_do_not_move_todays_balance(self):
        make_txn(self.user, self.bank, K.EXPENSE, 999, day=date(2025, 11, 5))
        self.assertEqual(account_balances(self.user, OCT)[self.bank.id], D("10000.00"))

    def test_negative_balance_is_allowed_and_exact(self):
        make_txn(self.user, self.cash, K.EXPENSE, "500.01")
        self.assertEqual(account_balances(self.user, OCT)[self.cash.id], D("-0.01"))

    def test_other_users_data_is_never_included(self):
        other = make_user("bob")
        other_acct = make_account(other, "Bank", opening="1")
        make_txn(other, other_acct, K.INCOME, 999999)
        self.assertEqual(set(account_balances(self.user, OCT)), {self.bank.id, self.cash.id})
        self.assertEqual(period_totals(self.user, date(2025, 10, 1), OCT).income, D("0.00"))


class PeriodTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.bank = make_account(self.user)

    def test_expenses_are_net_of_refunds_and_savings_rate(self):
        make_txn(self.user, self.bank, K.INCOME, 40000)
        make_txn(self.user, self.bank, K.EXPENSE, 10000)
        make_txn(self.user, self.bank, K.REFUND, 1000)
        t = period_totals(self.user, date(2025, 10, 1), OCT)
        self.assertEqual((t.expenses, t.savings, t.savings_rate), (D("9000.00"), D("31000.00"), D("77.5")))

    def test_no_income_has_no_savings_rate(self):
        make_txn(self.user, self.bank, K.EXPENSE, 100)
        self.assertIsNone(period_totals(self.user, date(2025, 10, 1), OCT).savings_rate)

    def test_empty_period(self):
        t = period_totals(self.user, date(2025, 10, 1), OCT)
        self.assertEqual((t.income, t.expenses, t.savings), (D("0.00"),) * 3)

    def test_date_boundaries_are_inclusive(self):
        make_txn(self.user, self.bank, K.EXPENSE, 10, day=date(2025, 10, 1))
        make_txn(self.user, self.bank, K.EXPENSE, 20, day=date(2025, 10, 31))
        make_txn(self.user, self.bank, K.EXPENSE, 40, day=date(2025, 11, 1))
        self.assertEqual(period_totals(self.user, date(2025, 10, 1), OCT).expenses, D("30.00"))

    def test_category_spend_nets_refunds_and_flags_uncategorised(self):
        food = category(self.user, "Food & dining")
        make_txn(self.user, self.bank, K.EXPENSE, 500, category=food)
        make_txn(self.user, self.bank, K.REFUND, 100, category=food)
        make_txn(self.user, self.bank, K.EXPENSE, 70)
        spend = category_spend(self.user, date(2025, 10, 1), OCT)
        self.assertEqual(spend, {food.id: D("400.00"), None: D("70.00")})


class ModelValidationTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.a = make_account(self.user, "A")
        self.b = make_account(self.user, "B")

    def _txn(self, **kw):
        base = dict(user=self.user, account=self.a, kind=K.EXPENSE, amount=D("10"), date=date(2025, 10, 1))
        base.update(kw)
        return Transaction(**base)

    def test_zero_and_negative_amounts_rejected_by_clean_and_database(self):
        for bad in ("0", "-5"):
            with self.assertRaises(ValidationError):
                self._txn(amount=D(bad)).full_clean()
            with self.assertRaises(IntegrityError), transaction.atomic():
                self._txn(amount=D(bad)).save()

    def test_transfer_needs_two_different_accounts(self):
        with self.assertRaises(ValidationError):
            self._txn(kind=K.TRANSFER).full_clean()
        with self.assertRaises(ValidationError):
            self._txn(kind=K.TRANSFER, to_account=self.a).full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._txn(kind=K.TRANSFER, to_account=self.a).save()
        self._txn(kind=K.TRANSFER, to_account=self.b).full_clean()

    def test_non_transfer_cannot_have_destination(self):
        with self.assertRaises(ValidationError):
            self._txn(to_account=self.b).full_clean()

    def test_account_of_another_user_rejected(self):
        other = make_account(make_user("bob"), "X")
        with self.assertRaises(ValidationError):
            self._txn(account=other).full_clean()

    def test_duplicate_fingerprint_rejected_per_user_but_blank_allowed(self):
        self._txn(fingerprint="abc").save()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._txn(fingerprint="abc").save()
        self._txn().save()
        self._txn().save()

    def test_subcategory_depth_limited_to_one_level(self):
        parent = Category.objects.create(user=self.user, name="P")
        child = Category.objects.create(user=self.user, name="C", parent=parent)
        with self.assertRaises(ValidationError):
            Category(user=self.user, name="G", parent=child).full_clean()

    def test_new_users_get_default_categories(self):
        self.assertTrue(Category.objects.filter(user=self.user, name="Salary", flow="income").exists())
