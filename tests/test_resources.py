"""Tests for factpress.resources (F3 tail): built-in template/brandkit resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from factpress import resources


def test_builtin_root_resolves_in_dev_checkout():
    templates_root = resources.builtin_root("templates")
    assert templates_root.is_dir()
    assert templates_root.name == "templates"
    assert (templates_root / "daily_pnl" / "manifest.yaml").is_file()

    brandkits_root = resources.builtin_root("brandkits")
    assert brandkits_root.is_dir()
    assert (brandkits_root / "default.yaml").is_file()


def test_builtin_root_falls_back_to_packaged_wheel_layout(tmp_path, monkeypatch):
    # Simulate a wheel install: a fake package tree with no repo-checkout
    # sibling of `templates`/`brandkits`, only the packaged `_builtin` copy.
    fake_pkg_dir = tmp_path / "site-packages" / "factpress"
    fake_pkg_dir.mkdir(parents=True)
    fake_builtin_templates = fake_pkg_dir / "_builtin" / "templates" / "daily_pnl"
    fake_builtin_templates.mkdir(parents=True)
    (fake_builtin_templates / "manifest.yaml").write_text("id: daily_pnl\n", encoding="utf-8")

    monkeypatch.setattr(resources, "__file__", str(fake_pkg_dir / "resources.py"))

    resolved = resources.builtin_root("templates")
    assert resolved == fake_pkg_dir / "_builtin" / "templates"
    assert (resolved / "daily_pnl" / "manifest.yaml").is_file()


def test_builtin_root_missing_raises_naming_both_paths(tmp_path, monkeypatch):
    fake_pkg_dir = tmp_path / "site-packages" / "factpress"
    fake_pkg_dir.mkdir(parents=True)
    # Neither `<repo>/templates` (i.e. tmp_path/site-packages/templates) nor
    # `factpress/_builtin/templates` exists.

    monkeypatch.setattr(resources, "__file__", str(fake_pkg_dir / "resources.py"))

    with pytest.raises(FileNotFoundError) as excinfo:
        resources.builtin_root("templates")

    message = str(excinfo.value)
    expected_checkout_path = str(Path(fake_pkg_dir).parent / "templates")
    expected_packaged_path = str(fake_pkg_dir / "_builtin" / "templates")
    assert expected_checkout_path in message
    assert expected_packaged_path in message
