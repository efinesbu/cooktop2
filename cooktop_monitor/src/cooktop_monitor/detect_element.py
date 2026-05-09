"""Per-element on/off/hot, level, label, zone size — raw signals only (see reconcile)."""

from __future__ import annotations

from typing import Any

import numpy as np

from cooktop_monitor.mask import crop_roi, lit_fraction
from cooktop_monitor.rois import ElementLayout, clamp_rect, to_absolute

CLUSTER_LIT_FRACTION_ACTIVE = 0.012
ON_INDICATOR_LIT_FRACTION = 0.10
H_DIGIT_DIM_FRACTION = 0.04
H_DIGIT_BRIGHT_MAX = 0.02
BAR_SEGMENT_LIT_THRESHOLD = 0.30
LABEL_LIT_FRACTION = 0.05
ZONE_DOT_LIT_FRACTION = 0.10

_LABEL_KEYS = ("MELT", "K. WARM", "SIMMER", "BOIL")


def cluster_lit_fraction(bright_cluster_mask: np.ndarray) -> float:
    return lit_fraction(bright_cluster_mask)


def on_indicator_lit_fraction(bright_on_indicator_mask: np.ndarray) -> float:
    return lit_fraction(bright_on_indicator_mask)


def detect_h(
    bright_digit_mask: np.ndarray,
    dim_digit_mask: np.ndarray,
    *,
    dim_threshold: float = H_DIGIT_DIM_FRACTION,
    bright_max: float = H_DIGIT_BRIGHT_MAX,
) -> bool:
    dim_frac = lit_fraction(dim_digit_mask)
    bright_frac = lit_fraction(bright_digit_mask)
    return bool(dim_frac >= dim_threshold and bright_frac <= bright_max)


def bar_segment_count(
    bright_bar_mask: np.ndarray,
    *,
    segments: int = 10,
    lit_threshold: float = BAR_SEGMENT_LIT_THRESHOLD,
) -> int:
    if segments <= 0:
        raise ValueError("segments must be positive")
    if bright_bar_mask.size == 0:
        return 0
    h = int(bright_bar_mask.shape[0])
    if h == 0:
        return 0

    slice_h_float = float(h) / float(segments)
    count = 0
    # Slice 0 = bottom band, slice segments-1 = top band (vertical bar fills bottom-up).
    for k in range(segments):
        y1 = int(round(float(h) * float(segments - k - 1) / float(segments)))
        y2 = int(round(float(h) * float(segments - k) / float(segments)))
        # Ensure ascending half-open slices and cover all rows by the top slice.
        y1 = max(0, min(y1, h))
        y2 = max(y1, min(y2, h))
        if y2 <= y1:
            band = bright_bar_mask[0:0, :]
        else:
            band = bright_bar_mask[y1:y2, :]
        frac = lit_fraction(band)
        if frac >= lit_threshold:
            count += 1
        else:
            break

    return int(count)


def label_lit_flags(
    bright_label_masks: dict[str, np.ndarray],
    *,
    lit_threshold: float = LABEL_LIT_FRACTION,
) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for name, mask in bright_label_masks.items():
        out[name] = bool(lit_fraction(mask) >= lit_threshold)
    return out


def zone_dot_count(
    bright_zone_mask: np.ndarray,
    *,
    max_dots: int = 3,
    lit_threshold: float = ZONE_DOT_LIT_FRACTION,
) -> int | None:
    if bright_zone_mask.size == 0:
        return None
    if max_dots <= 0:
        raise ValueError("max_dots must be positive")

    h, w = bright_zone_mask.shape[:2]
    if h == 0 or w == 0:
        return None

    n = int(max_dots)
    lit_columns = 0
    for i in range(n):
        x1 = int(round(float(w) * float(i) / float(n)))
        x2 = int(round(float(w) * float(i + 1) / float(n)))
        x1 = max(0, min(x1, w))
        x2 = max(x1, min(x2, w))
        strip = bright_zone_mask[:, x1:x2] if x2 > x1 else bright_zone_mask[:, 0:0]
        if lit_fraction(strip) >= lit_threshold:
            lit_columns += 1

    return int(lit_columns)


def _effective_panel_bbox(
    panel_bgr: np.ndarray,
    panel_bbox: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int]:
    h, w = int(panel_bgr.shape[0]), int(panel_bgr.shape[1])
    if panel_bbox is None:
        return (0, 0, w, h)
    return panel_bbox


def _roi_crop(
    array: np.ndarray,
    frac_rect: tuple[float, float, float, float],
    panel_bbox_eff: tuple[int, int, int, int],
    img_wh: tuple[int, int],
) -> np.ndarray:
    abs_rect = to_absolute(frac_rect, panel_bbox_eff)
    clamped = clamp_rect(abs_rect, img_wh)
    return crop_roi(array, clamped)


def detect_element(
    element_id: str,
    panel_bgr: np.ndarray,
    bright_panel_mask: np.ndarray,
    dim_panel_mask: np.ndarray,
    element_layout: ElementLayout,
    panel_bbox: tuple[int, int, int, int] | None = None,
    **threshold_overrides: Any,
) -> dict[str, Any]:
    merged: dict[str, float] = {
        "cluster_lit_fraction_active": float(
            threshold_overrides.get(
                "cluster_lit_fraction_active", CLUSTER_LIT_FRACTION_ACTIVE
            )
        ),
        "on_indicator_lit_fraction": float(
            threshold_overrides.get(
                "on_indicator_lit_fraction", ON_INDICATOR_LIT_FRACTION
            )
        ),
        "h_digit_dim_fraction": float(
            threshold_overrides.get(
                "h_digit_dim_fraction", H_DIGIT_DIM_FRACTION
            )
        ),
        "h_digit_bright_max": float(
            threshold_overrides.get("h_digit_bright_max", H_DIGIT_BRIGHT_MAX)
        ),
        "bar_segment_lit_threshold": float(
            threshold_overrides.get(
                "bar_segment_lit_threshold", BAR_SEGMENT_LIT_THRESHOLD
            )
        ),
        "label_lit_fraction": float(
            threshold_overrides.get("label_lit_fraction", LABEL_LIT_FRACTION)
        ),
        "zone_dot_lit_fraction": float(
            threshold_overrides.get(
                "zone_dot_lit_fraction", ZONE_DOT_LIT_FRACTION
            )
        ),
    }

    h_img, w_img = int(panel_bgr.shape[0]), int(panel_bgr.shape[1])
    img_wh = (w_img, h_img)
    panel_eff = _effective_panel_bbox(panel_bgr, panel_bbox)

    bright_c = _roi_crop(
        bright_panel_mask, element_layout.cluster_bbox, panel_eff, img_wh
    )
    on_b = _roi_crop(
        bright_panel_mask, element_layout.on_indicator, panel_eff, img_wh
    )
    bright_digit = _roi_crop(
        bright_panel_mask, element_layout.digit_area, panel_eff, img_wh
    )
    dim_digit = _roi_crop(
        dim_panel_mask, element_layout.digit_area, panel_eff, img_wh
    )
    bright_bar = _roi_crop(
        bright_panel_mask, element_layout.bar_graph, panel_eff, img_wh
    )

    label_fracs = {
        "MELT": element_layout.label_melt,
        "K. WARM": element_layout.label_kwarm,
        "SIMMER": element_layout.label_simmer,
        "BOIL": element_layout.label_boil,
    }
    bright_label_masks: dict[str, np.ndarray] = {
        lk: _roi_crop(bright_panel_mask, label_fracs[lk], panel_eff, img_wh)
        for lk in _LABEL_KEYS
    }

    signals: dict[str, Any] = {
        "cluster_lit_fraction": cluster_lit_fraction(bright_c),
        "on_indicator_lit_fraction": on_indicator_lit_fraction(on_b),
        "h_detected": detect_h(
            bright_digit,
            dim_digit,
            dim_threshold=merged["h_digit_dim_fraction"],
            bright_max=merged["h_digit_bright_max"],
        ),
        "bar_count": bar_segment_count(
            bright_bar,
            segments=10,
            lit_threshold=merged["bar_segment_lit_threshold"],
        ),
        "label_lit": {},
        "zone_dot_count": None,
    }

    lf = merged["label_lit_fraction"]
    label_flags = label_lit_flags(bright_label_masks, lit_threshold=lf)
    signals["label_lit"] = {k: bool(label_flags[k]) for k in _LABEL_KEYS}

    zone_signal: int | None
    if element_layout.zone_size_dots is None:
        zone_signal = None
    else:
        zm = _roi_crop(
            bright_panel_mask,
            element_layout.zone_size_dots,
            panel_eff,
            img_wh,
        )
        zone_signal = zone_dot_count(
            zm,
            max_dots=3,
            lit_threshold=merged["zone_dot_lit_fraction"],
        )

    signals["zone_dot_count"] = zone_signal

    return {
        "element_id": element_id,
        "signals": signals,
        "thresholds": merged,
    }
