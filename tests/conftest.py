"""Shared pytest configuration for the FactPress test suite."""

from __future__ import annotations


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help=(
            "Regenerate tests/golden/hashes.json for the current platform "
            "(sys.platform) by actually rendering each golden fixture, "
            "instead of comparing rendered output against stored hashes."
        ),
    )
