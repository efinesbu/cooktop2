"""HSV bright-red and dim-red masks for lit display pixels (red hue wraps at 0°/180° in OpenCV HSV)."""

from __future__ import annotations

import cv2
import numpy as np

_HSV_DEPTH_UINT8 = 256
# OpenCV H is 0–179 for 8-bit HSV; S,V are 0–255. Thresholds follow coreidea.md (strict > on S,V).


def _ensure_bgr_uint8(panel_bgr: np.ndarray) -> None:
    if panel_bgr.ndim != 3 or panel_bgr.shape[2] != 3:
        raise ValueError(
            f"expected HxWx3 BGR image; got shape {panel_bgr.shape!r}"
        )
    if panel_bgr.dtype != np.uint8:
        raise ValueError(f"expected uint8 BGR image; got dtype {panel_bgr.dtype!r}")


def _red_mask(panel_bgr: np.ndarray, s_min: int, v_min: int) -> np.ndarray:
    """Union of low and high hue bands with S/V floors; returns uint8 {0, 255}."""
    _ensure_bgr_uint8(panel_bgr)
    hsv = cv2.cvtColor(panel_bgr, cv2.COLOR_BGR2HSV)

    # S > s_min, V > v_min  →  lower bounds s_min+1, v_min+1 for uint8 inRange
    lo_a = (0, s_min + 1, v_min + 1)
    hi_a = (10, _HSV_DEPTH_UINT8 - 1, _HSV_DEPTH_UINT8 - 1)
    lo_b = (170, s_min + 1, v_min + 1)
    hi_b = (179, _HSV_DEPTH_UINT8 - 1, _HSV_DEPTH_UINT8 - 1)

    m1 = cv2.inRange(hsv, np.array(lo_a, dtype=np.uint8), np.array(hi_a, dtype=np.uint8))
    m2 = cv2.inRange(hsv, np.array(lo_b, dtype=np.uint8), np.array(hi_b, dtype=np.uint8))
    out = cv2.bitwise_or(m1, m2)
    return out


def bright_red_mask(panel_bgr: np.ndarray) -> np.ndarray:
    """Saturated red: H in [0,10]∪[170,179], S > 120, V > 80."""
    return _red_mask(panel_bgr, s_min=120, v_min=80)


def dim_red_mask(panel_bgr: np.ndarray) -> np.ndarray:
    """Dimmer red (e.g. H indicator): same hue bands, S > 60, V > 40."""
    return _red_mask(panel_bgr, s_min=60, v_min=40)


def lit_fraction(mask: np.ndarray) -> float:
    """Fraction of pixels equal to 255 in a uint8 binary mask (0.0 if empty)."""
    if mask.size == 0:
        return 0.0
    if mask.dtype != np.uint8:
        raise ValueError(f"expected uint8 mask; got dtype {mask.dtype!r}")
    return float(np.count_nonzero(mask == 255)) / float(mask.size)


def crop_roi(
    image: np.ndarray,
    rect: tuple[int, int, int, int],
) -> np.ndarray:
    """Crop using (x1, y1, x2, y2) with top-left inclusive, bottom-right exclusive; clamped to bounds."""
    if image.ndim not in (2, 3):
        raise ValueError(f"expected 2D or 3D array; got shape {image.shape!r}")
    x1, y1, x2, y2 = rect
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    h, w = image.shape[:2]
    x1c = max(0, min(x1, w))
    x2c = max(0, min(x2, w))
    y1c = max(0, min(y1, h))
    y2c = max(0, min(y2, h))
    if x2c <= x1c or y2c <= y1c:
        raise ValueError(
            f"clamped ROI has zero size: ({x1c}, {y1c}, {x2c}, {y2c}) for image size {w}x{h}"
        )
    return np.ascontiguousarray(image[y1c:y2c, x1c:x2c])
