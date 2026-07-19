"""FactPress: facts in, identical prints out.

``FactPress`` (F2.4) is the whole plug-n-play public surface described in
FACTPRESS_DESIGN.md §3 -- a thin facade over :mod:`factpress.pipeline`
that builds the (optional) ``Director`` and (optional) ``Publisher`` once,
from constructor kwargs, and exposes ``render``/``publish``.

Import time stays light: constructing ``FactPress`` builds plain client
objects (no network calls, no LLM round-trips); those only happen when
``render``/``publish`` are called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

import threading

from factpress import pipeline
from factpress.director import Director, DirectorConfig, fallback_spec
from factpress.interactive.api import DecisionResult, InteractiveManager
from factpress.interactive.poller import Poller
from factpress.interactive.store import PendingApproval, PendingStore
from factpress.publisher import MessageRef, PublishError, Publisher, PublisherConfig
from factpress.schemas import (
    CardState,
    DailyPnlFacts,
    DesignSpec,
    FactPayload,
    StateInfo,
    TradeExecutedFacts,
)

__version__ = "0.2.0"

__all__ = [
    "FactPress",
    "MessageRef",
    "PublishError",
    "DesignSpec",
    "FactPayload",
    "DailyPnlFacts",
    "TradeExecutedFacts",
    "Director",
    "DirectorConfig",
    "fallback_spec",
    "CardState",
    "StateInfo",
    "InteractiveManager",
    "DecisionResult",
    "PendingApproval",
    "PendingStore",
    "Poller",
    "__version__",
]


class FactPress:
    """The public facade: facts in, rendered PNGs / published Telegram cards out.

    ``Director`` is built only when both ``llm_base_url`` and ``llm_model``
    are given -- without them, ``render``/``publish`` use the deterministic
    zero-LLM fallback spec directly. ``Publisher`` is built only when
    ``telegram_token`` is given; calling :meth:`publish` without one raises
    ``RuntimeError`` naming the missing constructor argument.

    ``_director_transport``/``_publisher_transport`` are a private testing
    seam: pass an ``httpx.BaseTransport`` (e.g. ``httpx.MockTransport``) to
    have the constructed ``Director``/``Publisher`` use it instead of a real
    ``httpx.Client``, without monkeypatching ``httpx`` globally. Not part of
    the public API.
    """

    def __init__(
        self,
        llm_base_url: str | None = None,
        llm_model: str | None = None,
        llm_api_key: str | None = None,
        telegram_token: str | None = None,
        default_chat_id: int | str | None = None,
        brandkit: dict[str, Any] | str | Path | None = None,
        template_paths: list[str | Path] | None = None,
        silent_hours: tuple[int, int] | None = None,
        pending_store: str | Path = "factpress_pending.sqlite3",
        *,
        _director_transport: httpx.BaseTransport | None = None,
        _publisher_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._brandkit = brandkit
        self._template_paths = template_paths
        self._pending_store_path = pending_store

        self._director: Director | None = None
        if llm_base_url and llm_model:
            director_config = DirectorConfig(
                base_url=llm_base_url, model=llm_model, api_key=llm_api_key
            )
            self._director = Director(director_config, transport=_director_transport)

        # Kept (not just consumed) so run_poller() can build its Poller over
        # the same transport seam the Publisher itself uses (F5.7).
        self._publisher_transport = _publisher_transport

        self._publisher: Publisher | None = None
        if telegram_token:
            publisher_config = PublisherConfig(
                token=telegram_token,
                default_chat_id=default_chat_id,
                silent_hours=silent_hours,
            )
            self._publisher = Publisher(publisher_config, transport=_publisher_transport)

        # F5: the pending-approvals store + InteractiveManager are built
        # lazily on first interactive-method use (see _get_interactive), so
        # constructing a FactPress never touches disk for pending_store.
        self._interactive: InteractiveManager | None = None

    def render(
        self,
        facts: FactPayload | dict[str, Any],
        event_type: str,
        *,
        size: str = "feed",
    ) -> bytes:
        """Render ``facts`` for ``event_type`` to PNG bytes. See :func:`factpress.pipeline.render`."""
        return pipeline.render(
            facts,
            event_type,
            director=self._director,
            template_paths=self._template_paths,
            brandkit=self._brandkit,
            size=size,
        )

    def publish(
        self,
        facts: FactPayload | dict[str, Any],
        event_type: str,
        *,
        chat_id: int | str | None = None,
        thread_id: int | None = None,
        size: str = "telegram",
        silent: bool | None = None,
    ) -> MessageRef:
        """Render ``facts`` for ``event_type`` and publish to Telegram.

        Raises ``RuntimeError`` if this ``FactPress`` was constructed
        without ``telegram_token`` (there is no ``Publisher`` to send with).
        """
        if self._publisher is None:
            raise RuntimeError(
                "FactPress.publish() requires a telegram_token; construct "
                "FactPress(telegram_token=..., ...) or use FactPress.render() "
                "to get PNG bytes without publishing."
            )
        return pipeline.publish(
            facts,
            event_type,
            publisher=self._publisher,
            director=self._director,
            template_paths=self._template_paths,
            brandkit=self._brandkit,
            size=size,
            chat_id=chat_id,
            thread_id=thread_id,
            silent=silent,
        )

    def _get_interactive(self) -> InteractiveManager:
        """Lazily construct the one :class:`InteractiveManager` this facade
        uses, and with it the on-disk :class:`PendingStore` -- deferred
        until an interactive method is actually called, so a plain
        ``FactPress()`` (or one only used for ``render``/``publish``) never
        creates the pending-store sqlite file.
        """
        if self._interactive is None:
            if self._publisher is None:
                raise RuntimeError(
                    "interactive approval methods require a telegram_token; "
                    "construct FactPress(telegram_token=..., ...)."
                )
            store = PendingStore(self._pending_store_path)
            self._interactive = InteractiveManager(
                store,
                self._publisher,
                template_paths=self._template_paths,
                brandkit=self._brandkit,
            )
        return self._interactive

    def publish_interactive(
        self,
        facts: FactPayload | dict[str, Any],
        event_type: str,
        *,
        actions: list[tuple[str, str]],
        authorized_users: list[int],
        timeout_s: float,
        default_action: str,
        on_decision: Any = None,
        chat_id: int | str | None = None,
        thread_id: int | None = None,
        size: str = "telegram",
    ) -> PendingApproval:
        """Publish an interactive approval card. See
        ``FACTPRESS_DESIGN.md`` §7 and :meth:`InteractiveManager.publish_interactive`.
        """
        return self._get_interactive().publish_interactive(
            facts,
            event_type,
            actions=actions,
            authorized_users=authorized_users,
            timeout_s=timeout_s,
            default_action=default_action,
            on_decision=on_decision,
            chat_id=chat_id,
            thread_id=thread_id,
            director=self._director,
            size=size,
        )

    def handle_callback(self, update: dict[str, Any]) -> DecisionResult | None:
        """Handle one Telegram callback_query update. See
        :meth:`InteractiveManager.handle_callback`."""
        return self._get_interactive().handle_callback(update)

    def update_state(
        self,
        token_or_approval: str | PendingApproval,
        *,
        state: str,
        facts_patch: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> None:
        """Second visual ack. See :meth:`InteractiveManager.update_state`."""
        self._get_interactive().update_state(
            token_or_approval, state=state, facts_patch=facts_patch, note=note
        )

    def check_expired(self, now: Any = None) -> list[str]:
        """Sweep + re-render timed-out cards. See
        :meth:`InteractiveManager.check_expired`."""
        return self._get_interactive().check_expired(now=now)

    def resume_after_restart(self, now: Any = None) -> list[str]:
        """Startup sweep for restart-orphaned cards. See
        :meth:`InteractiveManager.resume_after_restart`."""
        return self._get_interactive().resume_after_restart(now=now)

    def run_poller(
        self,
        *,
        stop_event: threading.Event | None = None,
        poll_timeout_s: int = 25,
        expiry_sweep_every_s: float = 15.0,
    ) -> None:
        """Blocking standalone long-poll loop (F5.7): the ``ff.run_poller()``
        half of §7's "Integration surface" for hosts with no dispatcher of
        their own (use :meth:`handle_callback` instead if you already run
        one). Builds a :class:`~factpress.interactive.poller.Poller` from
        this facade's own publisher config + manager -- see
        :meth:`factpress.interactive.poller.Poller.run`.
        """
        if self._publisher is None:
            raise RuntimeError(
                "FactPress.run_poller() requires a telegram_token; construct "
                "FactPress(telegram_token=..., ...)."
            )
        poller = Poller(
            self._get_interactive(),
            self._publisher.config,
            transport=self._publisher_transport,
            poll_timeout_s=poll_timeout_s,
            expiry_sweep_every_s=expiry_sweep_every_s,
        )
        poller.run(stop_event=stop_event)
