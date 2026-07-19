"""Resource resolution for FactPress's built-in templates and brandkits (F3).

Two locations may hold the built-in ``templates/`` and ``brandkits/``
resources depending on how FactPress is installed:

- **Dev checkout / editable install**: the repo-root siblings of the
  ``factpress`` package, i.e. ``<repo>/templates`` and ``<repo>/brandkits``
  (FACTPRESS_DESIGN.md §3 keeps these at the repo root as the source of
  truth for contributors).
- **Packaged (wheel) install**: no repo checkout exists alongside the
  installed package, so the wheel carries its own copies under
  ``factpress/_builtin/<kind>`` (see the ``force-include`` mapping in
  ``[tool.hatch.build.targets.wheel]`` in ``pyproject.toml``).

:func:`builtin_root` prefers the repo-checkout location when it exists,
falling back to the packaged copy, so a single call site works in both
dev and installed contexts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

_Kind = Literal["templates", "brandkits"]


def builtin_root(kind: _Kind) -> Path:
    """Return the built-in ``templates/`` or ``brandkits/`` directory.

    Prefers the repo-checkout sibling of the ``factpress`` package
    (``<repo>/<kind>``) when it exists -- correct for a source checkout or
    editable install. Falls back to the packaged copy shipped inside the
    wheel (``factpress/_builtin/<kind>``). Raises ``FileNotFoundError``
    naming both probed paths if neither exists.
    """
    package_parent = Path(__file__).resolve().parent.parent
    checkout_path = package_parent / kind
    if checkout_path.is_dir():
        return checkout_path

    packaged_path = Path(__file__).resolve().parent / "_builtin" / kind
    if packaged_path.is_dir():
        return packaged_path

    raise FileNotFoundError(
        f"could not locate built-in {kind!r}: checked repo-checkout path "
        f"{checkout_path} and packaged path {packaged_path}, but neither exists"
    )
