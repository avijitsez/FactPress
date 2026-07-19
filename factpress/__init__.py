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

from factpress import pipeline
from factpress.director import Director, DirectorConfig, fallback_spec
from factpress.publisher import MessageRef, PublishError, Publisher, PublisherConfig
from factpress.schemas import DailyPnlFacts, DesignSpec, FactPayload, TradeExecutedFacts

__version__ = "0.1.0"

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
        *,
        _director_transport: httpx.BaseTransport | None = None,
        _publisher_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._brandkit = brandkit
        self._template_paths = template_paths

        self._director: Director | None = None
        if llm_base_url and llm_model:
            director_config = DirectorConfig(
                base_url=llm_base_url, model=llm_model, api_key=llm_api_key
            )
            self._director = Director(director_config, transport=_director_transport)

        self._publisher: Publisher | None = None
        if telegram_token:
            publisher_config = PublisherConfig(
                token=telegram_token,
                default_chat_id=default_chat_id,
                silent_hours=silent_hours,
            )
            self._publisher = Publisher(publisher_config, transport=_publisher_transport)

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
