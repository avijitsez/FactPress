from datetime import datetime

import pytest

from factpress.renderer.format import (
    delta_arrow,
    direction,
    format_currency,
    format_metric,
    format_number,
    format_percent,
    format_timestamp,
    humanize_key,
)


# ---------------------------------------------------------------------------
# format_percent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,signed,precision,expected",
    [
        (2.41, True, 2, "+2.41%"),
        (-0.87, True, 2, "-0.87%"),
        (0.0, True, 2, "0.00%"),
        (0.0, False, 2, "0.00%"),
        (2.41, False, 2, "2.41%"),
        (-0.87, False, 2, "0.87%"),
        (0.004, True, 2, "0.00%"),  # rounds to zero -> no sign
        (-0.001, True, 2, "0.00%"),  # rounds to -0.00 -> displayed unsigned
        (1234567.89, True, 2, "+1234567.89%"),
        (100.0, True, 0, "+100%"),
    ],
)
def test_format_percent(value, signed, precision, expected):
    assert format_percent(value, signed=signed, precision=precision) == expected


# ---------------------------------------------------------------------------
# format_currency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,currency,signed,precision,expected",
    [
        (500.0, "USD", False, None, "$500.00"),
        (1204.0, "USD", False, None, "$1,204"),
        (500.0, "EUR", False, None, "€500.00"),
        (500.0, "GBP", False, None, "£500.00"),
        (500.0, "INR", False, None, "₹500.00"),
        (500.0, "JPY", False, None, "¥500.00"),
        (500.0, "SGD", False, None, "S$500.00"),
        (1204.0, "CHF", False, None, "CHF 1,204"),  # unknown code prefixed
        (-500.0, "USD", False, None, "-$500.00"),
        (2405.6, "USD", True, None, "+$2,406"),
        (-2405.6, "USD", True, None, "-$2,406"),
        (0.0, "USD", True, None, "$0.00"),  # signed zero, no sign
        (1234567.89, "USD", False, None, "$1,234,568"),  # huge magnitude
        (1.5, "USD", False, 0, "$2"),  # explicit precision overrides adaptive
    ],
)
def test_format_currency(value, currency, signed, precision, expected):
    assert format_currency(value, currency=currency, signed=signed, precision=precision) == expected


# ---------------------------------------------------------------------------
# format_number
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,precision,expected",
    [
        (1234, None, "1,234"),
        (7, None, "7"),
        (5.50, None, "5.5"),
        (5.00, None, "5"),
        (5.25, None, "5.25"),
        (0.004, None, "0"),  # tiny magnitude rounds/strips to 0
        (1234567.89, None, "1,234,568"),  # huge magnitude, adaptive 0dp
        (-3.5, None, "-3.5"),
        (3.14159, 3, "3.142"),
    ],
)
def test_format_number(value, precision, expected):
    assert format_number(value, precision=precision) == expected


def test_format_number_rejects_bool():
    with pytest.raises(ValueError):
        format_number(True)


# ---------------------------------------------------------------------------
# humanize_key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,expected",
    [
        ("daily_pnl_pct", "Daily P&L"),
        ("daily_pnl_abs", "P&L"),
        ("win_rate_pct", "Win rate"),
        ("trades_count", "Trades"),
        ("equity", "Equity"),
        ("fill_price", "Fill price"),
        ("max_drawdown_pct", "Max drawdown"),
        ("some_other_abs", "Some other"),
        ("open_positions", "Open positions"),
        ("single", "Single"),
    ],
)
def test_humanize_key(key, expected):
    assert humanize_key(key) == expected


# ---------------------------------------------------------------------------
# format_metric
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("daily_pnl_pct", 2.41, ("Daily P&L", "+2.41%")),
        ("daily_pnl_pct", -0.87, ("Daily P&L", "-0.87%")),
        ("daily_pnl_abs", 2405.6, ("P&L", "+$2,406")),
        ("daily_pnl_abs", -2405.6, ("P&L", "-$2,406")),
        ("equity", 15000.42, ("Equity", "$15,000")),
        ("fill_price", 42.5, ("Fill price", "$42.50")),
        ("trades_count", 12, ("Trades", "12")),
        ("open_orders_count", 7, ("Open orders count", "7")),
        ("win_rate_pct", 55.0, ("Win rate", "+55.00%")),
        ("max_drawdown_pct", -12.34, ("Max drawdown", "-12.34%")),
        ("misc_metric", 3.5, ("Misc metric", "3.5")),
    ],
)
def test_format_metric(key, value, expected):
    assert format_metric(key, value) == expected


def test_format_metric_rejects_bool():
    with pytest.raises(ValueError):
        format_metric("some_flag", True)


def test_format_metric_uses_currency_override():
    label, formatted = format_metric("daily_pnl_abs", 500.0, currency="EUR")
    assert label == "P&L"
    assert formatted == "+€500.00"


# ---------------------------------------------------------------------------
# direction / delta_arrow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (2.41, "up"),
        (-0.87, "down"),
        (0.0, "flat"),
        (-0.0, "flat"),
    ],
)
def test_direction(value, expected):
    assert direction(value) == expected


@pytest.mark.parametrize(
    "d,expected",
    [
        ("up", "▲"),
        ("down", "▼"),
        ("flat", "▶"),
    ],
)
def test_delta_arrow(d, expected):
    assert delta_arrow(d) == expected


def test_delta_arrow_rejects_unknown_direction():
    with pytest.raises(ValueError):
        delta_arrow("sideways")


# ---------------------------------------------------------------------------
# format_timestamp
# ---------------------------------------------------------------------------


def test_format_timestamp_no_tz():
    dt = datetime(2026, 7, 18, 15, 4)
    assert format_timestamp(dt) == "18 Jul 2026, 15:04"


def test_format_timestamp_with_tz_label():
    dt = datetime(2026, 7, 18, 15, 4)
    assert format_timestamp(dt, tz_label="UTC") == "18 Jul 2026, 15:04 UTC"


def test_format_timestamp_pads_single_digit_day_and_time():
    dt = datetime(2026, 1, 5, 9, 4)
    assert format_timestamp(dt) == "05 Jan 2026, 09:04"


@pytest.mark.parametrize(
    "month_index,expected_abbrev",
    list(enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        start=1,
    )),
)
def test_format_timestamp_all_month_abbreviations(month_index, expected_abbrev):
    dt = datetime(2026, month_index, 1, 0, 0)
    assert format_timestamp(dt).split(" ")[1] == expected_abbrev
