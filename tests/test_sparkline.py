import math

import pytest

from factpress.renderer.sparkline import build_paths


def test_determinism():
    series = [1.0, 3.5, -2.25, 7.0, 0.0]
    first = build_paths(series, 200, 100)
    second = build_paths(series, 200, 100)
    assert first == second


def test_known_tiny_series_hand_computed():
    line_path, area_path = build_paths([0, 1], 100, 50, pad=2.0)
    assert line_path == "M 2.00,48.00 L 98.00,2.00"
    assert area_path == "M 2.00,48.00 L 98.00,2.00 L 98.00,48.00 L 2.00,48.00 Z"


def test_flat_series_renders_midline():
    line_path, _ = build_paths([5, 5, 5], 100, 50, pad=2.0)
    assert line_path == "M 2.00,25.00 L 98.00,25.00"


def test_single_point_series_renders_flat_line():
    line_path, _ = build_paths([7], 100, 50, pad=2.0)
    assert line_path == "M 2.00,25.00 L 98.00,25.00"


def test_empty_series_raises():
    with pytest.raises(ValueError, match="at least one point"):
        build_paths([], 100, 50)


def test_nan_raises():
    with pytest.raises(ValueError):
        build_paths([1.0, math.nan, 2.0], 100, 50)


def test_inf_raises():
    with pytest.raises(ValueError):
        build_paths([1.0, math.inf, 2.0], 100, 50)
    with pytest.raises(ValueError):
        build_paths([1.0, -math.inf, 2.0], 100, 50)


def test_bad_dimensions_raise():
    with pytest.raises(ValueError):
        build_paths([1, 2, 3], 4, 50, pad=2.0)  # width == 2*pad
    with pytest.raises(ValueError):
        build_paths([1, 2, 3], 100, 4, pad=2.0)  # height == 2*pad
    with pytest.raises(ValueError):
        build_paths([1, 2, 3], 3, 50, pad=2.0)  # width < 2*pad


def test_area_path_closes_and_matches_line_start():
    line_path, area_path = build_paths([1.0, 4.0, 2.0], 100, 50, pad=2.0)
    assert area_path.endswith("Z")
    first_point = line_path.split(" ", 1)[1].split(" L ")[0]
    assert area_path.startswith(f"M {first_point}")


def test_monotonic_series_maps_extremes_to_padded_bounds():
    line_path, _ = build_paths([1, 2, 3, 4, 5], 100, 50, pad=2.0)
    assert line_path.startswith("M 2.00,48.00")
    assert line_path.endswith("L 98.00,2.00")
