"""F2.1 publisher tests: Telegram sendPhoto over httpx.MockTransport only.

No real network anywhere -- every ``Publisher`` is built with a
``httpx.MockTransport`` handler, and retry sleeps go through the
module-level ``factpress.publisher._sleep`` hook so tests run instantly.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from factpress.publisher import (
    MessageRef,
    PublishError,
    Publisher,
    PublisherConfig,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-png-body"


class ScriptedTelegram:
    """MockTransport handler replaying scripted responses and capturing requests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]


def ok_response(message_id: int = 555) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


def error_response(status: int, description: str, retry_after: int | None = None) -> httpx.Response:
    body: dict = {"ok": False, "description": description}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return httpx.Response(status, json=body)


def make_publisher(handler, config=None, clock=None):
    config = config or PublisherConfig(token="SECRET", default_chat_id=42)
    return Publisher(config, transport=httpx.MockTransport(handler), clock=clock)


def body_text(request: httpx.Request) -> str:
    """Decode a multipart request body for substring assertions, tolerating
    the binary PNG part (the fields under test are all ASCII)."""
    return request.content.decode("latin-1")


def test_success_parses_message_ref_with_thread_id():
    telegram = ScriptedTelegram([ok_response(777)])
    publisher = make_publisher(telegram)
    ref = publisher.send_photo(PNG_BYTES, caption="hi", thread_id=9)
    assert ref == MessageRef(chat_id=42, message_id=777, thread_id=9)


def test_explicit_chat_id_overrides_config_default():
    telegram = ScriptedTelegram([ok_response(1)])
    publisher = make_publisher(telegram)
    ref = publisher.send_photo(PNG_BYTES, chat_id=999)
    assert ref.chat_id == 999
    sent = body_text(telegram.requests[0])
    assert "999" in sent


def test_missing_chat_id_raises_value_error():
    telegram = ScriptedTelegram([ok_response()])
    config = PublisherConfig(token="SECRET")
    publisher = Publisher(config, transport=httpx.MockTransport(telegram))
    with pytest.raises(ValueError):
        publisher.send_photo(PNG_BYTES)
    assert len(telegram.requests) == 0


def test_caption_over_limit_raises_value_error_without_truncating():
    telegram = ScriptedTelegram([ok_response()])
    publisher = make_publisher(telegram)
    with pytest.raises(ValueError):
        publisher.send_photo(PNG_BYTES, caption="x" * 1025)
    assert len(telegram.requests) == 0


def test_caption_at_limit_is_accepted():
    telegram = ScriptedTelegram([ok_response()])
    publisher = make_publisher(telegram)
    publisher.send_photo(PNG_BYTES, caption="x" * 1024)
    assert len(telegram.requests) == 1


def test_silent_flag_forces_disable_notification():
    telegram = ScriptedTelegram([ok_response()])
    publisher = make_publisher(telegram)
    publisher.send_photo(PNG_BYTES, silent=True)
    sent = body_text(telegram.requests[0])
    assert "disable_notification" in sent


def test_silent_false_never_sets_disable_notification_even_in_window():
    telegram = ScriptedTelegram([ok_response()])
    config = PublisherConfig(token="SECRET", default_chat_id=1, silent_hours=(22, 8))
    publisher = Publisher(
        config,
        transport=httpx.MockTransport(telegram),
        clock=lambda: datetime(2026, 7, 18, 23, 0),
    )
    publisher.send_photo(PNG_BYTES, silent=False)
    sent = body_text(telegram.requests[0])
    assert "disable_notification" not in sent


@pytest.mark.parametrize("hour", [23, 7])
def test_wrapping_silent_hours_window_is_silent(hour):
    telegram = ScriptedTelegram([ok_response()])
    config = PublisherConfig(token="SECRET", default_chat_id=1, silent_hours=(22, 8))
    publisher = Publisher(
        config,
        transport=httpx.MockTransport(telegram),
        clock=lambda: datetime(2026, 7, 18, hour, 0),
    )
    publisher.send_photo(PNG_BYTES)
    sent = body_text(telegram.requests[0])
    assert "disable_notification" in sent


def test_wrapping_silent_hours_window_is_not_silent_at_noon():
    telegram = ScriptedTelegram([ok_response()])
    config = PublisherConfig(token="SECRET", default_chat_id=1, silent_hours=(22, 8))
    publisher = Publisher(
        config,
        transport=httpx.MockTransport(telegram),
        clock=lambda: datetime(2026, 7, 18, 12, 0),
    )
    publisher.send_photo(PNG_BYTES)
    sent = body_text(telegram.requests[0])
    assert "disable_notification" not in sent


def test_non_wrapping_silent_hours_window():
    telegram = ScriptedTelegram([ok_response(), ok_response(), ok_response()])
    config = PublisherConfig(token="SECRET", default_chat_id=1, silent_hours=(13, 14))
    publisher = Publisher(
        config,
        transport=httpx.MockTransport(telegram),
        clock=lambda: datetime(2026, 7, 18, 13, 30),
    )
    publisher.send_photo(PNG_BYTES)
    assert "disable_notification" in body_text(telegram.requests[0])

    publisher_before = Publisher(
        config,
        transport=httpx.MockTransport(ScriptedTelegram([ok_response()])),
        clock=lambda: datetime(2026, 7, 18, 12, 59),
    )
    ref = publisher_before.send_photo(PNG_BYTES)
    assert ref.message_id == 555

    publisher_after = Publisher(
        config,
        transport=httpx.MockTransport(ScriptedTelegram([ok_response()])),
        clock=lambda: datetime(2026, 7, 18, 14, 0),
    )
    publisher_after.send_photo(PNG_BYTES)


def test_429_with_retry_after_then_success(monkeypatch):
    sleeps = []
    monkeypatch.setattr("factpress.publisher._sleep", lambda s: sleeps.append(s))
    telegram = ScriptedTelegram(
        [error_response(429, "Too Many Requests", retry_after=17), ok_response()]
    )
    publisher = make_publisher(telegram)
    ref = publisher.send_photo(PNG_BYTES)
    assert ref.message_id == 555
    assert len(telegram.requests) == 2
    assert sleeps == [17.0]


def test_500_twice_then_success(monkeypatch):
    sleeps = []
    monkeypatch.setattr("factpress.publisher._sleep", lambda s: sleeps.append(s))
    telegram = ScriptedTelegram(
        [error_response(500, "Internal Server Error")] * 2 + [ok_response()]
    )
    publisher = make_publisher(telegram)
    ref = publisher.send_photo(PNG_BYTES)
    assert ref.message_id == 555
    assert len(telegram.requests) == 3
    assert sleeps == [1.0, 2.0]


def test_500_exhausts_retries_and_raises_publish_error(monkeypatch):
    sleeps = []
    monkeypatch.setattr("factpress.publisher._sleep", lambda s: sleeps.append(s))
    config = PublisherConfig(token="SECRET", default_chat_id=1, max_retries=3)
    telegram = ScriptedTelegram([error_response(500, "boom")] * 10)
    publisher = Publisher(config, transport=httpx.MockTransport(telegram))
    with pytest.raises(PublishError) as excinfo:
        publisher.send_photo(PNG_BYTES)
    assert excinfo.value.status == 500
    assert len(telegram.requests) == 1 + config.max_retries
    assert sleeps == [1.0, 2.0, 4.0]


def test_403_raises_publish_error_immediately_without_retry(monkeypatch):
    sleeps = []
    monkeypatch.setattr("factpress.publisher._sleep", lambda s: sleeps.append(s))
    telegram = ScriptedTelegram([error_response(403, "Forbidden: bot was blocked by the user")])
    publisher = make_publisher(telegram)
    with pytest.raises(PublishError) as excinfo:
        publisher.send_photo(PNG_BYTES)
    assert excinfo.value.status == 403
    assert len(telegram.requests) == 1
    assert sleeps == []


def test_token_never_appears_in_publish_error_text():
    telegram = ScriptedTelegram([error_response(403, "Forbidden: bot was blocked by the user")])
    publisher = make_publisher(telegram)
    with pytest.raises(PublishError) as excinfo:
        publisher.send_photo(PNG_BYTES)
    assert "SECRET" not in str(excinfo.value)
    assert "SECRET" not in repr(excinfo.value)


def test_multipart_body_contains_png_bytes():
    telegram = ScriptedTelegram([ok_response()])
    publisher = make_publisher(telegram)
    publisher.send_photo(PNG_BYTES)
    assert PNG_BYTES in telegram.requests[0].content


def test_transport_error_raises_publish_error_without_token(monkeypatch):
    def exploding_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed to reach {request.url}", request=request)

    publisher = Publisher(
        PublisherConfig(token="SECRET123", default_chat_id=1, max_retries=1),
        transport=httpx.MockTransport(exploding_handler),
    )
    with pytest.raises(PublishError) as excinfo:
        publisher.send_photo(b"\x89PNG fake")
    assert "SECRET123" not in str(excinfo.value)
    assert "SECRET123" not in repr(excinfo.value)
