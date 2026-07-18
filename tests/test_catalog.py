"""Tests for factpress.catalog (F1.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factpress.catalog import build_catalog, builtin_templates_dir, discover_templates

BUILTIN_DIR = builtin_templates_dir()


def _write_manifest(dir_path: Path, text: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "manifest.yaml").write_text(text, encoding="utf-8")
    return dir_path


def _minimal_manifest(template_id: str, version: str = "1.0.0") -> str:
    return f"""
id: {template_id}
version: {version}
name: "Test Template"
variants:
  - default
sizes:
  feed: [1080, 1350]
  telegram: [1280, 720]
slots:
  headline:
    max: 60
palettes_allowed:
  - midnight
sparkline: none
states:
  - static
"""


def test_build_catalog_default_finds_daily_pnl():
    catalog = build_catalog()
    templates = {entry["id"]: entry for entry in catalog["templates"]}
    assert "daily_pnl" in templates

    entry = templates["daily_pnl"]
    assert entry["version"] == "1.0.0"
    assert entry["slots"] == {
        "headline": {"max": 60},
        "subhead": {"max": 90},
        "emoji": {"max": 4},
    }
    assert entry["palettes_allowed"] == ["midnight", "ember", "aurora"]


def test_user_template_shadows_builtin_with_same_id(tmp_path):
    user_dir = tmp_path / "user_templates"
    _write_manifest(user_dir / "daily_pnl", _minimal_manifest("daily_pnl", version="9.0.0"))

    catalog = build_catalog(template_paths=[user_dir])
    templates = {entry["id"]: entry for entry in catalog["templates"]}

    assert templates["daily_pnl"]["version"] == "9.0.0"
    # only one daily_pnl entry survives — the builtin is shadowed, not duplicated.
    assert sum(1 for e in catalog["templates"] if e["id"] == "daily_pnl") == 1


def test_nonexistent_search_path_is_skipped_silently(tmp_path):
    missing = tmp_path / "does_not_exist"
    assert not missing.exists()

    catalog = build_catalog(template_paths=[missing])
    ids = {entry["id"] for entry in catalog["templates"]}
    assert "daily_pnl" in ids  # builtin still found; missing path just skipped


def test_broken_manifest_raises_with_path(tmp_path):
    broken_dir = tmp_path / "broken_templates"
    _write_manifest(broken_dir / "broken", "id: broken\nversion: 1.0.0\n")  # missing sizes, etc.

    with pytest.raises(ValueError) as exc_info:
        build_catalog(template_paths=[broken_dir])

    message = str(exc_info.value)
    assert str(broken_dir / "broken" / "manifest.yaml") in message


def test_catalog_is_json_serializable():
    catalog = build_catalog()
    dumped = json.dumps(catalog)
    assert json.loads(dumped) == catalog


def test_catalog_sorted_by_id(tmp_path):
    extra_dir = tmp_path / "extra_templates"
    _write_manifest(extra_dir / "aaa_first", _minimal_manifest("aaa_first"))

    catalog = build_catalog(template_paths=[extra_dir])
    ids = [entry["id"] for entry in catalog["templates"]]
    assert ids == sorted(ids)
    assert ids[0] == "aaa_first"


def test_discover_templates_returns_paths_by_id():
    discovered = discover_templates([BUILTIN_DIR])
    assert discovered["daily_pnl"] == BUILTIN_DIR / "daily_pnl"
