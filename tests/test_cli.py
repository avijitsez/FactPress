"""Tests for `factpress.cli` (F0.8): the zero-LLM render path."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from factpress.cli import main

_REPO_ROOT = Path(__file__).resolve().parent.parent
DAILY_PNL_FACTS = _REPO_ROOT / "examples" / "daily_pnl.json"

_EXPECTED_DIMS = {"feed": (1080, 1350), "telegram": (1280, 720)}


def _png_dims(png: bytes) -> tuple[int, int]:
    # PNG IHDR chunk: bytes 16-19 width, 20-23 height (big-endian), after
    # the 8-byte signature + 4-byte length + 4-byte "IHDR" tag.
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def test_render_default_size_writes_valid_feed_png(tmp_path):
    out = tmp_path / "out.png"
    rc = main(["render", str(DAILY_PNL_FACTS), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    png = out.read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert _png_dims(png) == _EXPECTED_DIMS["feed"]


def test_render_telegram_size(tmp_path):
    out = tmp_path / "out.png"
    rc = main(["render", str(DAILY_PNL_FACTS), "--size", "telegram", "--out", str(out)])
    assert rc == 0
    png = out.read_bytes()
    assert _png_dims(png) == _EXPECTED_DIMS["telegram"]


def test_render_is_byte_identical_on_rerun(tmp_path):
    out1 = tmp_path / "one.png"
    out2 = tmp_path / "two.png"
    assert main(["render", str(DAILY_PNL_FACTS), "--out", str(out1)]) == 0
    assert main(["render", str(DAILY_PNL_FACTS), "--out", str(out2)]) == 0
    assert out1.read_bytes() == out2.read_bytes()


def test_missing_facts_file_exits_2(tmp_path, capsys):
    missing = tmp_path / "does_not_exist.json"
    rc = main(["render", str(missing), "--out", str(tmp_path / "out.png")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "does_not_exist.json" in captured.err


def test_facts_without_event_type_exits_2(tmp_path):
    bad_facts = tmp_path / "bad.json"
    bad_facts.write_text(json.dumps({"daily_pnl_pct": 1.0}), encoding="utf-8")
    rc = main(["render", str(bad_facts), "--out", str(tmp_path / "out.png")])
    assert rc == 2


def test_preview_flag_accepted_without_blocking(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("factpress.cli._open_preview", lambda path: opened.append(path))
    out = tmp_path / "out.png"
    rc = main(["render", str(DAILY_PNL_FACTS), "--out", str(out), "--preview"])
    assert rc == 0
    assert out.exists()
    assert opened == [out]


def test_installed_console_script_renders_png(tmp_path):
    exe = _REPO_ROOT / ".venv" / "Scripts" / "factpress.exe"
    if not exe.exists():
        pytest.skip("factpress console script not found in .venv/Scripts")

    out = tmp_path / "out.png"
    result = subprocess.run(
        [str(exe), "render", str(DAILY_PNL_FACTS), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert out.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_facts_with_no_numeric_metrics_exits_2(tmp_path, capsys):
    facts_file = tmp_path / "no_metrics.json"
    facts_file.write_text('{"event_type": "custom_event", "note": "hello"}', encoding="utf-8")
    template_dir = Path(__file__).resolve().parent.parent / "templates" / "daily_pnl"
    rc = main(
        [
            "render",
            str(facts_file),
            "--template-dir",
            str(template_dir),
            "--out",
            str(tmp_path / "x.png"),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "no numeric metric" in err
