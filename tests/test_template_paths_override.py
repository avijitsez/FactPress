"""F3 exit criterion: a caller-supplied ``template_paths`` entry shadows the
built-in template with the same id.

Copies the built-in ``templates/daily_pnl`` directory into a private
``tmp_path`` location, bumps its manifest version and adds a visible marker
to the SVG template, then proves two things:

- ``factpress.catalog.build_catalog`` reports the private copy's version
  (1.0.1), not the built-in's (1.0.0) -- the shadowing is visible at the
  catalog layer.
- ``factpress.pipeline.render`` with ``template_paths=[private_dir]``
  produces PNG bytes that differ from the built-in render -- the private
  template actually gets used, not just discovered.
"""

from __future__ import annotations

import shutil

from factpress.catalog import build_catalog, builtin_templates_dir
from factpress.pipeline import render

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _make_private_daily_pnl(tmp_path):
    """Copy templates/daily_pnl into ``tmp_path/private/daily_pnl``, bump its
    manifest version to 1.0.1, and add a visible marker rect to the SVG."""
    private_root = tmp_path / "private"
    dest = private_root / "daily_pnl"
    shutil.copytree(builtin_templates_dir() / "daily_pnl", dest)

    manifest_path = dest / "manifest.yaml"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "version: 1.0.0" in manifest_text
    manifest_path.write_text(
        manifest_text.replace("version: 1.0.0", "version: 1.0.1"), encoding="utf-8"
    )

    template_path = dest / "template.svg.j2"
    template_text = template_path.read_text(encoding="utf-8")
    marker = '\n  <rect x="0" y="0" width="40" height="40" fill="#ff00ff"/>\n'
    anchor = '<rect x="0" y="0" width="{{ W }}" height="{{ H }}" fill="{{ pal.bg }}"/>'
    assert anchor in template_text
    template_path.write_text(template_text.replace(anchor, anchor + marker), encoding="utf-8")

    return private_root


def _facts_dict(**overrides):
    base = dict(
        event_type="daily_pnl",
        daily_pnl_pct=1.87,
        currency="USD",
        equity=54321.0,
        win_rate_pct=61.0,
        trades_count=11,
        series=[1.0, 1.4, 1.2, 1.9],
    )
    base.update(overrides)
    return base


def test_private_template_path_shadows_builtin_render(tmp_path):
    private_root = _make_private_daily_pnl(tmp_path)

    builtin_png = render(_facts_dict(), "daily_pnl")
    private_png = render(_facts_dict(), "daily_pnl", template_paths=[private_root])

    assert builtin_png.startswith(PNG_MAGIC)
    assert private_png.startswith(PNG_MAGIC)
    assert private_png != builtin_png


def test_catalog_reports_private_version(tmp_path):
    private_root = _make_private_daily_pnl(tmp_path)

    catalog = build_catalog([private_root])
    templates = {entry["id"]: entry for entry in catalog["templates"]}

    assert templates["daily_pnl"]["version"] == "1.0.1"
