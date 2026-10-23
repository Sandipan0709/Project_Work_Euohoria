from datetime import date
from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase

from ledger.importing import (
    ImportError_,
    Mapping,
    build_report,
    commit_import,
    decode,
    guess_mapping,
    parse_amount,
    parse_date,
    read_table,
    safe_cell,
)
from ledger.models import ImportBatch, Transaction

from .factories import category, make_account, make_txn, make_user


class ParsingTests(SimpleTestCase):
    def test_amount_formats(self):
        cases = {
            "1,23,456.50": "123456.50",
            "₹ 450": "450.00",
            "Rs. 99.5": "99.50",
            "(250.00)": "-250.00",
            "-12": "-12.00",
            "300 Dr": "-300.00",
            "300 Cr": "300.00",
            "0.005": "0.01",
        }
        for text, expected in cases.items():
            self.assertEqual(parse_amount(text), D(expected), text)

    def test_bad_amounts_rejected(self):
        for text in ("abc", "", "NaN", "Infinity", "1e999", "1,2,x"):
            with self.assertRaises(ValueError, msg=text):
                parse_amount(text)

    def test_dates(self):
        self.assertEqual(parse_date("31/12/2025", "dmy"), date(2025, 12, 31))
        self.assertEqual(parse_date("05-03-2025", "dmy"), date(2025, 3, 5))
        self.assertEqual(parse_date("05-03-2025", "mdy"), date(2025, 5, 3))
        self.assertEqual(parse_date("2025-03-05", "ymd"), date(2025, 3, 5))
        self.assertEqual(parse_date("5 Mar 2025", "dmy"), date(2025, 3, 5))
        for bad in ("32/01/2025", "2025-13-01", "yesterday"):
            with self.assertRaises(ValueError):
                parse_date(bad, "dmy")

    def test_decode_handles_bom_and_cp1252(self):
        self.assertEqual(decode("\ufeffDate,Amount".encode("utf-8")), "Date,Amount")
        self.assertEqual(decode("Caf\xe9 \u20b9".encode("cp1252", errors="ignore")), "Caf\xe9 ")

    def test_read_table_detects_semicolons_and_skips_blank_rows(self):
        headers, rows = read_table("Date;Amount;Payee\n01/10/2025;-50;Tea\n\n02/10/2025;100;Pay\n")
        self.assertEqual(headers, ["Date", "Amount", "Payee"])
        self.assertEqual(len(rows), 2)

    def test_empty_file_rejected(self):
        with self.assertRaises(ImportError_):
            read_table("")

    def test_guess_mapping(self):
        g = guess_mapping(["Txn Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Balance"])
        self.assertEqual(
            (g["date"], g["payee"], g["debit"], g["credit"]),
            ("Txn Date", "Narration", "Withdrawal Amt.", "Deposit Amt."),
        )

    def test_formula_injection_is_neutralised(self):
        self.assertEqual(safe_cell("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(safe_cell("@cmd"), "'@cmd")
        self.assertEqual(safe_cell("Normal"), "Normal")
        self.assertEqual(safe_cell(None), "")


CSV = """Date,Description,Amount,Category
01/10/2025,Swiggy,-450.00,Food & dining
02/10/2025,Salary,50000,Salary
03/10/2025,Bad row,not-a-number,
03/10/2025,Coffee,-120,
03/10/2025,Coffee,-120,
"""


class ReportAndCommitTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.acct = make_account(self.user)
        _, self.rows = read_table(CSV)
        self.mapping = Mapping(date="Date", amount="Amount", payee="Description", category="Category")

    def report(self):
        return build_report(self.user, self.acct, self.rows, self.mapping)

    def test_validation_report_counts_and_reasons(self):
        r = self.report()
        self.assertEqual(r.counts, {"total": 5, "ok": 4, "duplicate": 0, "invalid": 1})
        bad = r.of("invalid")[0]
        self.assertEqual(bad.line, 4)
        self.assertIn("not an amount", bad.reason)

    def test_commit_creates_transactions_with_signs_and_categories(self):
        batch = commit_import(self.user, self.acct, "stmt.csv", self.report())
        self.assertEqual((batch.rows_imported, batch.rows_invalid), (4, 1))
        food = Transaction.objects.get(payee="Swiggy")
        self.assertEqual(
            (food.kind, food.amount, food.category), ("expense", D("450.00"), category(self.user, "Food & dining"))
        )
        self.assertEqual(Transaction.objects.get(payee="Salary").kind, "income")
        self.assertEqual(Transaction.objects.filter(payee="Coffee").count(), 2)  # identical rows both kept

    def test_reimporting_the_same_file_is_fully_idempotent(self):
        commit_import(self.user, self.acct, "stmt.csv", self.report())
        again = self.report()
        self.assertEqual(again.counts, {"total": 5, "ok": 0, "duplicate": 4, "invalid": 1})
        commit_import(self.user, self.acct, "stmt.csv", again)
        self.assertEqual(Transaction.objects.count(), 4)
        self.assertEqual(ImportBatch.objects.count(), 2)

    def test_overlapping_statement_only_adds_new_rows(self):
        commit_import(self.user, self.acct, "a.csv", self.report())
        _, rows = read_table("Date,Description,Amount\n01/10/2025,Swiggy,-450\n04/10/2025,Metro,-60\n")
        r = build_report(self.user, self.acct, rows, Mapping(date="Date", amount="Amount", payee="Description"))
        self.assertEqual((r.counts["ok"], r.counts["duplicate"]), (1, 1))

    def test_row_matching_a_manual_entry_is_a_duplicate_once(self):
        make_txn(self.user, self.acct, "expense", 120, day=date(2025, 10, 3))
        r = self.report()
        coffee = [x for x in r.rows if x.payee == "Coffee"]
        self.assertEqual(sorted(x.status for x in coffee), ["duplicate", "ok"])

    def test_debit_credit_columns(self):
        _, rows = read_table("Date,Narr,Dr,Cr\n01/10/2025,Rent,15000,\n02/10/2025,Refund,,200\n03/10/2025,Both,5,5\n")
        r = build_report(self.user, self.acct, rows, Mapping(date="Date", debit="Dr", credit="Cr", payee="Narr"))
        kinds = [(x.kind, x.amount, x.status) for x in r.rows]
        self.assertEqual(kinds[0], ("expense", D("15000.00"), "ok"))
        self.assertEqual(kinds[1], ("income", D("200.00"), "ok"))
        self.assertEqual(r.rows[2].status, "invalid")

    def test_all_expense_mode_treats_positive_as_spending(self):
        _, rows = read_table("Date,Amount\n01/10/2025,300\n")
        r = build_report(self.user, self.acct, rows, Mapping(date="Date", amount="Amount", amount_mode="all_expense"))
        self.assertEqual(r.rows[0].kind, "expense")

    def test_zero_amount_and_bad_date_are_invalid(self):
        _, rows = read_table("Date,Amount\n01/10/2025,0\n99/99/2025,10\n")
        r = build_report(self.user, self.acct, rows, Mapping(date="Date", amount="Amount"))
        self.assertEqual([x.status for x in r.rows], ["invalid", "invalid"])

    def test_missing_mapping_is_an_error(self):
        with self.assertRaises(ImportError_):
            build_report(self.user, self.acct, self.rows, Mapping(date="", amount="Amount"))

    def test_same_file_into_another_account_is_not_a_duplicate(self):
        commit_import(self.user, self.acct, "a.csv", self.report())
        other = make_account(self.user, "Other")
        r = build_report(self.user, other, self.rows, self.mapping)
        self.assertEqual(r.counts["duplicate"], 0)

    def test_other_users_imports_do_not_collide(self):
        commit_import(self.user, self.acct, "a.csv", self.report())
        bob = make_user("bob")
        bob_acct = make_account(bob)
        r = build_report(bob, bob_acct, self.rows, self.mapping)
        self.assertEqual(r.counts["ok"], 4)
