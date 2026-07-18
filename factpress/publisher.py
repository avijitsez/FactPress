"""The publisher (F2.1): a synchronous Telegram ``sendPhoto`` client.

Per FACTPRESS_DESIGN.md §1 step 4 and §5, this is the pipeline's final
stage -- it takes the renderer's PNG bytes and pushes them to Telegram,
chat_id + message_thread_id aware (per-book "mode" topics vs. General
digests). It returns a :class:`MessageRef` -- JSON-trivial identifiers
consumed later by the interactive-card editMessageMedia flow (F5), not
the full Telegram response.

Silent hours (config-level local-hour window) mute notifications during
a broker's off-hours without suppressing the card itself; a caller can
still force ``silent=True/False`` per call to override the window.

Retries: Telegram's 429 responses carry a suggested backoff in
``parameters.retry_after``; 5xx responses get capped exponential backoff.
Other 4xx responses are the caller's fault (bad chat_id, bad token, etc.)
and raise immediately as :class:`PublishError` -- never silently retried,
and never carrying the bot token in their message.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import httpx

_TELEGRAM_API = "https://api.telegram.org"
_MAX_CAPTION_LEN = 1024
_BACKOFF_SCHEDULE = (1.0, 2.0, 4.0)


def _sleep(seconds: float) -> None:
    """Module-level indirection so tests can stub out real sleeping."""
    time.sleep(seconds)


@dataclass
class PublisherConfig:
    """Telegram bot configuration for :class:`Publisher`.

    ``silent_hours`` is a local-hour ``[start, end)`` window during which
    sends default to ``disable_notification=true`` unless the caller
    overrides via ``send_photo(..., silent=...)``. ``end <= start`` (e.g.
    ``(22, 8)``) means the window wraps past midnight.
    """

    token: str
    default_chat_id: int | str | None = None
    silent_hours: tuple[int, int] | None = None
    timeout_s: float = 30.0
    max_retries: int = 3


@dataclass
class MessageRef:
    """A published message's identifiers -- kept JSON-trivial so it can be
    persisted and later consumed by the interactive editMessageMedia flow
    (F5)."""

    chat_id: int | str
    message_id: int
    thread_id: int | None = None


class PublishError(Exception):
    """A non-retryable (or retries-exhausted) Telegram API failure.

    Carries ``status`` (HTTP status code) and ``description`` (Telegram's
    error text) but never the bot token -- the message is built from the
    endpoint's error body only, not from any request details.
    """

    def __init__(self, status: int, description: str) -> None:
        self.status = status
        self.description = description
        super().__init__(f"Telegram API error {status}: {description}")


def _in_silent_window(hour: int, window: tuple[int, int]) -> bool:
    start, end = window
    if start == end:
        # Degenerate: a zero-width window matches nothing.
        return False
    if start < end:
        return start <= hour < end
    # Wraps past midnight, e.g. (22, 8).
    return hour >= start or hour < end


class Publisher:
    """Synchronous Telegram ``sendPhoto`` publisher.

    ``transport`` lets tests inject an ``httpx.MockTransport`` so
    :meth:`send_photo` never touches the network; production code leaves
    it ``None`` and gets a real ``httpx.Client``. ``clock`` is a zero-arg
    callable returning the current local ``datetime``, injectable for
    deterministic silent-hours tests.
    """

    def __init__(
        self,
        config: PublisherConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport
        self._clock = clock or datetime.now

    def _endpoint(self) -> str:
        return f"{_TELEGRAM_API}/bot{self.config.token}/sendPhoto"

    def _resolve_silent(self, silent: bool | None) -> bool:
        if silent is not None:
            return silent
        if self.config.silent_hours is None:
            return False
        return _in_silent_window(self._clock().hour, self.config.silent_hours)

    def send_photo(
        self,
        png: bytes,
        *,
        caption: str | None = None,
        chat_id: int | str | None = None,
        thread_id: int | None = None,
        silent: bool | None = None,
    ) -> MessageRef:
        resolved_chat_id = chat_id if chat_id is not None else self.config.default_chat_id
        if resolved_chat_id is None:
            raise ValueError(
                "no chat_id: pass send_photo(..., chat_id=...) or set "
                "PublisherConfig.default_chat_id"
            )
        if caption is not None and len(caption) > _MAX_CAPTION_LEN:
            raise ValueError(
                f"caption is {len(caption)} chars, exceeds Telegram's {_MAX_CAPTION_LEN}-char "
                "limit (not truncated -- shorten it explicitly)"
            )

        data: dict[str, Any] = {"chat_id": resolved_chat_id}
        if caption is not None:
            data["caption"] = caption
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        if self._resolve_silent(silent):
            data["disable_notification"] = "true"

        files = {"photo": ("card.png", png, "image/png")}

        client_kwargs: dict[str, Any] = {}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        with httpx.Client(**client_kwargs) as client:
            attempt = 0
            while True:
                response = client.post(
                    self._endpoint(),
                    data=data,
                    files=files,
                    timeout=self.config.timeout_s,
                )
                if response.status_code == 200:
                    payload = response.json()
                    message_id = payload["result"]["message_id"]
                    return MessageRef(
                        chat_id=resolved_chat_id, message_id=message_id, thread_id=thread_id
                    )

                description = _error_description(response)

                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable:
                    raise PublishError(response.status_code, description)

                if attempt >= self.config.max_retries:
                    raise PublishError(response.status_code, description)

                if response.status_code == 429:
                    delay = _retry_after(response)
                else:
                    delay = _BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)]
                _sleep(delay)
                attempt += 1


def _error_description(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    description = body.get("description")
    return description if description is not None else response.text


def _retry_after(response: httpx.Response) -> float:
    try:
        body = response.json()
    except ValueError:
        return 1.0
    parameters = body.get("parameters") or {}
    retry_after = parameters.get("retry_after")
    return float(retry_after) if retry_after is not None else 1.0
