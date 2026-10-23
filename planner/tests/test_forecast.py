from datetime import date
from decimal import Decimal as D

from django.test import SimpleTestCase

from planner.forecast import Event, project

START = date(2025, 10, 20)


def ev(day, amount, label="x"):
    return Event(date(2025, 10, day), D(amount), label, "bill")


class ProjectTests(SimpleTestCase):
    def test_no_events_flat_line(self):
        f = project(D("1000"), [], START, 7, D("500"))
        self.assertEqual(len(f.points), 8)
        self.assertEqual(f.end_balance, D("1000.00"))
        self.assertIsNone(f.first_shortfall)
        self.assertIsNone(f.first_below_buffer)

    def test_shortfall_and_buffer_dates(self):
        events = [ev(22, "-6000"), ev(25, "-5000"), ev(28, "8000")]
        f = project(D("10000"), events, START, 14, D("5000"))
        self.assertEqual(f.first_below_buffer, date(2025, 10, 22))  # 4000 < 5000
        self.assertEqual(f.first_shortfall, date(2025, 10, 25))  # -1000 < 0
        self.assertEqual(f.min_balance, D("-1000.00"))
        self.assertEqual(f.min_date, date(2025, 10, 25))
        self.assertEqual(f.end_balance, D("7000.00"))
        self.assertEqual((f.total_in, f.total_out), (D("8000.00"), D("11000.00")))

    def test_events_outside_window_are_ignored(self):
        f = project(D("100"), [ev(19, "-50"), Event(date(2025, 12, 1), D("-50"), "late", "bill")], START, 7)
        self.assertEqual(f.end_balance, D("100.00"))

    def test_event_on_start_day_and_on_last_day_count(self):
        f = project(D("100"), [ev(20, "-10"), ev(27, "-5")], START, 7)
        self.assertEqual(f.points[0].balance, D("90.00"))
        self.assertEqual(f.end_balance, D("85.00"))

    def test_negative_opening_balance_is_an_immediate_shortfall(self):
        f = project(D("-200"), [], START, 7)
        self.assertEqual(f.first_shortfall, START)

    def test_decimal_precision_is_exact(self):
        f = project(D("0.10"), [ev(21, "0.20"), ev(21, "-0.30")], START, 7)
        self.assertEqual(f.end_balance, D("0.00"))

    def test_invalid_horizon(self):
        with self.assertRaises(ValueError):
            project(D("1"), [], START, 0)
