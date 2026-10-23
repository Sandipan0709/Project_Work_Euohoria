"""Loan and EMI maths. All figures are *calculated estimates*, not lender-confirmed.

Rounding: interest for each month is ``balance * annual_rate / 1200`` rounded half-up to
the paisa. Lenders differ (daily accrual, reducing-balance conventions), so results can
differ slightly from a lender statement.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .recurrence import add_months

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_MONTHS = 600  # 50 years; beyond this we report "does not pay off".


def _round(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def monthly_interest(balance: Decimal, annual_rate: Decimal) -> Decimal:
    return _round(balance * annual_rate / Decimal(1200))


def emi_for(principal: Decimal, annual_rate: Decimal, months: int) -> Decimal:
    """Standard reducing-balance EMI for a loan of ``months`` instalments."""
    if months < 1:
        raise ValueError("months must be at least 1")
    if annual_rate == 0:
        return _round(principal / months)
    r = annual_rate / Decimal(1200)
    growth = (1 + r) ** months
    return _round(principal * r * growth / (growth - 1))


@dataclass(frozen=True)
class Amortization:
    months: int | None  # None: the payment never clears the balance
    total_interest: Decimal
    total_paid: Decimal

    def payoff_date(self, today: date) -> date | None:
        return None if self.months is None else add_months(today, self.months)


def amortize(balance: Decimal, annual_rate: Decimal, payment: Decimal, extra: Decimal = ZERO) -> Amortization:
    """Pay ``payment + extra`` every month until the balance is cleared."""
    total_interest = total_paid = ZERO
    months = 0
    monthly = payment + extra
    while balance > 0:
        interest = monthly_interest(balance, annual_rate)
        if monthly <= interest or months >= MAX_MONTHS:
            return Amortization(None, total_interest, total_paid)
        paid = min(monthly, balance + interest)
        balance = balance + interest - paid
        total_interest += interest
        total_paid += paid
        months += 1
    return Amortization(months, total_interest, total_paid)


@dataclass(frozen=True)
class DebtInput:
    name: str
    balance: Decimal
    annual_rate: Decimal
    minimum: Decimal


@dataclass(frozen=True)
class PayoffPlan:
    strategy: str
    months: int | None
    total_interest: Decimal
    closing_order: tuple[tuple[str, int], ...]  # (debt name, month it is cleared)


def _priority(strategy: str, debt: DebtInput, balance: Decimal):
    if strategy == "avalanche":
        return (-debt.annual_rate, balance)
    if strategy == "snowball":
        return (balance, -debt.annual_rate)
    raise ValueError(f"Unknown strategy: {strategy!r}")


def plan_payoff(debts: list[DebtInput], extra_monthly: Decimal = ZERO, strategy: str = "avalanche") -> PayoffPlan:
    """Simulate paying every debt's minimum plus ``extra_monthly`` towards the priority debt.

    Minimums of cleared debts roll over into the pool, so the total monthly outlay stays
    constant (the usual definition of avalanche/snowball).
    """
    balances = {d.name: d.balance for d in debts if d.balance > 0}
    by_name = {d.name: d for d in debts}
    pool = sum((d.minimum for d in debts if d.balance > 0), ZERO) + extra_monthly
    total_interest = ZERO
    closing: list[tuple[str, int]] = []
    month = 0

    while balances:
        month += 1
        if month > MAX_MONTHS:
            return PayoffPlan(strategy, None, total_interest, tuple(closing))

        for name in balances:
            interest = monthly_interest(balances[name], by_name[name].annual_rate)
            balances[name] += interest
            total_interest += interest

        available = pool
        for name in list(balances):  # minimums first
            paid = min(by_name[name].minimum, balances[name], available)
            balances[name] -= paid
            available -= paid
        for name in sorted(balances, key=lambda n: _priority(strategy, by_name[n], balances[n])):
            if available <= 0:
                break
            paid = min(balances[name], available)
            balances[name] -= paid
            available -= paid

        for name in [n for n, b in balances.items() if b <= 0]:
            del balances[name]
            closing.append((name, month))

    return PayoffPlan(strategy, month, total_interest, tuple(closing))


def estimate_principal_part(outstanding: Decimal, annual_rate: Decimal, payment: Decimal) -> Decimal:
    """Share of a payment that reduces the balance (payment minus one month of interest)."""
    return max(ZERO, min(outstanding, payment - monthly_interest(outstanding, annual_rate)))
