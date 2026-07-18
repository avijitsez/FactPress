"""F1.2 director tests: strict validation, one retry, deterministic fallback.

Every LLM interaction goes through ``httpx.MockTransport`` — no network, no
monkeypatching of internals. The invariant under test: ``Director.design``
NEVER raises and NEVER returns an invalid spec; its worst case is exactly
``fallback_spec``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from factpress.catalog import catalog_entry
from factpress.director import Director, DirectorConfig, fallback_spec
from factpress.renderer.engine_svg import load_brandkit
from factpress.schemas import DailyPnlFacts, Tone, validate_spec_for_facts

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
    base = dict(
        daily_pnl_pct=2.41,
        daily_pnl_abs=1234.56,
        currency="USD",
        equity=98765.43,
        series=[100.0, 102.0, 101.5, 105.0],
        win_rate_pct=63.5,
        trades_count=42,
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
    """MockTransport handler replaying scripted replies and capturing requests."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(
            200, json={"choices": [{"message": {"content": reply}}]}
        )


def make_director(handler, api_key=None):
    config = DirectorConfig(
        base_url="http://llm.test/v1", model="test-model", api_key=api_key
    )
    return Director(config, transport=httpx.MockTransport(handler))


def test_valid_spec_first_call_is_returned_with_one_http_call(entry, brandkit):
    llm = ScriptedLLM([json.dumps(valid_spec_dict())])
    spec = make_director(llm).design(make_facts(), catalog_entry=entry, brandkit=brandkit)
    assert len(llm.requests) == 1
    assert spec.headline == "A clean sweep of a day"
    assert spec.tone == Tone.CELEBRATORY
    validate_spec_for_facts(spec, make_facts())  # must not raise


def test_invalid_then_valid_retries_once_with_rejection_text(entry, brandkit):
    bad = json.dumps(valid_spec_dict(headline="Up 12% today"))  # digits: rejected
    good = json.dumps(valid_spec_dict())
    llm = ScriptedLLM([bad, good])
    spec = make_director(llm).design(make_facts(), catalog_entry=entry, brandkit=brandkit)
    assert len(llm.requests) == 2
    assert spec.headline == "A clean sweep of a day"
    retry_body = json.loads(llm.requests[1].content.decode())
    retry_text = json.dumps(retry_body["messages"])
    assert "rejected" in retry_text
    assert "digits are forbidden" in retry_text


def test_invalid_twice_returns_fallback(entry, brandkit):
    bad = json.dumps(valid_spec_dict(palette_id="not_a_palette"))
    llm = ScriptedLLM([bad, bad])
    facts = make_facts()
    spec = make_director(llm).design(facts, catalog_entry=entry, brandkit=brandkit)
    assert len(llm.requests) == 2
    assert spec == fallback_spec(facts, manifest=entry, brandkit=brandkit)
    assert spec.tone == Tone.NEUTRAL


def test_transport_timeout_returns_fallback_without_raising(entry, brandkit):
    llm = ScriptedLLM([httpx.ConnectTimeout("connect timed out")])
    facts = make_facts()
    spec = make_director(llm).design(facts, catalog_entry=entry, brandkit=brandkit)
    assert spec == fallback_spec(facts, manifest=entry, brandkit=brandkit)


def test_non_json_reply_retries_then_falls_back(entry, brandkit):
    llm = ScriptedLLM(["I think the midnight palette would be lovely.", "still not json"])
    facts = make_facts()
    spec = make_director(llm).design(facts, catalog_entry=entry, brandkit=brandkit)
    assert len(llm.requests) == 2
    assert spec == fallback_spec(facts, manifest=entry, brandkit=brandkit)


def test_celebratory_on_red_day_is_rejected_to_fallback(entry, brandkit):
    red_facts = make_facts(daily_pnl_pct=-3.2)
    celebratory = json.dumps(valid_spec_dict())  # celebratory tone, hero now negative
    llm = ScriptedLLM([celebratory, celebratory])
    spec = make_director(llm).design(red_facts, catalog_entry=entry, brandkit=brandkit)
    assert len(llm.requests) == 2
    assert spec.tone == Tone.NEUTRAL
    assert spec == fallback_spec(red_facts, manifest=entry, brandkit=brandkit)
    retry_text = json.loads(llm.requests[1].content.decode())["messages"][-1]["content"]
    assert "celebratory" in retry_text


def test_code_fenced_json_reply_is_accepted(entry, brandkit):
    fenced = "```json\n" + json.dumps(valid_spec_dict()) + "\n```"
    llm = ScriptedLLM([fenced])
    spec = make_director(llm).design(make_facts(), catalog_entry=entry, brandkit=brandkit)
    assert len(llm.requests) == 1
    assert spec.palette_id == "midnight"


def test_api_key_controls_authorization_header(entry, brandkit):
    reply = json.dumps(valid_spec_dict())

    with_key = ScriptedLLM([reply])
    make_director(with_key, api_key="sk-test").design(
        make_facts(), catalog_entry=entry, brandkit=brandkit
    )
    assert with_key.requests[0].headers["Authorization"] == "Bearer sk-test"

    without_key = ScriptedLLM([reply])
    make_director(without_key).design(make_facts(), catalog_entry=entry, brandkit=brandkit)
    assert "authorization" not in without_key.requests[0].headers


def test_fallback_spec_never_promises_impossible_sparkline(entry, brandkit):
    no_series = make_facts(series=None)
    spec = fallback_spec(no_series, manifest=entry, brandkit=brandkit)
    assert spec.sparkline is False
    validate_spec_for_facts(spec, no_series)  # valid by construction

    with_series = make_facts()
    assert fallback_spec(with_series, manifest=entry, brandkit=brandkit).sparkline is True


def test_fallback_spec_is_valid_for_non_daily_pnl_templates(brandkit):
    from factpress.director import _catalog_violations
    from factpress.schemas import TradeExecutedFacts

    trade_entry = catalog_entry(REPO / "templates" / "trade_executed")
    facts = TradeExecutedFacts(
        symbol="AAPL", side="buy", qty=150.0, fill_price=189.42,
        plan_target_pct=8.5, plan_stop_pct=-3.0,
    )
    spec = fallback_spec(facts, manifest=trade_entry, brandkit=brandkit)
    assert spec.variant in trade_entry["variants"]
    assert spec.headline != "Daily P&L update"
    assert _catalog_violations(spec, trade_entry) == []
    validate_spec_for_facts(spec, facts)  # must not raise


def test_fallback_spec_daily_pnl_headline_unchanged(entry, brandkit):
    spec = fallback_spec(make_facts(), manifest=entry, brandkit=brandkit)
    assert spec.headline == "Daily P&L update"
    assert spec.variant == "default"
