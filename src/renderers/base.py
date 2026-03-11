"""Base renderer protocol for format-aware media generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.models import Content, Product, ProductImage


class BaseRenderer(ABC):
    """Renderer that produces a local MP4 for a given creative format."""

    format_id: str = ""

    @abstractmethod
    def render(
        self,
        content: "Content",
        product: "Product",
        images: list["ProductImage"],
    ) -> Path:
        """Produce a local MP4 and return its path.

        Must persist video_local_path on content via db.update_content_video_path.
        May persist asset_manifest_json for regeneration/debugging.
        """
        ...
