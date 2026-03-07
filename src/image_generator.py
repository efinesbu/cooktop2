from __future__ import annotations

import logging
import time
from pathlib import Path

from google import genai
from google.genai import types

from src import config, db
from src.models import Content, Cost, Product

logger = logging.getLogger(__name__)

# MIME types for reference images
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def generate_starting_image(content: Content, product: Product) -> Path:
    """Use Gemini to generate the starting frame image for a video ad.

    The product's hero image (if present) is sent as a reference image so the
    model can match the product's appearance, colors, and branding.

    Returns the path where the generated image was saved.
    """
    api_key = config.get("gemini.api_key")
    model = config.get("gemini.model", "gemini-2.0-flash")

    client = genai.Client(api_key=api_key)

    prompt = _build_prompt(content, product)
    reference_image_path = _first_hero_image_path(product)
    contents = _build_contents(prompt, reference_image_path)

    out_dir = config.videos_dir() / product.sku
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{content.id}_start.png"

    aspect_ratio = config.get("gemini.aspect_ratio", "9:16")
    image_bytes = _generate_with_retries(client, model, contents, aspect_ratio, max_attempts=3)

    out_path.write_bytes(image_bytes)
    logger.info("Saved starting image to %s", out_path)

    db.insert_cost(Cost(
        content_id=content.id,
        step="image_gen",
        api_provider="gemini",
        cost_usd=0.0,
    ))

    return out_path


def _first_hero_image_path(product: Product) -> Path | None:
    """Return the path of the first product hero image, or None if none exist."""
    hero_dir = config.product_images_dir() / product.sku
    if not hero_dir.exists():
        return None
    hero_images = sorted(hero_dir.glob("*"))
    return hero_images[0] if hero_images else None


def _mime_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    return _IMAGE_MIME.get(suffix, "image/png")


def _build_prompt(content: Content, product: Product) -> str:
    base_prompt = content.starting_image_prompt or ""

    if _first_hero_image_path(product):
        base_prompt += (
            f"\n\nUse the attached reference image of the product '{product.name}' "
            "to match its real appearance, colors, and branding as closely as possible."
        )

    return base_prompt


def _build_contents(
    prompt: str,
    reference_image_path: Path | None,
) -> list[types.Part] | str:
    """Build the contents sent to Gemini: optional reference image part + text prompt."""
    if reference_image_path is None or not reference_image_path.is_file():
        return prompt

    image_bytes = reference_image_path.read_bytes()
    mime_type = _mime_type_for_path(reference_image_path)
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    return [image_part, prompt]


def _generate_with_retries(
    client: genai.Client,
    model: str,
    contents: list[types.Part] | str,
    aspect_ratio: str = "9:16",
    max_attempts: int = 3,
) -> bytes:
    delay = 2.0
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
                ),
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    return part.inline_data.data
            raise RuntimeError("Gemini response contained no image data")
        except Exception as exc:
            if attempt == max_attempts:
                raise
            logger.warning("Gemini API attempt %d/%d failed: %s", attempt, max_attempts, exc)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("Exhausted retries without returning")
