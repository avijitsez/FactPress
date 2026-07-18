"""Numeral and label formatting for the renderer.

Per the FactPress design philosophy (see FACTPRESS_DESIGN.md and
``docs/rendering-contract.md``): the LLM never writes a number into
rendered copy — it only references facts by key. This module is the single
place where every numeral, currency symbol, sign, and timestamp gets its
final on-image string form. All functions here are pure and deterministic:
no I/O, no ``locale`` stdlib module (OS-dependent = nondeterministic output),
no wall-clock reads.
"""

from __future__ import annotations

from datetime import datetime

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

_CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "INR": "₹",
    "JPY": "¥",
    "SGD": "S$",
}

_KNOWN_LABELS = {
    "daily_pnl_pct": "Daily P&L",
    "daily_pnl_abs": "P&L",
    "win_rate_pct": "Win rate",
    "trades_count": "Trades",
    "equity": "Equity",
    "fill_price": "Fill price",
}

_ARROWS = {"up": "▲", "down": "▼", "flat": "▶"}


def format_percent(value: float, *, signed: bool = True, precision: int = 2) -> str:
    """Render a fraction/percent-ish float as a percent string.

    Zero (or anything that rounds to zero at ``precision``) always renders
    without a sign, even when ``signed`` is True — e.g. "0.00%", never
    "+0.00%" or "-0.00%".
    """
    magnitude = f"{abs(value):.{precision}f}"
    if float(magnitude) == 0.0:
        return f"{magnitude}%"
    if not signed:
        return f"{magnitude}%"
    sign = "+" if value > 0 else "-"
    return f"{sign}{magnitude}%"


def format_currency(
    value: float,
    *,
    currency: str = "USD",
    signed: bool = False,
    precision: int | None = None,
) -> str:
    """Render a currency amount with thousands separators.

    Known currency codes map to a symbol prefix (USD -> "$", EUR -> "€",
    GBP -> "£", INR -> "₹", JPY -> "¥", SGD -> "S$"); unknown
    codes prefix the raw code, e.g. "CHF 1,204".

    Precision is adaptive when not given explicitly: 0dp when
    ``abs(value) >= 1000``, else 2dp. Negative values always show a leading
    "-"; positive values only get a leading "+" when ``signed`` is True.
    """
    if precision is None:
        precision = 0 if abs(value) >= 1000 else 2
    formatted = f"{abs(value):,.{precision}f}"
    symbol = _CURRENCY_SYMBOLS.get(currency)
    body = f"{symbol}{formatted}" if symbol is not None else f"{currency} {formatted}"
    if value < 0:
        return f"-{body}"
    if signed and value > 0:
        return f"+{body}"
    return body


def format_number(value: float | int, *, precision: int | None = None) -> str:
    """Render a plain number with thousands separators.

    Ints always get thousands separators with no decimal point. Floats use
    adaptive precision when ``precision`` is None: 2dp under 1000, 0dp at or
    above 1000 — with insignificant trailing zeros stripped (e.g. 5.50 ->
    "5.5", 5.00 -> "5"). Passing an explicit ``precision`` disables stripping.
    """
    if isinstance(value, bool):
        raise ValueError("bool is not a valid number for format_number")
    if isinstance(value, int):
        return f"{value:,}"
    if precision is not None:
        return f"{value:,.{precision}f}"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    formatted = f"{value:,.2f}"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted if formatted not in ("", "-") else "0"


def humanize_key(key: str) -> str:
    """Map a fact key to a human display label.

    Known keys (daily_pnl_pct, daily_pnl_abs, win_rate_pct, trades_count,
    equity, fill_price) use hardcoded copy. Unknown keys fall back to a
    generic transform: strip a trailing "_pct"/"_abs" suffix, split on
    underscores, and capitalize only the first word, e.g.
    "max_drawdown_pct" -> "Max drawdown".
    """
    if key in _KNOWN_LABELS:
        return _KNOWN_LABELS[key]
    stripped = key
    for suffix in ("_pct", "_abs"):
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)]
            break
    words = [w for w in stripped.split("_") if w]
    if not words:
        return stripped
    words[0] = words[0].capitalize()
    return " ".join(words)


def format_metric(key: str, value: float | int, *, currency: str = "USD") -> tuple[str, str]:
    """Resolve a fact key/value pair to a (label, formatted_value) pair.

    Heuristics (see ``docs/rendering-contract.md``):
    - ``*_pct`` -> signed percent, 2dp.
    - ``*_abs`` -> signed currency.
    - ``equity`` or any key mentioning "price" -> unsigned currency.
    - ``*_count`` -> integer with thousands separators.
    - anything else -> ``format_number``.

    Raises ``ValueError`` for bool values — a boolean flag is never a
    renderable metric.
    """
    if isinstance(value, bool):
        raise ValueError(f"bool value not allowed for metric {key!r}")
    label = humanize_key(key)
    if key.endswith("_pct"):
        return label, format_percent(value, signed=True)
    if key.endswith("_abs"):
        return label, format_currency(value, currency=currency, signed=True)
    if key == "equity" or "price" in key:
        return label, format_currency(value, currency=currency, signed=False)
    if key.endswith("_count"):
        return label, f"{int(value):,}"
    return label, format_number(value)


def direction(value: float) -> str:
    """Classify a numeric delta as "up", "down", or "flat" (exactly 0.0)."""
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def delta_arrow(direction: str) -> str:
    """Map a direction string ("up"/"down"/"flat") to its arrow glyph."""
    try:
        return _ARROWS[direction]
    except KeyError:
        raise ValueError(f"unknown direction: {direction!r}") from None


def format_timestamp(dt: datetime, *, tz_label: str | None = None) -> str:
    """Render a datetime as "18 Jul 2026, 15:04" (+ " UTC"-style suffix).

    Month abbreviations are hardcoded English (no ``locale`` module, no
    ``strftime("%b")``, both of which are OS/locale-dependent).
    """
    month = _MONTHS[dt.month - 1]
    base = f"{dt.day:02d} {month} {dt.year:04d}, {dt.hour:02d}:{dt.minute:02d}"
    if tz_label:
        return f"{base} {tz_label}"
    return base
