"""Load images from disk with Pi-camera-friendly formats, EXIF orientation, and resize.

JPEG from a Raspberry Pi camera is the primary path; optional formats (PNG, BMP, TIFF, WEBP,
HEIC/HEIF when pillow-heif is installed) share the same decode → orient → RGB → BGR pipeline.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_LONG_EDGE: int = 2400

_SUPPORTED_EXTS: frozenset[str] = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".bmp",
        ".tif",
        ".tiff",
        ".webp",
        ".heic",
        ".heif",
    }
)

_HEIC_EXTS: frozenset[str] = frozenset({".heic", ".heif"})


def _resize_long_edge(bgr: np.ndarray, max_long_edge: int) -> np.ndarray:
    """Downsample so max(h, w) <= max_long_edge; never upscale. Uses INTER_AREA."""
    h, w = bgr.shape[:2]
    long_edge = max(h, w)
    if long_edge <= max_long_edge:
        return bgr
    scale = max_long_edge / float(long_edge)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _try_register_heif() -> None:
    from pillow_heif import register_heif_opener

    register_heif_opener()


def load_image(path: Path | str) -> np.ndarray:
    """Load an image as BGR uint8, EXIF-oriented, long edge at most :data:`MAX_LONG_EDGE`."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"image file not found: {p}")

    ext = p.suffix.lower()
    if ext not in _SUPPORTED_EXTS:
        raise ValueError(
            f"unsupported image extension {ext!r}; supported: "
            f"{', '.join(sorted(_SUPPORTED_EXTS))}"
        )

    if ext in _HEIC_EXTS:
        try:
            _try_register_heif()
        except ImportError as e:
            raise ValueError(
                "HEIC/HEIF decoding requires the optional 'pillow_heif' package; "
                "install pillow-heif or convert the image to JPEG/PNG."
            ) from e

    with Image.open(p) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        rgb = np.asarray(im)

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    bgr = _resize_long_edge(bgr, MAX_LONG_EDGE)
    return np.ascontiguousarray(bgr)
