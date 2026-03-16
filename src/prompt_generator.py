from __future__ import annotations

import importlib
import json
import logging
import re
import time
import unicodedata
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
- Use plain ASCII characters only in every field.
- Do not use emoji, curly quotes, smart apostrophes, ellipses, em dashes, or other Unicode punctuation.
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
    "instagram": "string — Instagram Reels caption (conversational plain text, 1-2 sentences, no emoji)",
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
- Use only plain ASCII characters in every field. No emoji or Unicode punctuation.
"""

_SIMPLIFIED_SYSTEM_PROMPT = """\
You are an expert creative director for cosmetic advertising.

TARGET PRODUCT: provided in the user message.

CORE DIRECTIVE
Generate exactly 1 unique creative variation for the target product. Output a short hook and platform captions for a slideshow or image-motion format (no AI video generation).
- Pick a `theme` and `hook_type` from the allowed whitelist unless locked.
- Return a concise `hook_text` that captures the opening hook.
- No medical or health claims. Use only approved softeners: "appears to", "feels like", "helps skin look".
- Use plain ASCII characters only in every field. No emoji or Unicode punctuation.

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
    "instagram": "string — conversational plain text, no emoji",
    "tiktok": "string — trendy, max 150 chars",
    "x": "string — max 280 chars"
  },
  "hashtags": ["list", "of", "hashtags", "without #"]
}
"""

_AUDIENCE_QUESTIONS = [
    "Does it work for my skin type?",
    "How fast will I see results?",
    "Will it irritate sensitive skin?",
    "Which ingredient actually matters?",
    "How does it fit into my routine?",
    "Is it worth the price? (yes)",
    "Will it help with dark spots, acne, dryness, or texture?",
]
_AUDIENCE_FEARS = [
    "Breakouts or purging",
    "Irritation or barrier damage",
    "Wasting money on hype",
    "Fake before-and-afters",
    "Buying the wrong product for the skin concern",
    "Overcomplicated routines",
    "Results that do not last",
    "Actives being too harsh for daily use",
]

_IMAGE_MOTION_SYSTEM_PROMPT = """\
You are an expert creative director for cosmetic image-motion ads.

TARGET PRODUCT: provided in the user message.

CORE DIRECTIVE
Generate exactly 1 unique creative for image_motion_15s: a 5-7 frame vertical (9:16) image sequence.
- Pick theme and hook_type from the allowed whitelist unless locked.
- Return hook_text, platform_captions, hashtags.
- Choose content_goal: "conversion" or "engagement".
- Also return an image_plan: a structured multi-frame plan for Gemini to generate 5-7 images.
- Use plain ASCII characters only in every field. No emoji or Unicode punctuation.

VISUAL DIRECTORY
- style_family: anamorphic, realistic_cinematic
- frame_role: hero_macro, hero_tabletop, texture_detail, lifestyle_portrait, lifestyle_in_use
- lighting: golden_window_light, soft_diffused_daylight, clean_studio_backlight
- camera_distance: macro_closeup, closeup, medium_shot

NARRATIVE DIRECTORY
- narrative_role: hook, problem, proof, cta
- mood: intrigue, concern, delight, invitation, calm_confidence, soft_curiosity

AUDIENCE INSIGHT - when theme is fear:
Choose ONE realistic fear from this list and frame it gently and compliantly: """ + "; ".join(_AUDIENCE_FEARS) + """.

AUDIENCE INSIGHT - when theme is curiosity:
Choose ONE question from this list and build the open loop around it: """ + "; ".join(_AUDIENCE_QUESTIONS) + """.

CONTROLLED VARIETY (use this vocabulary; vary at most 1-2 axes per creative):
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
- Build a mini-story across frames. Use at least 3 distinct narrative_role beats, and end on cta.
- Each frame must introduce a NEW idea. Do not repeat the same concept with different wording.
- Each image_prompt must include at least one visual detail that directly reinforces that frame's narrative beat.
- Consecutive frames must not use the same mood.
- Consecutive frames must not share the exact same combination of style_family, lighting, and camera_distance.
- If content_goal is engagement, prioritize intrigue, saves, and follows before the final CTA.
- If content_goal is conversion, make the final frame a clear product-led payoff with a warmer CTA visual.

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
    "instagram": "string — conversational plain text, no emoji",
    "tiktok": "string — trendy, max 150 chars",
    "x": "string — max 280 chars"
  },
  "hashtags": ["list", "of", "hashtags", "without #"],
  "image_plan": {
    "strategy_summary": "string — one-line creative strategy for this sequence",
    "total_duration_seconds": number — sum of frame durations, <= 15,
    "performance_rationale": "string — product_winners, global_winners, or default",
    "strategy_metadata": {
      "content_goal": "string — conversion | engagement",
      "primary_engagement_intent": "string — follow | save | share | comment | click",
      "audience_question_cluster": "string or null — if theme is curiosity, which question cluster",
      "audience_fear_cluster": "string or null — if theme is fear, which fear cluster"
    },
    "frames": [
      {
        "role": "string — hero_macro | hero_tabletop | texture_detail | lifestyle_portrait | lifestyle_in_use",
        "narrative_role": "string — hook | problem | proof | cta",
        "frame_intent": "string — what the viewer should feel or understand from this frame",
        "mood": "string — intrigue | concern | delight | invitation | calm_confidence | soft_curiosity",
        "duration_seconds": number — 1.5 to 2.0,
        "style_family": "string — anamorphic | realistic_cinematic",
        "lighting": "string — golden_window_light | soft_diffused_daylight | clean_studio_backlight",
        "camera_distance": "string — macro_closeup | closeup | medium_shot",
        "image_prompt": "string — exact prompt for Gemini to generate this frame; include product name, style, lighting, composition, and at least one concrete visual detail that reinforces the frame_intent"
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
VOICEOVER_END_BUFFER_MIN_SECONDS = 1.0
VOICEOVER_END_BUFFER_TARGET_SECONDS = 1.25
VOICEOVER_END_BUFFER_MAX_SECONDS = 1.5
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
IMAGE_MOTION_FRAME_ROLES = (
    "hero_macro",
    "hero_tabletop",
    "texture_detail",
    "lifestyle_portrait",
    "lifestyle_in_use",
)
IMAGE_MOTION_HERO_FRAME_ROLES = ("hero_macro", "hero_tabletop", "texture_detail")
IMAGE_MOTION_LIFESTYLE_FRAME_ROLES = ("lifestyle_portrait", "lifestyle_in_use")
IMAGE_MOTION_STYLE_FAMILIES = ("anamorphic", "realistic_cinematic")
IMAGE_MOTION_LIGHTING_OPTIONS = (
    "golden_window_light",
    "soft_diffused_daylight",
    "clean_studio_backlight",
)
IMAGE_MOTION_CAMERA_DISTANCES = ("macro_closeup", "closeup", "medium_shot")
IMAGE_MOTION_NARRATIVE_ROLES = ("hook", "problem", "proof", "cta")
IMAGE_MOTION_MOODS = (
    "intrigue",
    "concern",
    "delight",
    "invitation",
    "calm_confidence",
    "soft_curiosity",
)
IMAGE_MOTION_CONTENT_GOALS = ("conversion", "engagement")
IMAGE_MOTION_PRIMARY_ENGAGEMENT_INTENTS = ("follow", "save", "share", "comment", "click")
IMAGE_MOTION_PERFORMANCE_RATIONALES = ("product_winners", "global_winners", "default")

_UNICODE_TEXT_REPLACEMENTS = {
    "\u00a0": " ",
    "\u200b": "",
    "\u200c": "",
    "\u200d": "",
    "\ufeff": "",
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u2026": "...",
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
}


def _sanitize_generated_text(value: str) -> str:
    """Normalize model text to plain ASCII-safe content for downstream media tools."""
    text = value
    for source, target in _UNICODE_TEXT_REPLACEMENTS.items():
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _sanitize_generated_payload(value: Any) -> Any:
    """Recursively sanitize OpenAI-generated text fields before validation/persistence."""
    if isinstance(value, str):
        return _sanitize_generated_text(value)
    if isinstance(value, list):
        return [_sanitize_generated_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_generated_payload(item) for key, item in value.items()}
    return value


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
Your scripts will be read aloud by a single voice actor. Every word must earn its place.

TASK
Write exactly 1 voiceover script for an already-planned `image_motion_15s` clip.
- The visual plan is final. Do not invent scenes that are not represented in the provided frame plan.
- The script must fit the exact clip duration supplied in the user message.
- Aim for the spoken line to finish 1.0 to 1.5 seconds before the clip ends.
- The script must feel natural when read aloud in one continuous take.

TIMING RULES
- Keep the full script within the provided word budget.
- Target a natural premium read pace of about 2.1 to 2.5 words per second.
- Do not write right up to the final frame or last second.
- Do not add filler just to hit the maximum duration.
- When the frame plan includes per-frame durations, distribute words roughly proportionally. A 2.0s frame gets ~4-5 words; a 1.5s frame gets ~3-4 words. Brief pauses between beats are better than cramming.

EMOTIONAL ARC — match the frame plan's mood progression
- The user message includes a mood and narrative_role for each frame. Your script's emotional register MUST mirror this progression.
- hook frames: confident, attention-grabbing, slightly bold. Lead with the strongest idea.
- problem frames: shift to a contrasting register — empathetic concern, conspiratorial knowing, or gentle tension. Name the viewer's pain point without restating the hook.
- proof frames: warm credibility — calm confidence, quiet pride, or sensory delight. Deliver one specific reason to believe.
- cta frames: inviting, open, unhurried. Close with a clear call to action using fresh language.
- Do NOT flatten all beats to the same emotional register. The arc should feel like a mini-story: attention → tension → credibility → invitation.

SCRIPT STYLE — the user message specifies one of four styles. Write accordingly:
- conversational: casual, warm, as if talking to a close friend. Use contractions. Short sentences. Can start with "So," or "You know what?"
- direct: clean, assertive, minimal. No hedging. Declarative sentences. Gets to the point fast.
- storytelling: slightly narrative. Build a tiny arc. "I used to... then I found... now I..." transitions.
- tip_based: educational, gently authoritative. "Here's something most people miss..." framing.

CONTENT GOAL — the user message specifies "conversion" or "engagement":
- conversion: make the final CTA specific and action-oriented. Use "try", "shop", "see" language. The proof beat should focus on a concrete product benefit.
- engagement: favor curiosity and emotional resonance over direct selling. The CTA can be softer — "follow for more", "save this", "you'll want to see this again". The proof beat can lean into sensory or aspirational language.

AUDIENCE INSIGHT — when provided:
- If an audience_fear_cluster is present, gently acknowledge the underlying concern in the problem beat. Do not name the fear explicitly — hint at it, then pivot to reassurance.
- If an audience_question_cluster is present, echo the viewer's curiosity in the hook or problem beat. Leave the answer for the proof beat.

CONTENT RULES
- Reflect the actual frame order, visual details, and strategy summary from the provided image plan.
- The overall tone is calm, premium, warm, and confident — but modulated beat by beat per the emotional arc above.
- End with the provided CTA if it fits naturally.
- No medical or health claims.
- Do not use hypey urgency, slang, exaggerated promises, or forbidden phrasing.
- Use only plain ASCII characters. No emoji or Unicode punctuation.

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
        f"Content goal: {((plan.get('strategy_metadata') or {}).get('content_goal') or '').strip() or 'conversion'}",
        f"Primary engagement intent: {((plan.get('strategy_metadata') or {}).get('primary_engagement_intent') or '').strip() or 'click'}",
        "Audience question cluster: "
        f"{(((plan.get('strategy_metadata') or {}).get('audience_question_cluster')) or '').strip() or 'none'}",
        "Audience fear cluster: "
        f"{(((plan.get('strategy_metadata') or {}).get('audience_fear_cluster')) or '').strip() or 'none'}",
        "",
        f"Exact clip duration seconds: {total_duration_seconds:.1f}",
        "Voiceover should finish 1.0 to 1.5 seconds before clip end.",
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
    mood_sequence = [
        str(frame.get("mood", "")).strip()
        for frame in frames
        if isinstance(frame, dict) and frame.get("mood")
    ]
    if mood_sequence:
        lines.append(f"Emotional arc (mood per frame): {' -> '.join(mood_sequence)}")
        lines.append("Mirror this progression in the script's emotional register.")
        lines.append("")

    lines.append("Frame plan:")
    for idx, frame in enumerate(frames, start=1):
        if not isinstance(frame, dict):
            continue
        dur = float(frame.get("duration_seconds", 0))
        approx_words = max(1, round(dur * VOICEOVER_TARGET_WORDS_PER_SECOND))
        lines.extend([
            f"Frame {idx}:",
            f"  - duration_seconds: {dur:.1f} (~{approx_words} words)",
            f"  - role: {frame.get('role', '')}",
            f"  - narrative_role: {frame.get('narrative_role', '')}",
            f"  - frame_intent: {(frame.get('frame_intent') or '').strip()}",
            f"  - mood: {frame.get('mood', '')}",
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
    data = _sanitize_generated_payload(data)

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
 - Choose a style_family that fits the product and creative direction. Supported options are anamorphic and realistic_cinematic.
- Use plain ASCII characters only in every field. No emoji or Unicode punctuation.

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
    "instagram": "string — conversational plain text, no emoji",
    "tiktok": "string — trendy, max 150 chars",
    "x": "string — max 280 chars"
  },
  "hashtags": ["list", "of", "hashtags", "without #"],
  "video_plan": {
    "strategy_summary": "string — one-line creative strategy",
    "total_duration_seconds": number — 6 to 15,
    "style_family": "string — anamorphic | realistic_cinematic",
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

# Video V2: Audience research clusters for fear and curiosity style buckets
STYLE_BUCKETS = [
    "fear_non_user",
    "aspirational_luxury",
    "routine_upgrade",    
    "curiosity_reveal",    
]

_AI_VIDEO_V2_SYSTEM_PROMPT = """\
You are an expert creative director and AI video prompt engineer for premium beauty and skincare content. Your output powers both conversion-oriented ads and highly engaging organic content that builds followers and saves.

TARGET PRODUCT: provided in the user message.

VISUAL DIRECTORY (apply to scene descriptions when style_family matches):
- anamorphic: Cinematic 3D closeup of anthropomorphic product on luxury bathroom counter. Pixar-style face, large expressive eyes, articulated mouth, soft focus background, volumetric lighting, octane render, unreal engine 5, 4k, brand "velura" in brown serif (Cormorant Garamond, Georgia, Times New Roman).
- realistic_cinematic: Natural proportions, realistic hands and skin, soft diffusion, premium product hero shot.

STYLE_FAMILY DEFAULT:
- Prefer style_family "anamorphic" unless RESEARCH INSIGHT in the user message explicitly requests "realistic_cinematic". Anamorphic is the default for premium product-led video.

ANAMORPHIC SCENE RULES (apply to ALL scenes when style_family is "anamorphic"):
- The anthropomorphic product is the ONLY character in every scene. No human hands, models, or secondary characters.
- Every scene_description must include the anthropomorphic product with its Pixar-style face, expressive eyes, and articulated mouth.
- Scene variety comes from camera angle, expression, and lighting changes on the product — NOT from introducing new characters or environments.
- The luxury bathroom counter is the consistent environment. Do not switch to vanities, studios, or abstract backgrounds.
- The product speaks in first person in every voiceover script.
- When anamorphic, starting_image_prompt MUST use the full spec: cinematic 3D closeup, anthropomorphic product, luxury bathroom counter, Pixar-style face, volumetric lighting, octane render, unreal engine 5, 4k, brand "velura" in brown serif.

AUDIENCE INSIGHT — when theme is fear:
Choose ONE realistic fear from this list and frame it gently and compliantly: """ + "; ".join(_AUDIENCE_FEARS) + """.

AUDIENCE INSIGHT — when theme is curiosity:
Choose ONE question from this list and build the open loop around it: """ + "; ".join(_AUDIENCE_QUESTIONS) + """.

CORE DIRECTIVE
Generate exactly 1 unique creative for a 15-second video. Output MUST use a timeline with exactly 4 scenes and absolute timestamps. Total duration is LOCKED at 15 seconds.
- Pick theme and hook_type from the allowed whitelist unless locked.
- Return hook_text, platform_captions, hashtags.
- Choose content_goal: "conversion" (direct-response) or "engagement" (saves, shares, follows, watch-through).
- When content_goal is "engagement", CTA can be softer; prioritize stopping the scroll and earning a save or follow.
- If product reference images are provided, preserve the real package silhouette, label layout, and visible brand wordmark from the hero references in the starting frame and product hero scenes. Do not genericize or omit on-pack branding.
- Use plain ASCII characters only in every field. No emoji or Unicode punctuation.

STRICT TIMING RULES
- timeline: exactly 4 scenes. Use these exact timestamp brackets: [0:00–0:03], [0:03–0:07], [0:07–0:11], [0:11–0:15].
- Each scene has start_seconds (0, 3, 7, 11) and end_seconds (3, 7, 11, 15).
- Scenes 2, 3, and 4 MUST begin their scene_description with the exact phrase "HARD CUT:" to force visual cuts and prevent subject melting.

PACING (no word-count math)
- Write voiceover scripts that fit a moderate speaking pace of 2–3 words per second for each scene's timestamp bracket.
- Scene 1 (0–3s): ~6–9 words. Scene 2 (3–7s): ~8–12 words. Scene 3 (7–11s): ~8–12 words. Scene 4 (11–15s): ~8–12 words.

SCENE ROLE DEFINITIONS (each scene MUST serve its assigned role in BOTH visual direction and voiceover script):
- hook (Scene 1): Grab attention with a bold or surprising opening. Expression should be attention-catching (e.g., bold confidence, wide-eyed surprise). Script introduces ONE compelling claim or question — this is the only scene that states the core hook.
- problem (Scene 2): Create tension or contrast. Expression shifts to a distinctly different register (e.g., conspiratorial side-eye, gentle concern, empathetic knowing look). Script names a relatable pain point, gap, or contrast that the viewer recognizes. Do NOT restate or rephrase the hook.
- proof (Scene 3): Build credibility. Expression shifts to warm confidence or delight. Script delivers ONE specific reason to believe — a texture detail, key ingredient, sensory experience, or social validation. The visual must include at least one concrete detail that reinforces the proof (e.g., rack focus on creamy texture, light catching the product surface, a visual cue for the claimed benefit).
- cta (Scene 4): Invite action with warmth. Expression is inviting and open. Script closes with a clear, distinct call to action that does NOT repeat language from earlier scenes.

SCRIPT VARIETY (mandatory):
- Each scene's voiceover must advance the narrative to a NEW idea. The four scripts must read as a mini-story with a beginning, middle, and end — not four versions of the same tagline.
- No two scenes may express the same concept. Do not use synonyms of the same idea (e.g., "rebuying," "keep reaching for," "favorite," "repeat-purchase") across multiple scenes.
- If the theme is social_proof, distribute the proof: Scene 1 can claim popularity, but Scene 2 must pivot to a problem or contrast, Scene 3 must give a specific reason, and Scene 4 must invite action with fresh language.

VISUAL-SCRIPT COUPLING (mandatory):
- Each scene_description must include at least one specific visual detail that directly illustrates or emotionally reinforces the voiceover line for that scene.
- The product's facial expression MUST match the emotional register of the script line (e.g., conspiratorial for revealing a secret, warm pride for a proof point, beckoning for a CTA).
- Do not write generic beauty-shot descriptions disconnected from the script content. Every visual choice should serve the story beat.

EXPRESSION ARC (mandatory for anamorphic):
- The product's facial expression must follow a distinct emotional progression across the 4 scenes.
- No two consecutive scenes may use the same emotional register.
- Example arcs: bold confidence → conspiratorial concern → warm delight → inviting warmth, or wide-eyed surprise → empathetic knowing → proud satisfaction → playful beckoning.

FTC COMPLIANCE
- No medical or health claims. Use approved softeners: "appears to", "feels like", "helps skin look", "designed to".
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
  "starting_image_prompt": "string — first frame; preserve visible packaging branding/wordmark and label layout from hero reference images when provided; when style_family is anamorphic, MUST use full anamorphic spec per ANAMORPHIC SCENE RULES (cinematic 3D closeup, anthropomorphic product, luxury bathroom counter, Pixar-style face, volumetric lighting, octane render, unreal engine 5, 4k, brand velura in brown serif)",
  "platform_captions": {
    "youtube": "string — max 100 chars, end with 'Link in bio'",
    "instagram": "string — conversational plain text, no emoji",
    "tiktok": "string — trendy, max 150 chars",
    "x": "string — max 280 chars"
  },
  "hashtags": ["list", "of", "hashtags", "without #"],
  "strategy_metadata": {
    "style_family": "string — anamorphic (default) | realistic_cinematic (only when RESEARCH INSIGHT explicitly requests it)",
    "style_angle": "string — one-line summary of the execution",
    "content_goal": "conversion or engagement",
    "primary_engagement_intent": "follow | save | share | comment | click",
    "audience_question_cluster": "string or null — if theme is curiosity, which question",
    "audience_fear_cluster": "string or null — if theme is fear, which fear",
    "scene_roles": ["hook", "problem", "proof", "cta"]
  },
  "timeline": [
    {"start_seconds": 0, "end_seconds": 3, "scene_description": "string — ROLE: hook. Visual direction with attention-grabbing expression; NO HARD CUT for scene 1; when anamorphic, anthropomorphic product must be sole on-screen subject", "script": "string — voiceover introducing the core hook, first person when anamorphic"},
    {"start_seconds": 3, "end_seconds": 7, "scene_description": "string — ROLE: problem. MUST start with 'HARD CUT:'; expression shifts to a contrasting register; visual reinforces the pain point or contrast in the script; when anamorphic, new angle/expression on anthropomorphic product only, no new characters", "script": "string — names a pain point or contrast, must NOT restate the hook"},
    {"start_seconds": 7, "end_seconds": 11, "scene_description": "string — ROLE: proof. MUST start with 'HARD CUT:'; expression shifts to warm confidence; must include a concrete visual detail reinforcing the proof claim; when anamorphic, anthropomorphic product remains sole subject", "script": "string — delivers one specific reason to believe"},
    {"start_seconds": 11, "end_seconds": 15, "scene_description": "string — ROLE: cta. MUST start with 'HARD CUT:'; expression is inviting and open; when anamorphic, product hero shot with anthropomorphic product only", "script": "string — clear call to action with fresh language, no repeated concepts from earlier scenes"}
  ]
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
    video_v2: bool = False,
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
        if any((img.image_type or "").strip().lower() == "hero" for img in product_images):
            lines.append(
                "Reference-image rule: preserve the real package silhouette, label layout, "
                "and visible brand wordmark from hero product images. Do not genericize or "
                "omit the on-pack Velura branding in the starting image or product hero shots."
            )
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
    if video_v2:
        lines.append("")
        lines.append("VIDEO V2: Use the timeline format with exactly 4 scenes and absolute timestamps [0:00–0:03], [0:03–0:07], [0:07–0:11], [0:11–0:15]. Scenes 2–4 must start with 'HARD CUT:'.")
    return "\n".join(lines)


def generate_content(
    product: Product,
    theme: str | None,
    hook_type: str | None,
    product_images: list[ProductImage],
    creative_format: str | None = None,
    video_v2: bool = False,
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

    model = config.get("openai.model", "gpt-5.4")
    openai_module = _load_openai_module()
    client = openai_module.OpenAI(api_key=api_key)

    # Phase 3: inject research snapshot for reuse across generation cycles
    fmt = creative_format or "ai_video_15s"
    if video_v2:
        fmt = "ai_video_flex_15s"
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
        creative_format=fmt,
        performance_summary=performance_summary,
        video_v2=video_v2,
    )
    content_id = uuid.uuid4().hex[:16]

    use_image_motion = fmt == "image_motion_15s"
    use_ai_video_flex = fmt == "ai_video_flex_15s"
    use_ai_video_v2 = video_v2
    system_prompt = (
        _IMAGE_MOTION_SYSTEM_PROMPT if use_image_motion else
        (_AI_VIDEO_V2_SYSTEM_PROMPT if use_ai_video_v2 else
         (_AI_VIDEO_FLEX_SYSTEM_PROMPT if use_ai_video_flex else
          (_SIMPLIFIED_SYSTEM_PROMPT if fmt != "ai_video_15s" else _SYSTEM_PROMPT)))
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
        response, theme=theme, hook_type=hook_type, creative_format=fmt, video_v2=video_v2
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
        voiceover_model = config.get("openai.voiceover_model", "gpt-4.1")
        voiceover_plan, voice_prompt_input, voice_prompt_output, voiceover_response = _generate_image_motion_voiceover_plan(
            client,
            openai_module,
            voiceover_model,
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
        if not isinstance(total, (int, float)):
            raise ValueError("video_plan.total_duration_seconds must be a number")
        # V2 timeline uses fixed 15s format with 3,4,4,4 second scenes; skip clamp for that path.
        # For non-V2 ai_video_flex_15s, clamp LLM slip-ups (values outside 1.5–3.0).
        if not video_v2:
            for i, s in enumerate(scenes):
                if not isinstance(s, dict):
                    raise ValueError(f"video_plan.scenes[{i}] must be an object")
                dur = s.get("duration_seconds")
                if not isinstance(dur, (int, float)):
                    raise ValueError(
                        f"video_plan.scenes[{i}].duration_seconds must be a number, got {dur!r}"
                    )
                clamped = max(1.5, min(3.0, float(dur)))
                if abs(clamped - dur) > 0.01:
                    s["duration_seconds"] = clamped
            scene_sum = sum(
                s.get("duration_seconds", 0) for s in scenes
                if isinstance(s, dict) and isinstance(s.get("duration_seconds"), (int, float))
            )
            if scene_sum < 6 or scene_sum > 15:
                scale = 6.0 / scene_sum if scene_sum < 6 else 15.0 / scene_sum
                for s in scenes:
                    if isinstance(s, dict) and "duration_seconds" in s:
                        d = float(s["duration_seconds"]) * scale
                        s["duration_seconds"] = max(1.5, min(3.0, round(d, 1)))
                scene_sum = sum(
                    s.get("duration_seconds", 0) for s in scenes
                    if isinstance(s, dict) and isinstance(s.get("duration_seconds"), (int, float))
                )
            total = scene_sum
            plan["total_duration_seconds"] = total
        # When video_v2, video_plan comes from _validate_and_normalize_v2_timeline with fixed 3,4,4,4s scenes.
        manifest_payload = {
            "format": "ai_video_flex_15s",
            "video_plan": plan,
            "generation_metadata": {
                "total_duration_seconds": total,
                "scene_count": len(scenes),
                "scene_durations": [s.get("duration_seconds") for s in scenes if isinstance(s, dict)],
            },
        }
        if video_v2 and "strategy_metadata" in parsed:
            manifest_payload["schema_version"] = 2
            manifest_payload["strategy_metadata"] = parsed["strategy_metadata"]
            if "timeline" in parsed:
                manifest_payload["timeline"] = parsed["timeline"]
        asset_manifest_json = json.dumps(manifest_payload)

    strategy_metadata_json = None
    if fmt == "image_motion_15s" and "image_plan" in parsed:
        plan = parsed["image_plan"]
        strategy_metadata = plan.get("strategy_metadata") if isinstance(plan, dict) else None
        if isinstance(strategy_metadata, dict):
            strategy_metadata_json = json.dumps(strategy_metadata)
    elif video_v2 and "strategy_metadata" in parsed:
        strategy_metadata_json = json.dumps(parsed["strategy_metadata"])

    content = Content(
        id=content_id,
        product_sku=product.sku,
        theme=parsed["theme"],
        hook_type=parsed["hook_type"],
        hook_text=parsed["hook_text"],
        creative_format=parsed.get("creative_format") or fmt or "ai_video_15s",
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
        strategy_metadata_json=strategy_metadata_json,
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
    video_v2: bool = False,
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
    data = _sanitize_generated_payload(data)

    use_image_motion = creative_format == "image_motion_15s"
    use_ai_video_flex = creative_format == "ai_video_flex_15s" and not video_v2
    use_ai_video_v2 = video_v2
    required = [
        "theme", "hook_type", "hook_text",
        "creative_format", "cta_type", "cta_text",
        "problem_angle", "proof_type", "script_style",
        "platform_captions", "hashtags",
    ]
    if use_image_motion:
        required.append("image_plan")
    elif use_ai_video_v2:
        required.extend(["starting_image_prompt", "timeline", "strategy_metadata"])
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
    if use_image_motion:
        _validate_and_normalize_image_motion_plan(data)
    if use_ai_video_v2:
        _validate_and_normalize_v2_timeline(data)
    return data


def _validate_and_normalize_image_motion_plan(data: dict[str, Any]) -> None:
    """Validate image_motion_15s plans while preserving the existing render contract."""
    plan = data.get("image_plan")
    if not isinstance(plan, dict):
        raise ValueError("OpenAI response image_plan must be an object")

    strategy_summary = str(plan.get("strategy_summary") or "").strip()
    if not strategy_summary:
        raise ValueError("image_plan.strategy_summary is required")
    plan["strategy_summary"] = strategy_summary

    total_duration = plan.get("total_duration_seconds")
    if not isinstance(total_duration, (int, float)):
        raise ValueError("image_plan.total_duration_seconds must be a number")
    total_duration = float(total_duration)
    if total_duration <= 0 or total_duration > 15:
        raise ValueError("image_plan.total_duration_seconds must be > 0 and <= 15")
    plan["total_duration_seconds"] = total_duration

    performance_rationale = str(plan.get("performance_rationale") or "default").strip() or "default"
    if performance_rationale not in IMAGE_MOTION_PERFORMANCE_RATIONALES:
        raise ValueError(
            "image_plan.performance_rationale must be one of: "
            f"{', '.join(IMAGE_MOTION_PERFORMANCE_RATIONALES)}"
        )
    plan["performance_rationale"] = performance_rationale

    strategy_metadata = plan.get("strategy_metadata")
    if strategy_metadata is None:
        strategy_metadata = {}
        plan["strategy_metadata"] = strategy_metadata
    if not isinstance(strategy_metadata, dict):
        raise ValueError("image_plan.strategy_metadata must be an object")

    content_goal = str(strategy_metadata.get("content_goal") or "conversion").strip() or "conversion"
    if content_goal not in IMAGE_MOTION_CONTENT_GOALS:
        raise ValueError(
            "image_plan.strategy_metadata.content_goal must be one of: "
            f"{', '.join(IMAGE_MOTION_CONTENT_GOALS)}"
        )
    strategy_metadata["content_goal"] = content_goal

    primary_engagement_intent = (
        str(strategy_metadata.get("primary_engagement_intent") or "click").strip() or "click"
    )
    if primary_engagement_intent not in IMAGE_MOTION_PRIMARY_ENGAGEMENT_INTENTS:
        raise ValueError(
            "image_plan.strategy_metadata.primary_engagement_intent must be one of: "
            f"{', '.join(IMAGE_MOTION_PRIMARY_ENGAGEMENT_INTENTS)}"
        )
    strategy_metadata["primary_engagement_intent"] = primary_engagement_intent

    audience_question_cluster = strategy_metadata.get("audience_question_cluster")
    if audience_question_cluster is not None:
        audience_question_cluster = str(audience_question_cluster).strip() or None
    strategy_metadata["audience_question_cluster"] = audience_question_cluster

    audience_fear_cluster = strategy_metadata.get("audience_fear_cluster")
    if audience_fear_cluster is not None:
        audience_fear_cluster = str(audience_fear_cluster).strip() or None
    strategy_metadata["audience_fear_cluster"] = audience_fear_cluster

    frames = plan.get("frames")
    if not isinstance(frames, list) or len(frames) < 3 or len(frames) > 5:
        raise ValueError("image_plan.frames must have 3-5 entries")

    has_models = _has_model_reference_assets()
    seen_narrative_roles: list[str] = []
    total_frame_duration = 0.0
    hero_frames = 0
    previous_mood: str | None = None
    previous_visual_signature: tuple[str, str, str] | None = None

    for idx, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise ValueError(f"image_plan.frames[{idx}] must be an object")

        role = str(frame.get("role") or "").strip()
        if role not in IMAGE_MOTION_FRAME_ROLES:
            raise ValueError(
                f"image_plan.frames[{idx}].role must be one of: {', '.join(IMAGE_MOTION_FRAME_ROLES)}"
            )
        frame["role"] = role
        if role in IMAGE_MOTION_HERO_FRAME_ROLES:
            hero_frames += 1
        if role in IMAGE_MOTION_LIFESTYLE_FRAME_ROLES and not has_models:
            raise ValueError(
                f"image_plan.frames[{idx}].role {role!r} requires model reference assets"
            )

        narrative_role = str(frame.get("narrative_role") or "").strip()
        if narrative_role not in IMAGE_MOTION_NARRATIVE_ROLES:
            raise ValueError(
                f"image_plan.frames[{idx}].narrative_role must be one of: "
                f"{', '.join(IMAGE_MOTION_NARRATIVE_ROLES)}"
            )
        frame["narrative_role"] = narrative_role
        seen_narrative_roles.append(narrative_role)

        frame_intent = str(frame.get("frame_intent") or "").strip()
        if not frame_intent:
            raise ValueError(f"image_plan.frames[{idx}].frame_intent is required")
        frame["frame_intent"] = frame_intent

        mood = str(frame.get("mood") or "").strip()
        if mood not in IMAGE_MOTION_MOODS:
            raise ValueError(
                f"image_plan.frames[{idx}].mood must be one of: {', '.join(IMAGE_MOTION_MOODS)}"
            )
        if previous_mood and mood == previous_mood:
            raise ValueError(
                f"image_plan.frames[{idx}].mood must differ from the previous frame"
            )
        frame["mood"] = mood
        previous_mood = mood

        duration_seconds = frame.get("duration_seconds")
        if not isinstance(duration_seconds, (int, float)):
            raise ValueError(f"image_plan.frames[{idx}].duration_seconds must be a number")
        duration_seconds = float(duration_seconds)
        if duration_seconds < 1.5 or duration_seconds > 2.0:
            raise ValueError(
                f"image_plan.frames[{idx}].duration_seconds must be between 1.5 and 2.0"
            )
        frame["duration_seconds"] = duration_seconds
        total_frame_duration += duration_seconds

        style_family = str(frame.get("style_family") or "").strip()
        if style_family not in IMAGE_MOTION_STYLE_FAMILIES:
            raise ValueError(
                f"image_plan.frames[{idx}].style_family must be one of: "
                f"{', '.join(IMAGE_MOTION_STYLE_FAMILIES)}"
            )
        frame["style_family"] = style_family

        lighting = str(frame.get("lighting") or "").strip()
        if lighting not in IMAGE_MOTION_LIGHTING_OPTIONS:
            raise ValueError(
                f"image_plan.frames[{idx}].lighting must be one of: {', '.join(IMAGE_MOTION_LIGHTING_OPTIONS)}"
            )
        frame["lighting"] = lighting

        camera_distance = str(frame.get("camera_distance") or "").strip()
        if camera_distance not in IMAGE_MOTION_CAMERA_DISTANCES:
            raise ValueError(
                "image_plan.frames[{idx}].camera_distance must be one of: "
                f"{', '.join(IMAGE_MOTION_CAMERA_DISTANCES)}"
            )
        frame["camera_distance"] = camera_distance

        visual_signature = (style_family, lighting, camera_distance)
        if previous_visual_signature and visual_signature == previous_visual_signature:
            raise ValueError(
                "Consecutive image_motion_15s frames must vary at least one of style_family, "
                "lighting, or camera_distance"
            )
        previous_visual_signature = visual_signature

        image_prompt = str(frame.get("image_prompt") or "").strip()
        if not image_prompt:
            raise ValueError(f"image_plan.frames[{idx}].image_prompt is required")
        frame["image_prompt"] = image_prompt

    if hero_frames < 1:
        raise ValueError("image_plan.frames must include at least one hero-led frame")
    if len(set(seen_narrative_roles)) < 3:
        raise ValueError("image_plan.frames must use at least 3 distinct narrative_role values")
    if seen_narrative_roles[-1] != "cta":
        raise ValueError("The final image_motion_15s frame must use narrative_role 'cta'")
    if abs(total_frame_duration - total_duration) > 0.05:
        raise ValueError(
            "image_plan.total_duration_seconds must match the sum of frame durations"
        )


def _validate_and_normalize_v2_timeline(data: dict[str, Any]) -> None:
    """Validate V2 timeline and normalize to video_plan for downstream compatibility."""
    timeline = data.get("timeline", [])
    if not isinstance(timeline, list) or len(timeline) != 4:
        raise ValueError(
            f"Video V2 timeline must have exactly 4 scenes, got {len(timeline) if isinstance(timeline, list) else 'non-list'}"
        )
    expected_starts = [0, 3, 7, 11]
    expected_ends = [3, 7, 11, 15]
    strategy = data.get("strategy_metadata") or {}
    if not isinstance(strategy, dict):
        strategy = {}
    style_family = strategy.get("style_family") or "anamorphic"
    style_rationale = strategy.get("style_angle") or "Video V2 rigid timeline"

    scenes_normalized = []
    for i, scene in enumerate(timeline):
        if not isinstance(scene, dict):
            raise ValueError(f"timeline[{i}] must be an object")
        start = scene.get("start_seconds")
        end = scene.get("end_seconds")
        if start != expected_starts[i] or end != expected_ends[i]:
            raise ValueError(
                f"timeline[{i}] must have start_seconds={expected_starts[i]}, end_seconds={expected_ends[i]}, got {start}, {end}"
            )
        duration_seconds = float(end - start)
        desc = (scene.get("scene_description") or "").strip()
        script = (scene.get("script") or "").strip()
        if not desc:
            raise ValueError(f"timeline[{i}].scene_description is required")
        if not script:
            raise ValueError(f"timeline[{i}].script is required")
        if i >= 1 and not desc.upper().startswith("HARD CUT"):
            raise ValueError(
                f"timeline[{i}].scene_description must start with 'HARD CUT:' for scenes 2-4"
            )
        scenes_normalized.append({
            "duration_seconds": duration_seconds,
            "scene_description": desc,
            "script": script,
        })

    video_plan = {
        "strategy_summary": strategy.get("style_angle") or "Video V2",
        "total_duration_seconds": 15,
        "style_family": style_family,
        "style_rationale": style_rationale,
        "scenes": scenes_normalized,
    }
    data["video_plan"] = video_plan


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
- Use plain ASCII characters only in every field. No emoji or Unicode punctuation.

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "variants": [
    {
      "hook_text": "string — short opening hook, can differ from original",
      "cta_type": "string — see_product or shop_now",
      "cta_text": "string — CTA phrase (e.g. 'try me today', 'shop now')",
      "platform_captions": {
        "youtube": "string — max 100 chars, end with 'Link in bio'",
        "instagram": "string — conversational plain text, no emoji",
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
    data = _sanitize_generated_payload(json.loads(raw))

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
