"""Format-aware media rendering orchestration.

All formats output a durable local MP4 so review, scheduling, and posting
remain unchanged. Renderers are selected by content.creative_format.
"""

from __future__ import annotations

from pathlib import Path

from src.models import Content, Product, ProductImage

from .base import BaseRenderer
from .registry import get_renderer, render_media, register_renderer

__all__ = [
    "BaseRenderer",
    "get_renderer",
    "render_media",
    "register_renderer",
]
