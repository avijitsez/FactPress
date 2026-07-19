"""Template catalog (F1.1): a compact, JSON-able view of available templates.

This is the catalog the creative-director LLM (F1.2) sees when picking a
template and filling its slots — one dict per template, no filesystem paths,
safe to ``json.dumps``.

Discovery also seeds F3's ``template_paths`` search: user-supplied paths are
searched before the built-in ``templates/`` directory, and the first path to
contain a given template id wins (earlier paths shadow later ones, including
the built-ins). Manifest parsing itself is not reimplemented here — it reuses
``factpress.renderer.engine_svg.load_manifest`` so the catalog and the render
engine can never disagree about what a manifest means.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from factpress.renderer.engine_svg import load_manifest
from factpress.resources import builtin_root


def builtin_templates_dir() -> Path:
    """Return the built-in ``templates/`` directory.

    Resolves via :func:`factpress.resources.builtin_root`: the repo-checkout
    ``templates/`` directory in a source checkout / editable install, or the
    packaged copy under ``factpress/_builtin/templates`` in a wheel install.
    """
    return builtin_root("templates")


def discover_templates(template_paths: Sequence[Path]) -> dict[str, Path]:
    """Scan ``template_paths`` in order for ``<subdir>/manifest.yaml``.

    Returns a mapping of template id -> template directory. Earlier paths
    shadow later ones: once a template id has been found, later occurrences
    of the same id (in later search paths) are ignored. A search path that
    does not exist is skipped silently. A manifest that fails to load raises
    ``ValueError`` naming the offending path (via
    ``engine_svg.load_manifest``, which already includes the path in its
    error messages).
    """
    found: dict[str, Path] = {}
    for base in template_paths:
        base = Path(base)
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "manifest.yaml"
            if not manifest_path.is_file():
                continue
            manifest = load_manifest(entry)
            template_id = manifest["id"]
            if template_id not in found:
                found[template_id] = entry
    return found


def catalog_entry(template_dir: Path) -> dict[str, Any]:
    """Build the compact catalog entry for a single template directory."""
    manifest = load_manifest(template_dir)
    return {
        "id": manifest["id"],
        "version": manifest["version"],
        "name": manifest.get("name"),
        "variants": manifest.get("variants", []),
        "sizes": manifest["sizes"],
        "slots": manifest.get("slots", {}),
        "palettes_allowed": manifest["palettes_allowed"],
        "sparkline": manifest.get("sparkline"),
    }


def build_catalog(template_paths: Sequence[Path] | None = None) -> dict[str, Any]:
    """Build the full catalog: search paths, then the built-in templates.

    ``template_paths`` (if given) are searched before ``builtin_templates_dir()``,
    so a user-supplied template shadows a built-in one with the same id. The
    result is a JSON-serializable dict: ``{"templates": [entry, ...]}`` sorted
    by template id.
    """
    search_paths = [*(template_paths or []), builtin_templates_dir()]
    discovered = discover_templates(search_paths)
    entries = [catalog_entry(template_dir) for template_dir in discovered.values()]
    entries.sort(key=lambda entry: entry["id"])
    return {"templates": entries}
