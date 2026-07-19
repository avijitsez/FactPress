"""F5.7 standalone long-poll loop (FACTPRESS_DESIGN.md §7 "Integration surface").

FactPress is a library, not a daemon: hosts that already run their own
Telegram dispatcher wire updates straight into
:meth:`~factpress.interactive.api.InteractiveManager.handle_callback`
(``ff.handle_callback(update)``). :class:`Poller` is the other half of
that surface -- a small, blocking ``getUpdates`` long-poll loop for
standalone users who have no dispatcher of their own (``ff.run_poller()``).

It does exactly two things, forever, until told to stop:

* long-poll Telegram's ``getUpdates`` (``allowed_updates=["callback_query"]``,
  offset tracking so already-seen updates are never redelivered), routing
  each ``callback_query`` update into
  :meth:`InteractiveManager.handle_callback`;
* every ``expiry_sweep_every_s``, call
  :meth:`InteractiveManager.check_expired` so timed-out cards still
  re-render as EXPIRED even when nothing is being tapped.

Both are best-effort: an exception out of ``handle_callback`` or
``check_expired`` is logged and the loop continues -- one bad update must
never take the whole poller down. Telegram/network hiccups (transport
errors, non-200 responses) are logged with capped exponential backoff
(1s, 2s, 4s, 8s) and also do not stop the loop -- see the module docstring
of :mod:`factpress.publisher` for the same policy applied to sends.

The bot token lives only in the ``getUpdates`` URL; every place that
might surface request details (transport-error text, non-200 response
bodies) is sanitized before logging, mirroring ``publisher.py``'s
``str(exc).replace(token, "***")`` pattern -- the token must never reach
logs or exception messages.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import httpx

from factpress.interactive.api import InteractiveManager
from factpress.publisher import PublisherConfig

logger = logging.getLogger("factpress.interactive.poller")

_TELEGRAM_API = "https://api.telegram.org"
_BACKOFF_SCHEDULE = (1.0, 2.0, 4.0, 8.0)


def _sleep(seconds: float) -> None:
    """Module-level indirection so tests can stub out real sleeping."""
    time.sleep(seconds)


class _GetUpdatesError(Exception):
    """A non-200 ``getUpdates`` response. Carries a pre-sanitized
    description only -- never the request URL (which embeds the token)."""

    def __init__(self, status: int, description: str) -> None:
        self.status = status
        self.description = description
        super().__init__(f"getUpdates failed: {status} {description}")


class Poller:
    """Blocking Telegram ``getUpdates`` long-poll loop (F5.7).

    ``publisher_config`` may be a full :class:`~factpress.publisher.PublisherConfig`
    (the common case: reuse the same config a :class:`~factpress.publisher.Publisher`
    was built from) or a bare bot-token string, for standalone construction.
    Only the token is used here -- the poller never sends anything itself,
    it only reads updates and forwards them to ``manager``.

    ``transport`` is the same ``httpx.BaseTransport`` injection seam as
    ``Publisher``/``Director`` use: pass an ``httpx.MockTransport`` in tests,
    leave it ``None`` in production for a real ``httpx.Client``.
    """

    def __init__(
        self,
        manager: InteractiveManager,
        publisher_config: PublisherConfig | str,
        *,
        transport: httpx.BaseTransport | None = None,
        poll_timeout_s: int = 25,
        expiry_sweep_every_s: float = 15.0,
    ) -> None:
        self._manager = manager
        self._config = (
            publisher_config
            if isinstance(publisher_config, PublisherConfig)
            else PublisherConfig(token=publisher_config)
        )
        self._transport = transport
        self.poll_timeout_s = poll_timeout_s
        self.expiry_sweep_every_s = expiry_sweep_every_s
        self._offset: int | None = None

    def _token(self) -> str:
        return self._config.token

    def _get_updates_endpoint(self) -> str:
        return f"{_TELEGRAM_API}/bot{self._token()}/getUpdates"

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return kwargs

    def _sanitize(self, text: str) -> str:
        return text.replace(self._token(), "***")

    def _fetch_updates(self, client: httpx.Client) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": self.poll_timeout_s,
            "allowed_updates": json.dumps(["callback_query"]),
        }
        if self._offset is not None:
            params["offset"] = self._offset
        # A short margin over the long-poll timeout itself, so Telegram
        # returning right at its own deadline is never mistaken for a
        # client-side read timeout.
        response = client.get(
            self._get_updates_endpoint(),
            params=params,
            timeout=self.poll_timeout_s + 5,
        )
        if response.status_code != 200:
            try:
                body = response.json()
            except ValueError:
                description = response.text
            else:
                description = body.get("description") or response.text
            raise _GetUpdatesError(response.status_code, self._sanitize(description))
        payload = response.json()
        return list(payload.get("result", []))

    def _handle_updates(self, updates: list[dict[str, Any]]) -> None:
        for update in updates:
            # Advance past every update_id seen, callback_query or not --
            # Telegram redelivers anything below the offset on the next call.
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = update_id + 1
            if "callback_query" not in update:
                continue
            try:
                self._manager.handle_callback(update)
            except Exception:
                logger.exception(
                    "handle_callback raised for update_id=%s", update.get("update_id")
                )

    def _sweep_expired(self) -> None:
        try:
            self._manager.check_expired()
        except Exception:
            logger.exception("check_expired raised during poller sweep")

    def run(self, stop_event: threading.Event | None = None) -> None:
        """Blocking long-poll loop. Runs until ``stop_event`` is set (a
        fresh, never-set one is used if omitted, which then only stops on
        an unhandled exception from the loop body itself -- there is none
        by design, since every step below already contains its own
        failure handling)."""
        stop_event = stop_event if stop_event is not None else threading.Event()
        attempt = 0
        last_sweep = time.monotonic()
        with httpx.Client(**self._client_kwargs()) as client:
            while not stop_event.is_set():
                try:
                    updates = self._fetch_updates(client)
                    attempt = 0
                except httpx.HTTPError as exc:
                    logger.warning("getUpdates transport error: %s", self._sanitize(str(exc)))
                    delay = _BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)]
                    _sleep(delay)
                    attempt += 1
                    updates = []
                except _GetUpdatesError as exc:
                    logger.warning("%s", exc)
                    delay = _BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)]
                    _sleep(delay)
                    attempt += 1
                    updates = []

                self._handle_updates(updates)

                now = time.monotonic()
                if now - last_sweep >= self.expiry_sweep_every_s:
                    self._sweep_expired()
                    last_sweep = now

    def start_in_thread(self) -> tuple[threading.Thread, threading.Event]:
        """Convenience: run :meth:`run` on a daemon thread. Returns
        ``(thread, stop_event)`` -- set the event and ``thread.join()`` to
        shut down. Note the loop's own ``getUpdates`` call blocks for up to
        ``poll_timeout_s`` seconds at a time, so shutdown latency is bounded
        by ``poll_timeout_s``, not instant."""
        stop_event = threading.Event()
        thread = threading.Thread(target=self.run, args=(stop_event,), daemon=True)
        thread.start()
        return thread, stop_event
