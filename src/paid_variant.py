"""Phase 7: Lightweight paid-seed clone workflow.

Clones organic winners into 3–5 ad-safe variants by varying CTA, hook, caption.
Preserves lineage from paid variant back to source creative.
"""

from __future__ import annotations

import uuid
from typing import Optional

from src import config, db
from src.models import Content, PlatformPayload, Product
from src.prompt_generator import generate_paid_variant_captions
from src.utm import build_attribution_data


def clone_for_paid(
    source_content_id: str,
    variant_count: int = 5,
) -> list[Content]:
    """Clone an organic winner into N ad-safe variants.

    Preserves video, scripts, theme, hook_type, creative_format.
    Varies hook_text, cta_type, cta_text, platform_captions per variant.
    Returns the list of created Content records.
    """
    content = db.get_content(source_content_id)
    if not content:
        raise ValueError(f"Content {source_content_id} not found.")

    product = db.get_product(content.product_sku)
    if not product:
        raise ValueError(f"Product {content.product_sku} not found.")

    if not content.video_local_path:
        raise ValueError(
            f"Content {source_content_id} has no video_local_path. "
            "Clone only works for content with a rendered video."
        )

    variants_data = generate_paid_variant_captions(content, product, variant_count)
    if not variants_data:
        raise ValueError("No valid variants generated.")

    created: list[Content] = []
    for v in variants_data:
        variant_id = uuid.uuid4().hex[:16]
        variant = Content(
            id=variant_id,
            product_sku=content.product_sku,
            theme=content.theme,
            hook_type=content.hook_type,
            hook_text=v.get("hook_text") or content.hook_text,
            starting_image_prompt=content.starting_image_prompt,
            scene_1_desc=content.scene_1_desc,
            scene_2_desc=content.scene_2_desc,
            scene_1_script=content.scene_1_script,
            scene_2_script=content.scene_2_script,
            video_local_path=content.video_local_path,
            approved=False,
            review_status="pending",
            creative_format=content.creative_format or "ai_video_15s",
            cta_type=v.get("cta_type", "see_product"),
            cta_text=v.get("cta_text"),
            problem_angle=content.problem_angle,
            proof_type=content.proof_type,
            script_style=content.script_style,
            research_snapshot_id=content.research_snapshot_id,
            asset_manifest_json=content.asset_manifest_json,
            source_content_id=source_content_id,
        )
        db.insert_content(variant)

        platform_captions = v.get("platform_captions", {})
        hashtags = v.get("hashtags", [])
        hashtag_csv = ",".join(tag.strip().lstrip("#") for tag in hashtags if tag.strip())

        for platform in config.enabled_platforms("posting"):
            attr_data = build_attribution_data(variant, product, platform)
            payload = PlatformPayload(
                content_id=variant.id,
                platform=platform,
                caption=platform_captions.get(platform, variant.hook_text or product.name),
                hashtags=hashtag_csv,
                utm_url=attr_data.get("utm_url"),
                destination_url=attr_data.get("destination_url"),
                utm_source=attr_data.get("utm_source"),
                utm_medium=attr_data.get("utm_medium"),
                utm_campaign=attr_data.get("utm_campaign"),
                utm_content=attr_data.get("utm_content"),
                link_mode=attr_data.get("link_mode", "direct"),
            )
            payload.id = db.upsert_platform_payload(payload)

        created.append(variant)

    return created


