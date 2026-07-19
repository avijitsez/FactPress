"""F5.2 pending-store tests: atomic single-use tokens, expiry, restart survival."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta


from factpress.interactive.store import PendingApproval, PendingStore


def make_store(tmp_path):
    return PendingStore(tmp_path / "pending.sqlite3")


def create_row(store, *, timeout_s=60.0, now=None):
    return store.create(
        chat_id=7749600079,
        message_id=42,
        thread_id=99,
        event_type="trade_proposal",
        facts_json='{"event_type": "trade_proposal", "symbol": "AAPL"}',
        spec_json='{"template_id": "trade_proposal"}',
        actions=[("approve", "Approve"), ("reject", "Reject")],
        authorized_users=[111, 222],
        timeout_s=timeout_s,
        default_action="reject",
        now=now,
    )


def test_create_get_round_trip_preserves_json_fields(tmp_path):
    with make_store(tmp_path) as store:
        created = create_row(store)
        assert len(created.token) <= 64  # Telegram callback_data cap
        fetched = store.get(created.token)
        assert isinstance(fetched, PendingApproval)
        assert fetched.actions == [("approve", "Approve"), ("reject", "Reject")]
        assert fetched.authorized_users == [111, 222]
        assert fetched.thread_id == 99
        assert fetched.status == "pending"
        assert fetched.timeout_at > fetched.created_at


def test_consume_happy_path_stamps_decision(tmp_path):
    with make_store(tmp_path) as store:
        row = create_row(store)
        decided = store.consume(row.token, user_id=111, decision="approve")
        assert decided is not None
        assert decided.status == "decided"
        assert decided.decision == "approve"
        assert decided.decided_by == 111
        assert decided.decided_at is not None


def test_second_consume_is_noop(tmp_path):
    with make_store(tmp_path) as store:
        row = create_row(store)
        assert store.consume(row.token, user_id=111, decision="approve") is not None
        assert store.consume(row.token, user_id=222, decision="reject") is None
        # first decision stands
        assert store.get(row.token).decision == "approve"


def test_concurrent_consume_exactly_one_winner(tmp_path):
    store = make_store(tmp_path)
    try:
        row = create_row(store)
        results = []
        barrier = threading.Barrier(8)

        def tap(uid):
            barrier.wait()
            results.append(store.consume(row.token, user_id=uid, decision="approve"))

        threads = [threading.Thread(target=tap, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [r for r in results if r is not None]
        assert len(winners) == 1
    finally:
        store.close()


def test_consume_after_timeout_expires_row(tmp_path):
    with make_store(tmp_path) as store:
        start = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
        row = create_row(store, timeout_s=60.0, now=start)
        late = start + timedelta(seconds=61)
        assert store.consume(row.token, user_id=111, decision="approve", now=late) is None
        stored = store.get(row.token)
        assert stored.status == "expired"
        assert stored.decision is None


def test_rows_survive_reopen(tmp_path):
    store = make_store(tmp_path)
    row = create_row(store)
    store.close()
    reopened = PendingStore(tmp_path / "pending.sqlite3")
    try:
        assert reopened.get(row.token).token == row.token
    finally:
        reopened.close()


def test_sweep_restart_orphans_expires_only_overdue(tmp_path):
    with make_store(tmp_path) as store:
        start = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
        overdue = create_row(store, timeout_s=30.0, now=start)
        fresh = create_row(store, timeout_s=3600.0, now=start)
        swept = store.sweep_restart_orphans(now=start + timedelta(seconds=60))
        swept_tokens = {row.token for row in swept}
        assert swept_tokens == {overdue.token}
        assert store.get(overdue.token).status == "expired"
        assert store.get(fresh.token).status == "pending"
        assert {r.token for r in store.all_pending()} == {fresh.token}


def test_pending_older_than_timeout_lists_overdue(tmp_path):
    with make_store(tmp_path) as store:
        start = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
        overdue = create_row(store, timeout_s=30.0, now=start)
        create_row(store, timeout_s=3600.0, now=start)
        listed = store.pending_older_than_timeout(now=start + timedelta(seconds=45))
        assert [r.token for r in listed] == [overdue.token]


def test_mark_finalized_and_expired(tmp_path):
    with make_store(tmp_path) as store:
        row = create_row(store)
        store.consume(row.token, user_id=111, decision="approve")
        finalized = store.mark_finalized(row.token)
        assert finalized.status == "finalized"
        other = create_row(store)
        assert store.mark_expired(other.token).status == "expired"


def test_unknown_token_is_none_everywhere(tmp_path):
    with make_store(tmp_path) as store:
        assert store.get("nope") is None
        assert store.consume("nope", user_id=1, decision="approve") is None
        assert store.mark_finalized("nope") is None
        assert store.mark_expired("nope") is None


def test_context_manager_protocol(tmp_path):
    # The store opens a connection per call and holds none between calls, so
    # close() is a documented no-op; the context-manager protocol must still
    # work and data written inside the block must be durable after it.
    with make_store(tmp_path) as store:
        row = create_row(store)
    reopened = PendingStore(tmp_path / "pending.sqlite3")
    try:
        assert reopened.get(row.token) is not None
    finally:
        reopened.close()
