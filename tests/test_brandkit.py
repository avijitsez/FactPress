"""Tests for factpress.brandkit (F2.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from factpress.brandkit import BrandKit, as_engine_dict, load_kit, merge_kit

DEFAULT_KIT_PATH = Path(__file__).resolve().parents[1] / "brandkits" / "default.yaml"


def _minimal_kit_dict() -> dict:
    """A small, fully valid brand-kit mapping for override tests."""
    return {
        "name": "default",
        "fonts": {"sans": "Inter", "mono": "JetBrains Mono"},
        "logo_text": "FactPress",
        "watermark": True,
        "footer": "Automated summary",
        "palettes": {
            "midnight": {
                "bg": "#0d1117",
                "surface": "#161b22",
                "fg": "#f0f6fc",
                "muted": "#8b949e",
                "accent": "#58a6ff",
                "positive": "#3fb950",
                "negative": "#f85149",
                "grad_from": "#1f6feb",
                "grad_to": "#8957e5",
                "chip_bg": "#21262d",
            }
        },
    }


def test_default_kit_loads_and_validates():
    kit = load_kit()
    assert isinstance(kit, BrandKit)
    assert kit.name == "default"
    assert kit.fonts["sans"] and kit.fonts["mono"]
    assert "midnight" in kit.palettes


def test_round_trip_equality_with_yaml():
    """The critical guarantee: as_engine_dict(load_kit()) reproduces the raw YAML."""
    raw = yaml.safe_load(DEFAULT_KIT_PATH.read_text(encoding="utf-8"))
    assert as_engine_dict(load_kit()) == raw


def test_bad_hex_rejected_names_palette_id(tmp_path):
    data = _minimal_kit_dict()
    data["palettes"]["midnight"]["accent"] = "not-a-color"
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_kit(bad_path)

    message = str(excinfo.value)
    assert "midnight" in message
    assert "accent" in message


def test_missing_required_font_key_rejected(tmp_path):
    data = _minimal_kit_dict()
    del data["fonts"]["mono"]
    bad_path = tmp_path / "bad_fonts.yaml"
    bad_path.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_kit(bad_path)

    assert "mono" in str(excinfo.value)


def test_partial_override_keeps_other_fields_and_validates(tmp_path):
    base = load_kit()
    override = {
        "logo_text": "MyBrand",
        "palettes": {
            "ember2": {
                "bg": "#1a1210",
                "surface": "#241914",
                "fg": "#fbeee4",
                "muted": "#a68a7a",
                "accent": "#ff8a3d",
                "positive": "#3ddc84",
                "negative": "#ff5c5c",
                "grad_from": "#f7931e",
                "grad_to": "#ff3d68",
                "chip_bg": "#2a1e18",
            }
        },
    }
    override_path = tmp_path / "override.yaml"
    override_path.write_text(yaml.dump(override), encoding="utf-8")

    merged = merge_kit(base, override_path)

    assert isinstance(merged, BrandKit)
    assert merged.logo_text == "MyBrand"
    # untouched fields survive the merge unchanged
    assert merged.name == base.name
    assert merged.fonts == base.fonts
    assert merged.watermark == base.watermark
    assert merged.footer == base.footer
    # existing palettes remain, new one is added
    assert set(base.palettes) <= set(merged.palettes)
    assert "ember2" in merged.palettes


def test_override_replaces_existing_palette_wholesale(tmp_path):
    base = load_kit()
    assert "midnight" in base.palettes

    replacement = {
        "bg": "#000000",
        "surface": "#111111",
        "fg": "#ffffff",
        "muted": "#aaaaaa",
        "accent": "#00ffff",
        "positive": "#00ff00",
        "negative": "#ff0000",
        "grad_from": "#123456",
        "grad_to": "#654321",
        "chip_bg": "#222222",
    }
    override = {"palettes": {"midnight": replacement}}
    override_path = tmp_path / "replace.yaml"
    override_path.write_text(yaml.dump(override), encoding="utf-8")

    merged = merge_kit(base, override_path)

    assert merged.palettes["midnight"].model_dump() == replacement
    # other base palettes are untouched
    for palette_id in base.palettes:
        if palette_id != "midnight":
            assert merged.palettes[palette_id] == base.palettes[palette_id]


def test_extra_unknown_palette_color_key_rejected(tmp_path):
    base = load_kit()
    bad_palette = _minimal_kit_dict()["palettes"]["midnight"]
    bad_palette["glow"] = "#ff00ff"
    override = {"palettes": {"midnight": bad_palette}}
    override_path = tmp_path / "extra_key.yaml"
    override_path.write_text(yaml.dump(override), encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        merge_kit(base, override_path)

    assert "glow" in str(excinfo.value)


def test_footer_too_long_rejected(tmp_path):
    data = _minimal_kit_dict()
    data["footer"] = "x" * 121
    bad_path = tmp_path / "long_footer.yaml"
    bad_path.write_text(yaml.dump(data), encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_kit(bad_path)

    assert "footer" in str(excinfo.value)
