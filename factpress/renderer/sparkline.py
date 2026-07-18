"""Deterministic SVG path builder for sparklines.

Turns a numeric series into fixed-precision SVG path strings for the line
and its filled area. Same input must produce the identical string on every
platform, so all coordinates are formatted with a fixed 2-decimal format
(never repr/str of a raw float).
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def build_paths(
    series: Sequence[float], width: int, height: int, *, pad: float = 2.0
) -> tuple[str, str]:
    """Build (line_path, area_path) SVG path strings for a sparkline.

    X coordinates are evenly spaced across ``[pad, width - pad]``. Y
    coordinates are mapped linearly so the series maximum lands at ``pad``
    and the minimum at ``height - pad`` (SVG y grows downward). A flat
    series (max == min) or a single-point series renders as a horizontal
    line at the vertical center of the canvas.

    Raises:
        ValueError: if series is empty, contains a non-finite value, or if
            width/height do not each exceed ``2 * pad``.
    """
    if width <= 2 * pad or height <= 2 * pad:
        raise ValueError("width and height must each exceed 2 * pad")
    if len(series) == 0:
        raise ValueError("series must contain at least one point")
    for value in series:
        if not math.isfinite(value):
            raise ValueError(f"series values must be finite, got {value!r}")

    baseline = height - pad
    n = len(series)
    lo = min(series)
    hi = max(series)

    if n == 1 or hi == lo:
        mid_y = height / 2
        xs = [pad, width - pad]
        ys = [mid_y, mid_y]
    else:
        step = (width - 2 * pad) / (n - 1)
        xs = [pad + i * step for i in range(n)]
        span = hi - lo
        ys = [pad + (hi - value) / span * (baseline - pad) for value in series]

    points = " L ".join(f"{x:.2f},{y:.2f}" for x, y in zip(xs, ys))
    line_path = f"M {points}"

    first_x, last_x = xs[0], xs[-1]
    area_path = f"{line_path} L {last_x:.2f},{baseline:.2f} L {first_x:.2f},{baseline:.2f} Z"
    return line_path, area_path
