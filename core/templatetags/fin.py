"""Template filters and server-rendered SVG charts (no client-side chart library)."""

from decimal import Decimal

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from core.formatting import fmt_date, inr

register = template.Library()


@register.filter(name="inr")
def inr_filter(value):
    return inr(value)


@register.filter
def inr0(value):
    return inr(value, 0)


@register.filter
def idate(value):
    return fmt_date(value)


@register.filter
def idate_short(value):
    return fmt_date(value, with_year=False)


@register.filter
def absval(value):
    try:
        return abs(value)
    except TypeError:
        return value


@register.filter
def get_item(mapping, key):
    return mapping.get(key)


@register.simple_tag(takes_context=True)
def qs_with(context, **overrides):
    """Current query string with some parameters replaced (used by pagination and sort links)."""
    query = context["request"].GET.copy()
    for key, value in overrides.items():
        if value in (None, ""):
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()


def _short(value: float) -> str:
    sign, v = ("-" if value < 0 else ""), abs(value)
    if v >= 10_000_000:
        return f"{sign}₹{v / 10_000_000:.1f}Cr"
    if v >= 100_000:
        return f"{sign}₹{v / 100_000:.1f}L"
    if v >= 1_000:
        return f"{sign}₹{v / 1_000:.0f}K"
    return f"{sign}₹{v:.0f}"


@register.simple_tag
def forecast_chart(forecast):
    """Line chart of the projected balance with zero and buffer reference lines."""
    width, height, left, right, top, bottom = 760, 240, 62, 14, 14, 30
    points = forecast.points
    values = [float(p.balance) for p in points]
    buffer = float(forecast.buffer)
    low, high = min(values), max(values)
    if low < max(buffer, 0.0) * 2:  # near the buffer or zero: show them for context
        low, high = min(low, 0.0), max(high, buffer)
    pad = (high - low) * 0.08 or 1
    low, high = low - pad, high + pad

    def x(i):
        return left + (width - left - right) * i / max(1, len(points) - 1)

    def y(v):
        return top + (height - top - bottom) * (high - v) / (high - low)

    line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    base = height - bottom
    area = f"{x(0):.1f},{base} {line} {x(len(values) - 1):.1f},{base}"
    out = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Projected balance for the next {forecast.days} days. Lowest balance '
        f'{escape(inr(forecast.min_balance, 0))} on {escape(fmt_date(forecast.min_date))}.">'
    ]
    for tick in range(5):
        value = low + (high - low) * tick / 4
        out.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y(value):.1f}" y2="{y(value):.1f}"/>'
            f'<text class="axis" x="{left - 8}" y="{y(value) + 4:.1f}" text-anchor="end">{_short(value)}</text>'
        )
    if low < 0 < high:
        out.append(f'<line class="zero" x1="{left}" x2="{width - right}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>')
    if low < buffer < high and buffer > 0:
        out.append(
            f'<line class="buffer" x1="{left}" x2="{width - right}" y1="{y(buffer):.1f}" y2="{y(buffer):.1f}"/>'
            f'<text class="axis buffer-label" x="{width - right}" y="{y(buffer) - 5:.1f}" text-anchor="end">'
            f"Buffer {_short(buffer)}</text>"
        )
    out.append(f'<polygon class="area" points="{area}"/><polyline class="series" points="{line}"/>')
    for i, p in enumerate(points):
        if p.events or p.balance < 0:
            klass = "dot-neg" if p.balance < 0 else "dot"
            names = ", ".join(escape(e.label) for e in p.events) or "negative balance"
            out.append(
                f'<circle class="{klass}" cx="{x(i):.1f}" cy="{y(values[i]):.1f}" r="3.5">'
                f"<title>{escape(fmt_date(p.date))}: {escape(inr(p.balance, 0))} ({names})</title></circle>"
            )
    for i in (0, len(points) // 2, len(points) - 1):
        anchor = "start" if i == 0 else "end" if i == len(points) - 1 else "middle"
        out.append(
            f'<text class="axis" x="{x(i):.1f}" y="{height - 8}" text-anchor="{anchor}">'
            f"{escape(fmt_date(points[i].date, False))}</text>"
        )
    out.append("</svg>")
    return mark_safe("".join(out))


@register.simple_tag
def income_expense_chart(series):
    """Grouped bars: income and spending per month."""
    width, height, left, top, bottom = 560, 200, 52, 12, 28
    peak = max([float(s["income"]) for s in series] + [float(s["expenses"]) for s in series] + [1.0])
    slot = (width - left - 8) / max(1, len(series))
    bar = min(26.0, slot / 3)

    def h(v):
        return (height - top - bottom) * float(v) / peak

    out = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Income and spending for the last {len(series)} months">'
    ]
    for tick in range(4):
        value = peak * tick / 3
        yy = height - bottom - h(value)
        out.append(
            f'<line class="grid" x1="{left}" x2="{width - 8}" y1="{yy:.1f}" y2="{yy:.1f}"/>'
            f'<text class="axis" x="{left - 8}" y="{yy + 4:.1f}" text-anchor="end">{_short(value)}</text>'
        )
    for i, s in enumerate(series):
        cx = left + slot * i + slot / 2
        for offset, key, klass in ((-bar - 1, "income", "bar-in"), (1, "expenses", "bar-out")):
            bh = h(s[key])
            out.append(
                f'<rect class="{klass}" x="{cx + offset:.1f}" y="{height - bottom - bh:.1f}" width="{bar:.1f}" '
                f'height="{bh:.1f}"><title>{escape(s["label"])} {key}: {escape(inr(s[key], 0))}</title></rect>'
            )
        out.append(f'<text class="axis" x="{cx:.1f}" y="{height - 8}" text-anchor="middle">{escape(s["label"])}</text>')
    out.append("</svg>")
    return mark_safe("".join(out))


@register.filter
def percent_of(value, total):
    try:
        return int(Decimal(value) * 100 / Decimal(total)) if Decimal(total) else 0
    except (TypeError, ValueError, ArithmeticError):
        return 0
