from datetime import date
from decimal import Decimal as D

from django.test import SimpleTestCase

from core.formatting import fmt_date, group_indian, inr


class FormattingTests(SimpleTestCase):
    def test_indian_grouping(self):
        for digits, expected in {
            "0": "0",
            "999": "999",
            "1000": "1,000",
            "100000": "1,00,000",
            "12345678": "1,23,45,678",
        }.items():
            self.assertEqual(group_indian(digits), expected)

    def test_inr(self):
        self.assertEqual(inr(D("1234567.5")), "₹12,34,567.50")
        self.assertEqual(inr(-1500), "-₹1,500.00")
        self.assertEqual(inr(D("0.005")), "₹0.01")
        self.assertEqual(inr(49999.5, 0), "₹50,000")
        self.assertEqual(inr(10, symbol=False), "10.00")
        self.assertEqual(inr(None), "—")
        self.assertEqual(inr("junk"), "—")

    def test_fmt_date(self):
        self.assertEqual(fmt_date(date(2025, 10, 5)), "5 Oct 2025")
        self.assertEqual(fmt_date(date(2025, 10, 5), False), "5 Oct")
        self.assertEqual(fmt_date(None), "—")
