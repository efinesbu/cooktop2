"""Image motion 15s renderer: pans, zooms, and timing on product images."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from src import config, db
from src.models import Content, Cost, Product, ProductImage

from .base import BaseRenderer
from .registry import register_renderer

logger = logging.getLogger(__name__)

TARGET_DURATION_SECONDS = 15
TARGET_WIDTH = 720
TARGET_HEIGHT = 1280  # 9:16 vertical


def _ensure_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg is required for image_motion_15s. "
            "Install it (e.g. apt install ffmpeg, brew install ffmpeg) and ensure it's on PATH."
        )
    return ffmpeg


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
    duration_sec: int = TARGET_DURATION_SECONDS,
) -> None:
    """Render one image with a slow zoom-in using ffmpeg zoompan filter."""
    # zoompan: zoom in over duration; z=1.1 means 10% zoom over the clip
    # 15s at 30fps = 450 frames
    fps = 30
    total_frames = duration_sec * fps
    zoom_expr = f"zoompan=z='min(1.1,1+0.1*on/{total_frames})':d=1:s={TARGET_WIDTH}x{TARGET_HEIGHT}:fps={fps}"
    cmd = [
        ffmpeg,
        "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-vf", zoom_expr,
        "-t", str(duration_sec),
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
) -> None:
    """Render multiple images as a sequence with zoompan on each."""
    if len(image_paths) == 1:
        _render_single_image_zoompan(ffmpeg, image_paths[0], output_path, duration_sec)
        return

    per_image_sec = duration_sec / len(image_paths)
    tmp_dir = output_path.parent / f"_tmp_{output_path.stem}"
    tmp_dir.mkdir(exist_ok=True)
    try:
        segments: list[Path] = []
        for i, img in enumerate(image_paths):
            seg = tmp_dir / f"seg_{i}.mp4"
            _render_single_image_zoompan(ffmpeg, img, seg, int(per_image_sec))
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


@register_renderer
class ImageMotionRenderer(BaseRenderer):
    """Renders image_motion_15s from product images with pans and zooms."""

    format_id = "image_motion_15s"

    def render(
        self,
        content: Content,
        product: Product,
        images: list[ProductImage],
    ) -> Path:
        source_paths = _select_source_images(images, product.sku)
        if not source_paths:
            raise ValueError(
                f"No product images found for {product.sku}. "
                "Register images with `python cli.py register-images --product <sku>`."
            )

        ffmpeg = _ensure_ffmpeg()
        video_dir = config.videos_dir() / product.sku
        video_dir.mkdir(parents=True, exist_ok=True)
        video_path = video_dir / f"{content.id}.mp4"

        _render_multi_image_concatenated(ffmpeg, source_paths, video_path, TARGET_DURATION_SECONDS)

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
