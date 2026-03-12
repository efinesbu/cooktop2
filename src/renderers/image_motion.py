"""Image motion 15s renderer: Gemini-generated frames or product images with pans and zooms."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from src import config, db
from src.image_generator import generate_frame_images_for_plan
from src.models import Content, Cost, Product, ProductImage

from .base import BaseRenderer
from .ffmpeg_utils import find_ffmpeg
from .registry import register_renderer

logger = logging.getLogger(__name__)

TARGET_DURATION_SECONDS = 15
TARGET_WIDTH = 720
TARGET_HEIGHT = 1280  # 9:16 vertical


def _select_source_images(
    images: list[ProductImage],
    product_sku: str,
) -> list[Path]:
    """Select 1–3 source images, preferring hero then detail then lifestyle."""
    order = ["hero", "detail", "lifestyle"]
    selected: list[Path] = []
    for img_type in order:
        for img in images:
            if img.image_type == img_type:
                p = Path(img.file_path)
                if p.exists():
                    selected.append(p)
                    if len(selected) >= 3:
                        return selected
    return selected


def _render_single_image_zoompan(
    ffmpeg: str,
    image_path: Path,
    output_path: Path,
    duration_sec: float | int = TARGET_DURATION_SECONDS,
) -> None:
    """Render one image with a slow zoom-in using ffmpeg zoompan filter."""
    # zoompan: zoom in over duration; z=1.1 means 10% zoom over the clip
    fps = 30
    total_frames = int(duration_sec * fps)
    zoom_expr = f"zoompan=z='min(1.1,1+0.1*on/{total_frames})':d=1:s={TARGET_WIDTH}x{TARGET_HEIGHT}:fps={fps}"
    cmd = [
        ffmpeg,
        "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-vf", zoom_expr,
        "-t", str(float(duration_sec)),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _render_multi_image_concatenated(
    ffmpeg: str,
    image_paths: list[Path],
    output_path: Path,
    duration_sec: int = TARGET_DURATION_SECONDS,
    per_frame_durations: list[float] | None = None,
) -> None:
    """Render multiple images as a sequence with zoompan on each.

    If per_frame_durations is provided, use those (in seconds) instead of equal split.
    """
    if len(image_paths) == 1:
        _render_single_image_zoompan(ffmpeg, image_paths[0], output_path, duration_sec)
        return

    if per_frame_durations and len(per_frame_durations) == len(image_paths):
        durations = per_frame_durations
    else:
        per_image_sec = duration_sec / len(image_paths)
        durations = [per_image_sec] * len(image_paths)

    tmp_dir = output_path.parent / f"_tmp_{output_path.stem}"
    tmp_dir.mkdir(exist_ok=True)
    try:
        segments: list[Path] = []
        for i, (img, dur) in enumerate(zip(image_paths, durations)):
            seg = tmp_dir / f"seg_{i}.mp4"
            _render_single_image_zoompan(ffmpeg, img, seg, int(dur) if dur == int(dur) else dur)
            segments.append(seg)

        concat_file = tmp_dir / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{s.absolute()}'" for s in segments),
            encoding="utf-8",
        )
        cmd = [
            ffmpeg, "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        for f in tmp_dir.glob("*"):
            f.unlink(missing_ok=True)
        tmp_dir.rmdir()


def _parse_image_plan_from_manifest(content: Content) -> dict | None:
    """Extract image_plan from content.asset_manifest_json if present."""
    raw = content.asset_manifest_json
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
        return data.get("image_plan") if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


@register_renderer
class ImageMotionRenderer(BaseRenderer):
    """Renders image_motion_15s from Gemini-generated frames or product images with pans and zooms."""

    format_id = "image_motion_15s"

    def render(
        self,
        content: Content,
        product: Product,
        images: list[ProductImage],
    ) -> Path:
        ffmpeg = find_ffmpeg()
        video_dir = config.videos_dir() / product.sku
        video_dir.mkdir(parents=True, exist_ok=True)
        video_path = video_dir / f"{content.id}.mp4"

        plan = _parse_image_plan_from_manifest(content)
        if plan and plan.get("frames"):
            source_paths = generate_frame_images_for_plan(
                content, product, plan, output_dir=video_dir
            )
            frames = plan.get("frames", [])
            per_frame_durations = [
                float(f.get("duration_seconds", 1.5)) for f in frames[: len(source_paths)]
            ]
            total_duration = sum(per_frame_durations)
            _render_multi_image_concatenated(
                ffmpeg,
                source_paths,
                video_path,
                duration_sec=int(total_duration),
                per_frame_durations=per_frame_durations,
            )
            manifest_raw = content.asset_manifest_json or "{}"
            manifest = json.loads(manifest_raw) if manifest_raw else {}
            manifest["format"] = "image_motion_15s"
            manifest["generated_frame_paths"] = [str(p) for p in source_paths]
            manifest["total_duration_seconds"] = total_duration
            db.update_content_video_path(content.id, str(video_path))
            db.update_content_asset_manifest(content.id, json.dumps(manifest))
        else:
            source_paths = _select_source_images(images, product.sku)
            if not source_paths:
                raise ValueError(
                    f"No product images found for {product.sku}. "
                    "Register images with `python cli.py register-images --product <sku>`, "
                    "or ensure image_plan is persisted in asset_manifest_json."
                )
            _render_multi_image_concatenated(
                ffmpeg, source_paths, video_path, TARGET_DURATION_SECONDS
            )
            manifest = {
                "source_images": [str(p) for p in source_paths],
                "format": "image_motion_15s",
            }
            db.update_content_video_path(content.id, str(video_path))
            db.update_content_asset_manifest(content.id, json.dumps(manifest))

        db.insert_cost(Cost(
            content_id=content.id,
            step="image_motion_render",
            api_provider="ffmpeg",
            tokens_or_units=1,
            cost_usd=0.0,
        ))

        logger.info("Saved image motion video to %s", video_path)
        return video_path
