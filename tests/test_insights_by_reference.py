"""F3: insights-by-reference enforcement for `reflection_recap`.

Mirrors the numbers-by-reference discipline in tests/test_numbers_by_reference.py:
reflection prose is host-authored and arrives in facts as a candidate list
(`facts.reflection_candidates`); the director may only select one by index
(`DesignSpec.reflection_index`), and the renderer trims the selection to its
slot cap. The LLM can never author insight text -- only reference it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factpress.renderer.engine_svg import _truncate_reflection, build_view, load_brandkit
from factpress.schemas import (
    DailyPnlFacts,
    DesignSpec,
    ReflectionRecapFacts,
    SpecFactsMismatch,
    Tone,
    validate_spec_for_facts,
)

REPO = Path(__file__).resolve().parent.parent
BRANDKIT_PATH = REPO / "brandkits" / "default.yaml"


@pytest.fixture(scope="module")
def brandkit():
    return load_brandkit(BRANDKIT_PATH)


def _reflection_facts(**overrides):
    base = dict(
        week_pnl_pct=2.4,
        trades_count=14,
        win_rate_pct=57.0,
        reflection_candidates=[
            "Stayed disciplined on stop-losses even during the midweek dip.",
            "Overtraded on Thursday chasing a breakout that never confirmed.",
        ],
    )
    base.update(overrides)
    return ReflectionRecapFacts(**base)


def _reflection_spec(**overrides):
    base = dict(
        template_id="reflection_recap",
        template_version="1.0.0",
        variant="default",
        tone=Tone.NEUTRAL,
        palette_id="default",
        hero_metric_key="week_pnl_pct",
        headline="Week in review",
        sparkline=False,
        reflection_index=0,
    )
    base.update(overrides)
    return DesignSpec(**base)


# ---------------------------------------------------------------------------
# validate_spec_for_facts: reflection_index cross-validation
# ---------------------------------------------------------------------------


def test_reflection_index_out_of_range_raises_mismatch():
    facts = _reflection_facts()
    spec = _reflection_spec(reflection_index=5)
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any("reflection_index" in v for v in exc_info.value.violations)


def test_reflection_index_negative_raises_mismatch():
    facts = _reflection_facts()
    spec = _reflection_spec(reflection_index=-1)
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any("reflection_index" in v for v in exc_info.value.violations)


def test_reflection_recap_facts_with_index_none_is_a_violation():
    facts = _reflection_facts()
    spec = _reflection_spec(reflection_index=None)
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any(
        "reflection_recap requires the director to select" in v
        for v in exc_info.value.violations
    )


def test_reflection_index_valid_in_range_passes():
    facts = _reflection_facts()
    spec = _reflection_spec(reflection_index=1)
    validate_spec_for_facts(spec, facts)  # must not raise


def test_non_reflection_facts_with_index_set_is_a_violation():
    facts = DailyPnlFacts(daily_pnl_pct=1.0, equity=100.0, series=[1.0, 2.0])
    spec = DesignSpec(
        template_id="daily_pnl",
        template_version="1.0.0",
        tone=Tone.NEUTRAL,
        palette_id="default",
        hero_metric_key="daily_pnl_pct",
        headline="Steady day",
        reflection_index=0,
    )
    with pytest.raises(SpecFactsMismatch) as exc_info:
        validate_spec_for_facts(spec, facts)
    assert any("reflection_index" in v for v in exc_info.value.violations)


def test_non_reflection_facts_with_index_none_passes():
    facts = DailyPnlFacts(daily_pnl_pct=1.0, equity=100.0, series=[1.0, 2.0])
    spec = DesignSpec(
        template_id="daily_pnl",
        template_version="1.0.0",
        tone=Tone.NEUTRAL,
        palette_id="default",
        hero_metric_key="daily_pnl_pct",
        headline="Steady day",
    )
    validate_spec_for_facts(spec, facts)  # must not raise


# ---------------------------------------------------------------------------
# build_view: reflection selection + renderer-side truncation
# ---------------------------------------------------------------------------


def test_build_view_reflection_is_none_when_index_none(brandkit):
    facts = _reflection_facts()
    spec = _reflection_spec(reflection_index=None)
    view = build_view(facts, spec, brandkit)
    assert view["reflection"] is None


def test_build_view_selects_candidate_by_index(brandkit):
    facts = _reflection_facts()
    spec = _reflection_spec(reflection_index=1)
    view = build_view(facts, spec, brandkit)
    assert view["reflection"] == facts.reflection_candidates[1]


def test_build_view_short_candidate_passes_through_untrimmed(brandkit):
    short = "Kept risk small and let winners run this week."
    facts = _reflection_facts(reflection_candidates=[short])
    spec = _reflection_spec(reflection_index=0)
    view = build_view(facts, spec, brandkit)
    assert view["reflection"] == short
    assert not view["reflection"].endswith("…")


def test_build_view_long_candidate_is_truncated_at_word_boundary(brandkit):
    long_candidate = ("Reviewed every trade this week and noticed a pattern " * 8).strip()
    assert len(long_candidate) > 220
    facts = _reflection_facts(reflection_candidates=[long_candidate])
    spec = _reflection_spec(reflection_index=0)
    view = build_view(facts, spec, brandkit)
    assert len(view["reflection"]) <= 221  # 220 cap + ellipsis char
    assert view["reflection"].endswith("…")
    assert not view["reflection"][:-1].endswith(" ")


# ---------------------------------------------------------------------------
# _truncate_reflection unit tests (deterministic word-boundary trim)
# ---------------------------------------------------------------------------


def test_truncate_reflection_under_cap_is_unchanged():
    text = "Short reflection."
    assert _truncate_reflection(text) == text


def test_truncate_reflection_exactly_at_cap_is_unchanged():
    text = "x" * 220
    assert _truncate_reflection(text) == text


def test_truncate_reflection_over_cap_trims_on_word_boundary():
    text = "word " * 60  # 300 chars, well over the 220 cap
    result = _truncate_reflection(text)
    assert len(result) <= 221
    assert result.endswith("…")
    assert "  " not in result
