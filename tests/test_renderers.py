"""Tests for format-aware renderer orchestration and selection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models import Content, Product, ProductImage
from src.renderers import get_renderer, render_media


def test_get_renderer_returns_ai_video_for_ai_video_15s() -> None:
    r = get_renderer("ai_video_15s")
    assert r is not None
    assert r.format_id == "ai_video_15s"


def test_get_renderer_returns_renderer_for_all_builtin_formats() -> None:
    r = get_renderer("image_motion_15s")
    assert r is not None
    assert r.format_id == "image_motion_15s"

    r = get_renderer("slideshow_15s")
    assert r is not None
    assert r.format_id == "slideshow_15s"


def test_get_renderer_returns_none_for_unknown_format() -> None:
    r = get_renderer("unknown_format")
    assert r is None


def test_render_media_raises_for_unknown_format(
    tmp_db: Path,
    sample_product: Product,
    sample_content: Content,
) -> None:
    from src import db

    db.upsert_product(sample_product)

    content = Content(
        id="test-001",
        product_sku=sample_product.sku,
        theme="benefit",
        hook_type="bold_claim",
        creative_format="unknown_format",
    )
    images: list[ProductImage] = []

    with pytest.raises(ValueError) as exc_info:
        render_media(content, sample_product, images)

    assert "No renderer registered for format 'unknown_format'" in str(exc_info.value)
    assert "Supported:" in str(exc_info.value)


def test_render_media_ai_video_invokes_image_and_video_generators(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
    sample_product: Product,
) -> None:
    from src import db

    db.upsert_product(sample_product)

    content = Content(
        id="test-002",
        product_sku=sample_product.sku,
        theme="benefit",
        hook_type="bold_claim",
        creative_format="ai_video_15s",
        starting_image_prompt="A product on a counter",
        scene_1_desc="Scene 1",
        scene_2_desc="Scene 2",
        scene_1_script="First line.",
        scene_2_script="Second line.",
    )
    images: list[ProductImage] = []

    video_path = tmp_path / "output.mp4"
    video_path.write_bytes(b"fake-video")

    image_path = tmp_path / "start.png"
    image_path.write_bytes(b"fake-image")

    image_calls: list[tuple] = []
    video_calls: list[tuple] = []

    def fake_generate_starting_image(c, p):
        image_calls.append((c, p))
        return image_path

    def fake_generate_video(c, start_path, p):
        video_calls.append((c, start_path, p))
        db.update_content_video_path(c.id, str(video_path))
        return video_path

    monkeypatch.setattr("src.renderers.ai_video.generate_starting_image", fake_generate_starting_image)
    monkeypatch.setattr("src.renderers.ai_video.generate_video", fake_generate_video)

    monkeypatch.setattr(
        "src.config._config",
        {"data_root": str(tmp_path / "velura-data")},
    )

    db.insert_content(content)
    result = render_media(content, sample_product, images)

    assert result == video_path
    assert len(image_calls) == 1
    assert image_calls[0][0].id == content.id
    assert image_calls[0][1].sku == sample_product.sku
    assert len(video_calls) == 1
    assert video_calls[0][0].id == content.id
    assert video_calls[0][1] == image_path
    assert video_calls[0][2].sku == sample_product.sku

    updated = db.get_content(content.id)
    assert updated is not None
    assert updated.video_local_path == str(video_path)
