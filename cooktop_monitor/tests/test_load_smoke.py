"""Smoke tests for image loading against groundtruth JPEGs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from cooktop_monitor.load import MAX_LONG_EDGE, load_image

GROUNDTRUTHS = Path(__file__).resolve().parents[2] / "groundtruths"

GROUNDTRUTH_JPEGS = (
    "A0.jpeg",
    "A10boil.jpeg",
    "AOffH.jpeg",
    "timer2minutes.jpeg",
)


@pytest.mark.parametrize("name", GROUNDTRUTH_JPEGS)
def test_load_returns_bgr_uint8_within_max_edge(name: str) -> None:
    path = GROUNDTRUTHS / name
    if not path.is_file():
        pytest.skip(f"groundtruth not present: {path}")

    img = load_image(path)

    assert img.dtype == np.uint8
    assert img.ndim == 3
    assert img.shape[2] == 3
    h, w = img.shape[:2]
    assert max(h, w) <= MAX_LONG_EDGE
    assert img.flags["C_CONTIGUOUS"]


def test_load_unsupported_extension_raises(tmp_path: Path) -> None:
    bad = tmp_path / "dummy.xyz"
    bad.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="unsupported image extension"):
        load_image(bad)


def test_load_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.jpeg"
    with pytest.raises(FileNotFoundError, match="image file not found"):
        load_image(missing)
