from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factpress.renderer.engine_svg import (
    build_view,
    load_brandkit,
    load_manifest,
    render_png,
    render_svg,
)
from factpress.schemas import DailyPnlFacts, DesignSpec, SessionDigestFacts, Tone

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "daily_pnl"
BRANDKIT_PATH = Path(__file__).resolve().parent.parent / "brandkits" / "default.yaml"

_EXPECTED_DIMS = {"feed": (1080, 1350), "telegram": (1280, 720)}


@pytest.fixture
def brandkit():
    return load_brandkit(BRANDKIT_PATH)


def make_facts(**overrides):
    base = dict(
        daily_pnl_pct=2.41,
        daily_pnl_abs=1234.56,
        currency="USD",
        equity=98765.43,
        series=[100.0, 102.0, 101.5, 105.0, 104.2],
        win_rate_pct=63.5,
        trades_count=42,
        as_of=datetime(2026, 7, 18, 15, 4, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return DailyPnlFacts(**base)


def make_spec(**overrides):
    base = dict(
        template_id="daily_pnl",
        template_version="1.0.0",
        tone=Tone.CELEBRATORY,
        palette_id="midnight",
        hero_metric_key="daily_pnl_pct",
        emphasis_keys=["win_rate_pct"],
        callout_keys=["trades_count"],
        headline="Green day across the board",
        subhead="Momentum building",
    )
    base.update(overrides)
    return DesignSpec(**base)


def _png_dims(png: bytes) -> tuple[int, int]:
    # PNG IHDR chunk: bytes 16-19 width, 20-23 height (big-endian), after
    # the 8-byte signature + 4-byte length + 4-byte "IHDR" tag.
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


# ---------------------------------------------------------------------------
# manifest / brandkit loaders
# ---------------------------------------------------------------------------


def test_load_manifest_returns_expected_shape():
    manifest = load_manifest(TEMPLATE_DIR)
    assert manifest["id"] == "daily_pnl"
    assert manifest["sizes"]["feed"] == [1080, 1350]
    assert manifest["sizes"]["telegram"] == [1280, 720]
    assert "midnight" in manifest["palettes_allowed"]


def test_load_brandkit_returns_expected_shape(brandkit):
    assert brandkit["footer"]
    assert "midnight" in brandkit["palettes"]
    assert brandkit["fonts"]["sans"] == "Inter"


# ---------------------------------------------------------------------------
# determinism: byte-identical PNG on rerun, at both sizes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", ["feed", "telegram"])
def test_render_png_is_byte_identical_on_rerun(size, brandkit):
    facts = make_facts()
    spec = make_spec()
    png1 = render_png(facts, spec, template_dir=TEMPLATE_DIR, brandkit=brandkit, size=size)
    png2 = render_png(facts, spec, template_dir=TEMPLATE_DIR, brandkit=brandkit, size=size)
    assert png1 == png2
    assert png1.startswith(b"\x89PNG\r\n\x1a\n")
    assert _png_dims(png1) == _EXPECTED_DIMS[size]


def test_render_png_accepts_brandkit_path_and_dict_facts():
    facts_dict = dict(
        event_type="daily_pnl",
        daily_pnl_pct=1.5,
        daily_pnl_abs=100.0,
        currency="USD",
        series=[1.0, 2.0, 3.0],
    )
    spec = make_spec(emphasis_keys=[], callout_keys=[])
    png = render_png(facts_dict, spec, template_dir=TEMPLATE_DIR, brandkit=BRANDKIT_PATH)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert _png_dims(png) == _EXPECTED_DIMS["feed"]


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


def test_unknown_hero_metric_key_raises_keyerror(brandkit):
    facts = make_facts()
    spec = make_spec(hero_metric_key="does_not_exist")
    with pytest.raises(KeyError) as exc:
        build_view(facts, spec, brandkit)
    message = str(exc.value)
    assert "does_not_exist" in message
    assert "daily_pnl_pct" in message


def test_unknown_emphasis_key_raises_keyerror(brandkit):
    facts = make_facts()
    spec = make_spec(emphasis_keys=["nonexistent_metric"])
    with pytest.raises(KeyError, match="nonexistent_metric"):
        build_view(facts, spec, brandkit)


def test_disallowed_palette_raises_valueerror(brandkit):
    facts = make_facts()
    spec = make_spec(palette_id="neon")
    with pytest.raises(ValueError, match="palette_id"):
        render_svg(facts, spec, template_dir=TEMPLATE_DIR, brandkit=brandkit)


def test_template_id_mismatch_raises_valueerror(brandkit):
    facts = make_facts()
    spec = make_spec(template_id="weekly_pnl")
    with pytest.raises(ValueError, match="template_id"):
        render_svg(facts, spec, template_dir=TEMPLATE_DIR, brandkit=brandkit)


def test_major_version_mismatch_raises_valueerror(brandkit):
    facts = make_facts()
    spec = make_spec(template_version="2.0.0")
    with pytest.raises(ValueError, match="version"):
        render_svg(facts, spec, template_dir=TEMPLATE_DIR, brandkit=brandkit)


def test_unknown_size_raises_valueerror(brandkit):
    facts = make_facts()
    spec = make_spec()
    with pytest.raises(ValueError, match="size"):
        render_svg(facts, spec, template_dir=TEMPLATE_DIR, brandkit=brandkit, size="banner")


# ---------------------------------------------------------------------------
# sparkline presence rules
# ---------------------------------------------------------------------------


def test_sparkline_none_when_series_absent(brandkit):
    facts = make_facts(series=None)
    spec = make_spec()
    view = build_view(facts, spec, brandkit)
    assert view["sparkline"] is None


def test_sparkline_none_when_series_too_short(brandkit):
    facts = make_facts(series=[1.0])
    spec = make_spec()
    view = build_view(facts, spec, brandkit)
    assert view["sparkline"] is None


def test_sparkline_none_when_spec_disables_it(brandkit):
    facts = make_facts()
    spec = make_spec(sparkline=False)
    view = build_view(facts, spec, brandkit)
    assert view["sparkline"] is None


def test_sparkline_present_when_enabled_and_series_long_enough(brandkit):
    facts = make_facts()
    spec = make_spec()
    view = build_view(facts, spec, brandkit)
    assert view["sparkline"] is not None
    assert view["sparkline"]["w"] == 420
    assert view["sparkline"]["h"] == 96
    assert view["sparkline"]["path"].startswith("M ")


# ---------------------------------------------------------------------------
# extra fact keys, emoji suppression, dict-facts entry point
# ---------------------------------------------------------------------------


def test_extra_metric_key_usable_as_hero_metric(brandkit):
    facts = DailyPnlFacts.model_validate(
        {
            "event_type": "daily_pnl",
            "daily_pnl_pct": 1.0,
            "max_drawdown_pct": -3.25,
        }
    )
    spec = make_spec(hero_metric_key="max_drawdown_pct", emphasis_keys=[], callout_keys=[])
    view = build_view(facts, spec, brandkit)
    assert view["hero"]["label"] == "Max drawdown"
    assert view["hero"]["direction"] == "down"
    assert view["hero"]["color_role"] == "negative"


def test_emoji_always_none_in_view_even_when_spec_sets_it(brandkit):
    facts = make_facts()
    spec = make_spec(emoji="\U0001f389")  # party popper, no digits
    view = build_view(facts, spec, brandkit)
    assert view["emoji"] is None


def test_dict_facts_entry_point_works_for_render_svg(brandkit):
    facts_dict = dict(
        event_type="daily_pnl",
        daily_pnl_pct=1.5,
        daily_pnl_abs=100.0,
        currency="USD",
        series=[1.0, 2.0, 3.0],
    )
    spec = make_spec(emphasis_keys=[], callout_keys=[])
    svg = render_svg(facts_dict, spec, template_dir=TEMPLATE_DIR, brandkit=brandkit)
    assert svg.startswith("<svg")
    assert "Green day across the board" in svg


def test_as_of_aware_non_utc_is_converted_to_utc(brandkit):
    ist = timezone(timedelta(hours=5, minutes=30))
    facts = make_facts(as_of=datetime(2026, 7, 18, 21, 30, tzinfo=ist))
    view = build_view(facts, make_spec(), brandkit)
    assert view["as_of"] == "18 Jul 2026, 16:00 UTC"


def test_as_of_naive_gets_no_utc_label(brandkit):
    facts = make_facts(as_of=datetime(2026, 7, 18, 21, 30))
    view = build_view(facts, make_spec(), brandkit)
    assert view["as_of"] == "18 Jul 2026, 21:30"
    assert "UTC" not in view["as_of"]


# ---------------------------------------------------------------------------
# whitelisted text passthrough (text_lists / text_fields)
# ---------------------------------------------------------------------------


def test_text_lists_and_fields_populated_for_session_digest_facts(brandkit):
    facts = SessionDigestFacts(
        session="open",
        watchlist_symbols=["AAPL", "TSLA", "NVDA"],
        plan_notes=["Wait for confirmation", "Trim size on gaps"],
        regime="risk-on",
        watchlist_count=3,
    )
    spec = make_spec(
        template_id="session_digest",
        hero_metric_key="watchlist_count",
        emphasis_keys=[],
        callout_keys=[],
    )
    view = build_view(facts, spec, brandkit)
    assert view["text_lists"] == {
        "watchlist_symbols": ["AAPL", "TSLA", "NVDA"],
        "plan_notes": ["Wait for confirmation", "Trim size on gaps"],
    }
    assert view["text_fields"] == {"regime": "risk-on", "session": "open"}


def test_text_lists_and_fields_are_empty_dicts_when_absent(brandkit):
    facts = make_facts()
    view = build_view(facts, make_spec(), brandkit)
    assert view["text_lists"] == {}
    assert view["text_fields"] == {}


def test_daily_pnl_view_otherwise_unchanged_by_text_passthrough(brandkit):
    facts = make_facts()
    spec = make_spec()
    view = build_view(facts, spec, brandkit)
    expected_keys = {
        "headline",
        "subhead",
        "emoji",
        "hero",
        "delta_chips",
        "callouts",
        "sparkline",
        "as_of",
        "footer",
        "reflection",
        "text_lists",
        "text_fields",
    }
    assert set(view.keys()) == expected_keys
    assert view["headline"] == spec.headline
    assert view["hero"]["label"] == "Daily P&L"
    assert view["delta_chips"][0]["label"] == "Win rate"
    assert view["callouts"][0]["label"] == "Trades"
