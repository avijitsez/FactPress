"""F5.7 standalone long-poll loop tests: Poller over httpx.MockTransport
only -- no real network anywhere, and no real waiting either (the loop is
driven synchronously; MockTransport handlers below flip the shared
``stop_event`` themselves once the scripted exchange is done, so ``run()``
returns without a background thread or a sleep in the test)."""

from __future__ import annotations

import json
import logging
import threading

import httpx

from factpress.interactive import poller as poller_module
from factpress.interactive.api import InteractiveManager
from factpress.interactive.poller import Poller
from factpress.interactive.store import PendingStore
from factpress.publisher import Publisher, PublisherConfig

CHAT_ID = 424242
AUTHORIZED_USER = 111
TOKEN = "TESTPOLLTOKEN"


def make_proposal_facts(**overrides):
    base = dict(
        event_type="trade_proposal",
        symbol="NVDA",
        side="buy",
        qty=120,
        limit_price=128.4,
        confidence_pct=72.5,
        plan_target_pct=6.5,
        plan_stop_pct=-2.5,
        currency="USD",
        risk_note="Risk team: within limits, size capped",
    )
    base.update(overrides)
    return base


class RecordingTelegram:
    """Same routing pattern as tests/test_interactive_api.py's helper --
    used here to back the manager's own Publisher (sendPhoto/editMessageMedia/
    answerCallbackQuery), entirely separate from the poller's own getUpdates
    transport."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self._next_message_id = 1000

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/sendPhoto"):
            mid = self._next_message_id
            self._next_message_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": mid}})
        if path.endswith("/editMessageMedia"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        if path.endswith("/answerCallbackQuery"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"ok": False, "description": "unknown method"})

    def calls(self, suffix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(suffix)]


def make_manager(tmp_path):
    telegram = RecordingTelegram()
    store = PendingStore(tmp_path / "pending.sqlite3")
    publisher = Publisher(
        PublisherConfig(token=TOKEN, default_chat_id=CHAT_ID),
        transport=httpx.MockTransport(telegram),
    )
    manager = InteractiveManager(store, publisher)
    return manager, store, telegram


def publish_one(manager, **overrides):
    kwargs = dict(
        actions=[("approve", "Approve"), ("reject", "Reject")],
        authorized_users=[AUTHORIZED_USER],
        timeout_s=60.0,
        default_action="reject",
        chat_id=CHAT_ID,
    )
    kwargs.update(overrides)
    return manager.publish_interactive(make_proposal_facts(), "trade_proposal", **kwargs)


def callback_query_update(update_id: int, token: str, action: str) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cbq{update_id}",
            "from": {"id": AUTHORIZED_USER, "first_name": "Priya"},
            "data": f"{token}:{action}",
        },
    }


class ScriptedGetUpdates:
    """MockTransport handler for the poller's own getUpdates client:
    replays a fixed list of responses (by call index, clamped to the last
    entry), records requests, and optionally sets ``stop_event`` once a
    given call count is reached -- letting tests drive ``run()`` to a
    deterministic, prompt stop with no real waiting."""

    def __init__(self, responses, *, stop_event: threading.Event, stop_after: int):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self._stop_event = stop_event
        self._stop_after = stop_after

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        idx = min(len(self.requests) - 1, len(self.responses) - 1)
        response = self.responses[idx]
        if len(self.requests) >= self._stop_after:
            self._stop_event.set()
        if callable(response):
            return response()
        return response


def ok(updates: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": updates})


def test_run_processes_callback_and_advances_offset(tmp_path):
    manager, store, _telegram = make_manager(tmp_path)
    approval = publish_one(manager)

    stop_event = threading.Event()
    handler = ScriptedGetUpdates(
        [ok([callback_query_update(100, approval.token, "approve")]), ok([]), ok([])],
        stop_event=stop_event,
        stop_after=3,
    )
    poller = Poller(
        manager,
        PublisherConfig(token=TOKEN),
        transport=httpx.MockTransport(handler),
        expiry_sweep_every_s=1000.0,
    )

    poller.run(stop_event=stop_event)

    # The decision landed in the store via handle_callback.
    assert store.get(approval.token).status == "decided"
    assert store.get(approval.token).decision == "approve"

    # offset advanced past the processed update_id: the second call carries
    # offset=101 (and every subsequent one, since no further updates came in).
    assert len(handler.requests) == 3
    second_params = dict(httpx.QueryParams(handler.requests[1].url.query))
    assert second_params["offset"] == "101"
    third_params = dict(httpx.QueryParams(handler.requests[2].url.query))
    assert third_params["offset"] == "101"

    # allowed_updates was restricted to callback_query on every call.
    first_params = dict(httpx.QueryParams(handler.requests[0].url.query))
    assert json.loads(first_params["allowed_updates"]) == ["callback_query"]


def test_run_survives_http_500_then_recovers(tmp_path, monkeypatch):
    manager, _store, _telegram = make_manager(tmp_path)

    sleeps: list[float] = []
    monkeypatch.setattr(poller_module, "_sleep", lambda s: sleeps.append(s))

    stop_event = threading.Event()
    handler = ScriptedGetUpdates(
        [
            httpx.Response(500, json={"ok": False, "description": "Internal Server Error"}),
            ok([]),
        ],
        stop_event=stop_event,
        stop_after=2,
    )
    poller = Poller(
        manager,
        PublisherConfig(token=TOKEN),
        transport=httpx.MockTransport(handler),
        expiry_sweep_every_s=1000.0,
    )

    poller.run(stop_event=stop_event)

    assert len(handler.requests) == 2
    assert sleeps == [1.0]  # first backoff entry, loop survived and recovered


def test_stop_event_set_upfront_terminates_promptly(tmp_path):
    manager, _store, _telegram = make_manager(tmp_path)

    stop_event = threading.Event()
    stop_event.set()

    def explode(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("getUpdates must not be called once stop_event is set")

    poller = Poller(manager, PublisherConfig(token=TOKEN), transport=httpx.MockTransport(explode))

    poller.run(stop_event=stop_event)  # returns immediately, no HTTP call made


def test_expiry_sweep_expires_overdue_row(tmp_path):
    manager, store, _telegram = make_manager(tmp_path)
    # Already past its deadline the instant it's created.
    approval = publish_one(manager, timeout_s=-1.0)

    stop_event = threading.Event()
    handler = ScriptedGetUpdates([ok([])], stop_event=stop_event, stop_after=1)
    poller = Poller(
        manager,
        PublisherConfig(token=TOKEN),
        transport=httpx.MockTransport(handler),
        expiry_sweep_every_s=0.0,  # fire on every iteration, deterministically
    )

    poller.run(stop_event=stop_event)

    row = store.get(approval.token)
    assert row.status == "expired"
    assert len(_telegram.calls("/editMessageMedia")) == 1  # EXPIRED re-render


def test_token_never_appears_in_logs_on_transport_failure(tmp_path, caplog):
    manager, _store, _telegram = make_manager(tmp_path)

    stop_event = threading.Event()
    call_count = 0

    def failing(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            stop_event.set()
        raise httpx.ConnectError(f"connection refused to https://api.telegram.org/bot{TOKEN}/getUpdates")

    poller = Poller(
        manager,
        PublisherConfig(token=TOKEN),
        transport=httpx.MockTransport(failing),
        expiry_sweep_every_s=1000.0,
    )

    with caplog.at_level(logging.WARNING, logger="factpress.interactive.poller"):
        poller.run(stop_event=stop_event)

    assert call_count >= 2
    assert len(caplog.records) >= 1
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
