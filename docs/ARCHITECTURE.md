# Architecture and calculation rules

## Stack and boundaries
Django 5.2 server-rendered app, SQLite by default, WhiteNoise for static files, no JavaScript framework and no
client-side chart library (charts are server-rendered SVG). All financial calculations run on the server in plain
Python with `Decimal`.

| App | Responsibility |
|---|---|
| `core` | Accounts/auth pages, profile and alert preferences, Overview, notifications, data export/deletion, formatting helpers, template tags |
| `ledger` | `Account`, `Category`, `Transaction`, `ImportBatch`; balances and period totals (`services.py`); CSV import/export (`importing.py`) |
| `planner` | Budgets, bills and expected income (`ScheduledItem`/`Occurrence`), debts, goals, forecast, reports |

Pure calculation modules (no database access, unit-tested directly): `planner/forecast.py`, `planner/debt.py`,
`planner/goals.py`, `planner/recurrence.py`. Database-aware orchestration: `planner/services.py`, `planner/budgets.py`,
`planner/reports.py`, `ledger/services.py`.

The pre-existing `core.Expense` and `core.Budget` models are kept (read-only, unused). Migration
`ledger/0002` copies expenses into transactions and `planner/0002` copies the monthly limit into an overall budget.

## Money rules
- Amounts are positive `Decimal(14,2)`; `kind` gives direction. Database `CHECK` constraints enforce `amount > 0` and
  valid transfer shape. Interest is rounded half-up to the paisa each month.
- Dates are local calendar dates (`Asia/Kolkata`, `timezone.localdate()`); there is no time-of-day in financial logic.
- Currency is INR only. Formatting uses lakh/crore grouping (`core/formatting.py`).

## Definitions
| Figure | Definition |
|---|---|
| **Account balance** | opening balance + income + refunds + incoming transfers − expenses − outgoing transfers, over transactions dated **on or before today**. Future-dated transactions are scheduled, not yet moved. |
| **In your accounts (liquid)** | sum of balances of non-archived cash, bank and wallet accounts. Credit cards are excluded. |
| **Credit card balance** | normally negative (what you owe). Card purchases are expenses on the card; paying the card is a *transfer* from a bank account to the card, so spending is never counted twice. |
| **Net worth** | liquid balances + card balances − outstanding balances of active debts. Goals are not assets (contributions are tracked on the goal, not moved between accounts). Net worth uses the debt balance **you entered**. |
| **Income / spending (period)** | income = `income` transactions. Spending = `expense` − `refund`. **Transfers are excluded.** |
| **Savings / savings rate** | income − spending; rate = savings ÷ income (none if income is 0). |
| **Obligations** | unpaid bill due dates and EMIs from today (including overdue) through month end. |
| **Available to spend (estimate)** | liquid balance − obligations through month end. It is *not* reduced by the buffer or by unrecorded spending. |
| **Projected balance** | liquid balance + dated events from the forecast engine (below). |

## Forecast engine (`planner/forecast.py`, `planner/services.py`)
`project()` is a pure function: opening balance + a list of signed dated events → daily balances, lowest point,
first day below zero, first day below the buffer, and totals. Horizons: 7, 14, 30, 90 days.

`services.build_events()` decides which events exist, with these **double-counting rules**:
1. The opening balance already includes every transaction dated today or earlier.
2. A bill/income due date with an `Occurrence` row (paid, received, skipped) is not projected; paying created a transaction.
3. Transactions dated in the future are projected once, as scheduled transactions.
4. EMIs come from the debt record (not from bills); a month with a recorded payment is not projected again; the number
   of EMIs is limited by the estimated payoff.
5. Income due before today that was never marked received is **not** projected.
6. Overdue bills (unrecorded, up to 60 days back) are assumed paid on day 0.
7. Credit-card bills (`settles_account`) leave the bank account as transfers; card purchases are not projected.

Scenarios: **conservative** (default) counts only income flagged *confirmed*; **expected** counts all income. Variable
bills use their expected amount. Unrecorded future spending (groceries, etc.) is **not predicted**, which is stated on the page.
Account-specific forecasts include only items assigned to that account; items with no account appear in the overall view only.
Every forecast page lists its assumptions, produced alongside the numbers.

## Recurrence (`planner/recurrence.py`)
Each due date is computed from the original start date, never the previous occurrence: 31 Jan → 28/29 Feb → 31 Mar.
Yearly 29 Feb → 28 Feb in common years, 29 Feb in leap years. Changing a schedule never edits past transactions or
occurrences. An `Occurrence` is unique per (item, due date) so recording is idempotent.

## Budgets (`planner/budgets.py`)
Monthly (calendar month) or weekly (Mon–Sun). A category budget includes its subcategories. The overall budget is total
spending. Pace: elapsed share of the period vs. share used; projected spend = spent ÷ elapsed days × period days, shown
only after 3 days (monthly) or 2 days (weekly). State: *warn* at the configured percentage, *over* at 100%.

## Debt (`planner/debt.py`)
Reducing-balance maths with monthly interest = balance × rate ÷ 1200. EMI formula is the standard one. Payoff, interest
left and extra-payment scenarios are **estimates**; the UI says so and lets you overwrite the balance with the lender's.
Avalanche (highest rate first) and snowball (smallest balance first) pay every minimum, then roll freed minimums plus an
extra amount into the priority debt. If an EMI does not cover monthly interest the debt is reported as never paid off.

## Goals (`planner/goals.py`)
Required monthly = remaining ÷ months to target date. Estimated completion uses the planned monthly amount if set,
otherwise one third of contributions in the last 90 days; with neither, no estimate is given.

## CSV import (`ledger/importing.py`)
Upload → map columns (guessed from header names) → preview with a validation report → confirm. Nothing is written until
confirm; commit is atomic. Supports one signed amount column or separate debit/credit columns, day-first/year-first/month-first
dates, `₹`, `Rs.`, lakh commas, `(negative)` and `Dr/Cr` suffixes. Duplicates: a row fingerprint (user, account, date,
type, amount, payee, occurrence number within the file) makes re-importing a file a no-op while keeping genuinely
repeated purchases; rows matching a hand-entered transaction are skipped once per existing entry.

## Notifications (`core/notifications.py`)
Generated when you open the Overview or Notifications page (there is no background worker). Each event has a stable
`key` (e.g. `bill-due:<item>:<date>`), unique per user, so repeated generation never duplicates. Per-user toggles live in Settings.

## URL map (no public JSON API)
Pages are HTML views; there is no REST API, so there is no API reference beyond `core/urls.py`, `ledger/urls.py` and
`planner/urls.py`. The one machine-readable output is the per-user JSON export at `/settings/export/`.
