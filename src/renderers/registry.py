"""Registry of format -> renderer and orchestration entrypoint."""

from __future__ import annotations

from pathlib import Path

from src.models import Content, CREATIVE_FORMATS, Product, ProductImage

from .base import BaseRenderer

_RENDERERS: dict[str, type[BaseRenderer]] = {}


def register_renderer(renderer_cls: type[BaseRenderer]) -> type[BaseRenderer]:
    """Register a renderer for its format_id."""
    fmt = renderer_cls.format_id
    if not fmt:
        raise ValueError("Renderer must define a non-empty format_id")
    if fmt not in CREATIVE_FORMATS:
        raise ValueError(
            f"Renderer format_id '{fmt}' not in CREATIVE_FORMATS: {CREATIVE_FORMATS}"
        )
    _RENDERERS[fmt] = renderer_cls
    return renderer_cls


def get_renderer(creative_format: str) -> BaseRenderer | None:
    """Return the renderer for the given format, or None if not implemented."""
    _ensure_registry()
    cls = _RENDERERS.get(creative_format)
    return cls() if cls else None


def render_media(
    content: Content,
    product: Product,
    images: list[ProductImage],
) -> Path:
    """Orchestrate format-aware rendering. Returns path to the output MP4.

    Raises ValueError if creative_format is unknown or has no registered renderer.
    """
    _ensure_registry()
    fmt = content.creative_format or "ai_video_15s"
    renderer = get_renderer(fmt)
    if renderer is None:
        raise ValueError(
            f"No renderer registered for format '{fmt}'. "
            f"Supported: {sorted(_RENDERERS.keys())}"
        )
    return renderer.render(content, product, images)


def _register_builtin_renderers() -> None:
    """Import and register built-in renderers. Called on first use."""
    if _RENDERERS:
        return
    from . import ai_video  # noqa: F401  # registers on import
    from . import image_motion  # noqa: F401
    from . import slideshow  # noqa: F401


# Lazy init on first get_renderer or render_media call
def _ensure_registry() -> None:
    if not _RENDERERS:
        _register_builtin_renderers()
