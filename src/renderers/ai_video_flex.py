"""AI video flex 15s renderer: flexible multi-scene video from manifest plan."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src import config, db
from src.image_generator import generate_starting_image
from src.models import Content, Product, ProductImage
from src.video_generator import generate_video
from src.voiceover_generator import generate_voiceover

from .base import BaseRenderer
from .ffmpeg_utils import _mux_audio_into_video, _tts_enabled_for_format, find_ffmpeg
from .registry import register_renderer

logger = logging.getLogger(__name__)


def _artifact_dir(product_sku: str, content_id: str) -> Path:
    """Store ai-video-flex intermediates away from the final video folder."""
    path = config.data_root() / "render-artifacts" / "ai-video-flex" / product_sku / content_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_manifest(content: Content) -> dict:
    """Parse asset_manifest_json into a dict. Returns {} if invalid."""
    raw = content.asset_manifest_json
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


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
        manifest = _parse_manifest(content)
        voiceover_plan = manifest.get("voiceover_plan")
        use_tts = (
            _tts_enabled_for_format(content.creative_format)
            and isinstance(voiceover_plan, dict)
            and voiceover_plan.get("voiceover_script")
            and voiceover_plan.get("voice")
        )

        starting_image_path = generate_starting_image(content, product)
        video_path = generate_video(content, starting_image_path, product)
        if not use_tts:
            return video_path

        artifact_dir = _artifact_dir(product.sku, content.id)
        silent_path = artifact_dir / f"{content.id}_silent.mp4"
        audio_path = artifact_dir / f"{content.id}_voiceover.wav"
        ffmpeg = find_ffmpeg()

        video_path.replace(silent_path)
        manifest["silent_video_local_path"] = str(silent_path)
        generate_voiceover(
            script=voiceover_plan["voiceover_script"],
            voice=voiceover_plan["voice"],
            voice_instructions=voiceover_plan.get("voice_instructions", ""),
            output_path=audio_path,
            content_id=content.id,
            language=voiceover_plan.get("language"),
        )
        manifest["audio_local_path"] = str(audio_path)
        _mux_audio_into_video(ffmpeg, silent_path, audio_path, video_path)

        db.update_content_video_path(content.id, str(video_path))
        db.update_content_asset_manifest(content.id, json.dumps(manifest))
        logger.info("Saved ai video flex video with stitched voiceover to %s", video_path)
        return video_path
