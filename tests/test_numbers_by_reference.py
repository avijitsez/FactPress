"""F1.4 adversarial suite: numbers-by-reference enforcement.

Proves the phase-gate claim in FACTPRESS_DESIGN.md §1 -- "a hallucinated
number is structurally impossible" -- by scripting hostile LLM replies
through ``httpx.MockTransport`` (the ScriptedLLM pattern from
``tests/test_director_fallback.py``) and asserting that every attack either
gets corrected or collapses to the exact, byte-identical fallback render.
The only nondeterministic input in the whole pipeline is the LLM reply; no
other injection surface exists.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from factpress import pipeline
from factpress.catalog import catalog_entry
from factpress.director import Director, DirectorConfig, fallback_spec
from factpress.renderer.engine_svg import build_view, load_brandkit, render_svg
from factpress.schemas import DailyPnlFacts, DesignSpec, Tone, validate_spec_for_facts

REPO = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO / "templates" / "daily_pnl"
BRANDKIT_PATH = REPO / "brandkits" / "default.yaml"


@pytest.fixture(scope="module")
def entry():
    return catalog_entry(TEMPLATE_DIR)


@pytest.fixture(scope="module")
def brandkit():
    return load_brandkit(BRANDKIT_PATH)


def make_facts(**overrides):
    """Facts with distinctive values so accidental digit-substring matches
    in the provenance test are implausible."""
    base = dict(
        daily_pnl_pct=3.79,
        daily_pnl_abs=1234.56,
        currency="USD",
        equity=246813.0,
        series=[100.0, 102.0, 101.5, 105.0, 104.2],
        win_rate_pct=57.5,
        trades_count=23,
        as_of=datetime(2026, 7, 18, 15, 4, tzinfo=UTC),
    )
    base.update(overrides)
    return DailyPnlFacts(**base)


def valid_spec_dict(**overrides):
    base = dict(
        template_id="daily_pnl",
        template_version="1.0.0",
        variant="default",
        tone="celebratory",
        palette_id="midnight",
        hero_metric_key="daily_pnl_pct",
        emphasis_keys=["win_rate_pct"],
        callout_keys=["equity"],
        headline="A clean sweep of a day",
        subhead="Momentum carried every book",
        caption="Green across the board today",
        emoji="\U0001f525",
        sparkline=True,
    )
    base.update(overrides)
    return base


class ScriptedLLM:
    """MockTransport handler replaying scripted replies and capturing requests.

    Copied from tests/test_director_fallback.py's pattern (not imported --
    that file must not be modified).
    """

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})


def make_director(handler):
    config = DirectorConfig(base_url="http://llm.test/v1", model="test-model")
    return Director(config, transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# 1. HERO ATTACK
# ---------------------------------------------------------------------------


def test_hero_attack_headline_digit_falls_back_and_999_never_renders(entry, brandkit):
    facts = make_facts()
    hostile_reply = json.dumps(valid_spec_dict(headline="Portfolio exploded 999% today"))
    llm = ScriptedLLM([hostile_reply, hostile_reply])
    director = make_director(llm)

    hostile_png = pipeline.render(facts, "daily_pnl", director=director)
    fallback_png = pipeline.render(facts, "daily_pnl")

    assert len(llm.requests) == 2
    assert hostile_png == fallback_png  # byte-for-byte identical to pure fallback

    fb_spec = fallback_spec(facts, manifest=entry, brandkit=brandkit)
    svg = render_svg(facts, fb_spec, template_dir=TEMPLATE_DIR, brandkit=brandkit)
    # Scoped to text content, not attributes/path data: SVG coordinate floats
    # (e.g. "919.5659999999999") can incidentally contain "999" as rounding
    # noise, which is not the attack rendering as visible copy.
    visible_text = "".join(re.findall(r">([^<]*)<", svg))
    assert "999" not in visible_text


# ---------------------------------------------------------------------------
# 2. CAPTION / SUBHEAD / EMOJI SMUGGLING
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,hostile_value",
    [
        ("subhead", "up 42 points"),
        ("caption", "call me at 555-0199"),
        ("emoji", "\U0001f4af2x"),  # 💯2x
    ],
    ids=["subhead", "caption", "emoji"],
)
def test_copy_field_digit_smuggling_falls_back(entry, brandkit, field, hostile_value):
    facts = make_facts()
    bad = json.dumps(valid_spec_dict(**{field: hostile_value}))
    llm = ScriptedLLM([bad, bad])

    spec = make_director(llm).design(facts, catalog_entry=entry, brandkit=brandkit)

    assert len(llm.requests) == 2
    assert spec == fallback_spec(facts, manifest=entry, brandkit=brandkit)


# ---------------------------------------------------------------------------
# 3. UNKNOWN-KEY FABRICATION
# ---------------------------------------------------------------------------


def test_unknown_hero_metric_key_fabrication_falls_back(entry, brandkit):
    facts = make_facts()
    bad = json.dumps(valid_spec_dict(hero_metric_key="fantasy_gain_pct"))
    llm = ScriptedLLM([bad, bad])

    spec = make_director(llm).design(facts, catalog_entry=entry, brandkit=brandkit)

    assert len(llm.requests) == 2
    assert spec == fallback_spec(facts, manifest=entry, brandkit=brandkit)

    # Full pipeline: rendered output is identical to the zero-LLM render.
    director = make_director(ScriptedLLM([bad, bad]))
    hostile_png = pipeline.render(facts, "daily_pnl", director=director)
    fallback_png = pipeline.render(facts, "daily_pnl")
    assert hostile_png == fallback_png


# ---------------------------------------------------------------------------
# 4. EXTRA-FIELD INJECTION
# ---------------------------------------------------------------------------


def test_extra_field_injection_falls_back(entry, brandkit):
    facts = make_facts()
    payload = valid_spec_dict()
    payload["hero_value_override"] = "999%"
    payload["raw_svg"] = "<text>999%</text>"
    bad = json.dumps(payload)
    llm = ScriptedLLM([bad, bad])

    spec = make_director(llm).design(facts, catalog_entry=entry, brandkit=brandkit)

    assert len(llm.requests) == 2
    assert spec == fallback_spec(facts, manifest=entry, brandkit=brandkit)


# ---------------------------------------------------------------------------
# 5. SPARKLINE LIE
# ---------------------------------------------------------------------------


def test_sparkline_lie_falls_back_with_no_sparkline_group(entry, brandkit):
    facts = make_facts(series=None)
    bad = json.dumps(valid_spec_dict(sparkline=True))
    llm = ScriptedLLM([bad, bad])

    spec = make_director(llm).design(facts, catalog_entry=entry, brandkit=brandkit)

    assert len(llm.requests) == 2
    fb_spec = fallback_spec(facts, manifest=entry, brandkit=brandkit)
    assert spec == fb_spec
    assert fb_spec.sparkline is False

    svg = render_svg(facts, fb_spec, template_dir=TEMPLATE_DIR, brandkit=brandkit)
    # The sparkline block is the only place the template emits a
    # translated <g> wrapping path elements; its absence proves no
    # sparkline was drawn for facts that cannot support one.
    assert '<g transform="translate(' not in svg


# ---------------------------------------------------------------------------
# 6. NUMERAL PROVENANCE (property test)
# ---------------------------------------------------------------------------


def test_every_visible_numeral_traces_to_a_renderer_formatted_fact(brandkit):
    facts = make_facts()
    spec = DesignSpec(
        template_id="daily_pnl",
        template_version="1.0.0",
        variant="default",
        tone=Tone.CELEBRATORY,
        palette_id="midnight",
        hero_metric_key="daily_pnl_pct",
        emphasis_keys=["win_rate_pct", "trades_count"],
        callout_keys=["equity"],
        headline="Numbers speak for themselves",
        subhead="Every metric traces back to source",
        caption=None,
        emoji=None,
        sparkline=True,
    )

    svg = render_svg(facts, spec, template_dir=TEMPLATE_DIR, brandkit=brandkit)
    view = build_view(facts, spec, brandkit)

    traceable = [view["hero"]["value"]]
    traceable += [chip["value"] for chip in view["delta_chips"]]
    traceable += [callout["value"] for callout in view["callouts"]]
    if view["as_of"]:
        traceable.append(view["as_of"])

    text_segments = re.findall(r">[^<]*<", svg)
    digit_runs: set[str] = set()
    for segment in text_segments:
        digit_runs.update(re.findall(r"\d+", segment))

    assert digit_runs, "expected at least one numeral in the rendered text content"
    for run in digit_runs:
        assert any(run in value for value in traceable), (
            f"digit run {run!r} does not trace back to any renderer-formatted "
            f"facts value {traceable!r}"
        )


# ---------------------------------------------------------------------------
# 7. FALLBACK PURITY
# ---------------------------------------------------------------------------


def test_fallback_spec_is_provably_valid_for_red_day_facts(entry, brandkit):
    red_facts = make_facts(daily_pnl_pct=-3.79, series=None)
    fb_spec = fallback_spec(red_facts, manifest=entry, brandkit=brandkit)

    validate_spec_for_facts(fb_spec, red_facts)  # must not raise
    assert fb_spec.tone == Tone.NEUTRAL

    round_tripped = DesignSpec.model_validate(fb_spec.model_dump(mode="json"))
    assert round_tripped == fb_spec
