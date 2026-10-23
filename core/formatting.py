"""Indian-convention formatting for money and dates."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def group_indian(digits: str) -> str:
    """12345678 -> 1,23,45,678"""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def inr(value, decimals: int = 2, symbol: bool = True) -> str:
    """Format an amount as ₹1,23,456.50 (negative: -₹1,500.00). Bad input renders as an em dash."""
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return "—"
    quantum = Decimal(1).scaleb(-decimals)
    amount = amount.quantize(quantum, rounding=ROUND_HALF_UP)
    negative = amount < 0
    whole, _, frac = f"{abs(amount):f}".partition(".")
    text = group_indian(whole) + (f".{frac}" if decimals else "")
    return f"{'-' if negative else ''}{'₹' if symbol else ''}{text}"


def fmt_date(value: date | None, with_year: bool = True) -> str:
    if not value:
        return "—"
    return f"{value.day} {value.strftime('%b')} {value.year}" if with_year else f"{value.day} {value.strftime('%b')}"
