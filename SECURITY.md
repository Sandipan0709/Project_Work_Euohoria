# Security and privacy notes

## What is implemented (and tested)
- Every record belongs to a user. Views fetch objects with `user=request.user`; forms restrict every
  account/category choice to the owner. `core/tests/test_views.py::IsolationTests` checks that one user gets 404 on
  another user's records, cannot delete them in bulk, cannot import into their account, and only exports their own data.
- Passwords: Django's PBKDF2 hasher, 10-character minimum, common/numeric/similarity validators.
- CSRF protection on every POST; logout is POST-only; `next` redirects are restricted to this site.
- Production settings (when `FINTRACKR_DEBUG` is off): secure cookies, HSTS, SSL redirect, `X-Frame-Options: DENY`,
  nosniff, same-origin referrer policy. A missing `FINTRACKR_SECRET_KEY` stops the app from starting.
- CSV import: 2 MB and 5,000-row limits, extension check, parsed in memory, never executed or stored on disk.
  CSV export neutralises spreadsheet formula injection (cells beginning with `= + - @`).
- Money is stored as `Decimal` with database `CHECK` constraints (positive amounts, valid transfers).
- Account deletion requires the password and removes all of the user's data. A JSON export of everything is available.

## Known limitations
- **No login rate limiting or lockout.** Put the app behind a reverse proxy with rate limiting, or add `django-axes`.
- SQLite, no encryption at rest. Use disk encryption; use PostgreSQL for multi-user hosting.
- No email verification, password reset, or two-factor authentication.
- Sessions hold the uploaded CSV text between the upload and confirm steps (cleared on completion or cancel).
- No Content-Security-Policy header yet; the app ships no third-party scripts, so one can be added safely.
- This has not had an independent security review or penetration test.

## Reporting
Email the maintainer privately. Do not include real financial data in reports.
