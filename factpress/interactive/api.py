"""F5.3-F5.6 interactive approval API (FACTPRESS_DESIGN.md §7).

``InteractiveManager`` wires the pending-approvals store (F5.2), the
state-layer renderer (F5.1), and the Telegram publisher (F2.1/F5.4) into
the two-stage-acknowledgement lifecycle: publish a PENDING card with an
inline keyboard, react to a tap with an instant toast + decision-state
re-render, and let the host's own execution path push a final
EXECUTED/FAILED re-render once it knows the outcome. Timeouts and a
restart sweep both resolve to an EXPIRED re-render, never a dangling
live-looking card.

Ordering note (F5.3): Telegram's inline keyboard buttons must carry the
single-use decision token, but that token is only minted by
``PendingStore.create`` -- which itself wants the Telegram ``message_id``
to persist alongside it, and *that* doesn't exist until after
``send_photo`` returns. Resolved by creating the pending row with a
``message_id=0`` placeholder first (so the token exists to put on the
buttons), sending the photo with the token-bearing keyboard, then patching
in the real ``message_id`` via
:meth:`~factpress.interactive.store.PendingStore.update_message_id`.

Callback encoding: each button's ``callback_data`` is
``f"{token}:{action_id}"`` -- the bare token cannot distinguish which of a
card's several buttons was tapped (Approve vs Reject share one row), so
the action id rides alongside it (well under Telegram's 64-byte cap: a
``secrets.token_urlsafe(32)`` token is 43 chars, plus a separator and a
short action id). It is validated against the row's own declared
``actions`` before anything else runs -- an unrecognized action id is
treated exactly like an unknown token.

``on_decision`` callbacks are kept in an in-memory ``{token: callable}``
dict -- they do not survive a process restart. After a restart,
:meth:`InteractiveManager.resume_after_restart` still re-renders any
swept-orphan cards as EXPIRED and returns their tokens, but no
``on_decision`` fires for them; a host that needs guaranteed delivery
across restarts should treat the returned token list, not the callback,
as the source of truth.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from factpress.interactive.store import PendingApproval, PendingStore
from factpress.pipeline import _template_dir_for, direct_spec
from factpress.publisher import Publisher
from factpress.renderer.engine_svg import load_brandkit, render_png
from factpress.resources import builtin_root
from factpress.schemas import CardState, DesignSpec, EVENT_MODELS, FactPayload, StateInfo

logger = logging.getLogger("factpress.interactive")

# Default decision action ids -> the CardState they stamp. Hosts using
# other action ids (e.g. confirm/cancel for a kill-switch gate) supply an
# ``action_states`` mapping at InteractiveManager construction; every action
# id must resolve to APPROVED or REJECTED before a card can be published.
_DEFAULT_ACTION_STATES = {"approve": CardState.APPROVED, "reject": CardState.REJECTED}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DecisionResult:
    """The outcome of one interactive-card decision -- a tap or an expiry.

    ``user_id`` is the tapping user's Telegram id for a real decision, and
    ``None`` for an expiry-driven one (``expired=True``): the clock fired
    the ``default_action``, not a person. ``approval`` is the store row as
    it stood immediately after the decision/expiry was recorded.
    """

    token: str
    decision: str
    user_id: int | None
    decided_at: datetime
    approval: PendingApproval
    expired: bool = False


def _caption_for(spec: DesignSpec) -> str | None:
    """Same emoji-prepend rule as ``pipeline.publish``: ``"{emoji} {caption}"``
    when both are set, the emoji alone when only it is set."""
    caption = spec.caption
    if spec.emoji:
        caption = f"{spec.emoji} {caption}" if caption else spec.emoji
    return caption


class InteractiveManager:
    """Wires :class:`PendingStore` + the state-layer renderer + :class:`Publisher`
    into the §7 lifecycle. See the module docstring for the ordering and
    callback-encoding notes.
    """

    def __init__(
        self,
        store: PendingStore,
        publisher: Publisher,
        *,
        template_paths: list[str | Path] | None = None,
        brandkit: dict[str, Any] | str | Path | None = None,
        action_states: dict[str, str] | None = None,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._template_paths = template_paths
        self._brandkit = brandkit
        # Manager-level config (not per-message state): hosts re-supply it at
        # construction, so it survives restarts by the same route the store
        # path does. Extra ids extend the defaults; values must stamp a
        # decided state.
        self._decision_to_state = dict(_DEFAULT_ACTION_STATES)
        for action_id, state_name in (action_states or {}).items():
            state = CardState(state_name)
            if state not in (CardState.APPROVED, CardState.REJECTED):
                raise ValueError(
                    f"action_states[{action_id!r}] must map to 'approved' or "
                    f"'rejected', got {state_name!r}"
                )
            self._decision_to_state[action_id] = state
        # In-memory only -- documented not to survive a restart (see module
        # docstring); resume_after_restart's re-renders never fire these.
        self._on_decision: dict[str, Callable[[DecisionResult], None]] = {}

    def _resolve_brandkit(self) -> dict[str, Any]:
        brandkit = self._brandkit
        if brandkit is None:
            brandkit = builtin_root("brandkits") / "default.yaml"
        return brandkit if isinstance(brandkit, dict) else load_brandkit(Path(brandkit))

    def _load_row_render_inputs(
        self, row: PendingApproval, *, facts_patch: dict[str, Any] | None = None
    ) -> tuple[FactPayload, DesignSpec, Path, dict[str, Any]]:
        """Reconstruct (facts, spec, template_dir, brandkit) from a stored
        row for a re-render. ``facts_patch`` (F5.6 ``update_state``) is
        merged into the stored facts dict *before* model validation, so a
        bogus patch raises exactly like any other invalid fact payload --
        the patched facts go through the same ``EVENT_MODELS`` validation
        as everything else, never a raw-dict shortcut.
        """
        facts_dict = json.loads(row.facts_json)
        if facts_patch:
            facts_dict.update(facts_patch)
        model = EVENT_MODELS.get(row.event_type, FactPayload)
        payload = model.model_validate(facts_dict)
        spec = DesignSpec.model_validate(json.loads(row.spec_json))
        template_dir = _template_dir_for(row.event_type, self._template_paths)
        kit = self._resolve_brandkit()
        return payload, spec, template_dir, kit

    def _render_and_edit(
        self,
        row: PendingApproval,
        state_info: StateInfo,
        *,
        facts_patch: dict[str, Any] | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload, spec, template_dir, kit = self._load_row_render_inputs(
            row, facts_patch=facts_patch
        )
        png = render_png(
            payload,
            spec,
            template_dir=template_dir,
            brandkit=kit,
            size="telegram",
            state=state_info,
        )
        self._publisher.edit_message_media(
            png,
            chat_id=row.chat_id,
            message_id=row.message_id,
            caption=_caption_for(spec),
            reply_markup=reply_markup,
        )

    def _expire_row(self, row: PendingApproval, *, now: datetime) -> DecisionResult:
        """Shared EXPIRED re-render + default-action ``on_decision`` fire,
        used by :meth:`check_expired`, :meth:`resume_after_restart`, and the
        "tap arrived just after the deadline" branch of
        :meth:`handle_callback`.
        """
        state_info = StateInfo(state=CardState.EXPIRED, stamped_at=now)
        self._render_and_edit(row, state_info, reply_markup=None)
        result = DecisionResult(
            token=row.token,
            decision=row.default_action,
            user_id=None,
            decided_at=now,
            approval=row,
            expired=True,
        )
        callback = self._on_decision.pop(row.token, None)
        if callback is not None:
            try:
                callback(result)
            except Exception:
                logger.exception(
                    "on_decision callback raised for expired token=%s", row.token
                )
        return result

    def publish_interactive(
        self,
        facts: FactPayload | dict[str, Any],
        event_type: str,
        *,
        actions: list[tuple[str, str]],
        authorized_users: list[int],
        timeout_s: float,
        default_action: str,
        on_decision: Callable[[DecisionResult], None] | None = None,
        chat_id: int | str | None = None,
        thread_id: int | None = None,
        director: Any | None = None,
        size: str = "telegram",
    ) -> PendingApproval:
        """Render + send a PENDING interactive card and register it for
        decisions. See the module docstring for the message-id/token
        ordering resolution.
        """
        payload, spec, template_dir, kit = direct_spec(
            facts,
            event_type,
            director=director,
            template_paths=self._template_paths,
            brandkit=self._brandkit,
        )

        resolved_chat_id = (
            chat_id if chat_id is not None else self._publisher.config.default_chat_id
        )
        if resolved_chat_id is None:
            raise ValueError(
                "no chat_id: pass publish_interactive(..., chat_id=...) or set "
                "PublisherConfig.default_chat_id"
            )

        # Placeholder message_id=0: the token must exist to put on the
        # buttons before send_photo has returned a real message_id.
        approval = self._store.create(
            chat_id=resolved_chat_id,
            message_id=0,
            thread_id=thread_id,
            event_type=event_type,
            facts_json=payload.model_dump_json(),
            spec_json=spec.model_dump_json(),
            actions=list(actions),
            authorized_users=list(authorized_users),
            timeout_s=timeout_s,
            default_action=default_action,
        )

        png = render_png(
            payload,
            spec,
            template_dir=template_dir,
            brandkit=kit,
            size=size,
            state=StateInfo(state=CardState.PENDING),
        )
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": label, "callback_data": f"{approval.token}:{action_id}"}
                    for action_id, label in actions
                ]
            ]
        }

        ref = self._publisher.send_photo(
            png,
            caption=_caption_for(spec),
            chat_id=resolved_chat_id,
            thread_id=thread_id,
            reply_markup=reply_markup,
        )
        self._store.update_message_id(approval.token, ref.message_id)

        if on_decision is not None:
            self._on_decision[approval.token] = on_decision

        return self._store.get(approval.token)

    def handle_callback(self, update: dict[str, Any]) -> DecisionResult | None:
        """Handle one Telegram ``callback_query`` update per the §7 flow:
        unknown token/action -> refusal toast; unauthorized user -> refusal
        toast (logged, card unchanged); double-tap/expired -> no-op (with an
        EXPIRED re-render if the deadline had just passed); a fresh decision
        -> toast + first visual ack (decision-state re-render, keyboard
        removed) + ``on_decision`` fire.
        """
        cq = update.get("callback_query")
        if not cq:
            return None
        callback_id = cq["id"]
        user_id = cq["from"]["id"]
        token, _, action_id = cq.get("data", "").partition(":")

        row = self._store.get(token)
        valid_actions = {aid for aid, _label in row.actions} if row is not None else set()
        if row is None or action_id not in valid_actions or action_id not in self._decision_to_state:
            self._publisher.answer_callback_query(callback_id, "This card is no longer active.")
            return None

        if user_id not in row.authorized_users:
            self._publisher.answer_callback_query(
                callback_id, "You are not authorized to decide this.", show_alert=True
            )
            logger.warning(
                "unauthorized decision attempt: user_id=%s token=%s action=%s",
                user_id,
                token,
                action_id,
            )
            return None

        now = _utcnow()
        decided = self._store.consume(token, user_id=user_id, decision=action_id, now=now)
        if decided is None:
            current = self._store.get(token)
            if current is not None and current.status == "expired":
                self._expire_row(current, now=now)
                self._publisher.answer_callback_query(
                    callback_id, "This card is no longer active."
                )
            else:
                self._publisher.answer_callback_query(callback_id, "Already decided.")
            return None

        self._publisher.answer_callback_query(callback_id, "Got it — applying…")

        from_user = cq.get("from") or {}
        decider_display = from_user.get("first_name") or str(user_id)
        state_info = StateInfo(
            state=self._decision_to_state[action_id],
            decider=decider_display,
            stamped_at=now,
        )
        self._render_and_edit(decided, state_info, reply_markup=None)

        result = DecisionResult(
            token=token,
            decision=action_id,
            user_id=user_id,
            decided_at=now,
            approval=decided,
            expired=False,
        )
        callback = self._on_decision.pop(token, None)
        if callback is not None:
            try:
                callback(result)
            except Exception:
                logger.exception("on_decision callback raised for token=%s", token)

        return result

    def update_state(
        self,
        token_or_approval: str | PendingApproval,
        *,
        state: Literal["executed", "failed"],
        facts_patch: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> None:
        """Second visual ack (F5.6): the host's execution path reports the
        real outcome. ``facts_patch`` is merged into the stored facts and
        revalidated through ``EVENT_MODELS`` before anything is sent -- a
        bogus patch raises and neither the edit nor
        :meth:`~factpress.interactive.store.PendingStore.mark_finalized`
        happens.
        """
        token = (
            token_or_approval.token
            if isinstance(token_or_approval, PendingApproval)
            else token_or_approval
        )
        row = self._store.get(token)
        if row is None:
            raise ValueError(f"unknown token: {token!r}")

        card_state = CardState.EXECUTED if state == "executed" else CardState.FAILED
        now = _utcnow()
        state_info = StateInfo(state=card_state, note=note, stamped_at=now)
        self._render_and_edit(row, state_info, facts_patch=facts_patch, reply_markup=None)
        self._store.mark_finalized(token, decision_note=note)

    def check_expired(self, now: datetime | None = None) -> list[str]:
        """Sweep still-pending rows whose deadline has passed: mark expired,
        re-render EXPIRED, fire ``on_decision`` with the row's
        ``default_action`` flagged ``expired=True``. Returns the tokens
        expired this pass.
        """
        now = now or _utcnow()
        tokens: list[str] = []
        for row in self._store.pending_older_than_timeout(now=now):
            marked = self._store.mark_expired(row.token)
            if marked is None:
                continue
            self._expire_row(marked, now=now)
            tokens.append(marked.token)
        return tokens

    def resume_after_restart(self, now: datetime | None = None) -> list[str]:
        """Startup pass (§7 trust properties): sweep rows orphaned by a
        restart (still 'pending' past their deadline) and re-render each as
        EXPIRED rather than leave a dangling, live-looking card. Returns the
        tokens swept.
        """
        now = now or _utcnow()
        tokens: list[str] = []
        for row in self._store.sweep_restart_orphans(now=now):
            self._expire_row(row, now=now)
            tokens.append(row.token)
        return tokens
