from datetime import date

from django.test import SimpleTestCase

from planner.recurrence import add_months, occurrence_dates


class AddMonthsTests(SimpleTestCase):
    def test_month_end_clamps_without_drifting(self):
        start = date(2025, 1, 31)
        got = [add_months(start, k) for k in range(5)]
        self.assertEqual(
            got, [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30), date(2025, 5, 31)]
        )

    def test_leap_year_february(self):
        self.assertEqual(add_months(date(2024, 1, 31), 1), date(2024, 2, 29))
        self.assertEqual(add_months(date(2023, 1, 31), 1), date(2023, 2, 28))

    def test_crosses_year_boundary(self):
        self.assertEqual(add_months(date(2025, 11, 15), 3), date(2026, 2, 15))


class OccurrenceDatesTests(SimpleTestCase):
    def test_monthly_on_the_31st_over_a_window(self):
        dates = occurrence_dates(date(2025, 1, 31), "monthly", date(2025, 2, 1), date(2025, 4, 30))
        self.assertEqual(dates, [date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30)])

    def test_yearly_leap_day_returns_on_leap_years_only_as_29th(self):
        dates = occurrence_dates(date(2024, 2, 29), "yearly", date(2024, 1, 1), date(2028, 12, 31))
        self.assertEqual(
            dates, [date(2024, 2, 29), date(2025, 2, 28), date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29)]
        )

    def test_weekly(self):
        dates = occurrence_dates(date(2025, 1, 1), "weekly", date(2025, 1, 10), date(2025, 1, 31))
        self.assertEqual(dates, [date(2025, 1, 15), date(2025, 1, 22), date(2025, 1, 29)])

    def test_quarterly(self):
        dates = occurrence_dates(date(2025, 1, 15), "quarterly", date(2025, 1, 1), date(2025, 12, 31))
        self.assertEqual(dates, [date(2025, 1, 15), date(2025, 4, 15), date(2025, 7, 15), date(2025, 10, 15)])

    def test_once_only_inside_window(self):
        self.assertEqual(
            occurrence_dates(date(2025, 3, 3), "once", date(2025, 3, 1), date(2025, 3, 31)), [date(2025, 3, 3)]
        )
        self.assertEqual(occurrence_dates(date(2025, 3, 3), "once", date(2025, 4, 1), date(2025, 4, 30)), [])

    def test_window_before_start_is_empty(self):
        self.assertEqual(occurrence_dates(date(2025, 6, 1), "monthly", date(2025, 1, 1), date(2025, 5, 31)), [])

    def test_end_date_is_respected(self):
        dates = occurrence_dates(
            date(2025, 1, 5), "monthly", date(2025, 1, 1), date(2025, 12, 31), end_date=date(2025, 3, 31)
        )
        self.assertEqual(dates, [date(2025, 1, 5), date(2025, 2, 5), date(2025, 3, 5)])

    def test_far_future_window_starts_correctly(self):
        dates = occurrence_dates(date(2020, 1, 31), "monthly", date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(dates, [date(2026, 2, 28)])

    def test_unknown_frequency_raises(self):
        with self.assertRaises(ValueError):
            occurrence_dates(date(2025, 1, 1), "fortnightly", date(2025, 1, 1), date(2025, 2, 1))
