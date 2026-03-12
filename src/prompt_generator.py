from __future__ import annotations

import importlib
import json
import logging
import time
import uuid
from typing import Any

from src import config, db
from src.creative_strategy import whitelist_prompt_lines
from src.organic_evaluation import get_image_motion_performance_summary
from src.models import (
    Content, Cost, CTA_TYPES, CREATIVE_FORMATS, HOOK_TYPES,
    PlatformPayload, Product, ProductImage, PROOF_TYPES, SCRIPT_STYLES, THEMES,
)
from src.utm import build_attribution_data

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
  "creative_format": "string — must be 'ai_video_15s' for this generation",
  "cta_type": "string — chosen from: see_product, shop_now",
  "cta_text": "string — the exact CTA phrase used in scene_2_script (e.g. 'try me today', 'shop now')",
  "problem_angle": "string — one-line description of the problem/angle if theme is problem_solution, else null",
  "proof_type": "string — chosen from: test_result, testimonial, before_after, ingredient, none",
  "script_style": "string — chosen from: conversational, direct, storytelling, tip_based",
  "starting_image_prompt": "string — must describe a cinematic 3D closeup of an anthropomorphic target product standing on a luxury bathroom counter. Include a high-quality Pixar-style face with large expressive eyes and an articulated mouth, soft focus luxury bathroom background, volumetric lighting, octane render, unreal engine 5, 4k, and the brand 'velura' in brown writing using font style Cormorant Garamond, Georgia, Times New Roman, serif. Add 1-2 sentences of variation-specific visual detail.",
  "scene_1_desc": "string — 7.5-second shot description that starts with a strong hook and focuses on expression plus minimal, slow movements. accurate lipsync with the voiceover script.",
  "scene_2_desc": "string — 7.5-second shot description that starts with 'HARD CUT' and moves to a new angle with subtle product demo visuals. accurate lipsync with the voiceover script.",
  "scene_1_script": "string — 15 words, first person, simple vocabulary.",
  "scene_2_script": "string — 15 words, first person, FTC-compliant benefits, ending with a call to action.",
  "platform_captions": {
    "youtube": "string — YouTube Shorts caption (max 100 chars, keyword-rich, must end with 'Link in bio')",
    "instagram": "string — Instagram Reels caption (conversational, emoji-friendly, 1-2 sentences)",
    "tiktok": "string — TikTok caption (trendy, casual, max 150 chars)",
    "x": "string — X/Twitter caption (max 280 chars, concise and punchy)"
  },
  "hashtags": ["list", "of", "relevant", "hashtags", "without #"]
}

RULES:
- `theme` must exactly match one allowed theme from the user message.
- `hook_type` must exactly match one allowed hook type from the user message.
- `creative_format` must be exactly 'ai_video_15s'.
- `cta_type` must be one of: see_product, shop_now.
- `proof_type` must be one of: test_result, testimonial, before_after, ingredient, none.
- `script_style` must be one of: conversational, direct, storytelling, tip_based.
- Voiceover scripts must sound natural when spoken aloud.
- `scene_1_script` must be 10-20 words.
- `scene_2_script` must be 10-20 words.
- The `starting_image_prompt` must stay visually grounded in a luxury bathroom counter setup.
- Keep the anthropomorphic product as the only character.
- Keep the total video pacing to 15 seconds.
"""

_SIMPLIFIED_SYSTEM_PROMPT = """\
You are an expert creative director for cosmetic advertising.

TARGET PRODUCT: provided in the user message.

CORE DIRECTIVE
Generate exactly 1 unique creative variation for the target product. Output a short hook and platform captions for a slideshow or image-motion format (no AI video generation).
- Pick a `theme` and `hook_type` from the allowed whitelist unless locked.
- Return a concise `hook_text` that captures the opening hook.
- No medical or health claims. Use only approved softeners: "appears to", "feels like", "helps skin look".

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "theme": "string — from allowed themes",
  "hook_type": "string — from allowed hook types",
  "hook_text": "string — short opening hook line",
  "creative_format": "string — must match the locked format in user message",
  "cta_type": "string — see_product or shop_now",
  "cta_text": "string — CTA phrase (e.g. 'try me today', 'shop now')",
  "problem_angle": "string or null",
  "proof_type": "string — test_result, testimonial, before_after, ingredient, none",
  "script_style": "string — conversational, direct, storytelling, tip_based",
  "platform_captions": {
    "youtube": "string — max 100 chars, end with 'Link in bio'",
    "instagram": "string — conversational, emoji-friendly",
    "tiktok": "string — trendy, max 150 chars",
    "x": "string — max 280 chars"
  },
  "hashtags": ["list", "of", "hashtags", "without #"]
}
"""

_IMAGE_MOTION_SYSTEM_PROMPT = """\
You are an expert creative director for cosmetic image-motion ads.

TARGET PRODUCT: provided in the user message.

CORE DIRECTIVE
Generate exactly 1 unique creative for image_motion_15s: a 3–5 frame vertical (9:16) image sequence.
- Pick theme and hook_type from the allowed whitelist unless locked.
- Return hook_text, platform_captions, hashtags.
- Also return an image_plan: a structured multi-frame plan for Gemini to generate 3–5 images.

CONTROLLED VARIETY (use this vocabulary; vary at most 1–2 axes per creative):
- style_family: anamorphic, realistic_cinematic
- frame_role: hero_macro, hero_tabletop, texture_detail, lifestyle_portrait, lifestyle_in_use
- lighting: golden_window_light, soft_diffused_daylight, clean_studio_backlight
- camera_distance: macro_closeup, closeup, medium_shot

PLANNER RULES:
- Require at least 1 hero-led frame (hero_macro, hero_tabletop, or texture_detail) in every sequence.
- Allow lifestyle frames (lifestyle_portrait, lifestyle_in_use) only when model reference assets exist (check user message).
- total_duration_seconds must be <= 15; shorter clips are allowed (e.g. 9–12 seconds).
- Each frame duration_seconds: 1.5–2.0.
- Bias style/role mix from PERFORMANCE_SUMMARY when provided.

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "theme": "string — from allowed themes",
  "hook_type": "string — from allowed hook types",
  "hook_text": "string — short opening hook line",
  "creative_format": "image_motion_15s",
  "cta_type": "string — see_product or shop_now",
  "cta_text": "string — CTA phrase",
  "problem_angle": "string or null",
  "proof_type": "string — test_result, testimonial, before_after, ingredient, none",
  "script_style": "string — conversational, direct, storytelling, tip_based",
  "platform_captions": {
    "youtube": "string — max 100 chars, end with 'Link in bio'",
    "instagram": "string — conversational, emoji-friendly",
    "tiktok": "string — trendy, max 150 chars",
    "x": "string — max 280 chars"
  },
  "hashtags": ["list", "of", "hashtags", "without #"],
  "image_plan": {
    "strategy_summary": "string — one-line creative strategy for this sequence",
    "total_duration_seconds": number — sum of frame durations, <= 15,
    "performance_rationale": "string — product_winners, global_winners, or default",
    "frames": [
      {
        "role": "string — hero_macro | hero_tabletop | texture_detail | lifestyle_portrait | lifestyle_in_use",
        "duration_seconds": number — 1.5 to 2.0,
        "style_family": "string — anamorphic | realistic_cinematic",
        "lighting": "string — golden_window_light | soft_diffused_daylight | clean_studio_backlight",
        "camera_distance": "string — macro_closeup | closeup | medium_shot",
        "image_prompt": "string — exact prompt for Gemini to generate this frame; include product name, style, lighting, composition"
      }
    ]
  }
}
"""

# TTS voiceover: bounded script templates and brand guardrails for image_motion_15s
TTS_VOICE_INSTRUCTIONS = (
    "Speak in a calm, premium, reassuring tone for a luxury skincare brand. "
    "Sound polished, warm, and confident. Keep the pace slightly unhurried and never overly salesy or bubbly."
)
TTS_VOICES = ("marin",)
TTS_SCRIPT_TEMPLATES = ("caption_led", "strategy_led", "proof_led")
TTS_WORDS_PER_SECOND_MAX = 2.5
TTS_WORDS_PER_SECOND_MIN = 2.1
VOICEOVER_TARGET_WORDS_PER_SECOND = 2.3
VOICEOVER_END_BUFFER_MIN_SECONDS = 0.5
VOICEOVER_END_BUFFER_TARGET_SECONDS = 0.75
VOICEOVER_END_BUFFER_MAX_SECONDS = 1.0
VOICEOVER_GUARDRAIL_MAX_ATTEMPTS = 3
# Patterns to reject or normalize (brand guardrails)
TTS_GUARDRAIL_FORBIDDEN = (
    "guarantee", "guaranteed", "miracle", "instant", "overnight", "permanent",
    "cure", "heal", "treat", "medical", "clinical", "dermatologist-approved",
    "omg", "lol", "slay", "vibes", "yass", "bae", "lit", "fire",
    "limited time", "act now", "don't miss", "hurry", "last chance",
    "before and after", "transformation", "dramatic results",
)
TTS_GUARDRAIL_SOFTENERS = ("appears to", "feels like", "helps skin look", "designed to")


def _build_voiceover_script_caption_led(parsed: dict, product_name: str) -> str:
    """Caption-led: hook_text + CTA."""
    hook = (parsed.get("hook_text") or "").strip()
    cta = (parsed.get("cta_text") or "see the product").strip()
    if not hook:
        hook = product_name
    return f"{hook}. {cta}."


def _build_voiceover_script_strategy_led(parsed: dict, product_name: str) -> str:
    """Strategy-led: strategy_summary + product + CTA."""
    plan = parsed.get("image_plan") or {}
    strategy = (plan.get("strategy_summary") or "").strip()
    cta = (parsed.get("cta_text") or "see the product").strip()
    if not strategy:
        strategy = f"{product_name} for your routine."
    return f"{strategy} {product_name}. {cta}."


def _build_voiceover_script_proof_led(parsed: dict, product_name: str) -> str:
    """Proof-led: soft proof framing + CTA."""
    cta = (parsed.get("cta_text") or "see the product").strip()
    return f"{product_name} helps skin look its best. {cta}."


def _select_script_template(parsed: dict) -> str:
    """Prefer strategy_led when strategy_summary is strong; proof_led when proof_type present; else caption_led."""
    plan = parsed.get("image_plan") or {}
    strategy = (plan.get("strategy_summary") or "").strip()
    proof = (parsed.get("proof_type") or "").strip()
    if strategy and len(strategy) >= 15:
        return "strategy_led"
    if proof and proof != "none":
        return "proof_led"
    return "caption_led"


def _build_voiceover_script(template_id: str, parsed: dict, product_name: str) -> str:
    builders = {
        "caption_led": _build_voiceover_script_caption_led,
        "strategy_led": _build_voiceover_script_strategy_led,
        "proof_led": _build_voiceover_script_proof_led,
    }
    fn = builders.get(template_id, _build_voiceover_script_caption_led)
    return fn(parsed, product_name)


def _voiceover_duration_targets(total_duration_seconds: float) -> tuple[float, float, float]:
    """Return min, target, and max spoken durations that preserve an end buffer."""
    min_spoken_duration = max(0.5, total_duration_seconds - VOICEOVER_END_BUFFER_MAX_SECONDS)
    target_spoken_duration = max(0.5, total_duration_seconds - VOICEOVER_END_BUFFER_TARGET_SECONDS)
    max_spoken_duration = max(0.5, total_duration_seconds - VOICEOVER_END_BUFFER_MIN_SECONDS)
    return min_spoken_duration, target_spoken_duration, max_spoken_duration


def _trim_script_to_duration(script: str, total_duration_seconds: float) -> str:
    """Trim script to a safer voiceover budget that leaves an end buffer."""
    _, _, max_spoken_duration = _voiceover_duration_targets(total_duration_seconds)
    max_words = max(1, int(max_spoken_duration * VOICEOVER_TARGET_WORDS_PER_SECOND))
    words = script.split()
    if len(words) <= max_words:
        return script
    return " ".join(words[:max_words])


def _guardrail_check(script: str) -> dict:
    """Check script against brand guardrails. Returns dict with passed, violations, normalized_script."""
    lower = script.lower()
    violations = []
    for forbidden in TTS_GUARDRAIL_FORBIDDEN:
        if forbidden in lower:
            violations.append(forbidden)
    normalized = script
    if violations:
        for v in violations:
            # Simple replacement: remove or soften - for now we reject
            pass
    return {"passed": len(violations) == 0, "violations": violations, "normalized_script": normalized}


def _pick_voice(content_id: str) -> str:
    """Use marin voice."""
    return TTS_VOICES[0]


def _build_voiceover_plan(
    parsed: dict,
    content_id: str,
    product_name: str,
    total_duration_seconds: float,
) -> dict:
    """Build durable voiceover plan for image_motion_15s manifest."""
    template_id = _select_script_template(parsed)
    raw_script = _build_voiceover_script(template_id, parsed, product_name)
    script = _trim_script_to_duration(raw_script, total_duration_seconds)
    guardrail = _guardrail_check(script)
    if not guardrail["passed"]:
        # Fall back to shortest compliant script
        script = _build_voiceover_script_proof_led(parsed, product_name)
        script = _trim_script_to_duration(script, total_duration_seconds)
        guardrail = _guardrail_check(script)
    voice = _pick_voice(content_id)
    word_count = len(script.split())
    speech_rate = word_count / total_duration_seconds if total_duration_seconds > 0 else 2.3
    return {
        "script_template_id": template_id,
        "voiceover_script": script,
        "voice": voice,
        "voice_instructions": TTS_VOICE_INSTRUCTIONS,
        "language": "english",
        "speech_rate_words_per_second": round(speech_rate, 1),
        "guardrail_checks": {
            "passed": guardrail["passed"],
            "violations": guardrail["violations"],
        },
    }


_IMAGE_MOTION_VOICEOVER_SYSTEM_PROMPT = """\
You are an expert short-form ad scriptwriter for premium cosmetic image-motion ads.

TASK
Write exactly 1 voiceover script for an already-planned `image_motion_15s` clip.
- The visual plan is final. Do not invent scenes that are not represented in the provided frame plan.
- The script must fit the exact clip duration supplied in the user message.
- Aim for the spoken line to finish 0.5 to 1.0 seconds before the clip ends.
- The script must feel natural when read aloud in one continuous take.

TIMING RULES
- Keep the full script within the provided word budget.
- Target a natural premium read pace of about 2.1 to 2.5 words per second.
- Do not write right up to the final frame or last half-second.
- Do not add filler just to hit the maximum duration.

CONTENT RULES
- Reflect the actual frame order, visual details, and strategy summary from the provided image plan.
- Keep the tone calm, premium, warm, and confident.
- End with the provided CTA if it fits naturally.
- No medical or health claims.
- Do not use hypey urgency, slang, exaggerated promises, or forbidden phrasing.

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "voiceover_script": "string — one continuous spoken script that fits the supplied duration",
  "estimated_word_count": "number — word count for the final script",
  "timing_rationale": "string — one short sentence explaining why the script fits the duration and scene pacing"
}
"""


def _build_voiceover_guardrail_lines() -> list[str]:
    return [
        "Brand guardrails:",
        "Do not use any of these forbidden words or phrases:",
        f"Forbidden terms: {', '.join(TTS_GUARDRAIL_FORBIDDEN)}",
        f"Approved softeners when needed: {', '.join(TTS_GUARDRAIL_SOFTENERS)}",
    ]


def _build_image_motion_voiceover_user_message(
    parsed: dict,
    product_name: str,
    total_duration_seconds: float,
    violations: list[str] | None = None,
) -> str:
    plan = parsed.get("image_plan") or {}
    frames = plan.get("frames") or []
    min_spoken_duration, target_spoken_duration, max_spoken_duration = _voiceover_duration_targets(
        total_duration_seconds
    )
    min_words = max(1, round(min_spoken_duration * VOICEOVER_TARGET_WORDS_PER_SECOND))
    max_words = max(1, int(max_spoken_duration * VOICEOVER_TARGET_WORDS_PER_SECOND))
    target_words = max(1, round(target_spoken_duration * VOICEOVER_TARGET_WORDS_PER_SECOND))

    lines = [
        f"Product: {product_name}",
        f"Hook text: {(parsed.get('hook_text') or '').strip() or product_name}",
        f"CTA text: {(parsed.get('cta_text') or 'see the product').strip()}",
        f"Theme: {(parsed.get('theme') or '').strip()}",
        f"Hook type: {(parsed.get('hook_type') or '').strip()}",
        f"Proof type: {(parsed.get('proof_type') or '').strip() or 'none'}",
        f"Script style: {(parsed.get('script_style') or '').strip() or 'direct'}",
        f"Strategy summary: {(plan.get('strategy_summary') or '').strip()}",
        "",
        f"Exact clip duration seconds: {total_duration_seconds:.1f}",
        "Voiceover should finish 0.5 to 1.0 seconds before clip end.",
        f"Preferred spoken duration: {min_spoken_duration:.1f}-{max_spoken_duration:.1f} seconds",
        f"Preferred spoken word range: {min_words}-{max_words} words",
        f"Target word count: {target_words}",
        "",
        *_build_voiceover_guardrail_lines(),
        "",
    ]
    if violations:
        lines.extend([
            "Retry instruction:",
            f"The previous draft used forbidden terms: {', '.join(violations)}.",
            "Return a new script that avoids every forbidden term listed above.",
            "",
        ])
    lines.extend([
        "Frame plan:",
    ])
    for idx, frame in enumerate(frames, start=1):
        if not isinstance(frame, dict):
            continue
        lines.extend([
            f"Frame {idx}:",
            f"  - duration_seconds: {float(frame.get('duration_seconds', 0)):.1f}",
            f"  - role: {frame.get('role', '')}",
            f"  - style_family: {frame.get('style_family', '')}",
            f"  - lighting: {frame.get('lighting', '')}",
            f"  - camera_distance: {frame.get('camera_distance', '')}",
            f"  - scene_description: {(frame.get('image_prompt') or '').strip()}",
        ])
    return "\n".join(lines)


def _parse_voiceover_response(raw: str, total_duration_seconds: float) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OpenAI returned invalid voiceover JSON: {exc}") from exc

    script = str(data.get("voiceover_script") or "").strip()
    if not script:
        raise ValueError("OpenAI voiceover response missing `voiceover_script`.")

    script = _trim_script_to_duration(script, total_duration_seconds)
    guardrail = _guardrail_check(script)
    if not guardrail["passed"]:
        raise ValueError(
            f"Voiceover script violated brand guardrails: {', '.join(guardrail['violations'])}"
        )

    data["voiceover_script"] = script
    data["estimated_word_count"] = len(script.split())
    return data


def _generate_image_motion_voiceover_plan(
    client: Any,
    openai_module: Any,
    model: str,
    parsed: dict,
    content_id: str,
    product_name: str,
    total_duration_seconds: float,
) -> tuple[dict[str, Any], str, str, Any]:
    guardrail_violations: list[str] = []
    user_msg = ""
    raw = ""
    response = None
    data: dict[str, Any] | None = None
    for attempt in range(1, VOICEOVER_GUARDRAIL_MAX_ATTEMPTS + 1):
        user_msg = _build_image_motion_voiceover_user_message(
            parsed,
            product_name,
            total_duration_seconds,
            violations=guardrail_violations or None,
        )
        response = _call_with_retries(
            client,
            openai_module,
            model,
            user_msg,
            max_attempts=3,
            system_prompt=_IMAGE_MOTION_VOICEOVER_SYSTEM_PROMPT,
        )
        raw = _response_text(response)
        try:
            data = _parse_voiceover_response(raw, total_duration_seconds)
            break
        except ValueError as exc:
            if "Voiceover script violated brand guardrails:" not in str(exc):
                raise
            if attempt == VOICEOVER_GUARDRAIL_MAX_ATTEMPTS:
                raise
            guardrail_violations = [
                violation.strip()
                for violation in str(exc).split(":", 1)[1].split(",")
                if violation.strip()
            ]
            logger.warning(
                "Voiceover guardrail violation on attempt %d/%d: %s",
                attempt,
                VOICEOVER_GUARDRAIL_MAX_ATTEMPTS,
                ", ".join(guardrail_violations),
            )
    if data is None or response is None:
        raise ValueError("Voiceover generation failed before a valid script was returned.")
    voice = _pick_voice(content_id)
    speech_rate = (
        len(data["voiceover_script"].split()) / total_duration_seconds
        if total_duration_seconds > 0
        else VOICEOVER_TARGET_WORDS_PER_SECOND
    )
    voiceover_plan = {
        "script_template_id": "llm_scene_timed",
        "voiceover_script": data["voiceover_script"],
        "voice": voice,
        "voice_instructions": TTS_VOICE_INSTRUCTIONS,
        "language": "english",
        "speech_rate_words_per_second": round(speech_rate, 1),
        "guardrail_checks": {
            "passed": True,
            "violations": [],
        },
        "timing_rationale": str(data.get("timing_rationale") or "").strip(),
        "estimated_word_count": int(data["estimated_word_count"]),
    }
    return voiceover_plan, user_msg, raw, response


_AI_VIDEO_FLEX_SYSTEM_PROMPT = """\
You are an expert creative director and AI video prompt engineer specializing in cosmetic advertising.

TARGET PRODUCT: provided in the user message.

CORE DIRECTIVE
Generate exactly 1 unique creative for ai_video_flex_15s: a flexible multi-scene video plan (3–7 scenes, 6–15 seconds total).
- Pick theme and hook_type from the allowed whitelist unless locked.
- Return hook_text, platform_captions, hashtags.
- Return a video_plan: structured scene list with durations, visual descriptions, and voiceover scripts.
- Choose a style_family that fits the product and creative direction. Anamorphic is one option; you may choose other styles (e.g. realistic_cinematic, soft_minimal, bold_contrast).

STYLE REFERENCE — when style_family is "anamorphic":
Use cinematic 3D closeup of anthropomorphic product on luxury bathroom counter: Pixar-style face, large expressive eyes, articulated mouth, soft focus background, volumetric lighting, octane render, unreal engine 5, 4k, brand "velura" in brown serif (Cormorant Garamond, Georgia, Times New Roman).

PLANNER RULES:
- total_duration_seconds: 6 to 15 (prompt chooses; audience prefers quicker scene changes).
- scenes: 3 to 7 entries; each scene duration_seconds: 1.5 to 3.0 (aim for faster pacing).
- Sum of scene durations must equal total_duration_seconds.
- script_total_words: combined word count of all scene scripts; must fit speaking pace for total_duration_seconds (roughly 2–3 words per second).
- Require at least one hero/product-led scene.
- No medical or health claims. Use approved softeners: "appears to", "feels like", "helps skin look".
- Keep movements subtle for AI video generation.

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "theme": "string — from allowed themes",
  "hook_type": "string — from allowed hook types",
  "hook_text": "string — short opening hook line",
  "creative_format": "ai_video_flex_15s",
  "cta_type": "string — see_product or shop_now",
  "cta_text": "string — CTA phrase",
  "problem_angle": "string or null",
  "proof_type": "string — test_result, testimonial, before_after, ingredient, none",
  "script_style": "string — conversational, direct, storytelling, tip_based",
  "starting_image_prompt": "string — flexible starting frame for the video; when anamorphic, use luxury bathroom + anthropomorphic product per style reference above",
  "platform_captions": {
    "youtube": "string — max 100 chars, end with 'Link in bio'",
    "instagram": "string — conversational, emoji-friendly",
    "tiktok": "string — trendy, max 150 chars",
    "x": "string — max 280 chars"
  },
  "hashtags": ["list", "of", "hashtags", "without #"],
  "video_plan": {
    "strategy_summary": "string — one-line creative strategy",
    "total_duration_seconds": number — 6 to 15,
    "style_family": "string — anamorphic | realistic_cinematic | soft_minimal | bold_contrast | or other named style",
    "style_rationale": "string — why this style fits the product and creative direction",
    "script_total_words": number — sum of words across all scene scripts,
    "scenes": [
      {
        "duration_seconds": number — 1.5 to 3.0,
        "scene_description": "string — visual direction for this scene; include HARD CUT for scenes after the first",
        "script": "string — voiceover for this scene; must fit duration"
      }
    ]
  }
}
"""


def _has_model_reference_assets() -> bool:
    """True if human-model reference images exist for lifestyle frames."""
    models_dir = config.data_root() / "models"
    if not models_dir.exists():
        return False
    return any(models_dir.iterdir())

def _build_user_message(
    product: Product,
    theme: str | None,
    hook_type: str | None,
    product_images: list[ProductImage],
    research_summary: str | None = None,
    creative_format: str | None = None,
    performance_summary: str | None = None,
) -> str:
    theme_ids = [theme] if theme else None
    hook_ids = [hook_type] if hook_type else None
    lines = [
        f"Product: {product.name}",
        f"SKU: {product.sku}",
        f"Category: {product.category or 'general'}",
        f"Price: ${product.price:.2f}" if product.price else "Price: not set",
    ]
    if theme or hook_type or creative_format:
        lines.append("Locked creative constraints:")
        if theme:
            lines.append(f"  - Theme must be: {theme}")
        if hook_type:
            lines.append(f"  - Hook type must be: {hook_type}")
        if creative_format:
            lines.append(f"  - Creative format must be: {creative_format}")
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
    if research_summary and research_summary.strip():
        lines.append("")
        lines.append("RESEARCH INSIGHT (use to inform your creative choices):")
        lines.append(research_summary.strip())
    if performance_summary and performance_summary.strip():
        lines.append("")
        lines.append("PERFORMANCE_SUMMARY (bias your image_plan toward these):")
        lines.append(performance_summary.strip())
    if creative_format == "image_motion_15s":
        has_models = _has_model_reference_assets()
        lines.append("")
        lines.append(f"Model reference assets for lifestyle frames: {'available' if has_models else 'not configured'}")
    if creative_format == "ai_video_flex_15s":
        lines.append("")
        lines.append("AUDIENCE: prefers quicker scene changes; avoid long 7.5s scenes.")
    return "\n".join(lines)


def generate_content(
    product: Product,
    theme: str | None,
    hook_type: str | None,
    product_images: list[ProductImage],
    creative_format: str | None = None,
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

    # Phase 3: inject research snapshot for reuse across generation cycles
    fmt = creative_format or "ai_video_15s"
    snapshot = db.get_best_matching_snapshot(
        product_sku=product.sku,
        platform=None,
        creative_format=fmt,
    )
    research_summary = snapshot.summary if snapshot else None
    performance_summary = None
    performance_rationale = "default"
    if fmt == "image_motion_15s":
        rank_by = str(config.get("bandit.ranking_objective", "engagement_rate"))
        if rank_by not in ("engagement_rate", "views", "composite", "revenue", "sessions", "purchases"):
            rank_by = "engagement_rate"
        performance_summary, performance_rationale = get_image_motion_performance_summary(
            product.sku, rank_by=rank_by
        )
    user_msg = _build_user_message(
        product, theme, hook_type, product_images,
        research_summary=research_summary,
        creative_format=creative_format,
        performance_summary=performance_summary,
    )
    content_id = uuid.uuid4().hex[:16]

    use_image_motion = fmt == "image_motion_15s"
    use_ai_video_flex = fmt == "ai_video_flex_15s"
    system_prompt = (
        _IMAGE_MOTION_SYSTEM_PROMPT if use_image_motion else
        (_AI_VIDEO_FLEX_SYSTEM_PROMPT if use_ai_video_flex else
         (_SIMPLIFIED_SYSTEM_PROMPT if fmt != "ai_video_15s" else _SYSTEM_PROMPT))
    )

    response = _call_with_retries(
        client,
        openai_module,
        model,
        user_msg,
        max_attempts=3,
        system_prompt=system_prompt,
    )

    prompt_output_raw = _response_text(response)

    parsed = _parse_response(
        response, theme=theme, hook_type=hook_type, creative_format=creative_format
    )

    asset_manifest_json = None
    voice_prompt_input = None
    voice_prompt_output = None
    voiceover_response = None
    if fmt == "image_motion_15s" and "image_plan" in parsed:
        plan = parsed["image_plan"]
        if not isinstance(plan, dict):
            raise ValueError("OpenAI response image_plan must be an object")
        frames = plan.get("frames", [])
        if not isinstance(frames, list) or len(frames) < 3 or len(frames) > 5:
            raise ValueError("image_plan.frames must have 3–5 entries")
        total = plan.get("total_duration_seconds", 0)
        if not isinstance(total, (int, float)) or total > 15:
            raise ValueError("image_plan.total_duration_seconds must be <= 15")
        plan["performance_rationale"] = plan.get("performance_rationale", performance_rationale)
        voiceover_plan, voice_prompt_input, voice_prompt_output, voiceover_response = _generate_image_motion_voiceover_plan(
            client,
            openai_module,
            model,
            parsed,
            content_id,
            product.name,
            float(total),
        )
        asset_manifest_json = json.dumps({
            "format": "image_motion_15s",
            "image_plan": plan,
            "voiceover_plan": voiceover_plan,
        })
    elif fmt == "ai_video_flex_15s" and "video_plan" in parsed:
        plan = parsed["video_plan"]
        if not isinstance(plan, dict):
            raise ValueError("OpenAI response video_plan must be an object")
        scenes = plan.get("scenes", [])
        if not isinstance(scenes, list) or len(scenes) < 3 or len(scenes) > 7:
            raise ValueError("video_plan.scenes must have 3–7 entries")
        total = plan.get("total_duration_seconds", 0)
        if not isinstance(total, (int, float)) or total < 6 or total > 15:
            raise ValueError("video_plan.total_duration_seconds must be 6–15")
        scene_sum = sum(
            s.get("duration_seconds", 0) for s in scenes
            if isinstance(s, dict) and isinstance(s.get("duration_seconds"), (int, float))
        )
        if abs(scene_sum - total) > 0.1:
            raise ValueError(
                f"video_plan scene durations sum to {scene_sum}, must equal total_duration_seconds {total}"
            )
        for i, s in enumerate(scenes):
            if not isinstance(s, dict):
                raise ValueError(f"video_plan.scenes[{i}] must be an object")
            dur = s.get("duration_seconds")
            if not isinstance(dur, (int, float)) or dur < 1.5 or dur > 3.0:
                raise ValueError(
                    f"video_plan.scenes[{i}].duration_seconds must be 1.5–3.0, got {dur}"
                )
        asset_manifest_json = json.dumps({
            "format": "ai_video_flex_15s",
            "video_plan": plan,
            "generation_metadata": {
                "total_duration_seconds": total,
                "scene_count": len(scenes),
                "scene_durations": [s.get("duration_seconds") for s in scenes if isinstance(s, dict)],
            },
        })

    content = Content(
        id=content_id,
        product_sku=product.sku,
        theme=parsed["theme"],
        hook_type=parsed["hook_type"],
        hook_text=parsed["hook_text"],
        creative_format=parsed.get("creative_format") or creative_format or "ai_video_15s",
        cta_type=parsed.get("cta_type", "see_product"),
        cta_text=parsed.get("cta_text"),
        problem_angle=parsed.get("problem_angle"),
        proof_type=parsed.get("proof_type"),
        script_style=parsed.get("script_style"),
        research_snapshot_id=snapshot.id if snapshot else None,
        starting_image_prompt=parsed.get("starting_image_prompt"),
        scene_1_desc=parsed.get("scene_1_desc") if fmt != "ai_video_flex_15s" else None,
        scene_2_desc=parsed.get("scene_2_desc") if fmt != "ai_video_flex_15s" else None,
        scene_1_script=parsed.get("scene_1_script") if fmt != "ai_video_flex_15s" else None,
        scene_2_script=parsed.get("scene_2_script") if fmt != "ai_video_flex_15s" else None,
        asset_manifest_json=asset_manifest_json,
    )
    db.insert_content(content)

    if voiceover_response is not None:
        usage = voiceover_response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        input_per_m = float(config.get("openai.input_per_million_usd", 2.50))
        output_per_m = float(config.get("openai.output_per_million_usd", 15.0))
        cost_usd = (input_tokens / 1_000_000 * input_per_m) + (output_tokens / 1_000_000 * output_per_m)
        db.insert_cost(Cost(
            content_id=content_id,
            step="voiceover_plan_gen",
            api_provider="openai",
            tokens_or_units=input_tokens + output_tokens,
            cost_usd=cost_usd,
        ))

    platform_captions: dict[str, str] = parsed.get("platform_captions", {})
    # Ensure YouTube caption always ends with "Link in bio"
    if "youtube" in platform_captions:
        cap = platform_captions["youtube"].strip()
        if cap and not cap.rstrip().lower().endswith("link in bio"):
            platform_captions["youtube"] = f"{cap}\n\nLink in bio"
        elif not cap:
            platform_captions["youtube"] = "Link in bio"
    hashtags = parsed.get("hashtags", [])
    hashtag_csv = ",".join(tag.strip().lstrip("#") for tag in hashtags if tag.strip())
    
    for platform in config.enabled_platforms("posting"):
        attr_data = build_attribution_data(content, product, platform)
        payload = PlatformPayload(
            content_id=content.id,
            platform=platform,
            caption=platform_captions.get(platform, content.hook_text or product.name),
            hashtags=hashtag_csv,
            utm_url=attr_data["utm_url"],
            destination_url=attr_data["destination_url"],
            utm_source=attr_data["utm_source"],
            utm_medium=attr_data["utm_medium"],
            utm_campaign=attr_data["utm_campaign"],
            utm_content=attr_data["utm_content"],
            link_mode=attr_data["link_mode"],
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
        "prompt_input": f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{user_msg}",
        "prompt_output": prompt_output_raw,
    }
    if voice_prompt_input and voice_prompt_output:
        extras["voice_prompt_input"] = (
            f"[SYSTEM]\n{_IMAGE_MOTION_VOICEOVER_SYSTEM_PROMPT}\n\n[USER]\n{voice_prompt_input}"
        )
        extras["voice_prompt_output"] = voice_prompt_output
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
    system_prompt: str | None = None,
) -> Any:
    delay = 2.0
    prompt = system_prompt or _SYSTEM_PROMPT
    for attempt in range(1, max_attempts + 1):
        try:
            return client.chat.completions.create(
                model=model,
                max_completion_tokens=1500,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt},
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
    creative_format: str | None = None,
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

    use_image_motion = creative_format == "image_motion_15s"
    use_ai_video_flex = creative_format == "ai_video_flex_15s"
    required = [
        "theme", "hook_type", "hook_text",
        "creative_format", "cta_type", "cta_text",
        "problem_angle", "proof_type", "script_style",
        "platform_captions", "hashtags",
    ]
    if use_image_motion:
        required.append("image_plan")
    elif use_ai_video_flex:
        required.extend(["starting_image_prompt", "video_plan"])
    else:
        required.extend([
            "starting_image_prompt",
            "scene_1_desc", "scene_2_desc",
            "scene_1_script", "scene_2_script",
        ])
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

    # Phase 2: metadata validation
    creative_format = (data.get("creative_format") or "").strip()
    if creative_format and creative_format not in CREATIVE_FORMATS:
        raise ValueError(
            f"OpenAI response creative_format '{creative_format}' not in whitelist. "
            f"Allowed: {', '.join(CREATIVE_FORMATS)}"
        )
    cta_type_val = (data.get("cta_type") or "").strip()
    if cta_type_val and cta_type_val not in CTA_TYPES:
        raise ValueError(
            f"OpenAI response cta_type '{cta_type_val}' not in whitelist. "
            f"Allowed: {', '.join(CTA_TYPES)}"
        )
    proof_type_val = data.get("proof_type")
    if proof_type_val is not None and str(proof_type_val).strip():
        if str(proof_type_val).strip() not in PROOF_TYPES:
            raise ValueError(
                f"OpenAI response proof_type '{proof_type_val}' not in whitelist. "
                f"Allowed: {', '.join(PROOF_TYPES)}"
            )
    script_style_val = data.get("script_style")
    if script_style_val is not None and str(script_style_val).strip():
        if str(script_style_val).strip() not in SCRIPT_STYLES:
            raise ValueError(
                f"OpenAI response script_style '{script_style_val}' not in whitelist. "
                f"Allowed: {', '.join(SCRIPT_STYLES)}"
            )


def _response_text(response: Any) -> str:
    choice = response.choices[0]
    message = choice.message
    raw = message.content or ""
    if not raw.strip():
        raise ValueError("OpenAI returned an empty response.")
    return raw.strip()


# ---------------------------------------------------------------------------
# Phase 7: Paid variant caption generation
# ---------------------------------------------------------------------------

_PAID_VARIANT_SYSTEM_PROMPT = """\
You are an expert creative director for cosmetic ad copy.

TASK: Generate N ad-safe caption variants for a proven organic winner. Each variant must:
- Preserve the core concept and product message
- Vary the CTA (see_product vs shop_now), opening hook, or caption tone
- Stay FTC-compliant; no medical or health claims
- Use only approved softeners: "appears to", "feels like", "helps skin look"

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "variants": [
    {
      "hook_text": "string — short opening hook, can differ from original",
      "cta_type": "string — see_product or shop_now",
      "cta_text": "string — CTA phrase (e.g. 'try me today', 'shop now')",
      "platform_captions": {
        "youtube": "string — max 100 chars, end with 'Link in bio'",
        "instagram": "string — conversational, emoji-friendly",
        "tiktok": "string — trendy, max 150 chars",
        "x": "string — max 280 chars"
      },
      "hashtags": ["list", "of", "hashtags", "without #"]
    }
  ]
}

RULES:
- Each variant must be distinctly different (vary CTA, hook, or caption style).
- cta_type must be one of: see_product, shop_now.
- All platform_captions keys (youtube, instagram, tiktok, x) required per variant.
"""


def generate_paid_variant_captions(
    content: Content,
    product: Product,
    variant_count: int,
) -> list[dict[str, Any]]:
    """Generate N ad-safe caption variants for a proven organic winner.

    Returns a list of dicts with hook_text, cta_type, cta_text, platform_captions, hashtags.
    Preserves core concept; varies CTA, hook, or caption style.
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

    user_msg = (
        f"Product: {product.name}\n"
        f"SKU: {product.sku}\n\n"
        f"Original winning creative:\n"
        f"- Theme: {content.theme}\n"
        f"- Hook type: {content.hook_type}\n"
        f"- Hook text: {content.hook_text or '(none)'}\n"
        f"- CTA: {content.cta_type} / {content.cta_text or '(none)'}\n\n"
        f"Generate exactly {variant_count} ad-safe variants. Each must vary CTA, opening hook, or caption tone "
        "while preserving the core concept."

    )

    response = _call_with_retries(
        client,
        openai_module,
        model,
        user_msg,
        max_attempts=3,
        system_prompt=_PAID_VARIANT_SYSTEM_PROMPT,
    )
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    input_per_m = float(config.get("openai.input_per_million_usd", 2.50))
    output_per_m = float(config.get("openai.output_per_million_usd", 15.0))
    cost_usd = (input_tokens / 1_000_000 * input_per_m) + (output_tokens / 1_000_000 * output_per_m)
    db.insert_cost(Cost(
        content_id=content.id,
        step="paid_variant_gen",
        api_provider="openai",
        tokens_or_units=input_tokens + output_tokens,
        cost_usd=cost_usd,
    ))
    raw = _response_text(response)
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
    data = json.loads(raw)

    variants = data.get("variants", [])
    if not isinstance(variants, list) or len(variants) < 1:
        raise ValueError("OpenAI response must contain a non-empty 'variants' array.")

    required_keys = {"hook_text", "cta_type", "cta_text", "platform_captions", "hashtags"}
    result = []
    for i, v in enumerate(variants[:variant_count]):
        if not isinstance(v, dict):
            continue
        missing = required_keys - set(v.keys())
        if missing:
            logger.warning("Variant %d missing keys %s, skipping", i + 1, missing)
            continue
        caps = v.get("platform_captions", {})
        if not isinstance(caps, dict):
            continue
        for plat in ("youtube", "instagram", "tiktok", "x"):
            if plat not in caps:
                caps[plat] = v.get("hook_text", "")
        if caps.get("youtube") and not caps["youtube"].rstrip().lower().endswith("link in bio"):
            caps["youtube"] = f"{caps['youtube'].rstrip()}\n\nLink in bio"
        v["platform_captions"] = caps
        v["cta_type"] = (v.get("cta_type") or "see_product").strip()
        if v["cta_type"] not in CTA_TYPES:
            v["cta_type"] = "see_product"
        result.append(v)

    return result
