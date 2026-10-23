from decimal import Decimal as D

from django.test import SimpleTestCase

from planner.debt import DebtInput, amortize, emi_for, estimate_principal_part, monthly_interest, plan_payoff


class EmiTests(SimpleTestCase):
    def test_known_emi_value(self):
        # 10 lakh, 10% p.a., 120 months -> standard reference value 13,215.07
        self.assertEqual(emi_for(D("1000000"), D("10"), 120), D("13215.07"))

    def test_zero_interest_splits_evenly(self):
        self.assertEqual(emi_for(D("12000"), D("0"), 12), D("1000.00"))

    def test_monthly_interest_rounds_half_up(self):
        self.assertEqual(monthly_interest(D("1000.00"), D("12")), D("10.00"))
        self.assertEqual(monthly_interest(D("100.50"), D("10")), D("0.84"))  # 0.83750 -> 0.84


class AmortizeTests(SimpleTestCase):
    def test_pays_off_and_totals_reconcile(self):
        a = amortize(D("100000"), D("12"), D("9000"))
        self.assertIsNotNone(a.months)
        self.assertEqual(a.total_paid, D("100000") + a.total_interest)

    def test_extra_payment_saves_time_and_interest(self):
        base = amortize(D("500000"), D("11"), D("12000"))
        faster = amortize(D("500000"), D("11"), D("12000"), extra=D("3000"))
        self.assertLess(faster.months, base.months)
        self.assertLess(faster.total_interest, base.total_interest)

    def test_payment_below_interest_never_pays_off(self):
        a = amortize(D("100000"), D("24"), D("1500"))  # interest is 2000/month
        self.assertIsNone(a.months)

    def test_zero_balance(self):
        a = amortize(D("0"), D("10"), D("100"))
        self.assertEqual((a.months, a.total_interest), (0, D("0.00")))

    def test_zero_rate_loan(self):
        a = amortize(D("1000"), D("0"), D("300"))
        self.assertEqual((a.months, a.total_interest), (4, D("0.00")))

    def test_estimated_principal_part(self):
        self.assertEqual(estimate_principal_part(D("100000"), D("12"), D("5000")), D("4000.00"))
        self.assertEqual(estimate_principal_part(D("100000"), D("12"), D("500")), D("0.00"))  # interest > payment


class StrategyTests(SimpleTestCase):
    DEBTS = [
        DebtInput("Card", D("40000"), D("36"), D("2000")),
        DebtInput("Car", D("300000"), D("9"), D("8000")),
        DebtInput("Phone", D("12000"), D("15"), D("1500")),
    ]

    def test_avalanche_never_costs_more_interest_than_snowball(self):
        av = plan_payoff(self.DEBTS, D("5000"), "avalanche")
        sb = plan_payoff(self.DEBTS, D("5000"), "snowball")
        self.assertLessEqual(av.total_interest, sb.total_interest)
        self.assertIsNotNone(av.months)
        self.assertIsNotNone(sb.months)

    def test_snowball_clears_smallest_balance_first(self):
        sb = plan_payoff(self.DEBTS, D("5000"), "snowball")
        self.assertEqual(sb.closing_order[0][0], "Phone")

    def test_every_debt_is_closed_exactly_once(self):
        plan = plan_payoff(self.DEBTS, D("0"), "avalanche")
        self.assertEqual(sorted(n for n, _ in plan.closing_order), ["Car", "Card", "Phone"])

    def test_extra_money_shortens_payoff(self):
        none = plan_payoff(self.DEBTS, D("0"), "avalanche")
        some = plan_payoff(self.DEBTS, D("5000"), "avalanche")
        self.assertLess(some.months, none.months)

    def test_unpayable_debt_reports_none(self):
        plan = plan_payoff([DebtInput("Bad", D("100000"), D("48"), D("100"))], D("0"), "avalanche")
        self.assertIsNone(plan.months)

    def test_unknown_strategy(self):
        with self.assertRaises(ValueError):
            plan_payoff(self.DEBTS, D("0"), "random")

    def test_no_debts(self):
        plan = plan_payoff([], D("100"), "avalanche")
        self.assertEqual(plan.months, 0)
