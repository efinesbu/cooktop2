"""Unit tests for red masks, lit fraction, and ROI cropping."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from cooktop_monitor.mask import (
    bright_red_mask,
    crop_roi,
    dim_red_mask,
    lit_fraction,
)


def test_bright_red_mask_lights_pure_red() -> None:
    img = np.full((4, 4, 3), (0, 0, 255), dtype=np.uint8)
    mask = bright_red_mask(img)
    assert mask.shape[:2] == img.shape[:2]
    assert np.all(mask == 255)


def test_bright_red_mask_excludes_pure_blue_and_green() -> None:
    blue = np.full((4, 4, 3), (255, 0, 0), dtype=np.uint8)
    green = np.full((4, 4, 3), (0, 255, 0), dtype=np.uint8)
    assert np.all(bright_red_mask(blue) == 0)
    assert np.all(bright_red_mask(green) == 0)


def test_bright_red_mask_dimmer_red_excluded_but_dim_mask_includes() -> None:
    hsv = np.uint8([[[0, 80, 100]]])
    bgr_px = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    img = np.broadcast_to(bgr_px, (16, 16, 3)).copy()

    bright = bright_red_mask(img)
    dim = dim_red_mask(img)

    assert np.count_nonzero(bright) == 0
    assert np.all(dim == 255)


def test_lit_fraction() -> None:
    half = np.zeros((4, 4), dtype=np.uint8)
    half.flat[: half.size // 2] = 255
    assert lit_fraction(half) == 0.5

    empty = np.array([], dtype=np.uint8)
    assert lit_fraction(empty) == 0.0


def test_crop_roi_clamps_and_returns_view_shape() -> None:
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    out = crop_roi(img, (8, 8, 20, 20))
    assert out.shape == (2, 2, 3)


def test_crop_roi_raises_on_empty_clamped_rect() -> None:
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="clamped ROI has zero size"):
        crop_roi(img, (20, 20, 30, 30))
