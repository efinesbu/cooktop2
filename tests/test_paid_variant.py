"""Phase 7: Tests for paid-seed clone workflow and lineage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src import db
from src.models import Content, PlatformPayload, Product
from src.paid_variant import clone_for_paid


def _mock_variant_captions(content: Content, product: Product, variant_count: int) -> list[dict]:
    """Return deterministic variant data for tests."""
    return [
        {
            "hook_text": f"Variant hook {i}",
            "cta_type": "see_product" if i % 2 == 0 else "shop_now",
            "cta_text": f"try variant {i}",
            "platform_captions": {
                "youtube": f"Variant {i} caption\n\nLink in bio",
                "instagram": f"Variant {i} IG",
                "tiktok": f"Variant {i} TT",
                "x": f"Variant {i} X",
            },
            "hashtags": ["variant", str(i)],
        }
        for i in range(variant_count)
    ]


@pytest.fixture
def winner_content(tmp_db: Path, sample_product: Product) -> Content:
    """Content with video path (organic winner)."""
    db.upsert_product(sample_product)
    video_path = tmp_db.parent / "output" / "test-video.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_text("fake video")
    content = Content(
        id="winner-abc123",
        product_sku=sample_product.sku,
        theme="benefit",
        hook_type="bold_claim",
        hook_text="Original hook",
        cta_type="see_product",
        cta_text="shop now",
        video_local_path=str(video_path),
        creative_format="ai_video_15s",
    )
    db.insert_content(content)
    return content


@patch("src.paid_variant.config.enabled_platforms", return_value=["youtube", "instagram"])
@patch("src.paid_variant.generate_paid_variant_captions", side_effect=_mock_variant_captions)
def test_clone_for_paid_creates_variants_with_lineage(
    mock_gen: object,
    mock_platforms: object,
    winner_content: Content,
    mock_config: dict,
) -> None:
    """Clone creates N content records with source_content_id and platform payloads."""
    created = clone_for_paid(winner_content.id, variant_count=3)

    assert len(created) == 3
    for c in created:
        assert c.source_content_id == winner_content.id
        assert c.video_local_path == winner_content.video_local_path
        assert c.theme == winner_content.theme
        assert c.hook_type == winner_content.hook_type

    # Variants have distinct hook_text and cta
    hooks = [c.hook_text for c in created]
    assert hooks == ["Variant hook 0", "Variant hook 1", "Variant hook 2"]

    # Payloads persisted
    for c in created:
        payloads = db.list_platform_payloads(c.id)
        assert len(payloads) >= 1
        assert any(p.platform == "youtube" for p in payloads)


def test_clone_for_paid_raises_when_content_not_found(tmp_db: Path, mock_config: dict) -> None:
    """Clone raises when source content does not exist."""
    with pytest.raises(ValueError, match="not found"):
        clone_for_paid("nonexistent-id", variant_count=3)


def test_clone_for_paid_raises_when_no_video(winner_content: Content) -> None:
    """Clone raises when source has no video_local_path."""
    # Remove video path
    content_no_video = Content(
        id=winner_content.id,
        product_sku=winner_content.product_sku,
        theme=winner_content.theme,
        hook_type=winner_content.hook_type,
        video_local_path=None,
    )
    with db._connect() as conn:
        conn.execute(
            "UPDATE content SET video_local_path = NULL WHERE id = ?",
            (winner_content.id,),
        )

    with pytest.raises(ValueError, match="no video_local_path"):
        with patch("src.paid_variant.generate_paid_variant_captions", side_effect=_mock_variant_captions):
            clone_for_paid(winner_content.id, variant_count=2)


def test_clone_for_paid_copies_strategy_metadata_json(
    winner_content: Content,
    mock_config: dict,
) -> None:
    """Paid variant clone copies strategy_metadata_json from source content."""
    strategy = {"style_family": "realistic_cinematic", "style_angle": "Curiosity-led product reveal"}
    with db._connect() as conn:
        conn.execute(
            "UPDATE content SET strategy_metadata_json = ? WHERE id = ?",
            (json.dumps(strategy), winner_content.id),
        )

    with patch("src.paid_variant.config.enabled_platforms", return_value=["youtube"]):
        with patch("src.paid_variant.generate_paid_variant_captions", side_effect=_mock_variant_captions):
            created = clone_for_paid(winner_content.id, variant_count=2)

    assert len(created) == 2
    for variant in created:
        assert variant.strategy_metadata_json is not None
        parsed = json.loads(variant.strategy_metadata_json)
        assert parsed["style_family"] == "realistic_cinematic"
        assert parsed["style_angle"] == "Curiosity-led product reveal"
