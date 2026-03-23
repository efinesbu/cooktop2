from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from google import genai
from google.genai import types

from src import config, db
from src.models import Content, Cost, Product

logger = logging.getLogger(__name__)


def _gemini_image_generation_model() -> str:
    """Model id for Gemini image output (image_motion_15s, starting frames, etc.)."""
    return config.gemini_image_model()


# MIME types for reference images
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

# V5 horoscope refs: first match wins (prefer JPEG when both exist).
_HOROSCOPE_REF_EXTENSIONS = (".jpeg", ".jpg", ".png", ".webp")


def _horoscope_reference_path(horoscope: str) -> Path | None:
    """Resolve ``horoscopes/{sign}.{ext}`` for the first existing extension."""
    base = config.horoscopes_dir()
    sign = horoscope.strip().lower()
    for ext in _HOROSCOPE_REF_EXTENSIONS:
        p = base / f"{sign}{ext}"
        if p.is_file():
            return p
    return None


def build_v5_starting_image_prompt(horoscope: str, name: str) -> str:
    """Return the exact Gemini prompt used for V5 horoscope starting frames."""
    sign_words = (horoscope or "").strip().replace("_", " ")
    sign_label = sign_words.title() if sign_words else "Zodiac"
    display_name = (name or "").strip()
    return (
        f"You are given a reference image of a cute chibi-style {sign_label} horoscope creature with big eyes "
        "wearing a nameplate necklace. Reproduce this image exactly -- same character design, pose, "
        "proportions, expression, lighting, background, and overall composition. "
        f"The ONLY two changes: update the 'Jessica' text on the necklace pendant so it clearly reads "
        f"{display_name!r} in metallic gold lettering (legible, engraved or embossed look), and update "
        f"the 'Jessica' text on the top left so it clearly reads {display_name!r}. "
        "Do not alter the character species, zodiac identity, art style, colors, or any other detail. "
        "Output a single high-quality vertical 9:16 frame."
    )


def generate_starting_image(content: Content, product: Product) -> Path:
    """Use Gemini to generate the starting frame image for a video ad.

    The product's hero image (if present) is sent as a reference image so the
    model can match the product's appearance, colors, and branding.

    Returns the path where the generated image was saved.
    """
    api_key = config.get("gemini.api_key")
    model = _gemini_image_generation_model()

    client = genai.Client(api_key=api_key)

    prompt = _build_prompt(content, product)
    reference_image_path = _hero_image_path_for_content(content, product)
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


def generate_v5_starting_image(content: Content, horoscope: str, name: str) -> Path:
    """Generate the V5 horoscope starting frame from a fixed zodiac reference image.

    Loads ``horoscopes/{sign}.jpeg`` / ``.jpg`` / ``.png`` / ``.webp`` (first found; see
    :func:`config.horoscopes_dir`), sends it to Gemini using :func:`config.gemini_v5_model`
    (optional ``gemini.v5_model`` override, else :func:`config.gemini_image_model` — must
    support IMAGE response modalities),
    and asks to preserve the character while updating both visible `Jessica` labels to *name*.

    Returns:
        Path to the saved PNG (``{content.id}_start.png`` under the content's video folder).

    Raises:
        FileNotFoundError: If no reference image exists for the sign.
        ValueError: If Gemini API key is not configured.
    """
    api_key = config.get("gemini.api_key")
    if not api_key:
        raise ValueError(
            "Missing `gemini.api_key` in config.yaml for V5 horoscope starting image generation."
        )
    model = config.gemini_v5_model()
    client = genai.Client(api_key=api_key)

    ref = _horoscope_reference_path(horoscope)
    if ref is None:
        sign = horoscope.strip().lower()
        raise FileNotFoundError(
            f"V5 horoscope reference image not found for sign {sign!r}. "
            f"Add horoscopes/{sign}.jpeg, .jpg, .png, or .webp under the project root."
        )

    prompt = build_v5_starting_image_prompt(horoscope, name)
    contents = _build_contents(prompt, ref)

    sku = (content.product_sku or "horoscope").strip() or "horoscope"
    out_dir = config.videos_dir() / sku
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{content.id}_start.png"

    aspect_ratio = config.get("gemini.aspect_ratio", "9:16")
    image_bytes = _generate_with_retries(client, model, contents, aspect_ratio, max_attempts=3)

    out_path.write_bytes(image_bytes)
    logger.info("Saved V5 horoscope starting image to %s", out_path)

    db.insert_cost(Cost(
        content_id=content.id,
        step="image_gen",
        api_provider="gemini",
        cost_usd=0.0,
    ))

    return out_path


def _product_image_dir(product: Product) -> Path:
    configured_dir = (product.image_dir or "").strip()
    if configured_dir:
        return Path(configured_dir)
    return config.product_images_dir() / product.sku


def _first_hero_image_path(product: Product) -> Path | None:
    """Prefer a hero reference image; fall back to the first supported product image."""
    image_dir = _product_image_dir(product)
    if not image_dir.exists():
        return None

    image_paths = [
        path
        for path in sorted(image_dir.glob("*"))
        if path.is_file() and path.suffix.lower() in _IMAGE_MIME
    ]
    if not image_paths:
        return None

    hero_images = [path for path in image_paths if "hero" in path.stem.lower()]
    return hero_images[0] if hero_images else image_paths[0]


def _hero_image_path_for_content(content: Content, product: Product) -> Path | None:
    """Content-aware hero selection: prefer -nolabel- when non-branded, else use normal hero."""
    image_dir = _product_image_dir(product)
    if not image_dir.exists():
        return None

    image_paths = [
        path
        for path in sorted(image_dir.glob("*"))
        if path.is_file() and path.suffix.lower() in _IMAGE_MIME
    ]
    if not image_paths:
        return None

    hero_images = [path for path in image_paths if "hero" in path.stem.lower()]
    heroes = hero_images if hero_images else image_paths

    if not _velura_branding_enabled(content):
        nolabel_heroes = [p for p in heroes if "-nolabel-" in p.stem]
        if nolabel_heroes:
            return sorted(nolabel_heroes)[0]
    return heroes[0]


def _nolabel_detail_reference_paths(product: Product) -> list[Path]:
    """Return nolabel detail/texture product images for non-branded detail-style frames."""
    image_dir = _product_image_dir(product)
    if not image_dir.exists():
        return []
    paths = [
        p
        for p in image_dir.glob("*")
        if p.is_file()
        and p.suffix.lower() in _IMAGE_MIME
        and "-nolabel-" in p.stem
        and ("detail" in p.stem.lower() or "texture" in p.stem.lower())
    ]
    return sorted(paths)


def _mime_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    return _IMAGE_MIME.get(suffix, "image/png")


def _velura_branding_enabled(content: Content) -> bool:
    raw_manifest = content.asset_manifest_json or ""
    if not raw_manifest.strip():
        return True
    try:
        manifest = json.loads(raw_manifest)
    except json.JSONDecodeError:
        return True
    if not isinstance(manifest, dict):
        return True
    value = manifest.get("velura_branding")
    return value if isinstance(value, bool) else True


def _build_prompt(content: Content, product: Product) -> str:
    base_prompt = content.starting_image_prompt or ""
    velura_branding = _velura_branding_enabled(content)

    if _first_hero_image_path(product):
        if velura_branding:
            base_prompt += (
                f"\n\nUse the attached reference image of the product '{product.name}' "
                "to match its real appearance, packaging, label layout, and visible brand "
                "wordmark as closely as possible. Do not replace, omit, or genericize the "
                "on-pack branding from the reference."
            )
        else:
            base_prompt += (
                f"\n\nUse the attached reference image of the product '{product.name}' "
                "to match its real appearance, colors, packaging, and label layout as closely "
                "as possible. Keep the packaging grounded in the reference without forcing an "
                "added brand wordmark."
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


def generate_frame_images_for_plan(
    content: Content,
    product: Product,
    plan: dict,
    output_dir: Path | None = None,
) -> list[Path]:
    """Generate 3–5 frame images from an image_plan using Gemini.

    Reference rules: hero (always), brand-kit (when branding enabled), models (lifestyle frames only).
    Saves to output_dir/{content_id}_frame_{i}.png. Returns list of paths.
    """
    api_key = config.get("gemini.api_key")
    if not api_key:
        raise ValueError(
            "Missing `gemini.api_key` in config.yaml for image_motion_15s generation."
        )
    model_name = _gemini_image_generation_model()
    aspect_ratio = config.get("gemini.aspect_ratio", "9:16")
    client = genai.Client(api_key=api_key)

    frames = plan.get("frames", [])
    if not frames:
        raise ValueError("image_plan has no frames")

    out_dir = output_dir or (config.videos_dir() / product.sku)
    out_dir.mkdir(parents=True, exist_ok=True)

    hero_path = _hero_image_path_for_content(content, product)
    velura_branding = _velura_branding_enabled(content)
    brand_paths = _brand_reference_paths() if velura_branding else []
    model_paths = _model_reference_paths()

    LIFESTYLE_ROLES = {"lifestyle_portrait", "lifestyle_in_use"}
    DETAIL_STYLE_ROLES = {"hero_macro", "hero_tabletop", "texture_detail"}
    result_paths: list[Path] = []
    for i, frame in enumerate(frames):
        role = (frame.get("role") or "").strip()
        is_lifestyle = role in LIFESTYLE_ROLES
        is_detail_style = role in DETAIL_STYLE_ROLES
        extra_detail_paths = (
            _nolabel_detail_reference_paths(product)
            if (not velura_branding and is_detail_style)
            else []
        )
        ref_paths = _collect_reference_paths(
            hero_path, brand_paths, model_paths, is_lifestyle, extra_detail_paths
        )
        prompt = frame.get("image_prompt") or ""
        if not prompt.strip():
            raise ValueError(f"Frame {i} has no image_prompt")
        if hero_path:
            if velura_branding:
                prompt += (
                    f"\n\nUse the attached reference image of the product '{product.name}' "
                    "to match its real appearance, colors, and branding as closely as possible."
                )
            else:
                prompt += (
                    f"\n\nUse the attached reference image of the product '{product.name}' "
                    "to match its real appearance, colors, packaging, and label layout as "
                    "closely as possible without forcing an explicit wordmark."
                )
        contents = _build_contents_multi(ref_paths, prompt)
        image_bytes = _generate_with_retries(
            client, model_name, contents, aspect_ratio, max_attempts=3
        )
        out_path = out_dir / f"{content.id}_frame_{i}.png"
        out_path.write_bytes(image_bytes)
        result_paths.append(out_path)
        logger.info("Saved frame %d to %s", i, out_path)

    db.insert_cost(Cost(
        content_id=content.id,
        step="image_gen",
        api_provider="gemini",
        tokens_or_units=len(frames),
        cost_usd=0.0,
    ))
    return result_paths


def _brand_reference_paths() -> list[Path]:
    """Return brand-kit reference image paths (up to 3)."""
    brand_dir = config.brand_dir()
    if not brand_dir.exists():
        return []
    paths = sorted(brand_dir.glob("*"))[:3]
    return [p for p in paths if p.is_file() and p.suffix.lower() in _IMAGE_MIME]


def _model_reference_paths() -> list[Path]:
    """Return human-model reference image paths (up to 2)."""
    models_dir = config.models_dir()
    if not models_dir.exists():
        return []
    paths = sorted(models_dir.glob("*"))[:2]
    return [p for p in paths if p.is_file() and p.suffix.lower() in _IMAGE_MIME]


def _collect_reference_paths(
    hero_path: Path | None,
    brand_paths: list[Path],
    model_paths: list[Path],
    include_models: bool,
    extra_paths: list[Path] | None = None,
) -> list[Path]:
    """Collect reference paths: hero + brand + (models if include_models) + extra_paths."""
    out: list[Path] = []
    if hero_path and hero_path.is_file():
        out.append(hero_path)
    for p in brand_paths:
        if p.is_file():
            out.append(p)
    if include_models:
        for p in model_paths:
            if p.is_file():
                out.append(p)
    for p in extra_paths or []:
        if p.is_file():
            out.append(p)
    return out


def _build_contents_multi(
    reference_paths: list[Path],
    prompt: str,
) -> list[types.Part] | str:
    """Build Gemini contents: reference images + text prompt."""
    if not reference_paths:
        return prompt
    parts: list[types.Part] = []
    for p in reference_paths:
        image_bytes = p.read_bytes()
        mime = _mime_type_for_path(p)
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime))
    parts.append(types.Part.from_text(text=prompt))
    return parts


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
            return _extract_image_bytes(response)
        except Exception as exc:
            if attempt == max_attempts:
                raise
            logger.warning("Gemini API attempt %d/%d failed: %s", attempt, max_attempts, exc)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("Exhausted retries without returning")


def _extract_image_bytes(response: object) -> bytes:
    """Return inline image bytes or raise a helpful error for empty Gemini responses."""
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None)
            if data is not None:
                return data
    raise RuntimeError(_describe_response_issue(response))


def _describe_response_issue(response: object) -> str:
    """Summarize blocked or empty Gemini responses for logs and retries."""
    prompt_feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(prompt_feedback, "block_reason", None)
    block_message = getattr(prompt_feedback, "block_reason_message", None)
    if block_reason or block_message:
        details = [f"block_reason={block_reason!r}"] if block_reason else []
        if block_message:
            details.append(f"message={block_message}")
        return "Gemini response contained no image data (" + ", ".join(details) + ")"

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "Gemini response contained no candidates"

    finish_reasons: list[str] = []
    for candidate in candidates:
        finish_reason = getattr(candidate, "finish_reason", None)
        if finish_reason:
            finish_reasons.append(str(finish_reason))

    if finish_reasons:
        return (
            "Gemini response contained no image data "
            f"(finish_reasons={', '.join(finish_reasons)})"
        )

    return "Gemini response contained no image data"
