"""Find panel ROI from saturated-red pixels.

Uses the bright-red mask over the full image: axis-aligned bbox of all lit pixels,
expanded by 20% in width and height around the bbox center (round corners), then
clamped to image bounds. If no bright-red pixels exist, falls back to the lower-right
quadrant per coreidea.md.
"""

from __future__ import annotations

import numpy as np

from cooktop_monitor.mask import bright_red_mask


def _bright_red_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Return inclusive-exclusive bbox of mask==255 pixels, or None if empty."""
    ys, xs = np.where(mask == 255)
    if ys.size == 0:
        return None
    x1 = int(xs.min())
    y1 = int(ys.min())
    x2 = int(xs.max()) + 1
    y2 = int(ys.max()) + 1
    return (x1, y1, x2, y2)


def locate_panel(image: np.ndarray) -> tuple[int, int, int, int]:
    """Estimate panel (x1,y1,x2,y2) top-left inclusive, bottom-right exclusive."""
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"expected HxWx3 BGR image; got shape {image.shape!r}")
    h, w = int(image.shape[0]), int(image.shape[1])

    mask = bright_red_mask(image)
    bbox = _bright_red_bbox(mask)
    if bbox is None:
        return (w // 2, h // 2, w, h)

    x1, y1, x2, y2 = bbox
    bw = float(x2 - x1)
    bh = float(y2 - y1)
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    new_bw = bw * 1.2
    new_bh = bh * 1.2

    nx1 = int(round(cx - new_bw / 2.0))
    ny1 = int(round(cy - new_bh / 2.0))
    nx2 = int(round(cx + new_bw / 2.0))
    ny2 = int(round(cy + new_bh / 2.0))

    nx1 = max(0, min(nx1, w))
    ny1 = max(0, min(ny1, h))
    nx2 = max(0, min(nx2, w))
    ny2 = max(0, min(ny2, h))

    if nx2 <= nx1:
        nx2 = min(w, nx1 + 1)
    if ny2 <= ny1:
        ny2 = min(h, ny1 + 1)

    return (nx1, ny1, nx2, ny2)
