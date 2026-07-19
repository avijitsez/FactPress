"""Unit tests for the F5 interactive-card state layer (StateInfo/CardState).

Covers manifest-declared-states enforcement, the `state=None` no-op path,
the aware-datetime -> UTC conversion for `stamped_at`, the "state is never
part of DesignSpec" contract, and that every one of the six deterministic
states renders visibly distinguishing text in `trade_proposal`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factpress.renderer import format as fmt
from factpress.renderer.engine_svg import load_brandkit, render_svg
from factpress.schemas import (
    CardState,
    DailyPnlFacts,
    DesignSpec,
    StateInfo,
    Tone,
    TradeProposalFacts,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TRADE_PROPOSAL_DIR = _REPO_ROOT / "templates" / "trade_proposal"
_DAILY_PNL_DIR = _REPO_ROOT / "templates" / "daily_pnl"
_BRANDKIT_PATH = _REPO_ROOT / "brandkits" / "default.yaml"

_STATE_SUBSTRINGS = {
    CardState.PENDING: "AWAITING",
    CardState.APPROVED: "APPROVED",
    CardState.REJECTED: "REJECTED",
    CardState.EXECUTED: "EXECUTED",
    CardState.FAILED: "FAILED",
    CardState.EXPIRED: "EXPIRED",
}


@pytest.fixture
def brandkit():
    return load_brandkit(_BRANDKIT_PATH)


def make_facts(**overrides):
    base = dict(
        symbol="NVDA",
        side="buy",
        qty=120,
        limit_price=128.40,
        confidence_pct=72.5,
        plan_target_pct=6.5,
        plan_stop_pct=-2.5,
        risk_note="Risk team: within limits, size capped",
    )
    base.update(overrides)
    return TradeProposalFacts(**base)


def make_spec(**overrides):
    base = dict(
        template_id="trade_proposal",
        template_version="1.0.0",
        variant="default",
        tone=Tone.NEUTRAL,
        palette_id="midnight",
        hero_metric_key="limit_price",
        emphasis_keys=["qty", "confidence_pct"],
        callout_keys=["plan_target_pct", "plan_stop_pct"],
        headline="NVDA",
        sparkline=False,
    )
    base.update(overrides)
    return DesignSpec(**base)


def make_daily_pnl_facts(**overrides):
    base = dict(daily_pnl_pct=1.0)
    base.update(overrides)
    return DailyPnlFacts(**base)


def make_daily_pnl_spec(**overrides):
    base = dict(
        template_id="daily_pnl",
        template_version="1.0.0",
        tone=Tone.NEUTRAL,
        palette_id="midnight",
        hero_metric_key="daily_pnl_pct",
        headline="Green day",
    )
    base.update(overrides)
    return DesignSpec(**base)


# ---------------------------------------------------------------------------
# manifest enforcement
# ---------------------------------------------------------------------------


def test_state_not_in_manifest_states_raises_valueerror(brandkit):
    """daily_pnl's manifest declares only `states: [static]` -- passing a
    `pending` StateInfo against it must raise, proving the engine actually
    validates the state name against the template's manifest rather than
    silently accepting any CardState value."""
    facts = make_daily_pnl_facts()
    spec = make_daily_pnl_spec()
    state = StateInfo(state=CardState.PENDING)
    with pytest.raises(ValueError, match="pending"):
        render_svg(facts, spec, template_dir=_DAILY_PNL_DIR, brandkit=brandkit, state=state)


# ---------------------------------------------------------------------------
# state=None no-op
# ---------------------------------------------------------------------------


def test_state_context_none_when_kwarg_absent(brandkit):
    facts = make_facts()
    spec = make_spec()
    svg = render_svg(facts, spec, template_dir=_TRADE_PROPOSAL_DIR, brandkit=brandkit)
    assert "AWAITING" not in svg


# ---------------------------------------------------------------------------
# aware non-UTC stamped_at -> UTC string
# ---------------------------------------------------------------------------


def test_aware_non_utc_stamped_at_converted_to_utc_string(brandkit):
    ist = timezone(timedelta(hours=5, minutes=30))
    stamped = datetime(2026, 7, 19, 14, 45, tzinfo=ist)
    expected = fmt.format_timestamp(stamped.astimezone(timezone.utc), tz_label="UTC")

    facts = make_facts()
    spec = make_spec()
    state = StateInfo(state=CardState.APPROVED, decider="Avijit", stamped_at=stamped)
    svg = render_svg(
        facts, spec, template_dir=_TRADE_PROPOSAL_DIR, brandkit=brandkit, state=state
    )
    assert expected in svg
    assert "UTC" in expected


# ---------------------------------------------------------------------------
# state is never part of DesignSpec
# ---------------------------------------------------------------------------


def test_state_not_in_design_spec_fields():
    assert "state" not in DesignSpec.model_fields


# ---------------------------------------------------------------------------
# every one of the six states renders visibly distinguishing text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("card_state", list(CardState))
def test_each_state_renders_distinguishing_text(card_state, brandkit):
    facts = make_facts()
    spec = make_spec()
    state = StateInfo(
        state=card_state,
        decider="Avijit",
        stamped_at=datetime(2026, 7, 19, 9, 15, tzinfo=timezone.utc),
        note="filled at open",
    )
    svg = render_svg(
        facts, spec, template_dir=_TRADE_PROPOSAL_DIR, brandkit=brandkit, state=state
    )
    assert _STATE_SUBSTRINGS[card_state] in svg


@pytest.mark.parametrize(
    ("side", "expect_badge", "absent"),
    [("buy", "BUY", ">SELL<"), ("sell", "SELL", ">BUY<")],
)
def test_side_badge_renders_facts_side_verbatim(side, expect_badge, absent):
    facts = make_facts(side=side)
    svg = render_svg(
        facts,
        make_spec(),
        template_dir=_TRADE_PROPOSAL_DIR,
        brandkit=load_brandkit(_BRANDKIT_PATH),
    )
    assert f">{expect_badge}<" in svg
    assert absent not in svg
