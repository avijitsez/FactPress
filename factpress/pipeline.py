"""Pipeline (F1.5, partial): facts -> (director | fallback) -> renderer.

``render`` wires the three-stage separation end to end for one event:
validate the facts, obtain a ``DesignSpec`` (from the quarantined director
when one is configured, else the deterministic fallback), and rasterize.
The director can never block or break a notification: ``Director.design``
returns ``fallback_spec`` on any failure, so ``render`` succeeds whenever
the facts themselves are renderable.

Publishing (Telegram) joins in F2; the ``FactPress`` facade class arrives
with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factpress.catalog import builtin_templates_dir, catalog_entry, discover_templates
from factpress.director import Director, fallback_spec
from factpress.renderer.engine_svg import load_brandkit, render_png
from factpress.schemas import DailyPnlFacts, FactPayload, TradeExecutedFacts

_EVENT_MODELS: dict[str, type[FactPayload]] = {
    "daily_pnl": DailyPnlFacts,
    "trade_executed": TradeExecutedFacts,
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

    With a ``director``, the spec is LLM-art-directed (worst case: the
    director's own deterministic fallback). Without one, the fallback spec
    is used directly — the zero-LLM path. ``brandkit`` accepts a loaded
    dict, a path, or ``None`` for the built-in default kit.
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

    return render_png(payload, spec, template_dir=template_dir, brandkit=kit, size=size)
