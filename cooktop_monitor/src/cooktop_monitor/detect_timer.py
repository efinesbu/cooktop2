"""Timer digits and MINUTES label."""

from __future__ import annotations

from typing import Any

import numpy as np


def detect_timer(panel_bgr: np.ndarray, layout: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError("detect_timer.detect_timer")
