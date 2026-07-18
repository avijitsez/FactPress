"""Pipeline (F1.5 + F2.4): facts -> (director | fallback) -> renderer -> publisher.

``direct_spec`` runs the first two stages -- validate the facts and obtain a
``DesignSpec`` (from the quarantined director when one is configured, else
the deterministic fallback) -- without rasterizing. ``render`` and
``publish`` both build on it: ``render`` rasterizes to PNG bytes, ``publish``
additionally sends the PNG to Telegram via a caller-supplied ``Publisher``.
The director can never block or break a notification: ``Director.design``
returns ``fallback_spec`` on any failure, so both ``render`` and ``publish``
succeed whenever the facts themselves are renderable.

The ``FactPress`` facade class (F2.4) wraps this module for the zero-config
public API; see ``factpress.__init__``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factpress.catalog import builtin_templates_dir, catalog_entry, discover_templates
from factpress.director import Director, fallback_spec
from factpress.publisher import MessageRef, Publisher
from factpress.renderer.engine_svg import load_brandkit, render_png
from factpress.schemas import (
    DailyPnlFacts,
    DesignSpec,
    DigestTopPicksFacts,
    FactPayload,
    MilestoneFacts,
    PulseUpdateFacts,
    ReflectionRecapFacts,
    SessionDigestFacts,
    TradeExecutedFacts,
)

_EVENT_MODELS: dict[str, type[FactPayload]] = {
    "daily_pnl": DailyPnlFacts,
    "trade_executed": TradeExecutedFacts,
    "pulse_update": PulseUpdateFacts,
    "session_digest": SessionDigestFacts,
    "digest_top_picks": DigestTopPicksFacts,
    "milestone": MilestoneFacts,
    "reflection_recap": ReflectionRecapFacts,
}


def _coerce_facts(facts: FactPayload | dict[str, Any], event_type: str) -> FactPayload:
    """Validate ``facts`` for ``event_type``, whichever form they arrive in.

    A dict without ``event_type`` inherits the argument; a mismatch between
    the two is an error, never silently resolved.
    """
    if isinstance(facts, FactPayload):
        if facts.event_type != event_type:
            raise ValueError(
                f"facts.event_type {facts.event_type!r} does not match "
                f"requested event_type {event_type!r}"
            )
        return facts

    if isinstance(facts, dict):
        data = dict(facts)
        declared = data.setdefault("event_type", event_type)
        if declared != event_type:
            raise ValueError(
                f"facts['event_type'] {declared!r} does not match "
                f"requested event_type {event_type!r}"
            )
        model = _EVENT_MODELS.get(event_type, FactPayload)
        return model.model_validate(data)

    raise TypeError(f"facts must be a FactPayload or dict, got {type(facts).__name__}")


def _template_dir_for(event_type: str, template_paths: list[Path] | None) -> Path:
    search = [Path(p) for p in (template_paths or [])] + [builtin_templates_dir()]
    templates = discover_templates(search)
    if event_type not in templates:
        raise ValueError(
            f"no template found for event_type {event_type!r}; "
            f"available templates: {sorted(templates)}"
        )
    return templates[event_type]


def direct_spec(
    facts: FactPayload | dict[str, Any],
    event_type: str,
    *,
    director: Director | None = None,
    template_paths: list[Path] | None = None,
    brandkit: dict[str, Any] | str | Path | None = None,
) -> tuple[FactPayload, DesignSpec, Path, dict[str, Any]]:
    """Validate ``facts`` and obtain a ``DesignSpec`` for ``event_type``, without rasterizing.

    With a ``director``, the spec is LLM-art-directed (worst case: the
    director's own deterministic fallback). Without one, the fallback spec
    is used directly — the zero-LLM path. ``brandkit`` accepts a loaded
    dict, a path, or ``None`` for the built-in default kit.

    Returns ``(payload, spec, template_dir, kit)`` -- everything ``render_png``
    needs, plus the resolved facts/kit for callers (like ``publish``) that
    need them too.
    """
    payload = _coerce_facts(facts, event_type)
    template_dir = _template_dir_for(event_type, template_paths)
    entry = catalog_entry(template_dir)

    if brandkit is None:
        brandkit = builtin_templates_dir().parent / "brandkits" / "default.yaml"
    kit = brandkit if isinstance(brandkit, dict) else load_brandkit(Path(brandkit))

    if director is not None:
        spec = director.design(payload, catalog_entry=entry, brandkit=kit)
    else:
        spec = fallback_spec(payload, manifest=entry, brandkit=kit)

    return payload, spec, template_dir, kit


def render(
    facts: FactPayload | dict[str, Any],
    event_type: str,
    *,
    director: Director | None = None,
    template_paths: list[Path] | None = None,
    brandkit: dict[str, Any] | str | Path | None = None,
    size: str = "feed",
) -> bytes:
    """Render ``facts`` for ``event_type`` to PNG bytes.

    See :func:`direct_spec` for the director/fallback/brandkit semantics.
    """
    payload, spec, template_dir, kit = direct_spec(
        facts, event_type, director=director, template_paths=template_paths, brandkit=brandkit
    )
    return render_png(payload, spec, template_dir=template_dir, brandkit=kit, size=size)


def publish(
    facts: FactPayload | dict[str, Any],
    event_type: str,
    *,
    publisher: Publisher,
    director: Director | None = None,
    template_paths: list[Path] | None = None,
    brandkit: dict[str, Any] | str | Path | None = None,
    size: str = "telegram",
    chat_id: int | str | None = None,
    thread_id: int | None = None,
    silent: bool | None = None,
) -> MessageRef:
    """Render ``facts`` for ``event_type`` and publish the result to Telegram.

    Runs :func:`direct_spec` + ``render_png`` exactly like :func:`render`
    (default size ``"telegram"``, 1280x720), then sends the PNG via
    ``publisher.send_photo``. The caption is ``spec.caption``, with
    ``spec.emoji`` prepended (``"{emoji} {caption}"``) when both are set;
    when only ``spec.emoji`` is set, the caption is the emoji alone. Per
    ``docs/rendering-contract.md``, ``spec.emoji`` is caption-only -- it is
    never drawn into the rendered image.
    """
    payload, spec, template_dir, kit = direct_spec(
        facts, event_type, director=director, template_paths=template_paths, brandkit=brandkit
    )
    png = render_png(payload, spec, template_dir=template_dir, brandkit=kit, size=size)

    caption = spec.caption
    if spec.emoji:
        caption = f"{spec.emoji} {caption}" if caption else spec.emoji

    return publisher.send_photo(
        png, caption=caption, chat_id=chat_id, thread_id=thread_id, silent=silent
    )
