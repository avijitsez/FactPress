"""F5 pending-approvals store: SQLite persistence for interactive cards.

Per FACTPRESS_DESIGN.md §7, a published interactive card carries only a
single-use decision **token** in its inline keyboard (callback_data is
64-byte-capped on Telegram; ``secrets.token_urlsafe(32)`` yields 43
ASCII chars, well under that). Everything else -- who may decide, what
happens on timeout, the eventual decision/audit stamp -- lives here,
keyed by that token, so:

* a double-tap or a race between two taps on the same card is a no-op
  (exactly one caller ever gets a non-``None`` :meth:`PendingStore.consume`)
* the pending set survives a process restart (it is a file on disk, not
  in-memory state)
* a token whose card was shown by a process that no longer exists
  re-renders as EXPIRED on the next startup sweep rather than dangling
  with live-looking buttons that nothing will ever answer

This module is **pure persistence**: no Telegram, no rendering, no
engine/director imports. It knows nothing about how a card looks or how
a callback arrives -- callers hand it plain values and get plain
dataclasses back.

Thread-safety: every method opens its own short-lived
``sqlite3.connect(..., check_same_thread=False)`` connection and wraps
mutations in an explicit ``BEGIN IMMEDIATE`` / ``COMMIT`` transaction
with ``PRAGMA busy_timeout`` set. This pushes serialization of
concurrent writers down into SQLite's own file-level write lock (a
writer blocks until the lock is free, retried up to the busy timeout)
rather than a Python-level ``threading.Lock``. Two consequences: (1) the
single-use guarantee in :meth:`PendingStore.consume` holds even across
separate OS processes sharing the same database file, not just threads
in one process, and (2) the atomicity of "check pending + flip status"
is a single SQL statement (see :meth:`PendingStore.consume`), so there
is no read-then-write gap for a race to land in.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_approvals (
    token TEXT PRIMARY KEY,
    chat_id,
    message_id INTEGER NOT NULL,
    thread_id INTEGER,
    event_type TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    actions TEXT NOT NULL,
    authorized_users TEXT NOT NULL,
    created_at TEXT NOT NULL,
    timeout_at TEXT NOT NULL,
    default_action TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT,
    decided_by INTEGER,
    decided_at TEXT
)
"""
# ``chat_id`` deliberately has no declared type (SQLite "BLOB affinity"):
# Telegram chat ids are int | str, and an untyped column stores whichever
# storage class was bound without coercing it, so the round-trip preserves
# the original Python type instead of stringifying every int chat_id.


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """ISO-8601 UTC, always with an explicit microseconds field so that
    lexicographic string comparison (used directly in SQL WHERE clauses
    below) agrees with chronological order regardless of whether a given
    timestamp happens to fall on a whole second."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


@dataclass
class PendingApproval:
    """One row of the pending-approvals table.

    ``actions`` and ``authorized_users`` are plain Python objects here
    (a list of ``(action_id, label)`` tuples, a list of user ids) --
    :class:`PendingStore` handles JSON encoding for storage.
    ``facts_json``/``spec_json`` are, per their name, already-serialized
    JSON strings handed through as-is (the host owns that shape; this
    store does not parse it).
    """

    token: str
    chat_id: int | str
    message_id: int
    thread_id: int | None
    event_type: str
    facts_json: str
    spec_json: str
    actions: list[tuple[str, str]]
    authorized_users: list[int]
    created_at: datetime
    timeout_at: datetime
    default_action: str
    status: str = "pending"
    decision: str | None = None
    decided_by: int | None = None
    decided_at: datetime | None = None


def _row_to_approval(row: sqlite3.Row) -> PendingApproval:
    return PendingApproval(
        token=row["token"],
        chat_id=row["chat_id"],
        message_id=row["message_id"],
        thread_id=row["thread_id"],
        event_type=row["event_type"],
        facts_json=row["facts_json"],
        spec_json=row["spec_json"],
        actions=[tuple(pair) for pair in json.loads(row["actions"])],
        authorized_users=list(json.loads(row["authorized_users"])),
        created_at=_parse_iso(row["created_at"]),
        timeout_at=_parse_iso(row["timeout_at"]),
        default_action=row["default_action"],
        status=row["status"],
        decision=row["decision"],
        decided_by=row["decided_by"],
        decided_at=_parse_iso(row["decided_at"]),
    )


class PendingStore:
    """SQLite-backed persistence for pending interactive approvals (F5)."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._init_lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path, timeout=30.0, check_same_thread=False, isolation_level=None
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._init_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(_SCHEMA)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def close(self) -> None:
        """No persistent connection is held open (see module docstring);
        provided for symmetry and the context-manager protocol."""

    def __enter__(self) -> PendingStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def create(
        self,
        *,
        chat_id: int | str,
        message_id: int,
        thread_id: int | None = None,
        event_type: str,
        facts_json: str,
        spec_json: str,
        actions: list[tuple[str, str]],
        authorized_users: list[int],
        timeout_s: float,
        default_action: str,
        now: datetime | None = None,
    ) -> PendingApproval:
        """Generate a fresh single-use token and persist a pending row."""
        now = now or _utcnow()
        approval = PendingApproval(
            token=secrets.token_urlsafe(32),
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
            event_type=event_type,
            facts_json=facts_json,
            spec_json=spec_json,
            actions=list(actions),
            authorized_users=list(authorized_users),
            created_at=now,
            timeout_at=now + timedelta(seconds=timeout_s),
            default_action=default_action,
            status="pending",
        )
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO pending_approvals (
                    token, chat_id, message_id, thread_id, event_type,
                    facts_json, spec_json, actions, authorized_users,
                    created_at, timeout_at, default_action, status,
                    decision, decided_by, decided_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    approval.token,
                    approval.chat_id,
                    approval.message_id,
                    approval.thread_id,
                    approval.event_type,
                    approval.facts_json,
                    approval.spec_json,
                    json.dumps(approval.actions),
                    json.dumps(approval.authorized_users),
                    _iso(approval.created_at),
                    _iso(approval.timeout_at),
                    approval.default_action,
                    approval.status,
                    approval.decision,
                    approval.decided_by,
                    None,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return approval

    def get(self, token: str) -> PendingApproval | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE token = ?", (token,)
            ).fetchone()
        finally:
            conn.close()
        return _row_to_approval(row) if row is not None else None

    def consume(
        self,
        token: str,
        *,
        user_id: int,
        decision: str,
        now: datetime | None = None,
    ) -> PendingApproval | None:
        """Atomically consume a single-use token.

        A single ``UPDATE ... WHERE token = ? AND status = 'pending'``
        does the work: rows that are already decided/finalized/expired,
        or that never existed, match zero rows and this returns ``None``
        (a double-tap or an unknown token is always a no-op). Expiry
        wins over decision -- the SET clause branches on whether
        ``timeout_at`` has already passed at ``now`` and flips to
        'expired' instead of 'decided' in that case, so a decision that
        arrives after the deadline still returns ``None`` (and leaves
        the row correctly marked expired) rather than being honored.
        """
        now = now or _utcnow()
        now_iso = _iso(now)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE pending_approvals
                SET
                    status = CASE WHEN timeout_at < ? THEN 'expired' ELSE 'decided' END,
                    decision = CASE WHEN timeout_at < ? THEN decision ELSE ? END,
                    decided_by = CASE WHEN timeout_at < ? THEN decided_by ELSE ? END,
                    decided_at = CASE WHEN timeout_at < ? THEN decided_at ELSE ? END
                WHERE token = ? AND status = 'pending'
                """,
                (now_iso, now_iso, decision, now_iso, user_id, now_iso, now_iso, token),
            )
            if cur.rowcount == 0:
                conn.execute("COMMIT")
                return None
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE token = ?", (token,)
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        approval = _row_to_approval(row)
        return None if approval.status == "expired" else approval

    def update_message_id(self, token: str, message_id: int) -> PendingApproval | None:
        """Patch in the real Telegram ``message_id`` once ``send_photo``
        returns it (F5.3).

        The pending row is created with a ``message_id=0`` placeholder
        *before* the send, because the single-use token must already exist
        to put on the card's inline-keyboard buttons -- but the token's own
        row wants the message id to persist alongside it, and that id
        doesn't exist until after the send completes. Guarded to 'pending'
        rows only; a no-op (``None``) for an unknown token or one no longer
        'pending'.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE pending_approvals SET message_id = ? WHERE token = ? AND status = 'pending'",
                (message_id, token),
            )
            if cur.rowcount == 0:
                conn.execute("COMMIT")
                return None
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE token = ?", (token,)
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return _row_to_approval(row)

    def mark_finalized(
        self, token: str, *, decision_note: str | None = None
    ) -> PendingApproval | None:
        """Second-stage ack: a decided row becomes 'finalized'. No-op
        (returns ``None``) for an unknown token or one not currently
        'decided'. ``decision_note`` (e.g. execution outcome) is folded
        into the existing ``decision`` text since there is no separate
        column for it."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT decision FROM pending_approvals WHERE token = ? AND status = 'decided'",
                (token,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            new_decision = row["decision"]
            if decision_note:
                new_decision = f"{new_decision}: {decision_note}" if new_decision else decision_note
            conn.execute(
                "UPDATE pending_approvals SET status = 'finalized', decision = ? WHERE token = ?",
                (new_decision, token),
            )
            updated = conn.execute(
                "SELECT * FROM pending_approvals WHERE token = ?", (token,)
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return _row_to_approval(updated)

    def mark_expired(self, token: str) -> PendingApproval | None:
        """Force-expire a still-pending row. No-op for an unknown token
        or one no longer 'pending'."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE pending_approvals SET status = 'expired' WHERE token = ? AND status = 'pending'",
                (token,),
            )
            if cur.rowcount == 0:
                conn.execute("COMMIT")
                return None
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE token = ?", (token,)
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return _row_to_approval(row)

    def pending_older_than_timeout(self, now: datetime | None = None) -> list[PendingApproval]:
        """Still-'pending' rows whose ``timeout_at`` has already passed,
        without mutating them -- for an expiry sweep to inspect before
        acting, or for tests/diagnostics."""
        now = now or _utcnow()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM pending_approvals WHERE status = 'pending' AND timeout_at < ? "
                "ORDER BY created_at",
                (_iso(now),),
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_approval(row) for row in rows]

    def sweep_restart_orphans(self, now: datetime | None = None) -> list[PendingApproval]:
        """Startup expiry sweep: any row still 'pending' whose deadline
        already elapsed becomes 'expired' -- its card should re-render
        EXPIRED on restart rather than keep dangling buttons that a
        now-gone process will never answer. Returns the rows that were
        just flipped, for the caller's re-render pass."""
        now = now or _utcnow()
        now_iso = _iso(now)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            overdue = conn.execute(
                "SELECT token FROM pending_approvals WHERE status = 'pending' AND timeout_at < ?",
                (now_iso,),
            ).fetchall()
            tokens = [row["token"] for row in overdue]
            if tokens:
                conn.executemany(
                    "UPDATE pending_approvals SET status = 'expired' "
                    "WHERE token = ? AND status = 'pending'",
                    [(t,) for t in tokens],
                )
                placeholders = ",".join("?" * len(tokens))
                expired_rows = conn.execute(
                    f"SELECT * FROM pending_approvals WHERE token IN ({placeholders})",
                    tokens,
                ).fetchall()
            else:
                expired_rows = []
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return [_row_to_approval(row) for row in expired_rows]

    def all_pending(self) -> list[PendingApproval]:
        """All rows still genuinely 'pending' (i.e. not yet expired) --
        for the startup pass to resume tracking, after
        :meth:`sweep_restart_orphans` has moved overdue rows out of the
        way."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM pending_approvals WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_approval(row) for row in rows]
