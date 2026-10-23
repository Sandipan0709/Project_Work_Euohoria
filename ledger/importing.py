"""CSV import: parsing, validation, duplicate detection and commit.

Duplicate detection (documented in docs/ARCHITECTURE.md):

* Each row gets a fingerprint: hash of user, account, date, signed kind, amount, normalised
  payee and the row's *occurrence number* among identical rows in the file. Re-importing
  the same file therefore produces the same fingerprints and is skipped entirely, while two
  genuinely identical purchases on one day within a single file are both kept.
* A row that matches a manually entered transaction (same account, date, kind and amount,
  no fingerprint) is also skipped, once per existing manual entry.
"""

import csv
import datetime as dt
import hashlib
import io
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction as db_transaction

from .models import Account, Category, ImportBatch, Transaction

MAX_ROWS = 5000
MAX_AMOUNT = Decimal("1000000000000")
DATE_FORMATS = {
    "dmy": ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d-%b-%Y", "%d %B %Y"],
    "ymd": ["%Y-%m-%d", "%Y/%m/%d"],
    "mdy": ["%m/%d/%Y", "%m-%d-%Y"],
}
DATE_FORMAT_LABELS = {
    "dmy": "Day first (31/12/2025)",
    "ymd": "Year first (2025-12-31)",
    "mdy": "Month first (12/31/2025)",
}


class ImportError_(ValueError):
    """The file cannot be read at all (as opposed to individual bad rows)."""


@dataclass(frozen=True)
class Mapping:
    date: str
    amount: str = ""
    debit: str = ""
    credit: str = ""
    payee: str = ""
    category: str = ""
    notes: str = ""
    date_format: str = "dmy"
    amount_mode: str = "signed"  # "signed": negative = money out. "all_expense": every row is spending.


@dataclass
class ParsedRow:
    line: int
    status: str = "ok"  # ok | duplicate | invalid
    reason: str = ""
    date: dt.date | None = None
    kind: str = ""
    amount: Decimal | None = None
    payee: str = ""
    category_name: str = ""
    notes: str = ""
    fingerprint: str = ""


@dataclass
class Report:
    rows: list[ParsedRow] = field(default_factory=list)

    def of(self, status: str) -> list[ParsedRow]:
        return [r for r in self.rows if r.status == status]

    @property
    def counts(self) -> dict[str, int]:
        c = Counter(r.status for r in self.rows)
        return {"total": len(self.rows), "ok": c["ok"], "duplicate": c["duplicate"], "invalid": c["invalid"]}


def decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportError_("The file is not text we can read. Export it as CSV (UTF-8) and try again.")


def read_table(text: str) -> tuple[list[str], list[dict[str, str]]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
    if not headers:
        raise ImportError_("The file has no header row.")
    rows = []
    for raw in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k}
        if any(row.values()):
            rows.append(row)
        if len(rows) > MAX_ROWS:
            raise ImportError_(f"Too many rows. Import at most {MAX_ROWS} at a time.")
    return headers, rows


def guess_mapping(headers: list[str]) -> dict[str, str]:
    """Suggest columns by common bank-statement header names."""
    lowered = {h.lower(): h for h in headers}

    def find(*words):
        for word in words:
            for low, original in lowered.items():
                if word in low:
                    return original
        return ""

    return {
        "date": find("date"),
        "amount": find("amount"),
        "debit": find("debit", "withdrawal"),
        "credit": find("credit", "deposit"),
        "payee": find("payee", "merchant", "description", "narration", "particulars"),
        "category": find("category"),
        "notes": find("notes", "remark", "memo"),
    }


def parse_amount(text: str) -> Decimal:
    value = text.strip()
    negative = False
    if value.startswith("(") and value.endswith(")"):
        negative, value = True, value[1:-1]
    for token in ("₹", "INR", "Rs.", "Rs", ",", " "):
        value = value.replace(token, "")
    lowered = value.lower()
    if lowered.endswith("dr"):
        negative, value = True, value[:-2]
    elif lowered.endswith("cr"):
        value = value[:-2]
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"'{text}' is not an amount") from exc
    if not amount.is_finite() or abs(amount) >= MAX_AMOUNT:
        raise ValueError(f"'{text}' is not a valid amount")
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return -amount if negative else amount


def parse_date(text: str, date_format: str) -> dt.date:
    for pattern in DATE_FORMATS.get(date_format, ()):
        try:
            return dt.datetime.strptime(text.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f"'{text}' is not a date in the chosen format")


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _fingerprint(user_id: int, account_id: int, row: ParsedRow, occurrence: int) -> str:
    key = "|".join(
        [
            str(user_id),
            str(account_id),
            row.date.isoformat(),
            row.kind,
            str(row.amount),
            _norm(row.payee),
            str(occurrence),
        ]
    )
    return hashlib.sha256(key.encode()).hexdigest()


def _interpret(row: dict[str, str], mapping: Mapping, parsed: ParsedRow) -> None:
    parsed.date = parse_date(row.get(mapping.date, ""), mapping.date_format)
    if mapping.debit or mapping.credit:
        debit = parse_amount(row[mapping.debit]) if row.get(mapping.debit) else Decimal(0)
        credit = parse_amount(row[mapping.credit]) if row.get(mapping.credit) else Decimal(0)
        if debit and credit:
            raise ValueError("Both debit and credit are filled")
        signed = credit - abs(debit)
    elif mapping.amount:
        signed = parse_amount(row.get(mapping.amount, ""))
    else:
        raise ValueError("No amount column chosen")
    if signed == 0:
        raise ValueError("Amount is zero")
    if mapping.amount_mode == "all_expense":
        parsed.kind, parsed.amount = Transaction.Kind.EXPENSE, abs(signed)
    elif signed < 0:
        parsed.kind, parsed.amount = Transaction.Kind.EXPENSE, -signed
    else:
        parsed.kind, parsed.amount = Transaction.Kind.INCOME, signed
    parsed.payee = row.get(mapping.payee, "")[:120] if mapping.payee else ""
    parsed.category_name = row.get(mapping.category, "") if mapping.category else ""
    parsed.notes = row.get(mapping.notes, "") if mapping.notes else ""


def build_report(user, account: Account, rows: list[dict[str, str]], mapping: Mapping) -> Report:
    if not mapping.date or not (mapping.amount or mapping.debit or mapping.credit):
        raise ImportError_("Choose a date column and an amount (or debit/credit) column.")
    report = Report()
    seen: Counter = Counter()
    for index, row in enumerate(rows, start=2):  # line 1 is the header
        parsed = ParsedRow(line=index)
        try:
            _interpret(row, mapping, parsed)
        except ValueError as exc:
            parsed.status, parsed.reason = "invalid", str(exc)
        else:
            base = (parsed.date, parsed.kind, parsed.amount, _norm(parsed.payee))
            seen[base] += 1
            parsed.fingerprint = _fingerprint(user.id, account.id, parsed, seen[base])
        report.rows.append(parsed)
    _mark_duplicates(user, account, report)
    return report


def _mark_duplicates(user, account: Account, report: Report) -> None:
    candidates = [r for r in report.rows if r.status == "ok"]
    known = set(
        Transaction.objects.filter(user=user, fingerprint__in=[r.fingerprint for r in candidates]).values_list(
            "fingerprint", flat=True
        )
    )
    manual: Counter = Counter()
    if candidates:
        dates = {r.date for r in candidates}
        for d, kind, amount in Transaction.objects.filter(
            user=user, account=account, fingerprint="", date__in=dates
        ).values_list("date", "kind", "amount"):
            manual[(d, kind, amount)] += 1
    for r in candidates:
        if r.fingerprint in known:
            r.status, r.reason = "duplicate", "Already imported"
        elif manual[(r.date, r.kind, r.amount)] > 0:
            manual[(r.date, r.kind, r.amount)] -= 1
            r.status, r.reason = "duplicate", "Matches a transaction you already recorded"


@db_transaction.atomic
def commit_import(user, account: Account, filename: str, report: Report) -> ImportBatch:
    """Create transactions for the importable rows. All-or-nothing."""
    by_name = {c.name.lower(): c for c in Category.objects.filter(user=user)}
    counts = report.counts
    batch = ImportBatch.objects.create(
        user=user,
        account=account,
        filename=filename[:200],
        rows_total=counts["total"],
        rows_imported=counts["ok"],
        rows_duplicate=counts["duplicate"],
        rows_invalid=counts["invalid"],
    )
    Transaction.objects.bulk_create(
        [
            Transaction(
                user=user,
                account=account,
                kind=r.kind,
                amount=r.amount,
                date=r.date,
                payee=r.payee,
                notes=r.notes,
                category=by_name.get(r.category_name.lower()),
                fingerprint=r.fingerprint,
                import_batch=batch,
            )
            for r in report.of("ok")
        ]
    )
    return batch


# ---- export ---------------------------------------------------------------------------


def safe_cell(value) -> str:
    """Neutralise spreadsheet formula injection (cells starting with = + - @ tab or CR)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


EXPORT_HEADER = [
    "Date",
    "Type",
    "Amount",
    "Account",
    "To account",
    "Category",
    "Payee",
    "Notes",
    "Tags",
    "Payment method",
]


def export_row(t: Transaction) -> list[str]:
    return [
        t.date.isoformat(),
        t.get_kind_display(),
        str(t.amount),
        safe_cell(t.account.name),
        safe_cell(t.to_account.name if t.to_account_id else ""),
        safe_cell(str(t.category) if t.category_id else ""),
        safe_cell(t.payee),
        safe_cell(t.notes),
        safe_cell(t.tags),
        t.get_payment_method_display(),
    ]
