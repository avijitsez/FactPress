"""Golden-image tests (F0.7): same facts + same spec => byte-identical PNG.

resvg's output may legitimately differ across OS/platforms (font shaping,
rasterizer minor-version quirks), so hashes are stored *per platform* in
``hashes.json`` (keyed by ``sys.platform``). When the current platform has
no stored hashes, the comparison tests skip with a clear reason instead of
failing -- CI on Linux will populate its own ``linux`` entry the same way
this file populates ``win32``.

Run with ``--update-golden`` to (re)render every fixture x size for the
current platform and rewrite ``hashes.json`` accordingly; the comparison
tests then pass trivially (they only ever assert when NOT updating).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from factpress.pipeline import _EVENT_MODELS
from factpress.renderer.engine_svg import load_brandkit, render_png
from factpress.schemas import DesignSpec, FactPayload

_GOLDEN_DIR = Path(__file__).resolve().parent
_FIXTURES_DIR = _GOLDEN_DIR / "fixtures"
_HASHES_PATH = _GOLDEN_DIR / "hashes.json"
_REPO_ROOT = _GOLDEN_DIR.parent.parent
_BRANDKIT_PATH = _REPO_ROOT / "brandkits" / "default.yaml"

_FIXTURE_NAMES = sorted(p.stem for p in _FIXTURES_DIR.glob("*.json"))
_SIZES = ["feed", "telegram"]

# Single source of truth: the pipeline's event-type registry, so a new
# event type never needs a parallel edit here.
_FACTS_MODELS: dict[str, type[FactPayload]] = _EVENT_MODELS


def _load_fixture(name: str) -> tuple[FactPayload, DesignSpec]:
    data = json.loads((_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    facts_data = data["facts"]
    model = _FACTS_MODELS[facts_data["event_type"]]
    facts = model.model_validate(facts_data)
    spec = DesignSpec.model_validate(data["spec"])
    return facts, spec


def _render(name: str, size: str, brandkit: dict) -> bytes:
    facts, spec = _load_fixture(name)
    template_dir = _REPO_ROOT / "templates" / spec.template_id
    return render_png(facts, spec, template_dir=template_dir, brandkit=brandkit, size=size)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_hashes() -> dict:
    if not _HASHES_PATH.exists():
        return {}
    text = _HASHES_PATH.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def _write_hashes(hashes: dict) -> None:
    # Sorted keys, 2-space indent, trailing newline, LF-only line endings --
    # write_bytes so Windows text-mode newline translation never touches it.
    text = json.dumps(hashes, sort_keys=True, indent=2) + "\n"
    _HASHES_PATH.write_bytes(text.encode("utf-8"))


@pytest.fixture(scope="module")
def brandkit():
    return load_brandkit(_BRANDKIT_PATH)


@pytest.mark.parametrize("size", _SIZES)
@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
def test_golden_hash_matches(fixture_name, size, brandkit, request):
    png = _render(fixture_name, size, brandkit)
    digest = _sha256(png)
    key = f"{fixture_name}@{size}"

    if request.config.getoption("--update-golden"):
        hashes = _load_hashes()
        hashes.setdefault(sys.platform, {})[key] = digest
        _write_hashes(hashes)
        return

    hashes = _load_hashes()
    platform_hashes = hashes.get(sys.platform)
    if platform_hashes is None or key not in platform_hashes:
        pytest.skip(
            f"no golden hashes for platform {sys.platform!r}; "
            f"run `pytest tests/golden --update-golden` to generate them"
        )

    assert digest == platform_hashes[key], f"golden hash mismatch for {key} on {sys.platform}"


def test_green_day_feed_render_is_deterministic_in_process(brandkit):
    """Runs on every platform, never skipped: proves render_png is pure."""
    png1 = _render("green_day", "feed", brandkit)
    png2 = _render("green_day", "feed", brandkit)
    assert png1 == png2
    assert png1.startswith(b"\x89PNG\r\n\x1a\n")
