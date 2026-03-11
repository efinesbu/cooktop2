"""Slideshow 15s renderer: 4–6 product images stitched into a 9:16 short."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from src import config, db
from src.models import Content, Cost, Product, ProductImage

from .base import BaseRenderer
from .ffmpeg_utils import find_ffmpeg
from .registry import register_renderer

logger = logging.getLogger(__name__)

TARGET_DURATION_SECONDS = 15
TARGET_WIDTH = 720
TARGET_HEIGHT = 1280  # 9:16 vertical
MIN_SLIDES = 4
MAX_SLIDES = 6


def _select_slideshow_images(
    images: list[ProductImage],
    product_sku: str,
) -> list[Path]:
    """Select 4–6 images for slideshow, preferring hero, detail, lifestyle."""
    order = ["hero", "detail", "lifestyle"]
    selected: list[Path] = []
    for img_type in order:
        for img in images:
            if img.image_type == img_type:
                p = Path(img.file_path)
                if p.exists():
                    selected.append(p)
                    if len(selected) >= MAX_SLIDES:
                        return selected
    return selected[:MAX_SLIDES]


def _render_slideshow(
    ffmpeg: str,
    image_paths: list[Path],
    output_path: Path,
    duration_sec: int = TARGET_DURATION_SECONDS,
) -> None:
    """Render images as a slideshow with equal duration per slide."""
    if len(image_paths) < MIN_SLIDES:
        raise ValueError(
            f"Slideshow requires at least {MIN_SLIDES} images, got {len(image_paths)}. "
            "Register more product images."
        )

    per_slide_sec = duration_sec / len(image_paths)
    fps = 30

    # Scale and pad each image to 720x1280, then concat
    input_args: list[str] = []
    filters: list[str] = []
    for i, img in enumerate(image_paths):
        input_args.extend(["-loop", "1", "-i", str(img)])
        # scale to fit, pad to 720x1280, set duration
        filters.append(
            f"[{i}:v]scale=720:1280:force_original_aspect_ratio=decrease,"
            f"pad=720:1280:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={fps},trim=duration={per_slide_sec},setpts=PTS-STARTPTS[v{i}]"
        )
    concat_inputs = "".join(f"[v{i}]" for i in range(len(image_paths)))
    filters.append(f"{concat_inputs}concat=n={len(image_paths)}:v=1:a=0[outv]")

    cmd = [
        ffmpeg, "-y",
        *input_args,
        "-filter_complex", ";".join(filters),
        "-map", "[outv]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


@register_renderer
class SlideshowRenderer(BaseRenderer):
    """Renders slideshow_15s from 4–6 product images."""

    format_id = "slideshow_15s"

    def render(
        self,
        content: Content,
        product: Product,
        images: list[ProductImage],
    ) -> Path:
        source_paths = _select_slideshow_images(images, product.sku)
        if len(source_paths) < MIN_SLIDES:
            raise ValueError(
                f"Slideshow requires at least {MIN_SLIDES} product images for {product.sku}. "
                f"Found {len(source_paths)}. Register more with "
                "`python cli.py register-images --product <sku>`."
            )

        ffmpeg = find_ffmpeg()
        video_dir = config.videos_dir() / product.sku
        video_dir.mkdir(parents=True, exist_ok=True)
        video_path = video_dir / f"{content.id}.mp4"

        _render_slideshow(ffmpeg, source_paths, video_path, TARGET_DURATION_SECONDS)

        manifest = {
            "source_images": [str(p) for p in source_paths],
            "format": "slideshow_15s",
        }
        db.update_content_video_path(content.id, str(video_path))
        db.update_content_asset_manifest(content.id, json.dumps(manifest))

        db.insert_cost(Cost(
            content_id=content.id,
            step="slideshow_render",
            api_provider="ffmpeg",
            tokens_or_units=1,
            cost_usd=0.0,
        ))

        logger.info("Saved slideshow video to %s", video_path)
        return video_path
