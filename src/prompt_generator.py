from __future__ import annotations

import importlib
import json
import logging
import time
import uuid
from typing import Any

from src import config, db
from src.creative_strategy import whitelist_prompt_lines
from src.models import Content, Cost, HOOK_TYPES, PlatformPayload, Product, ProductImage, THEMES
from src.utm import build_full_utm_link

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an expert creative director and AI video prompt engineer specializing in cosmetic advertising.

TARGET PRODUCT: provided in the user message.

CORE DIRECTIVE
Generate exactly 1 unique creative variation for the target product. Output an image generation prompt and a 30-word video script featuring an anthropomorphic version of this product speaking in the first person.
- The single variation may be fear-based or positive, but it must stay FTC-compliant and visually simple.
- Pick a `theme` and `hook_type` from the allowed whitelist supplied in the user message unless a value is explicitly locked.
- Use the selected `theme` and `hook_type` as real creative direction, not as bookkeeping metadata.
- Return a concise `hook_text` that captures the opening hook in natural spoken language.

STRICT RULES AND CONSTRAINTS
- Each script must be exactly 30 words total, split into two 15-word parts: `scene_1_script` and `scene_2_script`.
- No medical or health claims.
- Use only approved softeners when needed: "appears to", "feels like", "helps skin look", "designed to".
- No before/after treatment framing.
- No quick or drastic movements.
- No more than 1 character in the scene.
- The only scene change allowed is a single hard cut between Scene 1 and Scene 2.
- Use simple vocabulary.
- Keep movements subtle and easy for an AI video generator to render.

RESPOND WITH ONLY valid JSON matching this exact schema — no markdown fences, no commentary:

{
  "theme": "string — chosen from allowed themes in the user message",
  "hook_type": "string — chosen from allowed hook types in the user message",
  "hook_text": "string — short opening hook line for overlay/caption fallback",
  "starting_image_prompt": "string — must describe a cinematic 3D closeup of an anthropomorphic target product standing on a luxury bathroom counter. Include a high-quality Pixar-style face with large expressive eyes and an articulated mouth, soft focus luxury bathroom background, volumetric lighting, octane render, unreal engine 5, 4k, and the brand 'velura' in brown writing using 'Cormorant Garamond', Georgia, 'Times New Roman', serif. Add 1-2 sentences of variation-specific visual detail.",
  "scene_1_desc": "string — 7.5-second shot description that starts with a strong hook and focuses on expression plus minimal, slow movements.",
  "scene_2_desc": "string — 7.5-second shot description that starts with 'HARD CUT' and moves to a new angle with subtle product demo visuals.",
  "scene_1_script": "string — 15 words, first person, simple vocabulary.",
  "scene_2_script": "string — 15 words, first person, FTC-compliant benefits, ending with a call to action.",
  "platform_captions": {
    "youtube": "string — YouTube Shorts caption (max 100 chars, keyword-rich)",
    "instagram": "string — Instagram Reels caption (conversational, emoji-friendly, 1-2 sentences)",
    "tiktok": "string — TikTok caption (trendy, casual, max 150 chars)",
    "x": "string — X/Twitter caption (max 280 chars, concise and punchy)"
  },
  "hashtags": ["list", "of", "relevant", "hashtags", "without #"]
}

RULES:
- `theme` must exactly match one allowed theme from the user message.
- `hook_type` must exactly match one allowed hook type from the user message.
- Voiceover scripts must sound natural when spoken aloud.
- `scene_1_script` must be 10-20 words.
- `scene_2_script` must be 10-20 words.
- The `starting_image_prompt` must stay visually grounded in a luxury bathroom counter setup.
- Keep the anthropomorphic product as the only character.
- Keep the total video pacing to 15 seconds.
"""


def _build_user_message(
    product: Product,
    theme: str | None,
    hook_type: str | None,
    product_images: list[ProductImage],
) -> str:
    theme_ids = [theme] if theme else None
    hook_ids = [hook_type] if hook_type else None
    lines = [
        f"Product: {product.name}",
        f"SKU: {product.sku}",
        f"Category: {product.category or 'general'}",
        f"Price: ${product.price:.2f}" if product.price else "Price: not set",
    ]
    if theme or hook_type:
        lines.append("Locked creative constraints:")
        if theme:
            lines.append(f"  - Theme must be: {theme}")
        if hook_type:
            lines.append(f"  - Hook type must be: {hook_type}")
    else:
        lines.append("Creative selection task:")
        lines.append("  - Choose the strongest theme and hook type from the whitelist below.")
        lines.append("  - Avoid picking overlapping strategies just because they sound dramatic.")
    lines.extend(whitelist_prompt_lines(theme_ids=theme_ids, hook_ids=hook_ids))
    if product_images:
        img_descriptions = [
            f"  - {img.image_type}: {img.file_path}" for img in product_images
        ]
        lines.append("Available product images:")
        lines.extend(img_descriptions)
    return "\n".join(lines)


def generate_content(
    product: Product,
    theme: str | None,
    hook_type: str | None,
    product_images: list[ProductImage],
) -> tuple[Content, dict]:
    """Call OpenAI to generate a structured content script for a 15-second video ad.

    Returns (Content persisted to DB, dict with platform_captions and hashtags).
    """
    api_key = config.get("openai.api_key")
    if not api_key:
        raise ValueError(
            "Missing `openai.api_key` in config.yaml. "
            "Copy config.example.yaml to config.yaml and add your OpenAI credentials."
        )

    model = config.get("openai.model", "gpt-4.1-mini")
    openai_module = _load_openai_module()
    client = openai_module.OpenAI(api_key=api_key)

    user_msg = _build_user_message(product, theme, hook_type, product_images)
    content_id = uuid.uuid4().hex[:16]

    response = _call_with_retries(
        client,
        openai_module,
        model,
        user_msg,
        max_attempts=3,
    )

    parsed = _parse_response(response, theme=theme, hook_type=hook_type)

    content = Content(
        id=content_id,
        product_sku=product.sku,
        theme=parsed["theme"],
        hook_type=parsed["hook_type"],
        hook_text=parsed["hook_text"],
        starting_image_prompt=parsed["starting_image_prompt"],
        scene_1_desc=parsed["scene_1_desc"],
        scene_2_desc=parsed["scene_2_desc"],
        scene_1_script=parsed["scene_1_script"],
        scene_2_script=parsed["scene_2_script"],
    )
    db.insert_content(content)

    platform_captions: dict[str, str] = parsed.get("platform_captions", {})
    hashtags = parsed.get("hashtags", [])
    hashtag_csv = ",".join(tag.strip().lstrip("#") for tag in hashtags if tag.strip())
    utm_url = build_full_utm_link(content, product)
    for platform in config.enabled_platforms("posting"):
        payload = PlatformPayload(
            content_id=content.id,
            platform=platform,
            caption=platform_captions.get(platform, content.hook_text or product.name),
            hashtags=hashtag_csv,
            utm_url=utm_url,
        )
        payload.id = db.upsert_platform_payload(payload)

    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    input_per_m = float(config.get("openai.input_per_million_usd", 2.50))
    output_per_m = float(config.get("openai.output_per_million_usd", 15.0))
    cost_usd = (input_tokens / 1_000_000 * input_per_m) + (output_tokens / 1_000_000 * output_per_m)
    db.insert_cost(Cost(
        content_id=content_id,
        step="prompt_gen",
        api_provider="openai",
        tokens_or_units=input_tokens + output_tokens,
        cost_usd=cost_usd,
    ))

    extras = {
        "platform_captions": platform_captions,
        "hashtags": hashtags,
    }
    return content, extras


def _load_openai_module() -> Any:
    try:
        return importlib.import_module("openai")
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI SDK is not installed. Run `pip install -r requirements.txt`."
        ) from exc


def _call_with_retries(
    client: Any,
    openai_module: Any,
    model: str,
    user_msg: str,
    max_attempts: int = 3,
) -> Any:
    delay = 2.0
    for attempt in range(1, max_attempts + 1):
        try:
            return client.chat.completions.create(
                model=model,
                max_completion_tokens=1500,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
        except (
            openai_module.APIConnectionError,
            openai_module.RateLimitError,
            openai_module.APIStatusError,
        ) as exc:
            if attempt == max_attempts:
                raise
            logger.warning("OpenAI API attempt %d/%d failed: %s", attempt, max_attempts, exc)
            time.sleep(delay)
            delay *= 2


def _parse_response(
    response: Any,
    theme: str | None = None,
    hook_type: str | None = None,
) -> dict:
    raw = _response_text(response)
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OpenAI returned invalid JSON: {exc}\n\nRaw response:\n{raw}") from exc

    required = [
        "theme", "hook_type", "hook_text",
        "starting_image_prompt",
        "scene_1_desc", "scene_2_desc",
        "scene_1_script", "scene_2_script",
        "platform_captions", "hashtags",
    ]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"OpenAI response missing required fields: {missing}")
    _validate_response_shape(data, theme=theme, hook_type=hook_type)
    return data


def _validate_response_shape(
    data: dict[str, Any],
    theme: str | None = None,
    hook_type: str | None = None,
) -> None:
    if not isinstance(data["theme"], str) or not data["theme"].strip():
        raise ValueError("OpenAI response field `theme` must be a non-empty string.")
    if not isinstance(data["hook_type"], str) or not data["hook_type"].strip():
        raise ValueError("OpenAI response field `hook_type` must be a non-empty string.")
    if not isinstance(data["hook_text"], str) or not data["hook_text"].strip():
        raise ValueError("OpenAI response field `hook_text` must be a non-empty string.")

    returned_theme = data["theme"].strip()
    returned_hook = data["hook_type"].strip()
    if returned_theme not in THEMES:
        raise ValueError(
            f"OpenAI response theme '{returned_theme}' not in whitelist. Allowed: {', '.join(THEMES)}"
        )
    if returned_hook not in HOOK_TYPES:
        raise ValueError(
            f"OpenAI response hook_type '{returned_hook}' not in whitelist. Allowed: {', '.join(HOOK_TYPES)}"
        )

    if theme and returned_theme != theme:
        raise ValueError(
            f"OpenAI response theme '{returned_theme}' did not match locked theme '{theme}'."
        )
    if hook_type and returned_hook != hook_type:
        raise ValueError(
            f"OpenAI response hook_type '{returned_hook}' did not match locked hook_type '{hook_type}'."
        )

    if not isinstance(data["platform_captions"], dict):
        raise ValueError("OpenAI response field `platform_captions` must be an object.")

    caption_keys = {"youtube", "instagram", "tiktok", "x"}
    missing_caption_keys = caption_keys.difference(data["platform_captions"])
    if missing_caption_keys:
        raise ValueError(
            "OpenAI response `platform_captions` missing keys: "
            f"{sorted(missing_caption_keys)}"
        )

    if not isinstance(data["hashtags"], list):
        raise ValueError("OpenAI response field `hashtags` must be a list.")


def _response_text(response: Any) -> str:
    choice = response.choices[0]
    message = choice.message
    raw = message.content or ""
    if not raw.strip():
        raise ValueError("OpenAI returned an empty response.")
    return raw.strip()
