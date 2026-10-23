# Contributing

1. Fork, create a branch, make a focused change.
2. Money logic lives in pure modules (`planner/forecast.py`, `planner/debt.py`, `planner/goals.py`,
   `planner/recurrence.py`, `ledger/services.py`). Add a test next to any change there.
3. Before opening a pull request:

   ```bash
   ruff check . && ruff format --check .
   python manage.py makemigrations --check --dry-run
   python manage.py test
   ```
4. Never commit `.env`, databases, exports or real financial data. Use `python manage.py seed_demo` for sample data.
5. Report security problems privately (see `SECURITY.md`), not in public issues.

No licence has been chosen yet; see the note in the README.
