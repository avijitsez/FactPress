"""F2.4 facade tests: the public ``FactPress`` class + package exports.

Testability seam: ``FactPress`` accepts private ``_director_transport`` /
``_publisher_transport`` kwargs (``httpx.BaseTransport``, e.g.
``httpx.MockTransport``) that are forwarded straight to the ``Director`` /
``Publisher`` it constructs. This lets these tests exercise the *real*
constructor path (no monkeypatching of ``httpx`` globally, no reaching into
private attributes after construction) while still touching zero network --
same pattern as ``tests/test_director_fallback.py`` and
``tests/test_publisher.py``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from factpress import (
    DailyPnlFacts,
    Director,
    DirectorConfig,
    FactPress,
    MessageRef,
    PublishError,
    fallback_spec,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def make_daily_pnl_facts(**overrides):
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


class ScriptedLLM:
    """MockTransport handler replaying scripted director replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})


class ScriptedTelegram:
    """MockTransport handler replaying scripted Telegram sendPhoto responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]


def ok_response(message_id: int = 999) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


def has_field(content: bytes, name: str, value: str) -> bool:
    """Check a multipart body for a ``name="<name>"`` field with exactly ``value``.

    Checked at the byte level (``value`` UTF-8 encoded) so non-ASCII payloads
    like emoji captions survive intact -- a ``str`` decode of a body that
    also contains raw PNG bytes is not reliably invertible.
    """
    needle = f'name="{name}"\r\n\r\n'.encode() + value.encode()
    return needle in content


def valid_spec_json(**overrides):
    base = dict(
        template_id="daily_pnl",
        template_version="1.0.0",
        variant="default",
        tone="celebratory",
        palette_id="aurora",
        hero_metric_key="daily_pnl_pct",
        emphasis_keys=["win_rate_pct"],
        callout_keys=[],
        headline="Solid day across the board",
        subhead=None,
        caption="Solid day across the board",
        emoji="\U0001f680",  # rocket
        sparkline=True,
    )
    base.update(overrides)
    return json.dumps(base)


def test_render_is_zero_config_for_daily_pnl_dict_facts():
    ff = FactPress()
    png = ff.render(make_daily_pnl_facts(), "daily_pnl")
    assert png.startswith(PNG_MAGIC)


def test_publish_without_telegram_token_raises_runtime_error():
    ff = FactPress()
    with pytest.raises(RuntimeError, match="telegram_token"):
        ff.publish(make_daily_pnl_facts(), "daily_pnl")


def test_publish_prepends_emoji_and_passes_chat_thread_through():
    llm = ScriptedLLM([valid_spec_json()])
    telegram = ScriptedTelegram([ok_response(4242)])

    ff = FactPress(
        llm_base_url="http://llm.test/v1",
        llm_model="test-model",
        telegram_token="SECRET",
        default_chat_id=1,
        _director_transport=httpx.MockTransport(llm),
        _publisher_transport=httpx.MockTransport(telegram),
    )

    ref = ff.publish(
        make_daily_pnl_facts(), "daily_pnl", chat_id=777, thread_id=88
    )

    assert isinstance(ref, MessageRef)
    assert ref.message_id == 4242
    assert ref.chat_id == 777
    assert ref.thread_id == 88

    assert len(telegram.requests) == 1
    sent = telegram.requests[0].content
    assert PNG_MAGIC in sent
    # emoji prepended to caption, per docs/rendering-contract.md
    assert has_field(sent, "caption", "\U0001f680 Solid day across the board")
    assert has_field(sent, "chat_id", "777")
    assert has_field(sent, "message_thread_id", "88")


def test_publish_uses_emoji_alone_when_caption_is_none():
    llm = ScriptedLLM([valid_spec_json(caption=None)])
    telegram = ScriptedTelegram([ok_response()])

    ff = FactPress(
        llm_base_url="http://llm.test/v1",
        llm_model="test-model",
        telegram_token="SECRET",
        default_chat_id=1,
        _director_transport=httpx.MockTransport(llm),
        _publisher_transport=httpx.MockTransport(telegram),
    )
    ff.publish(make_daily_pnl_facts(), "daily_pnl")

    sent = telegram.requests[0].content
    assert has_field(sent, "caption", "\U0001f680")


def test_design_doc_snippet_constructs():
    """FACTPRESS_DESIGN.md §3's public API block (minus the trailing-comma
    typo) must construct without error."""
    ff = FactPress(
        llm_base_url="http://localhost:8100/v1",
        llm_model="whatever-your-relay-serves",
        telegram_token="tok",
        default_chat_id=123,
        brandkit="brandkits/default.yaml",
        template_paths=["./templates_private"],
    )
    assert isinstance(ff, FactPress)


def test_package_exports_public_api():
    from factpress import DesignSpec, FactPayload, TradeExecutedFacts, __version__

    assert Director is not None
    assert DirectorConfig is not None
    assert fallback_spec is not None
    assert DesignSpec is not None
    assert FactPayload is not None
    assert DailyPnlFacts is not None
    assert TradeExecutedFacts is not None
    assert PublishError is not None
    assert isinstance(__version__, str)
