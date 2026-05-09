"""Control Lock text ROI lit-pixel detection."""

from __future__ import annotations

from typing import Any

import numpy as np


def detect_lock(panel_bgr: np.ndarray, layout: dict[str, Any]) -> bool:
    raise NotImplementedError("detect_lock.detect_lock")
