"""SVG/PNG rendering engine (F0.5).

Builds the render context defined in ``docs/rendering-contract.md``
from a facts payload + design spec + brandkit, renders it through the
template's ``template.svg.j2`` (Jinja2, ``StrictUndefined`` + autoescape),
and rasterizes to PNG via ``resvg_py`` with fully hermetic font settings
(``skip_system_fonts=True`` + explicit vendored ``font_dirs``) so the same
inputs produce byte-identical output on every machine and every run.

No formatting logic lives here beyond key resolution: every numeral is
formatted by ``factpress.renderer.format`` before it lands in ``view``.
"""

from __future__ import annotations

import re
from datetime import UTC
from pathlib import Path
from typing import Any

import resvg_py
import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from factpress.renderer import format as fmt
from factpress.renderer.sparkline import build_paths
from factpress.schemas import DailyPnlFacts, DesignSpec, FactPayload

_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_TEMPLATE_FILE = "template.svg.j2"
_SPARKLINE_W = 420
_SPARKLINE_H = 96

_DIRECTION_TO_COLOR_ROLE = {"up": "positive", "down": "negative", "flat": "neutral"}
_REFLECTION_CAP = 220


def _truncate_reflection(text: str, cap: int = _REFLECTION_CAP) -> str:
    """Deterministically truncate ``text`` to ``cap`` chars on a word boundary.

    Renderer's slot cap for the ``reflection`` view field: text at or under
    the cap passes through untouched; longer text is cut back to the last
    whitespace at-or-before the cap and gets a trailing "…" so the trim is
    visible. Never truncates mid-word.
    """
    if len(text) <= cap:
        return text
    truncated = text[:cap]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated.rstrip() + "…"


def load_manifest(template_dir: Path) -> dict[str, Any]:
    """Load and minimally validate a template's ``manifest.yaml``.

    Checks that ``id``, a semver ``version``, a non-empty ``sizes`` mapping,
    and a non-empty ``palettes_allowed`` list are all present.
    """
    path = Path(template_dir) / "manifest.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"manifest at {path} must be a mapping")

    for key in ("id", "version", "sizes", "palettes_allowed"):
        if key not in data:
            raise ValueError(f"manifest at {path} missing required key {key!r}")

    if not isinstance(data["id"], str) or not data["id"]:
        raise ValueError(f"manifest at {path}: 'id' must be a non-empty string")

    if not _SEMVER_RE.match(str(data["version"])):
        raise ValueError(f"manifest at {path}: version {data['version']!r} is not valid semver")

    if not isinstance(data["sizes"], dict) or not data["sizes"]:
        raise ValueError(f"manifest at {path}: 'sizes' must be a non-empty mapping")

    if not isinstance(data["palettes_allowed"], list) or not data["palettes_allowed"]:
        raise ValueError(f"manifest at {path}: 'palettes_allowed' must be a non-empty list")

    return data


def load_brandkit(path: Path) -> dict[str, Any]:
    """Thin brandkit loader: parse the YAML file and return it as a dict.

    A full-featured brandkit module (validation, defaults, inheritance) is a
    later phase; here we just load what F0.6 templates need.
    """
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"brandkit at {path} must be a mapping")
    return data


def _facts_value(facts: FactPayload, key: str) -> float | int:
    """Resolve ``key`` against ``facts`` (declared or extra fields).

    Raises ``KeyError`` naming the available metric keys when ``key`` is not
    a usable numeric metric.
    """
    available = facts.metric_keys()
    if key not in available:
        raise KeyError(
            f"metric key {key!r} not found in facts; available metric keys: "
            f"{sorted(available)}"
        )
    return dict(facts)[key]


def _resolve_metric(facts: FactPayload, key: str, currency: str) -> tuple[str, str, float | int]:
    raw = _facts_value(facts, key)
    label, value = fmt.format_metric(key, raw, currency=currency)
    return label, value, raw


def _coerce_facts(facts: FactPayload | dict[str, Any]) -> FactPayload:
    """Accept a ``FactPayload`` instance or a plain dict at public entry points."""
    if isinstance(facts, FactPayload):
        return facts
    if isinstance(facts, dict):
        if facts.get("event_type") == "daily_pnl":
            return DailyPnlFacts.model_validate(facts)
        return FactPayload.model_validate(facts)
    raise TypeError(f"facts must be a FactPayload or dict, got {type(facts).__name__}")


def build_view(facts: FactPayload, spec: DesignSpec, brandkit: dict[str, Any]) -> dict[str, Any]:
    """Build the ``view`` sub-context per ``docs/rendering-contract.md``."""
    currency = getattr(facts, "currency", None) or "USD"

    hero_label, hero_value, hero_raw = _resolve_metric(facts, spec.hero_metric_key, currency)
    hero_direction = fmt.direction(hero_raw)
    hero = {
        "label": hero_label,
        "value": hero_value,
        "direction": hero_direction,
        "color_role": _DIRECTION_TO_COLOR_ROLE[hero_direction],
    }

    delta_chips = []
    for key in spec.emphasis_keys:
        label, value, raw = _resolve_metric(facts, key, currency)
        delta_chips.append({"label": label, "value": value, "direction": fmt.direction(raw)})

    callouts = []
    for key in spec.callout_keys:
        label, value, _raw = _resolve_metric(facts, key, currency)
        callouts.append({"label": label, "value": value})

    series = getattr(facts, "series", None)
    sparkline = None
    if spec.sparkline and isinstance(series, list) and len(series) >= 2:
        path, area_path = build_paths(series, _SPARKLINE_W, _SPARKLINE_H)
        sparkline = {"path": path, "area_path": area_path, "w": _SPARKLINE_W, "h": _SPARKLINE_H}

    as_of = None
    if facts.as_of is not None:
        # Aware datetimes are converted to UTC before formatting — printing a
        # local wall-clock time under a "UTC" label would put a false fact on
        # the image. Naive datetimes are rendered as-is with no zone label.
        if facts.as_of.tzinfo is not None:
            as_of = fmt.format_timestamp(facts.as_of.astimezone(UTC), tz_label="UTC")
        else:
            as_of = fmt.format_timestamp(facts.as_of)

    reflection = None
    candidates = getattr(facts, "reflection_candidates", None)
    if spec.reflection_index is not None and isinstance(candidates, list):
        reflection = _truncate_reflection(candidates[spec.reflection_index])

    # Whitelisted host-authored text passthrough: list/string facts that
    # don't fit the numeric hero/delta_chips/callouts machinery (e.g.
    # watchlist symbols, plan notes, regime labels) but still need to reach
    # templates. Strictly this fixed whitelist -- no per-template logic here.
    # These are host-authored facts, not LLM copy: the director never
    # touches them, so the digit-ban / key-resolution guardrail is intact.
    text_lists = {
        k: list(getattr(facts, k))
        for k in ("watchlist_symbols", "plan_notes")
        if getattr(facts, k, None)
    }
    text_fields = {
        k: str(getattr(facts, k))
        for k in ("regime", "session", "label")
        if getattr(facts, k, None)
    }

    # Structured facts-passthrough for DigestTopPicksFacts: a ranked list of
    # PickItem objects doesn't fit the numeric hero/delta_chips/callouts
    # machinery (score/direction/note aren't independently-keyed metrics),
    # so it's whitelisted through like text_lists/text_fields above. Rank is
    # a renderer-formatted ordinal string (digits from the renderer, not LLM
    # copy, so the digit-ban is untouched); everything else is a verbatim
    # host-authored fact.
    picks = getattr(facts, "picks", None)
    view_picks = None
    if isinstance(picks, list) and picks:
        view_picks = [
            {
                "rank": str(i),
                "symbol": p.symbol,
                "score_str": fmt.format_number(p.score, precision=2),
                "direction": p.direction,
                "note": p.note,
            }
            for i, p in enumerate(picks, 1)
        ]

    return {
        "headline": spec.headline,
        "subhead": spec.subhead,
        "emoji": None,
        "hero": hero,
        "delta_chips": delta_chips,
        "callouts": callouts,
        "sparkline": sparkline,
        "as_of": as_of,
        "footer": brandkit["footer"],
        "reflection": reflection,
        "text_lists": text_lists,
        "text_fields": text_fields,
        "picks": view_picks,
    }


def _validate_spec_against_manifest(
    spec: DesignSpec, manifest: dict[str, Any], brandkit: dict[str, Any], size: str
) -> None:
    if spec.template_id != manifest["id"]:
        raise ValueError(
            f"spec.template_id {spec.template_id!r} does not match manifest id {manifest['id']!r}"
        )

    spec_major = spec.template_version.split(".")[0]
    manifest_major = str(manifest["version"]).split(".")[0]
    if spec_major != manifest_major:
        raise ValueError(
            f"spec.template_version {spec.template_version!r} (major {spec_major}) is "
            f"incompatible with manifest version {manifest['version']!r} (major {manifest_major})"
        )

    if spec.palette_id not in manifest["palettes_allowed"]:
        raise ValueError(
            f"palette_id {spec.palette_id!r} is not in manifest palettes_allowed "
            f"{manifest['palettes_allowed']!r}"
        )

    palettes = brandkit.get("palettes", {})
    if spec.palette_id not in palettes:
        raise ValueError(
            f"palette_id {spec.palette_id!r} not found in brandkit palettes "
            f"{sorted(palettes)!r}"
        )

    if size not in manifest["sizes"]:
        raise ValueError(f"unknown size {size!r}; manifest sizes: {sorted(manifest['sizes'])!r}")


def render_svg(
    facts: FactPayload | dict[str, Any],
    spec: DesignSpec,
    *,
    template_dir: Path,
    brandkit: dict[str, Any],
    size: str = "feed",
) -> str:
    """Render the full SVG markup for one (facts, spec) pair at ``size``."""
    facts = _coerce_facts(facts)
    template_dir = Path(template_dir)
    manifest = load_manifest(template_dir)
    _validate_spec_against_manifest(spec, manifest, brandkit, size)

    width, height = manifest["sizes"][size]
    view = build_view(facts, spec, brandkit)
    brand = {
        "fonts": brandkit["fonts"],
        "logo_text": brandkit["logo_text"],
        "watermark": brandkit["watermark"],
        "palette": brandkit["palettes"][spec.palette_id],
    }
    context = {
        "W": width,
        "H": height,
        "size": size,
        "spec": spec.model_dump(mode="json"),
        "brand": brand,
        "view": view,
    }

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=True,
        undefined=StrictUndefined,
    )
    template = env.get_template(_TEMPLATE_FILE)
    return template.render(**context)


def render_png(
    facts: FactPayload | dict[str, Any],
    spec: DesignSpec,
    *,
    template_dir: Path,
    brandkit: dict[str, Any] | str | Path,
    size: str = "feed",
) -> bytes:
    """Render to PNG bytes via a hermetic, deterministic resvg invocation.

    ``brandkit`` may be a pre-loaded dict or a path to a brandkit YAML file.
    """
    template_dir = Path(template_dir)
    brandkit_dict = (
        load_brandkit(Path(brandkit)) if isinstance(brandkit, (str, Path)) else brandkit
    )
    svg = render_svg(facts, spec, template_dir=template_dir, brandkit=brandkit_dict, size=size)
    manifest = load_manifest(template_dir)
    width, height = manifest["sizes"][size]

    return resvg_py.svg_to_bytes(
        svg_string=svg,
        width=width,
        height=height,
        skip_system_fonts=True,
        font_dirs=[str(_FONTS_DIR)],
    )
