# Design system

All tokens are CSS variables at the top of `static/css/fintrackr.css`. Pages use these tokens and do not introduce new colours.

## Colour
| Token | Value | Use |
|---|---|---|
| `--sky` | `#38BDF8` | Chart fills, meter bars, the accent rule on figure bands. **Never text** (fails contrast on white). |
| `--blue-700` | `#0369A1` | Links, primary buttons, focus ring, selected states (5.9:1 on white). |
| `--blue-600` | `#0284C7` | Chart line. |
| `--ink` / `--ink-2` | `#0F172A` / `#64748B` | Main / secondary text. |
| `--bg` / `--surface` / `--line` | `#F8FAFC` / `#FFFFFF` / `#E2E8F0` | Page, panels, borders. |
| `--danger` `--warn` `--ok` | `#B91C1C` `#B45309` `#15803D` | Negative/over, caution, positive. Always paired with words, never colour alone. |

## Type, spacing, shape
System font stack; tabular numerals for all money. Scale: 12 / 13 / 15 / 18 / 24 / 26 px. Spacing 4, 8, 12, 16, 24, 32.
Radius 6 px (4 px small). One elevation (a hairline shadow); structure comes from 1 px rules, not stacked cards.
Content max width 1180 px; sidebar 224 px, collapsing to a menu under 860 px.

## Patterns
- **Figures band**: a single ruled strip of 3–4 labelled figures, each tagged *Actual* or *Estimate* with a one-line reason.
- **Section header**: small uppercase label with a rule; no boxes around ordinary lists.
- **Tables**: sticky-style header row, right-aligned tabular numbers, row hover; wide tables scroll inside their own container.
- **Meters**: filled bar for amount used, dark tick for the share of the period elapsed.
- **Charts**: server-rendered SVG with `role="img"`, an `aria-label` summarising the result, `<title>` tooltips, and the same data in a table on the page.
- **States**: every list has an empty state that says what to do next; validation errors appear next to the field with `role="alert"`; destructive actions use a confirm page (bulk delete adds a second step).
- **Focus**: 2 px `--blue-700` outline on every interactive element; a skip link is the first focusable element.
- **Pages are shaped by their job**: Overview = figures band + alerts + next-30-days; Transactions = filter bar + dense table + bulk bar;
  Cash flow = horizon tabs + timeline + event ledger; Budgets = pace bars; Bills = agenda grouped by urgency; Debt = amortisation tables;
  Goals = per-goal projection; Reports = period comparison + insights with their basis; Calendar = month grid or agenda.

## Not yet verified
No automated accessibility audit (axe/Lighthouse) has been run. Contrast values above were calculated by hand. Screen-reader testing has not been done.
