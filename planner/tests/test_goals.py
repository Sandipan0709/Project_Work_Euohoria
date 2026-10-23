from datetime import date
from decimal import Decimal as D

from django.test import SimpleTestCase

from planner.goals import project_goal

TODAY = date(2025, 10, 1)


class GoalProjectionTests(SimpleTestCase):
    def test_planned_contribution_drives_completion(self):
        p = project_goal(D("120000"), D("20000"), TODAY, date(2026, 10, 1), D("10000"), [])
        self.assertEqual(p.remaining, D("100000"))
        self.assertEqual(p.estimated_completion, date(2026, 8, 1))  # 10 months
        self.assertTrue(p.on_track)
        self.assertEqual(p.progress_percent, 16)

    def test_required_monthly_amount(self):
        p = project_goal(D("60000"), D("0"), TODAY, date(2026, 4, 1), None, [])
        self.assertEqual(p.required_monthly, D("10000.00"))  # 182 days ~ 6 months

    def test_behind_schedule(self):
        p = project_goal(D("120000"), D("0"), TODAY, date(2026, 1, 1), D("5000"), [])
        self.assertFalse(p.on_track)

    def test_pace_from_recent_contributions_only(self):
        contributions = [
            (date(2025, 9, 15), D("6000")),
            (date(2025, 8, 20), D("3000")),
            (date(2024, 1, 1), D("99999")),
        ]  # old one must be ignored
        p = project_goal(D("20000"), D("9000"), TODAY, None, None, contributions)
        # 9000 in the last 90 days -> 3000/month; 11000 remaining -> 4 months
        self.assertEqual(p.estimated_completion, date(2026, 2, 1))
        self.assertIn("90 days", p.pace_basis)

    def test_no_data_gives_no_estimate(self):
        p = project_goal(D("20000"), D("0"), TODAY, None, None, [])
        self.assertIsNone(p.estimated_completion)
        self.assertIsNone(p.on_track)

    def test_target_reached(self):
        p = project_goal(D("10000"), D("12000"), TODAY, date(2026, 1, 1), None, [])
        self.assertTrue(p.achieved)
        self.assertEqual((p.remaining, p.progress_percent), (D("0.00"), 100))

    def test_past_deadline_without_pace_is_not_on_track(self):
        p = project_goal(D("10000"), D("1000"), TODAY, date(2025, 9, 1), None, [])
        self.assertFalse(p.on_track)
        self.assertIsNone(p.required_monthly)
