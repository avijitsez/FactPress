"""F1.5 pipeline tests: validation -> director/fallback -> renderer wiring."""

from __future__ import annotations

import json

import httpx
import pytest

from factpress.director import Director, DirectorConfig
from factpress.pipeline import render
from factpress.schemas import DailyPnlFacts

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def make_facts_dict(**overrides):
    base = dict(
        event_type="daily_pnl",
        daily_pnl_pct=1.87,
        currency="USD",
        equity=54321.0,
        win_rate_pct=61.0,
        trades_count=11,
        series=[1.0, 1.4, 1.2, 1.9],
    )
    base.update(overrides)
    return base


def scripted_director(*replies):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        reply = replies[min(calls["n"], len(replies) - 1)]
        calls["n"] += 1
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    config = DirectorConfig(base_url="http://llm.test/v1", model="m")
    return Director(config, transport=httpx.MockTransport(handler))


def valid_spec_json(**overrides):
    base = dict(
        template_id="daily_pnl",
        template_version="1.0.0",
        variant="default",
        tone="neutral",
        palette_id="aurora",
        hero_metric_key="daily_pnl_pct",
        emphasis_keys=["win_rate_pct"],
        callout_keys=[],
        headline="Steady climb through the session",
        subhead=None,
        caption=None,
        emoji=None,
        sparkline=True,
    )
    base.update(overrides)
    return json.dumps(base)


def test_zero_llm_path_renders_deterministic_png():
    a = render(make_facts_dict(), "daily_pnl")
    b = render(make_facts_dict(), "daily_pnl")
    assert a.startswith(PNG_MAGIC)
    assert a == b


def test_directed_path_renders_with_llm_chosen_palette():
    director = scripted_director(valid_spec_json())
    png = render(make_facts_dict(), "daily_pnl", director=director)
    assert png.startswith(PNG_MAGIC)
    # aurora palette differs from fallback's midnight -> different image
    assert png != render(make_facts_dict(), "daily_pnl")


def test_director_failure_never_blocks_render():
    director = scripted_director(httpx.ConnectTimeout("down"))
    png = render(make_facts_dict(), "daily_pnl", director=director)
    assert png.startswith(PNG_MAGIC)
    assert png == render(make_facts_dict(), "daily_pnl")  # exact fallback render


def test_unknown_event_type_raises_with_available_list():
    with pytest.raises(ValueError, match="daily_pnl"):
        render(make_facts_dict(event_type="mystery"), "mystery")


def test_facts_event_type_mismatch_raises():
    with pytest.raises(ValueError, match="does not match"):
        render(make_facts_dict(), "session_digest")
    model_facts = DailyPnlFacts(**{k: v for k, v in make_facts_dict().items()})
    with pytest.raises(ValueError, match="does not match"):
        render(model_facts, "session_digest")


def test_dict_facts_inherit_event_type():
    data = make_facts_dict()
    del data["event_type"]
    assert render(data, "daily_pnl").startswith(PNG_MAGIC)


def test_telegram_size_dimensions():
    png = render(make_facts_dict(), "daily_pnl", size="telegram")
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    assert (width, height) == (1280, 720)
