"""Command-line interface for FactPress: the zero-LLM render path (F0.8).

``factpress render facts.json --out out.png`` builds a deterministic
``DesignSpec`` from the facts via ``factpress.director.fallback_spec`` --
no LLM involved -- and renders a PNG end to end. ``fallback_spec`` lives in
``director.py`` (F1.2) since it is also the director's guaranteed-safe
worst case; here every choice is the simplest deterministic one that
satisfies the ``DesignSpec`` schema.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from factpress.director import fallback_spec
from factpress.renderer.engine_svg import load_brandkit, load_manifest, render_png
from factpress.schemas import DailyPnlFacts, FactPayload

# factpress/cli.py -> parent is the `factpress` package dir, parent.parent is
# the repo root. This resolves templates/brandkits for an in-repo checkout;
# resolving them for a packaged (pip-installed) distribution is a later phase.
_PACKAGE_PARENT = Path(__file__).resolve().parent.parent

class _CliError(Exception):
    """A user-facing CLI error: caught in `main`, printed, exit code 2."""


def _load_facts(path: Path) -> FactPayload:
    """Load and validate a facts JSON file, raising `_CliError` on any problem."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _CliError(f"cannot read facts file {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _CliError(f"facts file {path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise _CliError(
            f"facts file {path} must contain a JSON object, got {type(data).__name__}"
        )

    event_type = data.get("event_type")
    if not event_type:
        raise _CliError(f"facts file {path} is missing required key 'event_type'")

    try:
        if event_type == "daily_pnl":
            return DailyPnlFacts.model_validate(data)
        return FactPayload.model_validate(data)
    except ValidationError as exc:
        raise _CliError(f"facts file {path} failed validation:\n{exc}") from exc


def _default_template_dir(event_type: str) -> Path:
    return _PACKAGE_PARENT / "templates" / event_type


def _default_brandkit_path() -> Path:
    return _PACKAGE_PARENT / "brandkits" / "default.yaml"


def _open_preview(path: Path) -> None:
    """Open the rendered PNG in the OS default viewer, without blocking."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _cmd_render(args: argparse.Namespace) -> int:
    facts_path = Path(args.facts_json)
    try:
        facts = _load_facts(facts_path)
    except _CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    template_dir = (
        Path(args.template_dir)
        if args.template_dir is not None
        else _default_template_dir(facts.event_type)
    )
    brandkit_path = Path(args.brandkit) if args.brandkit is not None else _default_brandkit_path()

    try:
        manifest = load_manifest(template_dir)
    except (OSError, ValueError) as exc:
        print(f"error: cannot load template manifest from {template_dir}: {exc}", file=sys.stderr)
        return 2

    try:
        brandkit = load_brandkit(brandkit_path)
    except (OSError, ValueError) as exc:
        print(f"error: cannot load brandkit from {brandkit_path}: {exc}", file=sys.stderr)
        return 2

    try:
        spec = fallback_spec(facts, manifest, brandkit)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        png = render_png(facts, spec, template_dir=template_dir, brandkit=brandkit, size=args.size)
    except (ValueError, KeyError) as exc:
        print(f"error: render failed: {exc}", file=sys.stderr)
        return 2

    out_path = Path(args.out) if args.out is not None else Path.cwd() / f"{facts_path.stem}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(png)
    print(f"wrote {out_path} ({len(png)} bytes)")

    if args.preview:
        _open_preview(out_path)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factpress",
        description="Facts in, identical prints out.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser(
        "render", help="Render a facts JSON file to a PNG using a deterministic default spec."
    )
    render_parser.add_argument("facts_json", help="Path to a facts JSON file.")
    render_parser.add_argument(
        "--template-dir",
        default=None,
        help=(
            "Template directory (default: templates/<event_type> resolved relative to the "
            "package parent; packaged-install resolution is a later phase)."
        ),
    )
    render_parser.add_argument(
        "--brandkit",
        default=None,
        help=(
            "Brandkit YAML path (default: brandkits/default.yaml resolved relative to the "
            "package parent; packaged-install resolution is a later phase)."
        ),
    )
    render_parser.add_argument("--size", choices=["feed", "telegram"], default="feed")
    render_parser.add_argument(
        "--out", default=None, help="Output PNG path (default: <facts stem>.png in cwd)."
    )
    render_parser.add_argument(
        "--preview", action="store_true", help="Open the rendered PNG after writing."
    )
    render_parser.set_defaults(func=_cmd_render)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
