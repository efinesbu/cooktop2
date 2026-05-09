"""Digit recognition: template match + tesseract fallback."""

from __future__ import annotations

import numpy as np


def ocr_digits(roi_bgr: np.ndarray, bright_mask: np.ndarray) -> str | None:
    raise NotImplementedError("ocr.ocr_digits")
