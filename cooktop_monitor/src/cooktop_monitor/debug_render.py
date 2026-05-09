"""Annotated debug image (ROIs, mask overlay, labels)."""

from __future__ import annotations

from pathlib import Path

import numpy as np


def render_debug(
    source_bgr: np.ndarray,
    output_path: Path,
    *,
    panel_bbox: tuple[int, int, int, int] | None = None,
) -> None:
    raise NotImplementedError("debug_render.render_debug")
