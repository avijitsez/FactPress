"""F5.3-F5.6 interactive approval API tests: the full §7 lifecycle over
``httpx.MockTransport`` only -- no real Telegram network calls anywhere.
"""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from factpress.interactive.api import DecisionResult, InteractiveManager
from factpress.interactive.store import PendingStore
from factpress.publisher import Publisher, PublisherConfig

CHAT_ID = 424242
AUTHORIZED_USER = 111
OTHER_USER = 222


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
    """MockTransport handler routing by endpoint suffix, recording every
    request so tests can assert on call counts and bodies."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self._next_message_id = 1000
        self.answer_status = 200

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
            return httpx.Response(self.answer_status, json={"ok": self.answer_status == 200})
        return httpx.Response(404, json={"ok": False, "description": "unknown method"})

    def calls(self, suffix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(suffix)]


def body_field(request: httpx.Request, name: str) -> str | None:
    """Pull a text field's value out of a multipart body (tolerating the
    binary PNG part, same approach as test_publisher.py's body_text)."""
    text = request.content.decode("latin-1")
    marker = f'name="{name}"\r\n\r\n'
    idx = text.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    end = text.find("\r\n--", start)
    return text[start:end]


def form_field(request: httpx.Request, name: str) -> str | None:
    """Pull a field out of an ``application/x-www-form-urlencoded`` body
    (answerCallbackQuery has no file part, so it isn't sent as multipart)."""
    parsed = parse_qs(request.content.decode("utf-8"))
    values = parsed.get(name)
    return values[0] if values else None


def make_manager(tmp_path, telegram, *, default_chat_id=None):
    store = PendingStore(tmp_path / "pending.sqlite3")
    publisher = Publisher(
        PublisherConfig(token="TESTTOKEN", default_chat_id=default_chat_id),
        transport=httpx.MockTransport(telegram),
    )
    return InteractiveManager(store, publisher), store


def callback_update(callback_data: str, user_id: int, *, first_name: str = "Al") -> dict:
    return {
        "callback_query": {
            "id": "cbq1",
            "from": {"id": user_id, "first_name": first_name},
            "data": callback_data,
        }
    }


def publish(manager, *, timeout_s=60.0, on_decision=None):
    return manager.publish_interactive(
        make_proposal_facts(),
        "trade_proposal",
        actions=[("approve", "Approve"), ("reject", "Reject")],
        authorized_users=[AUTHORIZED_USER],
        timeout_s=timeout_s,
        default_action="reject",
        on_decision=on_decision,
        chat_id=CHAT_ID,
    )


def test_publish_interactive_sends_keyboard_and_patches_message_id(tmp_path):
    telegram = RecordingTelegram()
    manager, store = make_manager(tmp_path, telegram)

    approval = publish(manager)

    sends = telegram.calls("/sendPhoto")
    assert len(sends) == 1
    markup = json.loads(body_field(sends[0], "reply_markup"))
    tokens_seen = {
        btn["callback_data"].rsplit(":", 1)[0]
        for row in markup["inline_keyboard"]
        for btn in row
    }
    action_ids = {
        btn["callback_data"].rsplit(":", 1)[1] for row in markup["inline_keyboard"] for btn in row
    }
    assert tokens_seen == {approval.token}
    assert action_ids == {"approve", "reject"}

    # message_id was patched from the placeholder to the real send result.
    assert approval.message_id != 0
    assert store.get(approval.token).message_id == approval.message_id


def test_authorized_tap_decides_edits_and_fires_on_decision(tmp_path):
    telegram = RecordingTelegram()
    manager, store = make_manager(tmp_path, telegram)
    fired: list[DecisionResult] = []
    approval = publish(manager, on_decision=fired.append)

    update = callback_update(f"{approval.token}:approve", AUTHORIZED_USER, first_name="Priya")
    result = manager.handle_callback(update)

    assert result is not None
    assert result.token == approval.token
    assert result.decision == "approve"
    assert result.user_id == AUTHORIZED_USER
    assert result.expired is False
    assert isinstance(result.decided_at, datetime)

    edits = telegram.calls("/editMessageMedia")
    assert len(edits) == 1
    assert body_field(edits[0], "reply_markup") == json.dumps({"inline_keyboard": []})

    toasts = telegram.calls("/answerCallbackQuery")
    assert len(toasts) == 1
    assert "applying" in form_field(toasts[0], "text")

    assert store.get(approval.token).status == "decided"
    assert store.get(approval.token).decision == "approve"
    assert len(fired) == 1
    assert fired[0].token == approval.token


def test_unauthorized_tap_refuses_and_leaves_row_pending(tmp_path):
    telegram = RecordingTelegram()
    manager, store = make_manager(tmp_path, telegram)
    approval = publish(manager)

    update = callback_update(f"{approval.token}:approve", OTHER_USER)
    result = manager.handle_callback(update)

    assert result is None
    assert store.get(approval.token).status == "pending"
    assert len(telegram.calls("/editMessageMedia")) == 0

    toasts = telegram.calls("/answerCallbackQuery")
    assert len(toasts) == 1
    assert "not authorized" in form_field(toasts[0], "text")
    assert form_field(toasts[0], "show_alert") == "true"


def test_double_tap_second_is_noop_with_exactly_one_edit(tmp_path):
    telegram = RecordingTelegram()
    manager, store = make_manager(tmp_path, telegram)
    approval = publish(manager)
    callback_data = f"{approval.token}:approve"

    first = manager.handle_callback(callback_update(callback_data, AUTHORIZED_USER))
    second = manager.handle_callback(callback_update(callback_data, AUTHORIZED_USER))

    assert first is not None
    assert second is None
    assert len(telegram.calls("/editMessageMedia")) == 1
    toasts = telegram.calls("/answerCallbackQuery")
    assert len(toasts) == 2
    assert form_field(toasts[1], "text") == "Already decided."
    assert store.get(approval.token).decision == "approve"


def test_tap_after_timeout_renders_expired(tmp_path):
    telegram = RecordingTelegram()
    manager, store = make_manager(tmp_path, telegram)
    # Already past its deadline the instant it's created.
    approval = publish(manager, timeout_s=-1.0)

    result = manager.handle_callback(
        callback_update(f"{approval.token}:approve", AUTHORIZED_USER)
    )

    assert result is None
    assert store.get(approval.token).status == "expired"
    edits = telegram.calls("/editMessageMedia")
    assert len(edits) == 1


def test_update_state_executed_with_facts_patch_finalizes_row(tmp_path):
    telegram = RecordingTelegram()
    manager, store = make_manager(tmp_path, telegram)
    approval = publish(manager)
    manager.handle_callback(callback_update(f"{approval.token}:approve", AUTHORIZED_USER))

    manager.update_state(
        approval.token,
        state="executed",
        facts_patch={"confidence_pct": 91.0},
        note="filled at limit",
    )

    edits = telegram.calls("/editMessageMedia")
    assert len(edits) == 2  # decision ack + execution ack
    row = store.get(approval.token)
    assert row.status == "finalized"
    assert "filled at limit" in row.decision


def test_update_state_bogus_patch_raises_and_does_not_edit_or_finalize(tmp_path):
    telegram = RecordingTelegram()
    manager, store = make_manager(tmp_path, telegram)
    approval = publish(manager)
    manager.handle_callback(callback_update(f"{approval.token}:approve", AUTHORIZED_USER))
    edits_before = len(telegram.calls("/editMessageMedia"))

    with pytest.raises(ValidationError):
        manager.update_state(approval.token, state="executed", facts_patch={"qty": -5})

    assert len(telegram.calls("/editMessageMedia")) == edits_before
    assert store.get(approval.token).status == "decided"


def test_check_expired_renders_expired_and_fires_default_action(tmp_path):
    telegram = RecordingTelegram()
    manager, store = make_manager(tmp_path, telegram)
    fired: list[DecisionResult] = []
    approval = publish(manager, timeout_s=-1.0, on_decision=fired.append)

    tokens = manager.check_expired()

    assert tokens == [approval.token]
    assert store.get(approval.token).status == "expired"
    assert len(telegram.calls("/editMessageMedia")) == 1
    assert len(fired) == 1
    assert fired[0].expired is True
    assert fired[0].decision == "reject"  # default_action
    assert fired[0].user_id is None


def test_resume_after_restart_rerenders_swept_rows(tmp_path):
    telegram = RecordingTelegram()
    manager, store = make_manager(tmp_path, telegram)
    approval = publish(manager, timeout_s=-1.0)

    # Simulate a fresh process: on_decision callbacks are gone, but the
    # store (on disk) still has the orphaned pending row.
    manager._on_decision.clear()

    tokens = manager.resume_after_restart()

    assert tokens == [approval.token]
    assert store.get(approval.token).status == "expired"
    assert len(telegram.calls("/editMessageMedia")) == 1


def test_answer_callback_query_failure_does_not_raise(tmp_path):
    telegram = RecordingTelegram()
    telegram.answer_status = 500
    manager, store = make_manager(tmp_path, telegram)
    approval = publish(manager)

    result = manager.handle_callback(
        callback_update(f"{approval.token}:approve", AUTHORIZED_USER)
    )

    assert result is not None
    assert store.get(approval.token).status == "decided"
    assert len(telegram.calls("/editMessageMedia")) == 1


def test_custom_action_ids_via_action_states(tmp_path):
    telegram = RecordingTelegram()
    store = PendingStore(tmp_path / "pending.sqlite3")
    publisher = Publisher(
        PublisherConfig(token="TESTTOKEN"),
        transport=httpx.MockTransport(telegram),
    )
    manager = InteractiveManager(
        store, publisher, action_states={"confirm": "approved", "cancel": "rejected"}
    )
    approval = manager.publish_interactive(
        make_proposal_facts(),
        "trade_proposal",
        actions=[("confirm", "Confirm halt"), ("cancel", "Keep running")],
        authorized_users=[AUTHORIZED_USER],
        timeout_s=60.0,
        default_action="cancel",
        chat_id=CHAT_ID,
    )
    result = manager.handle_callback(
        callback_update(f"{approval.token}:confirm", AUTHORIZED_USER)
    )
    assert result is not None
    assert result.decision == "confirm"
    assert len(telegram.calls("/editMessageMedia")) == 1
    assert store.get(approval.token).status == "decided"


def test_invalid_action_states_value_rejected(tmp_path):
    telegram = RecordingTelegram()
    store = PendingStore(tmp_path / "pending.sqlite3")
    publisher = Publisher(
        PublisherConfig(token="TESTTOKEN"),
        transport=httpx.MockTransport(telegram),
    )
    with pytest.raises(ValueError, match="approved"):
        InteractiveManager(store, publisher, action_states={"confirm": "executed"})
