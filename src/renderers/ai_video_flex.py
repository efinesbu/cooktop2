"""AI video flex 15s renderer: flexible multi-scene video from manifest plan."""

from __future__ import annotations

from pathlib import Path

from src.image_generator import generate_starting_image
from src.models import Content, Product, ProductImage
from src.video_generator import generate_video

from .base import BaseRenderer
from .registry import register_renderer


@register_renderer
class AiVideoFlexRenderer(BaseRenderer):
    """Renders ai_video_flex_15s: multi-scene video from persisted manifest plan."""

    format_id = "ai_video_flex_15s"

    def render(
        self,
        content: Content,
        product: Product,
        images: list[ProductImage],
    ) -> Path:
        starting_image_path = generate_starting_image(content, product)
        return generate_video(content, starting_image_path, product)
