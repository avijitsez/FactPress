"""Validated brand-kit loading, with user-override support (F2.2).

FactPress ships one strong default brand kit (``brandkits/default.yaml``);
users may override any subset of it via their own ``brandkit.yaml``
(FACTPRESS_DESIGN.md §2). This module is the validated, Pydantic-backed
entry point for that: :func:`load_kit` loads and validates a kit (the
built-in default, or an arbitrary path), :func:`merge_kit` layers a partial
user override on top of an already-validated base kit, and
:func:`as_engine_dict` converts a validated :class:`BrandKit` back to the
plain dict shape ``engine_svg.load_brandkit`` / ``build_view`` / templates
already consume — the same shape ``yaml.safe_load`` produces for the raw
YAML file.

``factpress.renderer.engine_svg.load_brandkit`` remains a separate, thin
YAML-only loader for internal engine use and is not touched here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

_PALETTE_COLOR_FIELDS = (
    "bg",
    "surface",
    "fg",
    "muted",
    "accent",
    "positive",
    "negative",
    "grad_from",
    "grad_to",
    "chip_bg",
)

_REQUIRED_FONT_KEYS = ("sans", "mono")


class Palette(BaseModel):
    """One named color palette: ten hex-color fields, nothing else."""

    model_config = ConfigDict(extra="forbid")

    bg: str
    surface: str
    fg: str
    muted: str
    accent: str
    positive: str
    negative: str
    grad_from: str
    grad_to: str
    chip_bg: str

    @field_validator(*_PALETTE_COLOR_FIELDS)
    @classmethod
    def _validate_hex(cls, value: Any, info) -> str:
        if not isinstance(value, str) or not _HEX_COLOR_RE.match(value):
            raise ValueError(
                f"palette color {info.field_name!r} must be a hex color "
                f"(#rgb or #rrggbb), got {value!r}"
            )
        return value


class BrandKit(BaseModel):
    """A validated brand kit: fonts, logo/footer copy, and named palettes."""

    model_config = ConfigDict(extra="forbid")

    name: str
    fonts: dict[str, str]
    logo_text: str = Field(max_length=30)
    watermark: bool
    footer: str = Field(max_length=120)
    palettes: dict[str, Palette]

    @field_validator("fonts")
    @classmethod
    def _validate_fonts(cls, value: dict[str, str]) -> dict[str, str]:
        missing = [key for key in _REQUIRED_FONT_KEYS if key not in value]
        if missing:
            raise ValueError(f"fonts is missing required key(s): {missing}")
        for key in _REQUIRED_FONT_KEYS:
            if not isinstance(value[key], str) or not value[key]:
                raise ValueError(f"fonts.{key} must be a non-empty string")
        return value

    @field_validator("palettes")
    @classmethod
    def _validate_palettes_nonempty(cls, value: dict[str, Palette]) -> dict[str, Palette]:
        if not value:
            raise ValueError("palettes must contain at least one entry")
        return value


def _builtin_default_path() -> Path:
    """Return the repo's ``brandkits/default.yaml`` path.

    Resolved relative to this package (``factpress/../brandkits``), mirroring
    ``factpress.catalog.builtin_templates_dir``: correct for a source
    checkout / editable install. Packaged (wheel) install resolution is a
    later phase.
    """
    return Path(__file__).resolve().parent.parent / "brandkits" / "default.yaml"


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"])
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


def _load_yaml_mapping(path: Path, *, what: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{what} not found at {path}: {exc}") from exc

    data = yaml.safe_load(text)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{what} at {path} must be a mapping, got {type(data).__name__}")
    return data


def load_kit(path: str | Path | None = None) -> BrandKit:
    """Load and validate a brand kit from YAML.

    ``path=None`` loads the built-in ``brandkits/default.yaml``. Raises
    ``ValueError`` with a message naming the offending path and field(s) on
    any I/O, parse, or validation failure.
    """
    resolved = _builtin_default_path() if path is None else Path(path)
    data = _load_yaml_mapping(resolved, what="brand kit")

    try:
        return BrandKit.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            f"invalid brand kit at {resolved}:\n{_format_validation_error(exc)}"
        ) from exc


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` onto a copy of ``base``.

    Dict values are merged key by key, recursively — except ``palettes``,
    which merges by palette id: an id present only in ``override`` is added,
    and an id present in both replaces the *whole* palette from ``override``
    (colors are not merged field-by-field within a single palette).
    """
    merged = dict(base)
    for key, value in override.items():
        if (
            key == "palettes"
            and isinstance(value, dict)
            and isinstance(merged.get("palettes"), dict)
        ):
            palettes = dict(merged["palettes"])
            palettes.update(value)
            merged["palettes"] = palettes
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def merge_kit(base: BrandKit, override_path: str | Path) -> BrandKit:
    """Layer a partial user override YAML on top of an already-validated ``base``.

    The override file may specify any subset of brand-kit fields (e.g. just
    ``logo_text`` plus one extra palette). Raises ``ValueError`` naming the
    override path and field(s) on any I/O, parse, or validation failure.
    """
    override_path = Path(override_path)
    override_data = _load_yaml_mapping(override_path, what="brand kit override")

    merged = _deep_merge(base.model_dump(), override_data)
    try:
        return BrandKit.model_validate(merged)
    except ValidationError as exc:
        raise ValueError(
            f"invalid brand kit override at {override_path}:\n{_format_validation_error(exc)}"
        ) from exc


def as_engine_dict(kit: BrandKit) -> dict[str, Any]:
    """Return the plain dict shape the render engine consumes.

    Same keys/shape ``yaml.safe_load`` produces for a brand-kit YAML file —
    what ``engine_svg.load_brandkit`` returns today, and what
    ``build_view``/templates already expect.
    """
    return kit.model_dump(mode="python")
