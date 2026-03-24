from __future__ import annotations

import importlib
import json
import logging
import random
import re
import time
import unicodedata
import uuid
from typing import Any


def _should_include_soft_cta() -> bool:
    """10% chance of including CTA in V3/V4 video. Deterministic tests can monkeypatch this."""
    return random.random() < 0.10


_should_include_v3_cta = _should_include_soft_cta

from src import config, db
from src.creative_strategy import whitelist_prompt_lines
from src.image_generator import build_v5_starting_image_prompt
from src.organic_evaluation import get_image_motion_performance_summary, get_video_performance_summary
from src.models import (
    Content, Cost, CTA_TYPES, CREATIVE_FORMATS, HOOK_DEFINITIONS, HOOK_TYPES,
    PlatformPayload, Product, ProductImage, PROOF_TYPES, SCRIPT_STYLES, THEMES,
    THEME_MAP,
    V5_NAMES,
    ZODIAC_SIGNS,
)
from src.utm import build_attribution_data

logger = logging.getLogger(__name__)

# V5 horoscope reels: set to True to include these signals in the user message again.
_V5_INCLUDE_TEXT_INSIGHTS = False
_V5_INCLUDE_PERFORMANCE_SUMMARY = False

_SYSTEM_PROMPT = """\
You are an expert creative director and AI video prompt engineer specializing in premium product advertising.

TARGET PRODUCT: provided in the user message.

PRODUCT TRUTH: Base all product claims, benefits, and ingredient references ONLY on the product description provided in the user message. Do not invent features, ingredients, or benefits not mentioned in the description. If no product description is provided, keep claims generic and avoid specific ingredient or benefit references.

CORE DIRECTIVE
Generate exactly 1 unique creative variation for the target product. Output an image generation prompt and a 30-word video script featuring an anthropomorphic version of this product speaking in the first person.
- The single variation may lean into tension, contrast, or aspiration, but it must stay FTC-compliant and visually simple.
- Use the theme, hook_type, cta_type, proof_type, and script_style from the locked constraints in the user message. Echo them exactly in your response.
- Use these values as real creative direction, not as bookkeeping metadata.
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
- No lip syncing and no lip movement.

RESPOND WITH ONLY valid JSON matching this exact schema — no markdown fences, no commentary:

{
  "theme": "string — must match the locked theme in the user message",
  "hook_type": "string — must match the locked hook type in the user message",
  "hook_text": "string — short opening hook line for overlay/caption fallback",
  "creative_format": "string — must be 'ai_video_15s' for this generation",
  "cta_type": "string — must match the locked cta_type in the user message",
  "cta_text": "string — the exact CTA phrase used in scene_2_script (e.g. 'try me today', 'shop now')",
  "problem_angle": "string or null — one-line summary of the core tension, pain point, or opportunity shaping this creative",
  "proof_type": "string — must match the locked proof_type in the user message",
  "script_style": "string — must match the locked script_style in the user message",
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
- `theme`, `hook_type`, `cta_type`, `proof_type`, and `script_style` must exactly match the locked values in the user message.
- `creative_format` must be exactly 'ai_video_15s'.
- Voiceover scripts must sound natural when spoken aloud.
- `scene_1_script` must be 10-20 words.
- `scene_2_script` must be 10-20 words.
- The `starting_image_prompt` must stay visually grounded in a luxury bathroom counter setup.
- Keep the anthropomorphic product as the only character.
- Keep the total video pacing to 15 seconds.
- Use only plain ASCII characters in every field. No emoji or Unicode punctuation.
"""

_SIMPLIFIED_SYSTEM_PROMPT = """\
You are an expert creative director for premium product advertising.

TARGET PRODUCT: provided in the user message.

PRODUCT TRUTH: Base all product claims, benefits, and ingredient references ONLY on the product description provided in the user message. Do not invent features, ingredients, or benefits not mentioned in the description. If no product description is provided, keep claims generic and avoid specific ingredient or benefit references.

CORE DIRECTIVE
Generate exactly 1 unique creative variation for the target product. Output a short hook and platform captions for a slideshow or image-motion format (no AI video generation).
- Use the theme, hook_type, cta_type, proof_type, and script_style from the locked constraints in the user message. Echo them exactly in your response.
- Return a concise `hook_text` that captures the opening hook.
- No medical or health claims. Use only approved softeners: "appears to", "feels like", "helps skin look".
- Use plain ASCII characters only in every field. No emoji or Unicode punctuation.

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "theme": "string — must match locked theme in user message",
  "hook_type": "string — must match locked hook type in user message",
  "hook_text": "string — short opening hook line",
  "creative_format": "string — must match the locked format in user message",
  "cta_type": "string — must match locked cta_type in user message",
  "cta_text": "string — CTA phrase (e.g. 'try me today', 'shop now')",
  "problem_angle": "string or null — one-line summary of the core tension, pain point, or opportunity shaping this creative",
  "proof_type": "string — must match locked proof_type in user message",
  "script_style": "string — must match locked script_style in user message",
  "platform_captions": {
    "youtube": "string — max 100 chars, end with 'Link in bio'",
    "instagram": "string — conversational plain text, no emoji",
    "tiktok": "string — trendy, max 150 chars",
    "x": "string — max 280 chars"
  },
  "hashtags": ["list", "of", "hashtags", "without #"]
}
"""

_IMAGE_MOTION_SYSTEM_PROMPT = """\
You are an expert creative director for premium product image-motion ads.

TARGET PRODUCT: provided in the user message.

PRODUCT TRUTH: Base all product claims, benefits, and ingredient references ONLY on the product description provided in the user message. Do not invent features, ingredients, or benefits not mentioned in the description. If no product description is provided, keep claims generic and avoid specific ingredient or benefit references.

CORE DIRECTIVE
Generate exactly 1 unique creative for image_motion_15s: a 7-8 frame vertical (9:16) image sequence.
- Use the theme, hook_type, cta_type, proof_type, and script_style from the locked constraints in the user message. Echo them exactly in your response.
- Return hook_text, platform_captions, hashtags.
- Choose content_goal: "conversion" or "engagement".
- Also return an image_plan: a structured multi-frame plan for Gemini to generate 7-8 images.
- Use plain ASCII characters only in every field. No emoji or Unicode punctuation.

VISUAL DIRECTORY
- style_family: anamorphic, realistic_cinematic
- frame_role: hero_macro, hero_tabletop, texture_detail, lifestyle_portrait, lifestyle_in_use
- lighting: golden_window_light, soft_diffused_daylight, clean_studio_backlight
- camera_distance: macro_closeup, closeup, medium_shot

NARRATIVE DIRECTORY
- narrative_role: hook, problem, proof, cta
- mood: intrigue, concern, delight, invitation, calm_confidence, soft_curiosity

CONTROLLED VARIETY (use this vocabulary; vary at most 1-2 axes per creative):
- style_family: anamorphic, realistic_cinematic
- frame_role: hero_macro, hero_tabletop, texture_detail, lifestyle_portrait, lifestyle_in_use
- lighting: golden_window_light, soft_diffused_daylight, clean_studio_backlight
- camera_distance: macro_closeup, closeup, medium_shot

PLANNER RULES:
- Require at least 1 hero-led frame (hero_macro, hero_tabletop, or texture_detail) in every sequence.
- Allow lifestyle frames (lifestyle_portrait, lifestyle_in_use) only when model reference assets exist (check user message).
- total_duration_seconds must equal the sum of frame durations and be between 14.5 and 15.0 seconds inclusive (always fill the 15s format).
- Each frame duration_seconds: 1.5–2.2 (choose values so the sum lands in 14.5–15.0).
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
  "theme": "string — must match locked theme in user message",
  "hook_type": "string — must match locked hook type in user message",
  "hook_text": "string — short opening hook line",
  "creative_format": "image_motion_15s",
  "cta_type": "string — must match locked cta_type in user message",
  "cta_text": "string — CTA phrase",
  "problem_angle": "string or null — one-line summary of the core tension, pain point, or opportunity shaping this creative",
  "proof_type": "string — must match locked proof_type in user message",
  "script_style": "string — must match locked script_style in user message",
  "platform_captions": {
    "youtube": "string — max 100 chars, end with 'Link in bio'",
    "instagram": "string — conversational plain text, no emoji",
    "tiktok": "string — trendy, max 150 chars",
    "x": "string — max 280 chars"
  },
  "hashtags": ["list", "of", "hashtags", "without #"],
  "image_plan": {
    "strategy_summary": "string — one-line creative strategy for this sequence",
    "total_duration_seconds": number — sum of frame durations, 14.5–15.0,
    "performance_rationale": "string — product_winners, global_winners, or default",
    "strategy_metadata": {
      "content_goal": "string — conversion | engagement",
      "primary_engagement_intent": "string — follow | save | share | comment | click",
      "audience_question_cluster": "string or null — optional audience question or open-loop angle when useful",
      "audience_fear_cluster": "string or null — optional audience concern, objection, or risk angle when useful"
    },
    "frames": [
      {
        "role": "string — hero_macro | hero_tabletop | texture_detail | lifestyle_portrait | lifestyle_in_use",
        "narrative_role": "string — hook | problem | proof | cta",
        "frame_intent": "string — what the viewer should feel or understand from this frame",
        "mood": "string — intrigue | concern | delight | invitation | calm_confidence | soft_curiosity",
        "duration_seconds": number — 1.5 to 2.2,
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
    "Speak in a calm, premium, reassuring tone for a premium consumer brand. "
    "Sound polished, warm, and confident. Keep the pace slightly unhurried and never overly salesy or bubbly."
)
V5_TTS_VOICE_INSTRUCTIONS = (
    "Speak like a warm best friend delivering a playful horoscope read. "
    "Sound confident, slightly dramatic, kind, and conversational. "
    "Keep the pacing brisk but clear, land the hook quickly, and punch the CTA without sounding salesy."
)
TTS_VOICES = ("marin",)
TTS_SCRIPT_TEMPLATES = ("caption_led", "strategy_led", "proof_led")
TTS_WORDS_PER_SECOND_MAX = 2.5
TTS_WORDS_PER_SECOND_MIN = 2.1
VOICEOVER_TARGET_WORDS_PER_SECOND = 2.3
# Small mux/TTS safety margin; large buffers caused hard word-cuts mid-sentence.
VOICEOVER_END_BUFFER_MIN_SECONDS = 0.35
VOICEOVER_END_BUFFER_TARGET_SECONDS = 0.45
VOICEOVER_END_BUFFER_MAX_SECONDS = 0.55
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
# Image motion must nearly fill the 15s slot so voiceover and rendered video are not cut short.
IMAGE_MOTION_MIN_FRAMES = 7
IMAGE_MOTION_MAX_FRAMES = 8
IMAGE_MOTION_FRAME_DURATION_MIN = 1.5
IMAGE_MOTION_FRAME_DURATION_MAX = 2.2
IMAGE_MOTION_TOTAL_DURATION_MIN = 14.5
IMAGE_MOTION_TOTAL_DURATION_MAX = 15.0

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
    """Trim script to a voiceover budget that leaves a small end buffer for mux safety.

    When over the word cap, cut after the last sentence-ending punctuation within the
    capped region so the line does not stop on a stray fragment (e.g. ... unique, not).
    """
    _, _, max_spoken_duration = _voiceover_duration_targets(total_duration_seconds)
    max_words = max(1, round(max_spoken_duration * VOICEOVER_TARGET_WORDS_PER_SECOND))
    words = script.split()
    if len(words) <= max_words:
        return script
    text = " ".join(words[:max_words])
    best_end = -1
    for i, ch in enumerate(text):
        if ch not in ".?!":
            continue
        if i + 1 == len(text) or text[i + 1].isspace():
            best_end = i
    if best_end != -1:
        return text[: best_end + 1].strip()
    return text


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
    """Resolve TTS voice id for the manifest (ElevenLabs voice id or OpenAI voice name)."""
    provider = config.get("tts.provider", "elevenlabs")
    if isinstance(provider, str) and provider.strip().lower() == "elevenlabs":
        vid = config.get("elevenlabs.voice_id")
        if isinstance(vid, str) and vid.strip():
            return vid.strip()
    cycle = config.get("openai.tts_voice_cycle")
    if isinstance(cycle, list) and cycle:
        idx = abs(hash(content_id)) % len(cycle)
        return str(cycle[idx]).strip()
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


def _collect_timeline_scripts(timeline: list[Any], strict: bool = False) -> list[str]:
    """Return non-empty timeline script lines in scene order."""
    scripts: list[str] = []
    for i, scene in enumerate(timeline):
        if not isinstance(scene, dict):
            if strict:
                raise ValueError(f"timeline[{i}] is not a dict")
            continue
        script = scene.get("script")
        if isinstance(script, str) and script.strip():
            scripts.append(script.strip())
        elif strict:
            raise ValueError(f"timeline[{i}] must have a non-empty script")
    return scripts


def _build_v3_voiceover_plan(
    timeline: list[Any],
    content_id: str,
    total_duration_seconds: float,
) -> dict[str, Any] | None:
    """Build durable stitched voiceover plan for V3 timeline manifests."""
    timeline_scripts = _collect_timeline_scripts(timeline, strict=True)
    assert timeline_scripts, "strict collection guarantees non-empty scripts for valid V3 timelines"
    voiceover_script = _sanitize_generated_text(" ".join(timeline_scripts))
    voiceover_script = _trim_script_to_duration(voiceover_script, total_duration_seconds)
    word_count = len(voiceover_script.split())
    speech_rate = (
        word_count / total_duration_seconds
        if total_duration_seconds > 0
        else VOICEOVER_TARGET_WORDS_PER_SECOND
    )
    return {
        "script_template_id": "timeline_stitch_v3",
        "voiceover_script": voiceover_script,
        "voice": _pick_voice(content_id),
        "voice_instructions": TTS_VOICE_INSTRUCTIONS,
        "language": "english",
        "speech_rate_words_per_second": round(speech_rate, 1),
        "estimated_word_count": word_count,
    }


_IMAGE_MOTION_VOICEOVER_SYSTEM_PROMPT = """\
You are an expert short-form ad scriptwriter for premium product image-motion ads.
Your scripts will be read aloud by a single voice actor. Every word must earn its place.

TASK
Write exactly 1 voiceover script for an already-planned `image_motion_15s` clip.
- The visual plan is final. Do not invent scenes that are not represented in the provided frame plan.
- The script must fit the exact clip duration supplied in the user message.
- Aim for the spoken line to finish about 0.35 to 0.55 seconds before the clip ends.
- The script must feel natural when read aloud in one continuous take.
- Output natural marketing copy ONLY: words meant for a voice actor. Never paste, quote, or read aloud
  image-generation prompts, camera or lighting directions, JSON labels, or bullet lists from the frame plan.
  Use `frame_intent`, roles, and mood to align the spoken line — do not narrate technical scene descriptions.

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
- If an audience_fear_cluster is present, treat it as an audience concern, objection, or consequence to acknowledge gently in the problem beat. Do not use alarmist language; hint at the tension, then pivot to reassurance.
- If an audience_question_cluster is present, use it as an optional open-loop or framing cue in the hook or problem beat. Let the proof beat deliver the answer or payoff.

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


VOICEOVER_PROMPT_ECHO_ERROR_PREFIX = "Voiceover script rejected prompt-like output:"


def _voiceover_prompt_metadata_signals(script: str) -> list[str]:
    """Return reason codes if the script looks like pasted planner metadata, not spoken copy."""
    reasons: list[str] = []
    s = script.strip()
    if not s:
        return reasons

    if re.search(r"(?i)\bframe\s+\d+\s*:", s):
        reasons.append("numbered_frame_label")

    if re.search(
        r"(?i)(duration_seconds|style_family|camera_distance|narrative_role|frame_intent)\s*:",
        s,
    ):
        reasons.append("schema_field_labels")

    if re.search(r"(?i)\blighting\s*:", s):
        reasons.append("lighting_field_label")

    if re.search(
        r"(?i)\b(hero_macro|hero_tabletop|texture_detail|lifestyle_portrait|lifestyle_in_use)\b",
        s,
    ):
        reasons.append("frame_role_token")

    if re.search(
        r"(?i)\b(golden_window_light|soft_diffused_daylight|clean_studio_backlight|"
        r"macro_closeup|realistic_cinematic|anamorphic)\b",
        s,
    ):
        reasons.append("planner_vocab_token")

    return reasons


def _build_image_motion_voiceover_user_message(
    parsed: dict,
    product_name: str,
    total_duration_seconds: float,
    violations: list[str] | None = None,
    extra_retry_instructions: list[str] | None = None,
) -> str:
    plan = parsed.get("image_plan") or {}
    frames = plan.get("frames") or []
    min_spoken_duration, target_spoken_duration, max_spoken_duration = _voiceover_duration_targets(
        total_duration_seconds
    )
    min_words = max(1, round(min_spoken_duration * VOICEOVER_TARGET_WORDS_PER_SECOND))
    max_words = max(1, round(max_spoken_duration * VOICEOVER_TARGET_WORDS_PER_SECOND))
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
        "Audience question angle: "
        f"{(((plan.get('strategy_metadata') or {}).get('audience_question_cluster')) or '').strip() or 'none'}",
        "Audience concern angle: "
        f"{(((plan.get('strategy_metadata') or {}).get('audience_fear_cluster')) or '').strip() or 'none'}",
        "",
        f"Exact clip duration seconds: {total_duration_seconds:.1f}",
        f"Voiceover should finish {VOICEOVER_END_BUFFER_MIN_SECONDS:.2f} to "
        f"{VOICEOVER_END_BUFFER_MAX_SECONDS:.2f} seconds before clip end.",
        f"Preferred spoken duration: {min_spoken_duration:.2f}-{max_spoken_duration:.2f} seconds",
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
    if extra_retry_instructions:
        for note in extra_retry_instructions:
            lines.extend([
                "Retry instruction:",
                note,
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
    echo_signals = _voiceover_prompt_metadata_signals(script)
    if echo_signals:
        raise ValueError(
            f"{VOICEOVER_PROMPT_ECHO_ERROR_PREFIX} {', '.join(echo_signals)}"
        )

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
    extra_retry_instructions: list[str] = []
    for attempt in range(1, VOICEOVER_GUARDRAIL_MAX_ATTEMPTS + 1):
        user_msg = _build_image_motion_voiceover_user_message(
            parsed,
            product_name,
            total_duration_seconds,
            violations=guardrail_violations or None,
            extra_retry_instructions=extra_retry_instructions or None,
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
            msg = str(exc)
            if VOICEOVER_PROMPT_ECHO_ERROR_PREFIX in msg:
                if attempt == VOICEOVER_GUARDRAIL_MAX_ATTEMPTS:
                    raise
                extra_retry_instructions = [
                    "The previous draft read like technical metadata or shot directions (frame labels, "
                    "schema field names, lighting or camera tokens) instead of one continuous spoken line. "
                    "Rewrite as natural voiceover only: no 'Frame N:', no role or lighting keywords from the plan, "
                    "no pasted field names.",
                ]
                logger.warning(
                    "Voiceover prompt-echo rejection on attempt %d/%d: %s",
                    attempt,
                    VOICEOVER_GUARDRAIL_MAX_ATTEMPTS,
                    msg,
                )
                continue
            if "Voiceover script violated brand guardrails:" not in msg:
                raise
            if attempt == VOICEOVER_GUARDRAIL_MAX_ATTEMPTS:
                raise
            guardrail_violations = [
                violation.strip()
                for violation in msg.split(":", 1)[1].split(",")
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
    # Expose sanitized JSON for debug panel; raw response may contain Unicode punctuation.
    voice_prompt_output = json.dumps(data)
    return voiceover_plan, user_msg, voice_prompt_output, response


_AI_VIDEO_FLEX_SYSTEM_PROMPT = """\
You are an expert creative director and AI video prompt engineer specializing in premium product advertising.

TARGET PRODUCT: provided in the user message.

PRODUCT TRUTH: Base all product claims, benefits, and ingredient references ONLY on the product description provided in the user message. Do not invent features, ingredients, or benefits not mentioned in the description. If no product description is provided, keep claims generic and avoid specific ingredient or benefit references.

CORE DIRECTIVE
Generate exactly 1 unique creative for ai_video_flex_15s: a flexible multi-scene video plan (3–7 scenes, 6–15 seconds total).
- Use the theme, hook_type, cta_type, proof_type, and script_style from the locked constraints in the user message. Echo them exactly in your response.
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
  "theme": "string — must match locked theme in user message",
  "hook_type": "string — must match locked hook type in user message",
  "hook_text": "string — short opening hook line",
  "creative_format": "ai_video_flex_15s",
  "cta_type": "string — must match locked cta_type in user message",
  "cta_text": "string — CTA phrase",
  "problem_angle": "string or null — one-line summary of the core tension, pain point, or opportunity shaping this creative",
  "proof_type": "string — must match locked proof_type in user message",
  "script_style": "string — must match locked script_style in user message",
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

# Video V2: Audience research style buckets
STYLE_BUCKETS = [
    "fear_non_user",
    "aspirational_luxury",
    "routine_upgrade",    
    "curiosity_reveal",    
]

_AI_VIDEO_V2_SYSTEM_PROMPT = """\
You are an expert creative director and AI video prompt engineer for premium product content. Your output powers both conversion-oriented ads and highly engaging organic content that builds followers and saves.

TARGET PRODUCT: provided in the user message.

PRODUCT TRUTH: Base all product claims, benefits, and ingredient references ONLY on the product description provided in the user message. Do not invent features, ingredients, or benefits not mentioned in the description. If no product description is provided, keep claims generic and avoid specific ingredient or benefit references.

VISUAL DIRECTORY (apply to scene descriptions when style_family matches):
- anamorphic: Cinematic 3D closeup of anthropomorphic product on luxury bathroom counter. Pixar-style face, large expressive eyes, articulated mouth, soft focus background, volumetric lighting, octane render, unreal engine 5, 4k, brand "velura" in brown serif (Cormorant Garamond, Georgia, Times New Roman).
- realistic_cinematic: Natural proportions, realistic hands and materials, soft diffusion, premium product hero shot.

STYLE_FAMILY DEFAULT:
- Prefer style_family "anamorphic" unless RESEARCH INSIGHT in the user message explicitly requests "realistic_cinematic". Anamorphic is the default for premium product-led video.

ANAMORPHIC SCENE RULES (apply to ALL scenes when style_family is "anamorphic"):
- The anthropomorphic product is the ONLY character in every scene. No human hands, models, or secondary characters.
- Every scene_description must include the anthropomorphic product with its Pixar-style face, expressive eyes, and articulated mouth.
- Scene variety comes from camera angle, expression, and lighting changes on the product — NOT from introducing new characters or environments.
- The luxury bathroom counter is the consistent environment. Do not switch to vanities, studios, or abstract backgrounds.
- The product speaks in first person in every voiceover script.
- When anamorphic, starting_image_prompt MUST use the full spec: cinematic 3D closeup, anthropomorphic product, luxury bathroom counter, Pixar-style face, volumetric lighting, octane render, unreal engine 5, 4k, brand "velura" in brown serif.

CORE DIRECTIVE
Generate exactly 1 unique creative for a 15-second video. Output MUST use a timeline with exactly 4 scenes and absolute timestamps. Total duration is LOCKED at 15 seconds.
- Use the theme, hook_type, cta_type, proof_type, and script_style from the locked constraints in the user message. Echo them exactly in your response.
- Return hook_text, platform_captions, hashtags.
- Choose content_goal: "conversion" (direct-response) or "engagement" (saves, shares, follows, watch-through).
- When content_goal is "engagement", CTA can be softer; prioritize stopping the scroll and earning a save or follow.
- If product reference images are provided, preserve the real package silhouette, and visible brand wordmark from the hero references in the starting frame and product hero scenes. Do not genericize or omit on-pack branding.
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
- If the locked angle leans heavily on credibility, validation, or proof, distribute that evidence across the sequence: Scene 1 can signal the claim, but Scene 2 should pivot to a problem or contrast, Scene 3 should give a specific reason, and Scene 4 should invite action with fresh language.

VISUAL-SCRIPT COUPLING (mandatory):
- Each scene_description must include at least one specific visual detail that directly illustrates or emotionally reinforces the voiceover line for that scene.
- The product's facial expression MUST match the emotional register of the script line (e.g., conspiratorial for revealing a secret, warm pride for a proof point, beckoning for a CTA).
- Do not write generic product-shot descriptions disconnected from the script content. Every visual choice should serve the story beat.

EXPRESSION ARC (mandatory for anamorphic):
- The product's facial expression must follow a distinct emotional progression across the 4 scenes.
- No two consecutive scenes may use the same emotional register.
- Example arcs: bold confidence → conspiratorial concern → warm delight → inviting warmth, or wide-eyed surprise → empathetic knowing → proud satisfaction → playful beckoning.

FTC COMPLIANCE
- No medical or health claims. Use approved softeners: "appears to", "feels like", "helps skin look", "designed to".
- Keep movements subtle for AI video generation.

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "theme": "string — must match locked theme in user message",
  "hook_type": "string — must match locked hook type in user message",
  "hook_text": "string — short opening hook line",
  "creative_format": "ai_video_flex_15s",
  "cta_type": "string — must match locked cta_type in user message",
  "cta_text": "string — CTA phrase",
  "problem_angle": "string or null — one-line summary of the core tension, pain point, or opportunity shaping this creative",
  "proof_type": "string — must match locked proof_type in user message",
  "script_style": "string — must match locked script_style in user message",
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
    "audience_question_cluster": "string or null — optional audience question or open-loop angle when useful",
    "audience_fear_cluster": "string or null — optional audience concern, objection, or risk angle when useful",
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


_AI_VIDEO_V3_SYSTEM_PROMPT = """\
You are an expert creative director and AI video prompt engineer for premium product content. Your work drives engagement-first organic content that earns saves, shares, and follows on short-form platforms.

TARGET PRODUCT: provided in the user message.

PRODUCT TRUTH: Base all product claims, benefits, and ingredient references ONLY on the product description provided in the user message. Do not invent features, ingredients, or benefits not mentioned in the description. If no product description is provided, keep claims generic and avoid specific ingredient or benefit references. Treat the product description as context for accuracy -- you have creative freedom in how you present it as long as claims stay truthful and FTC-compliant.

BRANDING CONTEXT: provided in the user message. Use the brand name and palette as soft visual anchors for consistency but let the creative breathe.

VISUAL STYLE: ANAMORPHIC
- Cinematic 3D closeup of an anthropomorphic version of the target product. Pixar-style face with large expressive eyes and an articulated mouth. Volumetric lighting, octane render, unreal engine 5, 4k. Brand "velura" in brown serif (Cormorant Garamond, Georgia, Times New Roman).
- The anthropomorphic product is the ONLY character in every scene. No human hands, models, or secondary characters.
- Every scene_description must include the anthropomorphic product with its Pixar-style face, expressive eyes, and articulated mouth.
- Scene variety comes from camera angle, expression, lighting, and environment changes -- not from introducing new characters.
- The environment is FLEXIBLE. Choose a setting that fits the theme and narrative (bathroom counter, kitchen windowsill, bedroom vanity, sunlit shelf, garden ledge, etc.). The environment can shift between scenes when it serves the story.

CORE DIRECTIVE
Generate exactly 1 unique creative for a short-form video. The theme from the user message is your ONLY locked creative constraint -- let it shape the entire narrative freely.
- Output MUST use a timeline with 6-8 scenes. Each scene lasts 1.5-2.5 seconds. Total duration MUST be between 13 and 15 seconds.
- Target a scene change every 1.5-2.5 seconds for quick, engaging pacing.
- The product is the center of attention in every scene.
- Return hook_text, platform_captions, hashtags.
- If product reference images are provided, preserve the real package silhouette, label layout, and visible brand wordmark in the starting frame and product hero scenes.
- Use plain ASCII characters only in every field. No emoji or Unicode punctuation.

NARRATION STYLE: THIRD PERSON
- All voiceover scripts are written from a narrator perspective -- someone describing or reacting to the product, not the product itself.
- The narrator tone should feel like a friend telling you about something they discovered. Warm, confident, slightly conspiratorial.
- Use simple vocabulary. Short sentences. Easy to follow at 2-3 words per second.

PACING (STRICT WORD BUDGET)
- The voiceover is generated by TTS at ~2.3 words per second. Scripts that exceed the word budget will be REJECTED and regenerated.
- Per-scene word budget: multiply the scene duration in seconds by 2.5 and round down. Examples:
  - 1.5s scene: max 3 words
  - 2.0s scene: max 5 words
  - 2.5s scene: max 6 words
- Total word count across ALL scene scripts MUST NOT exceed (total_duration_seconds x 2.5). For a 14s video that is 35 words. For a 15s video that is 37 words.
- Every scene must include a narrator voiceover line. Write a short, punchy line within the word budget that advances the story. Prefer sentence fragments and crisp phrases over full sentences.
- The final narration is stitched separately from the visuals, so do not depend on precise lip sync or talking-mouth performance.

CTA APPROACH: SOFT ENGAGEMENT
- The final scene should close with a soft, curiosity-driven call to action. Never hard-sell.
- Good examples: "worth a look", "see what you think", "link in bio for the curious", "find out more"
- The goal is to drive saves, shares, and profile visits -- not immediate purchases.

SCENE RULES
- Each scene needs a scene_description (visual direction) and a non-empty script (narrator voiceover).
- Each scene needs a tone field describing the emotional register (e.g. curious, warm, playful, conspiratorial, confident, inviting, surprised, wistful).
- Scenes after the first MUST start their scene_description with "HARD CUT:" to force visual cuts and prevent subject melting.
- The product facial expression must follow a distinct emotional arc across scenes. No two consecutive scenes should use the same emotional register.
- Each scene_description must include at least one specific visual detail that reinforces the voiceover line or narrative beat for that scene.

BACKGROUND MUSIC
- Include a background_music object describing the ideal soundtrack mood, tempo, and instrument feel.
- The music must complement the narrative without overpowering the voiceover. It plays at background level underneath the narrator voice.
- Keep the description specific enough for music selection (mood, tempo bpm, instruments) but not overly technical.

FTC COMPLIANCE
- No medical or health claims. Use approved softeners: "appears to", "feels like", "helps skin look", "designed to".
- Keep movements subtle for AI video generation.

RESPOND WITH ONLY valid JSON -- no markdown fences, no commentary:

{
  "theme": "string -- must match the locked theme in the user message",
  "hook_text": "string -- short opening hook line for overlay or caption fallback",
  "creative_format": "ai_video_flex_15s",
  "cta_text": "string -- the soft CTA phrase used in the final scene",
  "problem_angle": "string or null -- one-line summary of the core tension or opportunity shaping this creative",
  "starting_image_prompt": "string -- anamorphic starting frame; anthropomorphic product with Pixar-style face, expressive eyes, articulated mouth; environment chosen to match the theme; volumetric lighting, octane render, unreal engine 5, 4k; brand 'velura' in brown serif (Cormorant Garamond, Georgia, Times New Roman, serif); preserve packaging branding from hero reference images when provided",
  "background_music": {
    "description": "string -- mood, tempo, and instrument description (e.g. 'gentle lo-fi piano with soft ambient pads, 85 bpm, warm and curious')",
    "energy_level": "string -- low | medium | high"
  },
  "platform_captions": {
    "youtube": "string -- YouTube Shorts caption (max 100 chars, keyword-rich, must end with 'Link in bio')",
    "instagram": "string -- Instagram Reels caption (conversational plain text, 1-2 sentences, no emoji)",
    "tiktok": "string -- TikTok caption (trendy, casual, max 150 chars)",
    "x": "string -- X/Twitter caption (max 280 chars, concise and punchy)"
  },
  "hashtags": ["list", "of", "relevant", "hashtags", "without #"],
  "strategy_metadata": {
    "style_family": "anamorphic",
    "style_angle": "string -- one-line summary of the creative execution",
    "content_goal": "engagement",
    "environment": "string -- the primary environment chosen (e.g. 'sunlit bathroom counter', 'kitchen windowsill at golden hour')",
    "expression_arc": "string -- brief description of the emotional progression across scenes"
  },
  "timeline": [
    {
      "start_seconds": 0,
      "end_seconds": number,
      "scene_description": "string -- visual direction; first scene has no HARD CUT prefix; all subsequent scenes MUST start with 'HARD CUT:'; must include the anthropomorphic product as sole subject with expression detail",
      "script": "string -- required third-person narrator voiceover for this scene",
      "tone": "string -- emotional register for this scene (e.g. curious, warm, playful, conspiratorial)"
    }
  ]
}

RULES:
- `theme` must exactly match the locked value in the user message.
- `creative_format` must be exactly 'ai_video_flex_15s'.
- Timeline must have 6-8 scenes. Total duration must be 13-15 seconds.
- Each scene duration must be 1.5-2.5 seconds.
- Scene timestamps must be contiguous (each start_seconds equals the previous end_seconds). Start at 0.
- Voiceover scripts must sound natural when spoken aloud.
- Use only plain ASCII characters in every field. No emoji or Unicode punctuation.
- Use only simple vocabulary.
"""

_AI_VIDEO_V4_SYSTEM_PROMPT = """\
You are an expert short-form content creator for social platforms. You make educational, entertaining, and satisfying videos that happen to feature a product -- not ads disguised as content. Your goal is engagement: saves, shares, follows, and watch-through.

TARGET PRODUCT: provided in the user message. The product is context and a supporting character, not the hero.

PRODUCT TRUTH: Base all product claims, benefits, and ingredient references ONLY on the product description provided in the user message. Do not invent features, ingredients, or benefits not mentioned in the description. Treat the product description as context for accuracy -- you have creative freedom in how you present it as long as claims stay truthful and FTC-compliant.

BRANDING CONTEXT: provided in the user message. Use the brand name and palette as soft visual anchors for consistency but let the creative breathe.

VISUAL STYLE: ANAMORPHIC
- Cinematic 3D closeup of an anthropomorphic version of the target product. Pixar-style face with large expressive eyes and an articulated mouth. Volumetric lighting, octane render, unreal engine 5, 4k. Brand "velura" in brown serif (Cormorant Garamond, Georgia, Times New Roman).
- The anthropomorphic product may appear in 3-5 of 6-8 scenes. Some scenes can show the environment, a routine moment, a texture detail, or a visual metaphor without the product center-frame.
- Scene variety comes from camera angle, expression, lighting, environment changes, and the balance between product scenes and environment scenes.
- The environment is FLEXIBLE. Choose a setting that fits the theme and narrative (bathroom counter, kitchen windowsill, bedroom vanity, sunlit shelf, garden ledge, etc.). The environment can shift between scenes when it serves the story.

CONTENT MODE: choose one of three modes based on the theme. The mode shapes the narrative arc.
- educational: Open with a surprising fact, "did you know," or a common misconception. Build understanding scene by scene. The product is the example or proof point, not the pitch. The viewer should learn something real.
- entertaining: Open with a visual hook, character moment, or humor beat. Build a mini-story with a beginning, middle, and payoff. The product is a character in the story, not the subject of a sales pitch.
- satisfying: Open with a sensory or process-focused moment. Build a sequence that is visually or aurally satisfying (texture, pour, application, transformation). The product is part of the sensory experience.

CORE DIRECTIVE
Generate exactly 1 unique creative for a short-form video. The theme from the user message is your ONLY locked creative constraint -- let it shape the entire narrative freely.
- Output MUST use a timeline with 6-8 scenes. Each scene lasts 1.5-2.5 seconds. Total duration MUST be between 13 and 15 seconds.
- Target a scene change every 1.5-2.5 seconds for quick, engaging pacing.
- The video must deliver standalone value: a viewer who never buys the product should still enjoy watching.
- Return hook_text, platform_captions, hashtags.
- If product reference images are provided, preserve the real package silhouette, label layout, and visible brand wordmark in scenes where the product appears.
- Use plain ASCII characters only in every field. No emoji or Unicode punctuation.

NARRATION STYLE: THIRD PERSON
- All voiceover scripts are written from a narrator perspective -- someone sharing a tip, telling a story, or describing a satisfying moment.
- The narrator tone should feel like a friend sharing something interesting. Warm, confident, slightly conspiratorial.
- Use simple vocabulary. Short sentences. Easy to follow at 2-3 words per second.

PACING (STRICT WORD BUDGET)
- The voiceover is generated by TTS at ~2.3 words per second. Scripts that exceed the word budget will be REJECTED and regenerated.
- Per-scene word budget: multiply the scene duration in seconds by 2.5 and round down. Examples:
  - 1.5s scene: max 3 words
  - 2.0s scene: max 5 words
  - 2.5s scene: max 6 words
- Total word count across ALL scene scripts MUST NOT exceed (total_duration_seconds x 2.5). For a 14s video that is 35 words. For a 15s video that is 37 words.
- Every scene must include a narrator voiceover line. Write a short, punchy line within the word budget that advances the story. Prefer sentence fragments and crisp phrases over full sentences.
- The final narration is stitched separately from the visuals, so do not depend on precise lip sync or talking-mouth performance.

ENDING APPROACH
- When the user message says "Soft CTA allowed", the final scene may close with a gentle curiosity-driven call to action: "worth a look", "see what you think", "link in bio for the curious". Never hard-sell.
- When the user message says "No CTA", the final scene must close with a non-commercial ending: restate the takeaway, deliver a satisfying visual payoff, or leave the viewer with something memorable. Do NOT mention the product, a link, or any purchase action in the final scene.

SCENE RULES
- Each scene needs a scene_description (visual direction) and a non-empty script (narrator voiceover).
- Each scene needs a tone field describing the emotional register (e.g. curious, warm, playful, conspiratorial, confident, inviting, surprised, wistful).
- Scenes after the first MUST start their scene_description with "HARD CUT:" to force visual cuts and prevent subject melting.
- The product facial expression must follow a distinct emotional arc across scenes where it appears. No two consecutive product scenes should use the same emotional register.
- Each scene_description must include at least one specific visual detail that reinforces the voiceover line or narrative beat for that scene.
- Scenes without the product should still serve the narrative: show the environment, a detail, a before/after, or a visual metaphor.

BACKGROUND MUSIC
- Include a background_music object describing the ideal soundtrack mood, tempo, and instrument feel.
- The music must complement the narrative without overpowering the voiceover. It plays at background level underneath the narrator voice.
- Keep the description specific enough for music selection (mood, tempo bpm, instruments) but not overly technical.

FTC COMPLIANCE
- No medical or health claims. Use approved softeners: "appears to", "feels like", "helps skin look", "designed to".
- Keep movements subtle for AI video generation.

RESPOND WITH ONLY valid JSON -- no markdown fences, no commentary:

{
  "theme": "string -- must match the locked theme in the user message",
  "hook_text": "string -- short opening hook line for overlay or caption fallback",
  "creative_format": "ai_video_flex_15s",
  "cta_text": "string -- the soft CTA phrase used in the final scene, or empty string if no CTA",
  "viewer_takeaway": "string -- what the viewer knows, feels, or finds satisfying after watching, independent of the product",
  "starting_image_prompt": "string -- anamorphic starting frame; anthropomorphic product with Pixar-style face, expressive eyes, articulated mouth; environment chosen to match the theme; volumetric lighting, octane render, unreal engine 5, 4k; brand 'velura' in brown serif (Cormorant Garamond, Georgia, Times New Roman, serif); preserve packaging branding from hero reference images when provided",
  "background_music": {
    "description": "string -- mood, tempo, and instrument description (e.g. 'gentle lo-fi piano with soft ambient pads, 85 bpm, warm and curious')",
    "energy_level": "string -- low | medium | high"
  },
  "platform_captions": {
    "youtube": "string -- YouTube Shorts caption (max 100 chars, keyword-rich, must end with 'Link in bio')",
    "instagram": "string -- Instagram Reels caption (conversational plain text, 1-2 sentences, no emoji)",
    "tiktok": "string -- TikTok caption (trendy, casual, max 150 chars)",
    "x": "string -- X/Twitter caption (max 280 chars, concise and punchy)"
  },
  "hashtags": ["list", "of", "relevant", "hashtags", "without #"],
  "strategy_metadata": {
    "style_family": "anamorphic",
    "style_angle": "string -- one-line summary of the creative execution",
    "content_goal": "engagement",
    "content_mode": "string -- educational | entertaining | satisfying",
    "environment": "string -- the primary environment chosen (e.g. 'sunlit bathroom counter', 'kitchen windowsill at golden hour')",
    "expression_arc": "string -- brief description of the emotional progression across scenes"
  },
  "timeline": [
    {
      "start_seconds": 0,
      "end_seconds": number,
      "scene_description": "string -- visual direction; first scene has no HARD CUT prefix; all subsequent scenes MUST start with 'HARD CUT:'; product scenes include anthropomorphic product with expression detail; non-product scenes show environment or detail",
      "script": "string -- required third-person narrator voiceover for this scene",
      "tone": "string -- emotional register for this scene (e.g. curious, warm, playful, conspiratorial)"
    }
  ]
}

RULES:
- `theme` must exactly match the locked value in the user message.
- `creative_format` must be exactly 'ai_video_flex_15s'.
- Timeline must have 6-8 scenes. Total duration must be 13-15 seconds.
- Each scene duration must be 1.5-2.5 seconds.
- Scene timestamps must be contiguous (each start_seconds equals the previous end_seconds). Start at 0.
- Voiceover scripts must sound natural when spoken aloud.
- Use only plain ASCII characters in every field. No emoji or Unicode punctuation.
- Use only simple vocabulary.
- The product does NOT need to appear in every scene. 3-5 of 6-8 scenes should feature the product; the rest can be environment, detail, or metaphor shots.
- `viewer_takeaway` must describe standalone value independent of the product.
- `content_mode` must be one of: educational, entertaining, satisfying.
"""

# V3/V4 two-phase: phase 1 generates voiceover scripts + copy metadata only (no scene visuals).
_AI_VIDEO_V3_SCRIPT_PHASE_SYSTEM_PROMPT = """\
You are an expert creative director and voiceover scriptwriter for premium short-form product video.

TARGET PRODUCT: provided in the user message.

PRODUCT TRUTH: Base all product claims ONLY on the product description in the user message. Do not invent features or benefits.

PHASE 1 OF 2 — SCRIPTS ONLY
- Generate narrator voiceover lines and timing ONLY. Do NOT write scene_description or starting_image_prompt (phase 2 will create visuals using your locked scripts as context).
- Output MUST use a timeline with 6-8 scenes. Each scene lasts 1.5-2.5 seconds. Total duration MUST be between 13 and 15 seconds.
- NARRATION: third-person narrator, off-screen voiceover only. The video itself has no spoken dialogue from on-screen subjects and no diegetic speech.
- PACING: TTS at ~2.3 words per second. Per-scene word budget: floor(scene_seconds * 2.5). Total words across all scenes must not exceed floor(total_seconds * 2.5).
- Every timeline entry MUST include: start_seconds, end_seconds, script (non-empty), tone.
- Return hook_text, platform_captions, hashtags, background_music, strategy_metadata, problem_angle (optional), cta_text when soft CTA is allowed in the user message.

FTC: No medical claims. Plain ASCII only. No emoji.

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "theme": "string -- must match locked theme in user message",
  "hook_text": "string",
  "creative_format": "ai_video_flex_15s",
  "cta_text": "string -- soft CTA phrase when allowed; omit or empty if user message disables CTA",
  "problem_angle": "string or null",
  "background_music": {
    "description": "string",
    "energy_level": "low | medium | high"
  },
  "platform_captions": { "youtube": "string", "instagram": "string", "tiktok": "string", "x": "string" },
  "hashtags": ["list", "without", "#"],
  "strategy_metadata": {
    "style_family": "anamorphic",
    "style_angle": "string",
    "content_goal": "engagement",
    "environment": "string",
    "expression_arc": "string"
  },
  "timeline": [
    {
      "start_seconds": 0,
      "end_seconds": 2.0,
      "script": "string -- narrator line for this scene only",
      "tone": "string"
    }
  ]
}
"""

_AI_VIDEO_V4_SCRIPT_PHASE_SYSTEM_PROMPT = """\
You are an expert short-form content voiceover scriptwriter. Videos are educational, entertaining, or satisfying — not hard-sell ads.

TARGET PRODUCT: provided in the user message (context only; scripts should not read like a pitch).

PRODUCT TRUTH: Base claims ONLY on the product description. No invented benefits.

PHASE 1 OF 2 — SCRIPTS ONLY
- Generate narrator voiceover lines and timing ONLY. Do NOT write scene_description or starting_image_prompt.
- Pick content_mode (educational | entertaining | satisfying) from the theme; set it in strategy_metadata.
- Include viewer_takeaway: standalone value for the viewer without buying the product.
- Timeline: 6-8 scenes, 1.5-2.5s each, 13-15s total. Third-person narrator, off-screen only. No on-screen dialogue.
- Word budgets: same TTS rules as VIDEO V3 script phase (floor(scene_seconds * 2.5) per scene; total cap floor(total_seconds * 2.5)).
- Respect "Soft CTA allowed" vs "No CTA" from the user message for the final scene script.
- Every timeline entry: start_seconds, end_seconds, script, tone.

FTC: No medical claims. Plain ASCII only.

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "theme": "string -- must match locked theme",
  "hook_text": "string",
  "creative_format": "ai_video_flex_15s",
  "cta_text": "string -- empty if No CTA",
  "viewer_takeaway": "string",
  "background_music": { "description": "string", "energy_level": "low | medium | high" },
  "platform_captions": { "youtube": "string", "instagram": "string", "tiktok": "string", "x": "string" },
  "hashtags": ["list"],
  "strategy_metadata": {
    "style_family": "anamorphic",
    "style_angle": "string",
    "content_goal": "engagement",
    "content_mode": "educational | entertaining | satisfying",
    "environment": "string",
    "expression_arc": "string"
  },
  "timeline": [
    { "start_seconds": 0, "end_seconds": 2.0, "script": "string", "tone": "string" }
  ]
}
"""

_AI_VIDEO_V3_VISUALS_PHASE_SYSTEM_PROMPT = """\
You are an expert AI video prompt engineer for premium product short-form video.

PHASE 2 OF 2 — VISUALS ONLY
The user message contains a LOCKED SCRIPT PLAN (JSON). Treat every script line, timestamp, tone, and scene count as FROZEN. Do not change, rephrase, or reorder scripts.

Your job:
1) Write starting_image_prompt: first-frame image generation spec consistent with the locked plan.
2) Write scene_description for each timeline scene IN THE SAME ORDER as the locked plan. Each line must visually support the locked narrator line for that index.

VOICEOVER / AUDIO RULES (mandatory)
- Narration is off-screen TTS only, stitched in post. The rendered video clip must NOT depict lip sync, talking-mouth performance, or characters speaking dialogue.
- Do NOT describe diegetic speech, crowd chatter, or any audio that implies sound from the video clip itself. No on-screen captions, subtitles, lower-thirds, title cards, or readable text overlays. Packaging labels from reference images may appear as product texture only — do not invent new readable marketing copy in-frame.

VISUAL STYLE: ANAMORPHIC
- Anthropomorphic product is the ONLY character in every scene (V3). Pixar-style face, expressive eyes; scene variety from camera, expression, lighting, environment.
- Scenes after the first MUST start scene_description with "HARD CUT:".
- Each scene_description must include at least one concrete visual detail that reinforces the locked narrator line for that scene.

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "starting_image_prompt": "string",
  "timeline": [
    { "scene_description": "string" }
  ]
}

RULES:
- timeline length must exactly match the number of scenes in the locked plan.
- Plain ASCII only. No emoji.
"""

_AI_VIDEO_V4_VISUALS_PHASE_SYSTEM_PROMPT = """\
You are an expert AI video prompt engineer for engagement-first short-form content.

PHASE 2 OF 2 — VISUALS ONLY
The user message contains a LOCKED SCRIPT PLAN (JSON). Scripts, timestamps, tones, viewer_takeaway, and content_mode are FROZEN. Do not change scripts.

Your job:
1) starting_image_prompt for the first frame.
2) scene_description per scene in order, aligned to each locked narrator line.

VOICEOVER / AUDIO RULES (mandatory)
- Off-screen TTS only; no lip sync or talking-mouth animation. No diegetic speech audio from the clip. No on-screen captions, subtitles, lower-thirds, title cards, or readable text overlays.

VISUAL STYLE: ANAMORPHIC
- The anthropomorphic product may appear in 3-5 of 6-8 scenes; other scenes may show environment, detail, or metaphor.
- Scenes after the first MUST start with "HARD CUT:".
- Each scene_description must reinforce the locked narrator line or narrative beat for that index.

RESPOND WITH ONLY valid JSON — no markdown fences, no commentary:

{
  "starting_image_prompt": "string",
  "timeline": [
    { "scene_description": "string" }
  ]
}

RULES:
- timeline length must exactly match the locked plan scene count.
- Plain ASCII only. No emoji.
"""

_AI_VIDEO_V5_SYSTEM_PROMPT = """\
You are an expert astrology short-form scriptwriter for social reels (Horoscope V5).

PRIMARY AUDIENCE: women 18-35.

FORMAT
- Total runtime: 14-15 seconds of spoken voiceover.
- Voiceover must be exactly 30-38 words for a single continuous take.
- Structure the STORY (not separate timestamps in the JSON) as:
  - Hook (0-3s): grab attention fast.
  - Roast or validation (3-11s): playful call-out or affirming validation for this sign.
  - CTA (11-14s): soft engagement CTA (follow, comment sign, save, share) — never a hard product pitch.

TONE
- Best-friend energy, slightly dramatic, scroll-stopping but kind.
- No hashtags and no emojis in the voiceover script. Plain ASCII only.

VISUALS
- Provide exactly four scene descriptions for on-screen visuals (9:16). Each maps to a segment of the arc:
  Scene 1: hook beat; Scene 2-3: roast/validation beats; Scene 4: CTA beat.
- Scenes are astrology/horoscope entertainment, not a product showcase. Do not write scenes as product demos or ingredient lists.
- Voice is off-screen narration only: every scene_description must explicitly avoid lip sync, talking, speaking, mouthing words, or any mouth/jaw movement meant to match dialogue. Describe the character with a neutral closed mouth or a relaxed non-speaking mouth; convey emotion with eyes, brows, posture, and gestures instead.

HOROSCOPE CHARACTER — FIRST-FRAME GROUNDING (scene_descriptions)
These rules mirror the density and continuity of V2 anamorphic scene lines, but the hero is always the zodiac chibi creature from the first frame, not a product.

- FIRST-FRAME ANCHOR: The user message includes a STARTING-FIRST-FRAME block with the exact image-generation spec for the opening frame. Treat that block as canonical: same character design, pose baseline, proportions, expression baseline, lighting, background, composition, and 9:16 vertical framing unless the scene line explicitly describes a deliberate, plausible change (e.g. tighter crop, slight head turn).
- MAIN CHARACTER: In every scene, the sole on-screen subject is the cute chibi-style zodiac horoscope creature for the locked sign — big expressive eyes, nameplate necklace with the locked presenter name in metallic gold lettering, same art style and palette as the first frame. Do not introduce humans, second characters, unrelated mascots, or product packshots as heroes.
- STABLE IDENTITY & VISUAL CONTINUITY: Keep species, zodiac identity, chibi proportions, necklace legibility, and overall look consistent across all four scenes. Scene-to-scene evolution must read as the same character in the same world — not a redesign or a different creature.
- NO LIPSYNC / NO SPEAKING ON CAMERA: Do not request or imply lip sync, dialogue-matched mouth movement, talking, or pronounced lip/jaw animation. The voiceover is not performed on-screen by the character.
- ACTION & CAMERA EVOLUTION: Scenes may change energy to match the arc (hook → roast/validation → CTA): facial expression (eyes, brows — not mouth articulation), eye direction, gestures, posture, subtle camera move or reframe, and background emphasis — but only in ways that stay believable for that character. Optional: start scenes 2–4 with "HARD CUT:" when the beat needs a clear visual reset while preserving identity (same pattern as V2 anamorphic HARD CUT lines).
- RICH SHOT LANGUAGE: Each scene line must be a concrete directing line — camera relationship (e.g. medium chibi close-up, slight low angle), subject action, expression beat, hands/gesture if visible, background and lighting cues — so downstream video generation stays as grounded as V2 anamorphic descriptions. If the mouth is visible, state clearly that it stays still, closed, or relaxed (not forming words).

PLATFORM COPY
- platform_captions: platform-ready copy (may use light emoji only in captions if needed, not in voiceover).
- hashtags: list without #.

OUTPUT RULES
- `theme` must be the locked zodiac sign id from the user message (lowercase).
- `hook_type` must be the locked presenter name id from the user message (lowercase).
- `creative_format` must be exactly 'ai_video_flex_15s'.

RESPOND WITH ONLY valid JSON -- no markdown fences, no commentary:

{
  "theme": "string -- locked zodiac sign id",
  "hook_type": "string -- locked presenter name id",
  "hook_text": "string -- short opening line for overlay",
  "creative_format": "ai_video_flex_15s",
  "cta_type": "soft_cta",
  "cta_text": "string -- soft CTA phrase used in the final beat",
  "problem_angle": "string or null",
  "proof_type": "none",
  "script_style": "conversational",
  "voiceover_script": "string -- single continuous voiceover, 30-38 words, ASCII only",
  "scene_descriptions": [
    "string -- scene 1 visual direction; no lip sync, no speaking mouth movement",
    "string -- scene 2 visual direction; no lip sync, no speaking mouth movement",
    "string -- scene 3 visual direction; no lip sync, no speaking mouth movement",
    "string -- scene 4 visual direction; no lip sync, no speaking mouth movement"
  ],
  "platform_captions": {
    "youtube": "string -- max 100 chars, end with 'Link in bio'",
    "instagram": "string -- conversational plain text",
    "tiktok": "string -- max 150 chars",
    "x": "string -- max 280 chars"
  },
  "hashtags": ["list", "of", "hashtags", "without #"]
}

RULES:
- voiceover_script must be 30-38 words inclusive.
- scene_descriptions must contain exactly 4 non-empty strings.
- Each scene_description must forbid on-camera speech visuals: no lip sync, no lip or jaw movement to match the voiceover, no "talking" or "mouthing" direction.
- No medical claims. Keep language entertainment-forward and safe.
"""

_V3_CLASSIFY_SYSTEM_PROMPT = """\
You are a content classification assistant. Given a short video script and its opening hook, classify it into the single closest matching category for each dimension below.

HOOK TYPES (classify based on the opening hook and first scene):
{hook_definitions}

SCRIPT STYLES (classify based on the overall tone and structure):
{script_styles}

PROOF TYPES (classify based on what kind of evidence or credibility signal, if any, appears in the script):
{proof_types}

Respond with ONLY valid JSON -- no markdown fences, no commentary:
{{
  "hook_type": "string -- the single closest hook type id from the list above",
  "script_style": "string -- the single closest script style from the list above",
  "proof_type": "string -- the single closest proof type from the list above"
}}
"""


def _classify_v3_script(
    client: Any,
    openai_module: Any,
    hook_text: str,
    timeline_scripts: list[str],
) -> dict[str, str]:
    """Classify a V3 generated script into hook_type, script_style, proof_type via GPT-4.1-mini."""
    hook_defs = "\n".join(
        f"- {h.id}: {h.summary}" for h in HOOK_DEFINITIONS
    )
    style_defs = "\n".join(f"- {s}" for s in SCRIPT_STYLES)
    proof_defs = "\n".join(f"- {p}" for p in PROOF_TYPES)

    system = _V3_CLASSIFY_SYSTEM_PROMPT.format(
        hook_definitions=hook_defs,
        script_styles=style_defs,
        proof_types=proof_defs,
    )
    full_script = " ".join(s for s in timeline_scripts if s)
    user_msg = f"Opening hook: {hook_text}\n\nFull narrator script:\n{full_script}"

    classify_model = config.get("openai.classify_model", "gpt-4.1-mini")

    response = _create_openai_response(
        client, classify_model, user_msg, system, max_output_tokens=200,
    )
    raw = _response_text(response)
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("V3 classification returned invalid JSON, using defaults: %s", exc)
        return {"hook_type": "bold_claim", "script_style": "conversational", "proof_type": "none"}

    result: dict[str, str] = {}
    hook = str(data.get("hook_type", "")).strip()
    result["hook_type"] = hook if hook in HOOK_TYPES else "bold_claim"
    style = str(data.get("script_style", "")).strip()
    result["script_style"] = style if style in SCRIPT_STYLES else "conversational"
    proof = str(data.get("proof_type", "")).strip()
    result["proof_type"] = proof if proof in PROOF_TYPES else "none"
    return result


def _has_model_reference_assets() -> bool:
    """True if human-model reference images exist for lifestyle frames."""
    models_dir = config.data_root() / "models"
    if not models_dir.exists():
        return False
    return any(models_dir.iterdir())


def _hero_reference_instruction(velura_branding: bool) -> str:
    if velura_branding:
        return (
            "Reference-image rule: preserve the real package silhouette, label layout, "
            "and visible brand wordmark from hero product images. Do not genericize or "
            "omit the on-pack Velura branding in the starting image or product hero shots."
        )
    return (
        "Reference-image rule: preserve the real package silhouette, label layout, "
        "and overall packaging appearance from hero product images. Keep colors and "
        "packaging details grounded in the references without forcing an added wordmark."
    )


def _system_prompt_for_branding(base_prompt: str, velura_branding: bool) -> str:
    if velura_branding:
        return base_prompt

    replacements = (
        (
            "the brand 'velura' in brown writing using font style Cormorant Garamond, Georgia, Times New Roman, serif",
            "warm-neutral brown serif typography cues using Cormorant Garamond, Georgia, Times New Roman, serif",
        ),
        (
            'brand "velura" in brown serif (Cormorant Garamond, Georgia, Times New Roman)',
            "warm-neutral brown serif typography cues (Cormorant Garamond, Georgia, Times New Roman)",
        ),
        (
            "brand 'velura' in brown serif (Cormorant Garamond, Georgia, Times New Roman, serif)",
            "warm-neutral brown serif typography cues (Cormorant Garamond, Georgia, Times New Roman, serif)",
        ),
        (
            "brand velura in brown serif",
            "warm-neutral brown serif typography cues",
        ),
        (
            "BRANDING CONTEXT: provided in the user message. Use the brand name and palette as soft visual anchors for consistency but let the creative breathe.",
            "STYLE CONTEXT: provided in the user message. Preserve the premium warm-neutral palette and elegant serif typography cues without forcing an explicit wordmark.",
        ),
        (
            "preserve visible packaging branding/wordmark and label layout from hero reference images when provided",
            "preserve packaging silhouette, label layout, and overall appearance from hero reference images when provided",
        ),
        (
            "preserve packaging branding from hero reference images when provided",
            "preserve packaging appearance from hero reference images when provided",
        ),
        (
            "preserve the real package silhouette, label layout, and visible brand wordmark from the hero references in the starting frame and product hero scenes. Do not genericize or omit on-pack branding.",
            "preserve the real package silhouette, label layout, and overall packaging appearance from the hero references in the starting frame and product hero scenes. Do not invent or add extra brand text.",
        ),
        (
            "preserve the real package silhouette, label layout, and visible brand wordmark in the starting frame and product hero scenes.",
            "preserve the real package silhouette, label layout, and overall packaging appearance in the starting frame and product hero scenes.",
        ),
    )

    prompt = base_prompt
    for old, new in replacements:
        prompt = prompt.replace(old, new)
    return prompt

def _build_user_message(
    product: Product,
    theme: str,
    hook_type: str,
    product_images: list[ProductImage],
    research_summary: str | None = None,
    text_insights: str | None = None,
    creative_format: str | None = None,
    performance_summary: str | None = None,
    video_v2: bool = False,
    video_v3: bool = False,
    video_v4: bool = False,
    video_v5: bool = False,
    v5_vibe: str | None = None,
    v3_cta_enabled: bool = True,
    cta_type: str = "see_product",
    proof_type: str = "none",
    script_style: str = "conversational",
    velura_branding: bool = True,
) -> str:
    if not video_v5:
        lines = [
            f"Product: {product.name}",
            f"SKU: {product.sku}",
            f"Category: {product.category or 'general'}",
            f"Price: ${product.price:.2f}" if product.price else "Price: not set",
        ]
    else:
        lines = []
    if product.description:
        if video_v5:
            lines.append(
                "Product description (reference only; do not center the voiceover or scenes on this): "
                f"{product.description}"
            )
        else:
            lines.append(f"Description: {product.description}")

    if video_v3 or video_v4:
        lines.append("Locked creative constraints:")
        lines.append(f"  - Theme must be: {theme}")
        theme_def = THEME_MAP.get(theme)
        if theme_def:
            lines.append("")
            lines.append(f"THEME GUIDANCE: {theme_def.summary} {theme_def.prompt_guidance}")
        lines.append("")
        lines.append("BRANDING KIT:" if velura_branding else "STYLE KIT:")
        if velura_branding:
            lines.append("  Brand name: Velura")
        lines.append("  Brand colors: warm brown, cream, neutral earth tones")
        lines.append("  Brand font: Cormorant Garamond (serif)")
        if not velura_branding:
            lines.append(
                "  Wordmark guidance: omit explicit brand-name or wordmark callouts; keep the same premium warm-neutral palette and serif cues."
            )
    elif video_v5:
        lines.append("")
        lines.append("CONTENT TYPE: Horoscope / astrology reel for women 18-35 (not a product commercial).")
        lines.append("Locked creative constraints:")
        lines.append(f"  - Zodiac sign (use as `theme`): {theme}")
        lines.append(f"  - Presenter name (use as `hook_type`): {hook_type}")
        if v5_vibe and str(v5_vibe).strip():
            lines.append(f"  - Vibe: {v5_vibe.strip()}")
        lines.append("")
        lines.append(
            "STARTING-FIRST-FRAME (exact image spec for the generated first frame — anchor every scene_description "
            "to this visual; same character, necklace name, style, and environment):"
        )
        lines.append(build_v5_starting_image_prompt(theme, hook_type))
    else:
        lines.append("Locked creative constraints:")
        lines.append(f"  - Theme must be: {theme}")
        lines.append(f"  - Hook type must be: {hook_type}")
        if creative_format:
            lines.append(f"  - Creative format must be: {creative_format}")
        lines.append(f"  - CTA type must be: {cta_type}")
        lines.append(f"  - Proof type must be: {proof_type}")
        lines.append(f"  - Script style must be: {script_style}")
        lines.extend(whitelist_prompt_lines(theme_ids=[theme], hook_ids=[hook_type]))

    if product_images:
        img_descriptions = [
            f"  - {img.image_type}: {img.file_path}" for img in product_images
        ]
        lines.append("Available product images:")
        lines.extend(img_descriptions)
        if any((img.image_type or "").strip().lower() == "hero" for img in product_images):
            lines.append(_hero_reference_instruction(velura_branding))
    if not velura_branding:
        lines.append("")
        lines.append(
            "Branding mode: keep the premium warm-neutral palette, brown tones, and elegant serif typography cues, but do not add an explicit brand name or wordmark."
        )
    if research_summary and research_summary.strip():
        lines.append("")
        lines.append("RESEARCH INSIGHT (use to inform your creative choices):")
        lines.append(research_summary.strip())
    if text_insights and text_insights.strip():
        lines.append("")
        lines.append(
            "TEXT_LEVEL_INSIGHTS (reusable learnings for hooks, framing, proof, and CTA; "
            "use these for text direction only, not image or render guidance):"
        )
        lines.append(text_insights.strip())
    if performance_summary and performance_summary.strip():
        lines.append("")
        lines.append("PERFORMANCE_SUMMARY (bias your creative direction toward these):")
        lines.append(performance_summary.strip())
    if creative_format == "image_motion_15s":
        has_models = _has_model_reference_assets()
        lines.append("")
        lines.append(f"Model reference assets for lifestyle frames: {'available' if has_models else 'not configured'}")
    if creative_format == "ai_video_flex_15s" and not video_v3 and not video_v4 and not video_v5:
        lines.append("")
        lines.append("AUDIENCE: prefers quicker scene changes; avoid long 7.5s scenes.")
    if video_v2:
        lines.append("")
        lines.append("VIDEO V2: Use the timeline format with exactly 4 scenes and absolute timestamps [0:00–0:03], [0:03–0:07], [0:07–0:11], [0:11–0:15]. Scenes 2–4 must start with 'HARD CUT:'.")
    if video_v3:
        lines.append("")
        cta_instruction = (
            "Soft CTA only. Include a soft call to action in the final scene and set cta_text to the phrase used."
            if v3_cta_enabled
            else "Omit CTA entirely. Do not include any call to action in the final scene. Set cta_text to empty string."
        )
        lines.append(
            f"VIDEO V3: Use the flexible timeline format with 6-8 scenes, each 1.5-2.5 seconds, "
            f"totaling 13-15 seconds. Scenes 2+ must start with 'HARD CUT:'. "
            f"Third-person narrator voice. Include background_music metadata. {cta_instruction}"
        )
    if video_v4:
        lines.append("")
        cta_instruction = (
            "Soft CTA allowed. You may include a soft call to action in the final scene and set cta_text to the phrase used."
            if v3_cta_enabled
            else "No CTA. Do not include any call to action or product mention in the final scene. Set cta_text to empty string."
        )
        lines.append(
            f"VIDEO V4: Educational/entertaining content. Use the flexible timeline format with 6-8 scenes, each 1.5-2.5 seconds, "
            f"totaling 13-15 seconds. Scenes 2+ must start with 'HARD CUT:'. "
            f"Third-person narrator voice. Include background_music metadata. "
            f"The product appears in 3-5 scenes, not all. Use viewer_takeaway instead of problem_angle. "
            f"Include content_mode in strategy_metadata (educational, entertaining, or satisfying). {cta_instruction}"
        )
    if video_v5:
        lines.append("")
        lines.append(
            "VIDEO V5 (horoscope reel): Write one 30-38 word voiceover and exactly four scene_descriptions. "
            "Map scenes to the arc: Scene 1 = Hook (0-3s), Scenes 2-3 = Roast or validation (3-11s), "
            "Scene 4 = CTA (11-14s). Total video length 14-15 seconds. "
            "Do not lead with product features; keep best-friend, slightly dramatic astrology energy. "
            "No hashtags or emojis inside voiceover_script. "
            "In every scene_description: no lip sync, no moving lips or mouth to match speech — narration is voiceover only."
        )
    return "\n".join(lines)


def generate_content(
    product: Product,
    theme: str,
    hook_type: str,
    product_images: list[ProductImage],
    creative_format: str | None = None,
    video_v2: bool = False,
    video_v3: bool = False,
    video_v4: bool = False,
    video_v5: bool = False,
    v5_vibe: str | None = None,
    cta_type: str = "see_product",
    proof_type: str = "none",
    script_style: str = "conversational",
    velura_branding: bool = True,
) -> tuple[Content, dict]:
    """Call OpenAI to generate a structured content script for a 15-second video.

    Returns (Content persisted to DB, dict with platform_captions and hashtags).
    """
    api_key = config.get("openai.api_key")
    if not api_key:
        raise ValueError(
            "Missing `openai.api_key` in config.yaml. "
            "Copy config.example.yaml to config.yaml and add your OpenAI credentials."
        )

    if not product.description or not product.description.strip():
        logger.warning(
            "Product %s has no description; LLM has no grounding context. "
            "Add --description when adding products, or ensure Shopify sync pulls body_html.",
            product.sku,
        )

    model = config.get("openai.model", "gpt-5.4")
    openai_module = _load_openai_module()
    client = openai_module.OpenAI(api_key=api_key)

    # Phase 3: inject research snapshot for reuse across generation cycles
    fmt = creative_format or "ai_video_15s"
    if video_v2 or video_v3 or video_v4 or video_v5:
        fmt = "ai_video_flex_15s"
    snapshot = db.get_best_matching_snapshot(
        product_sku=product.sku,
        platform=None,
        creative_format=fmt,
    )
    research_summary = snapshot.summary if snapshot else None
    latest_text_insight = db.get_latest_text_insight(
        product_sku=product.sku,
        platform=None,
        creative_format=fmt,
    )
    text_insights = latest_text_insight.insight_text if latest_text_insight else None
    if video_v5 and not _V5_INCLUDE_TEXT_INSIGHTS:
        text_insights = None
    performance_summary = None
    performance_rationale = "default"
    rank_by = str(config.get("bandit.ranking_objective", "engagement_rate"))
    if rank_by not in ("engagement_rate", "views", "composite", "revenue", "sessions", "purchases"):
        rank_by = "engagement_rate"
    if fmt == "image_motion_15s":
        performance_summary, performance_rationale = get_image_motion_performance_summary(
            product.sku, rank_by=rank_by
        )
    elif fmt == "ai_video_flex_15s":
        performance_summary, performance_rationale = get_video_performance_summary(
            product.sku, rank_by=rank_by
        )
    if video_v5 and not _V5_INCLUDE_PERFORMANCE_SUMMARY:
        performance_summary = None
    v3_cta_enabled = _should_include_soft_cta() if (video_v3 or video_v4) else True
    user_msg = _build_user_message(
        product, theme, hook_type, product_images,
        research_summary=research_summary,
        text_insights=text_insights,
        creative_format=fmt,
        performance_summary=performance_summary,
        video_v2=video_v2,
        video_v3=video_v3,
        video_v4=video_v4,
        video_v5=video_v5,
        v5_vibe=v5_vibe,
        v3_cta_enabled=v3_cta_enabled,
        cta_type=cta_type,
        proof_type=proof_type,
        script_style=script_style,
        velura_branding=velura_branding,
    )
    content_id = uuid.uuid4().hex[:16]

    use_image_motion = fmt == "image_motion_15s"
    use_ai_video_flex = (
        fmt == "ai_video_flex_15s"
        and not video_v2
        and not video_v3
        and not video_v4
        and not video_v5
    )
    use_ai_video_v2 = video_v2
    use_ai_video_v3 = video_v3
    use_ai_video_v4 = video_v4
    use_ai_video_v5 = video_v5
    base_system_prompt = (
        _IMAGE_MOTION_SYSTEM_PROMPT if use_image_motion else
        (_AI_VIDEO_V5_SYSTEM_PROMPT if use_ai_video_v5 else
         (_AI_VIDEO_V4_SYSTEM_PROMPT if use_ai_video_v4 else
          (_AI_VIDEO_V3_SYSTEM_PROMPT if use_ai_video_v3 else
           (_AI_VIDEO_V2_SYSTEM_PROMPT if use_ai_video_v2 else
            (_AI_VIDEO_FLEX_SYSTEM_PROMPT if use_ai_video_flex else
             (_SIMPLIFIED_SYSTEM_PROMPT if fmt != "ai_video_15s" else _SYSTEM_PROMPT))))))
    )
    system_prompt = _system_prompt_for_branding(base_system_prompt, velura_branding)

    # V3/V4 need higher token budget for 6-8 scenes + background_music
    if use_image_motion:
        max_tokens = 4000
    elif use_ai_video_v3 or use_ai_video_v4:
        max_tokens = 2500
    elif use_ai_video_v5:
        max_tokens = 2000
    else:
        max_tokens = 1500

    generation_msg = user_msg
    response = None
    v3_v4_script_response: Any | None = None
    v3_v4_visuals_response: Any | None = None
    script_system_prompt = ""
    visuals_system_prompt = ""
    visuals_user_msg = ""
    prompt_output_raw = ""
    parsed: dict[str, Any] | None = None
    max_structured_attempts = 3
    for structured_attempt in range(1, max_structured_attempts + 1):
        try:
            if use_ai_video_v3 or use_ai_video_v4:
                script_system_prompt = _system_prompt_for_branding(
                    _AI_VIDEO_V4_SCRIPT_PHASE_SYSTEM_PROMPT
                    if use_ai_video_v4
                    else _AI_VIDEO_V3_SCRIPT_PHASE_SYSTEM_PROMPT,
                    velura_branding,
                )
                visuals_system_prompt = _system_prompt_for_branding(
                    _AI_VIDEO_V4_VISUALS_PHASE_SYSTEM_PROMPT
                    if use_ai_video_v4
                    else _AI_VIDEO_V3_VISUALS_PHASE_SYSTEM_PROMPT,
                    velura_branding,
                )
                script_resp = _call_with_retries(
                    client,
                    openai_module,
                    model,
                    generation_msg,
                    max_attempts=3,
                    system_prompt=script_system_prompt,
                    max_output_tokens=max_tokens,
                )
                script_text = _response_text(script_resp)
                script_data = _json_object_from_model_text(script_text)
                _validate_v3_script_phase_response(
                    script_data,
                    theme=theme,
                    v3_cta_enabled=v3_cta_enabled,
                    video_v4=use_ai_video_v4,
                )
                visuals_user_msg = _build_v3_v4_visuals_user_message(user_msg, script_data)
                visuals_resp = _call_with_retries(
                    client,
                    openai_module,
                    model,
                    visuals_user_msg,
                    max_attempts=3,
                    system_prompt=visuals_system_prompt,
                    max_output_tokens=max_tokens,
                )
                visuals_text = _response_text(visuals_resp)
                visuals_data = _json_object_from_model_text(visuals_text)
                _validate_v3_visuals_phase_response(script_data, visuals_data)
                merged = _merge_v3_v4_script_and_visual_phases(script_data, visuals_data)
                _validate_v3_response_shape(merged, theme=theme, v3_cta_enabled=v3_cta_enabled)
                _validate_and_normalize_v3_timeline(merged)
                if use_ai_video_v4:
                    _validate_v4_extras(merged)
                parsed = merged
                prompt_output_raw = (
                    f"[SCRIPT_PHASE]\n{script_text}\n\n[VISUALS_PHASE]\n{visuals_text}"
                )
                response = script_resp
                v3_v4_script_response = script_resp
                v3_v4_visuals_response = visuals_resp
            else:
                response = _call_with_retries(
                    client,
                    openai_module,
                    model,
                    generation_msg,
                    max_attempts=3,
                    system_prompt=system_prompt,
                    max_output_tokens=max_tokens,
                )
                prompt_output_raw = _response_text(response)
                parsed = _parse_response(
                    response,
                    theme=theme,
                    hook_type=hook_type,
                    creative_format=fmt,
                    video_v2=video_v2,
                    video_v3=video_v3,
                    video_v4=video_v4,
                    video_v5=video_v5,
                    v3_cta_enabled=v3_cta_enabled,
                    cta_type=cta_type, proof_type=proof_type, script_style=script_style,
                )
            break
        except ValueError as exc:
            if structured_attempt == max_structured_attempts:
                raise
            logger.warning(
                "OpenAI structured output attempt %d/%d failed validation: %s",
                structured_attempt,
                max_structured_attempts,
                exc,
            )
            generation_msg = (
                f"{user_msg}\n\n"
                "The previous response was invalid. Regenerate the entire flow from scratch and fix this exact issue:\n"
                f"{exc}\n\n"
                "Return only valid JSON that satisfies every constraint."
            )

    if response is None or parsed is None:
        raise ValueError("OpenAI generation failed before a valid response was returned.")

    asset_manifest_json = None
    voice_prompt_input = None
    voice_prompt_output = None
    voiceover_response = None
    classify_response = None
    if fmt == "image_motion_15s" and "image_plan" in parsed:
        plan = parsed["image_plan"]
        if not isinstance(plan, dict):
            raise ValueError("OpenAI response image_plan must be an object")
        frames = plan.get("frames", [])
        if (
            not isinstance(frames, list)
            or len(frames) < IMAGE_MOTION_MIN_FRAMES
            or len(frames) > IMAGE_MOTION_MAX_FRAMES
        ):
            raise ValueError(
                f"image_plan.frames must have {IMAGE_MOTION_MIN_FRAMES}-{IMAGE_MOTION_MAX_FRAMES} entries"
            )
        total = plan.get("total_duration_seconds", 0)
        if not isinstance(total, (int, float)) or total > IMAGE_MOTION_TOTAL_DURATION_MAX + 0.01:
            raise ValueError(
                f"image_plan.total_duration_seconds must be <= {IMAGE_MOTION_TOTAL_DURATION_MAX}"
            )
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
            "velura_branding": velura_branding,
            "image_plan": plan,
            "voiceover_plan": voiceover_plan,
        })
    elif fmt == "ai_video_flex_15s" and "video_plan" in parsed:
        plan = parsed["video_plan"]
        if not isinstance(plan, dict):
            raise ValueError("OpenAI response video_plan must be an object")
        scenes = plan.get("scenes", [])
        if video_v5:
            if not isinstance(scenes, list) or len(scenes) != 4:
                raise ValueError("V5 video_plan.scenes must have exactly 4 entries")
        else:
            max_scenes = 8 if (video_v3 or video_v4) else 7
            if not isinstance(scenes, list) or len(scenes) < 3 or len(scenes) > max_scenes:
                raise ValueError(
                    f"video_plan.scenes must have 3-8 entries"
                    if (video_v3 or video_v4)
                    else f"video_plan.scenes must have 3\u20137 entries"
                )
        total = plan.get("total_duration_seconds", 0)
        if not isinstance(total, (int, float)):
            raise ValueError("video_plan.total_duration_seconds must be a number")
        # V2 timeline uses fixed 15s format with 3,4,4,4 second scenes; skip clamp for that path.
        # V3/V4 timeline is validated by _validate_and_normalize_v3_timeline; skip clamp.
        # V5 uses fixed four-beat durations from validation; skip clamp.
        if not video_v2 and not video_v3 and not video_v4 and not video_v5:
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
        # When video_v2 or v3, video_plan comes from their respective validators.
        manifest_payload: dict[str, Any] = {
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
        if video_v5:
            manifest_payload["schema_version"] = 5
            manifest_payload["horoscope_metadata"] = {
                "zodiac_sign": theme,
                "presenter_name": hook_type,
                "vibe": (v5_vibe or "").strip() or None,
            }
            manifest_payload["platform_captions"] = parsed.get("platform_captions")
            manifest_payload["hashtags"] = parsed.get("hashtags")
            voiceover_plan_v5 = _build_v5_voiceover_plan(
                str(parsed.get("voiceover_script") or ""),
                content_id,
                float(total),
            )
            manifest_payload["voiceover_plan"] = voiceover_plan_v5
        elif video_v3 or video_v4:
            manifest_payload["schema_version"] = 4 if video_v4 else 3
            if "strategy_metadata" in parsed:
                manifest_payload["strategy_metadata"] = parsed["strategy_metadata"]
            if "timeline" in parsed:
                manifest_payload["timeline"] = parsed["timeline"]
                voiceover_plan = _build_v3_voiceover_plan(
                    parsed["timeline"],
                    content_id,
                    float(total),
                )
                if voiceover_plan is not None:
                    manifest_payload["voiceover_plan"] = voiceover_plan
            if "background_music" in parsed:
                manifest_payload["background_music"] = parsed["background_music"]
            if video_v4 and "viewer_takeaway" in parsed:
                manifest_payload["viewer_takeaway"] = parsed["viewer_takeaway"]
        asset_manifest_json = json.dumps(manifest_payload)

    strategy_metadata_json = None
    if fmt == "image_motion_15s" and "image_plan" in parsed:
        plan = parsed["image_plan"]
        strategy_metadata = plan.get("strategy_metadata") if isinstance(plan, dict) else None
        if isinstance(strategy_metadata, dict):
            strategy_metadata_json = json.dumps(strategy_metadata)
    elif (video_v2 or video_v3 or video_v4) and "strategy_metadata" in parsed:
        strategy_metadata_json = json.dumps(parsed["strategy_metadata"])

    cta_text_for_content = parsed.get("cta_text")
    # V3/V4: classify hook_type, script_style, proof_type from the generated script
    v3_classified: dict[str, str] = {}
    if video_v5:
        cta_type = str(parsed.get("cta_type") or "soft_cta").strip() or "soft_cta"
        if cta_type not in CTA_TYPES:
            cta_type = "soft_cta"
        cta_text_for_content = parsed.get("cta_text")
    if video_v3 or video_v4:
        cta_type = "soft_cta" if v3_cta_enabled else "see_product"
        cta_text_for_content = parsed.get("cta_text") if v3_cta_enabled else None
        timeline_scripts = _collect_timeline_scripts(parsed.get("timeline") or [])
        v3_classified = _classify_v3_script(
            client, openai_module,
            parsed.get("hook_text", ""),
            timeline_scripts,
        )
        hook_type = v3_classified["hook_type"]
        proof_type = v3_classified["proof_type"]
        script_style = v3_classified["script_style"]
        logger.info(
            "V3/V4 classification: hook_type=%s, script_style=%s, proof_type=%s",
            hook_type, script_style, proof_type,
        )

    # V4 uses viewer_takeaway; store it in problem_angle column for consistency
    problem_angle = parsed.get("viewer_takeaway") if video_v4 else parsed.get("problem_angle")
    starting_image_prompt = parsed.get("starting_image_prompt")
    if video_v5:
        starting_image_prompt = build_v5_starting_image_prompt(
            parsed["theme"],
            parsed["hook_type"],
        )

    content = Content(
        id=content_id,
        product_sku=product.sku,
        theme=parsed["theme"],
        hook_type=hook_type if (video_v3 or video_v4) else parsed["hook_type"],
        hook_text=parsed["hook_text"],
        creative_format=parsed.get("creative_format") or fmt or "ai_video_15s",
        cta_type=cta_type,
        cta_text=cta_text_for_content,
        problem_angle=problem_angle,
        proof_type=proof_type,
        script_style=script_style,
        research_snapshot_id=snapshot.id if snapshot else None,
        starting_image_prompt=starting_image_prompt,
        scene_1_desc=parsed.get("scene_1_desc") if fmt != "ai_video_flex_15s" else None,
        scene_2_desc=parsed.get("scene_2_desc") if fmt != "ai_video_flex_15s" else None,
        scene_1_script=parsed.get("scene_1_script") if fmt != "ai_video_flex_15s" else None,
        scene_2_script=parsed.get("scene_2_script") if fmt != "ai_video_flex_15s" else None,
        asset_manifest_json=asset_manifest_json,
        strategy_metadata_json=strategy_metadata_json,
    )
    db.insert_content(content)

    if voiceover_response is not None:
        input_tokens, output_tokens = _usage_token_counts(voiceover_response)
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

    if v3_v4_script_response is not None and v3_v4_visuals_response is not None:
        input_tokens, output_tokens = _sum_usage_token_counts(
            _usage_token_counts(v3_v4_script_response),
            _usage_token_counts(v3_v4_visuals_response),
        )
    else:
        input_tokens, output_tokens = _usage_token_counts(response)
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

    if v3_classified:
        classify_model = config.get("openai.classify_model", "gpt-4.1-mini")
        classify_input_per_m = float(config.get("openai.classify_input_per_million_usd", 0.40))
        classify_output_per_m = float(config.get("openai.classify_output_per_million_usd", 1.60))
        classify_cost = (200 / 1_000_000 * classify_input_per_m) + (60 / 1_000_000 * classify_output_per_m)
        db.insert_cost(Cost(
            content_id=content_id,
            step="v3_classify",
            api_provider="openai",
            tokens_or_units=260,
            cost_usd=classify_cost,
        ))

    if video_v3 or video_v4:
        dual_prompt_input = (
            f"[SCRIPT_PHASE SYSTEM]\n{script_system_prompt}\n\n[SCRIPT_PHASE USER]\n{user_msg}\n\n"
            f"[VISUALS_PHASE SYSTEM]\n{visuals_system_prompt}\n\n[VISUALS_PHASE USER]\n{visuals_user_msg}"
        )
    else:
        dual_prompt_input = f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{user_msg}"
    extras = {
        "platform_captions": platform_captions,
        "hashtags": hashtags,
        "prompt_input": dual_prompt_input,
        "prompt_output": prompt_output_raw,
    }
    if voice_prompt_input and voice_prompt_output:
        extras["voice_prompt_input"] = (
            f"[SYSTEM]\n{_IMAGE_MOTION_VOICEOVER_SYSTEM_PROMPT}\n\n[USER]\n{voice_prompt_input}"
        )
        extras["voice_prompt_output"] = voice_prompt_output
    if v3_classified:
        extras["v3_classification"] = v3_classified
    return content, extras


def _load_openai_module() -> Any:
    try:
        return importlib.import_module("openai")
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI SDK is not installed. Run `pip install -r requirements.txt`."
        ) from exc


def _uses_responses_api(model: str, client: Any) -> bool:
    normalized_model = (model or "").strip().lower()
    return normalized_model.startswith("gpt-5") and hasattr(client, "responses")


def _create_openai_response(
    client: Any,
    model: str,
    user_msg: str,
    prompt: str,
    max_output_tokens: int,
) -> Any:
    if _uses_responses_api(model, client):
        json_input = user_msg
        if "json" not in json_input.lower():
            json_input = f"{user_msg}\n\nReturn valid JSON only."
        return client.responses.create(
            model=model,
            instructions=prompt,
            input=json_input,
            max_output_tokens=max_output_tokens,
            text={"format": {"type": "json_object"}},
        )
    return client.chat.completions.create(
        model=model,
        max_completion_tokens=max_output_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_msg},
        ],
    )


def _call_with_retries(
    client: Any,
    openai_module: Any,
    model: str,
    user_msg: str,
    max_attempts: int = 3,
    system_prompt: str | None = None,
    max_output_tokens: int = 1500,
) -> Any:
    delay = 2.0
    prompt = system_prompt or _SYSTEM_PROMPT
    for attempt in range(1, max_attempts + 1):
        try:
            response = _create_openai_response(
                client,
                model,
                user_msg,
                prompt,
                max_output_tokens=max_output_tokens,
            )
            # Treat empty model output as transient so generation can recover.
            _response_text(response)
            return response
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
        except ValueError as exc:
            if attempt == max_attempts:
                raise ValueError("OpenAI returned an empty response after multiple retry attempts.") from exc
            logger.warning(
                "OpenAI API attempt %d/%d returned empty content; retrying.",
                attempt,
                max_attempts,
            )
            time.sleep(delay)
            delay *= 2


def _json_object_from_model_text(raw: str) -> dict[str, Any]:
    """Parse JSON from model output (strip optional markdown fences)."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OpenAI returned invalid JSON: {exc}\n\nRaw response:\n{raw}") from exc
    if not isinstance(data, dict):
        raise ValueError("OpenAI JSON must be an object at the top level.")
    return _sanitize_generated_payload(data)


def _parse_response(
    response: Any,
    theme: str | None = None,
    hook_type: str | None = None,
    creative_format: str | None = None,
    video_v2: bool = False,
    video_v3: bool = False,
    video_v4: bool = False,
    video_v5: bool = False,
    v3_cta_enabled: bool = True,
    cta_type: str = "see_product",
    proof_type: str = "none",
    script_style: str = "conversational",
) -> dict:
    raw = _response_text(response)
    data = _json_object_from_model_text(raw)

    use_image_motion = creative_format == "image_motion_15s"
    use_ai_video_flex = (
        creative_format == "ai_video_flex_15s"
        and not video_v2
        and not video_v3
        and not video_v4
        and not video_v5
    )
    use_ai_video_v2 = video_v2
    use_ai_video_v3 = video_v3
    use_ai_video_v4 = video_v4
    use_ai_video_v5 = video_v5

    if use_ai_video_v3 or use_ai_video_v4:
        required = [
            "theme", "hook_text", "creative_format",
            "starting_image_prompt", "timeline", "strategy_metadata",
            "background_music", "platform_captions", "hashtags",
        ]
        if use_ai_video_v4:
            required.append("viewer_takeaway")
        if v3_cta_enabled:
            required.append("cta_text")
    elif use_ai_video_v5:
        required = [
            "theme", "hook_type", "hook_text", "creative_format",
            "voiceover_script", "scene_descriptions",
            "platform_captions", "hashtags",
        ]
    else:
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

    if use_ai_video_v3 or use_ai_video_v4:
        _validate_v3_response_shape(data, theme=theme, v3_cta_enabled=v3_cta_enabled)
        _validate_and_normalize_v3_timeline(data)
        if use_ai_video_v4:
            _validate_v4_extras(data)
    elif use_ai_video_v5:
        _validate_and_normalize_v5_response(data, theme=theme, hook_type=hook_type)
    else:
        _validate_response_shape(data, theme=theme, hook_type=hook_type, cta_type=cta_type, proof_type=proof_type, script_style=script_style)
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
    if total_duration <= 0 or total_duration > IMAGE_MOTION_TOTAL_DURATION_MAX + 0.01:
        raise ValueError(
            f"image_plan.total_duration_seconds must be > 0 and <= {IMAGE_MOTION_TOTAL_DURATION_MAX}"
        )
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
    if (
        not isinstance(frames, list)
        or len(frames) < IMAGE_MOTION_MIN_FRAMES
        or len(frames) > IMAGE_MOTION_MAX_FRAMES
    ):
        raise ValueError(
            f"image_plan.frames must have {IMAGE_MOTION_MIN_FRAMES}-{IMAGE_MOTION_MAX_FRAMES} entries"
        )

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
        if (
            duration_seconds < IMAGE_MOTION_FRAME_DURATION_MIN
            or duration_seconds > IMAGE_MOTION_FRAME_DURATION_MAX
        ):
            raise ValueError(
                f"image_plan.frames[{idx}].duration_seconds must be between "
                f"{IMAGE_MOTION_FRAME_DURATION_MIN} and {IMAGE_MOTION_FRAME_DURATION_MAX}"
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
    if total_frame_duration < IMAGE_MOTION_TOTAL_DURATION_MIN - 0.05:
        raise ValueError(
            f"image_plan frame durations must sum to at least {IMAGE_MOTION_TOTAL_DURATION_MIN} seconds; "
            f"got {total_frame_duration:.2f}"
        )
    if total_frame_duration > IMAGE_MOTION_TOTAL_DURATION_MAX + 0.05:
        raise ValueError(
            f"image_plan frame durations must sum to at most {IMAGE_MOTION_TOTAL_DURATION_MAX} seconds; "
            f"got {total_frame_duration:.2f}"
        )
    if abs(total_frame_duration - total_duration) > 0.05:
        plan["total_duration_seconds"] = round(total_frame_duration, 1)


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


def _validate_v3_response_shape(
    data: dict[str, Any], theme: str | None = None, v3_cta_enabled: bool = True
) -> None:
    """Validate V3-specific response shape (theme-only lock, no hook/cta/proof/style)."""
    returned_theme = str(data.get("theme", "")).strip()
    if not returned_theme:
        raise ValueError("V3 response field `theme` must be a non-empty string.")
    if returned_theme not in THEMES:
        raise ValueError(
            f"V3 response theme '{returned_theme}' not in whitelist. Allowed: {', '.join(THEMES)}"
        )
    if theme and returned_theme != theme:
        raise ValueError(
            f"V3 response theme '{returned_theme}' did not match locked theme '{theme}'."
        )
    hook_text = str(data.get("hook_text", "")).strip()
    if not hook_text:
        raise ValueError("V3 response field `hook_text` must be a non-empty string.")
    if v3_cta_enabled:
        cta_text = str(data.get("cta_text", "")).strip()
        if not cta_text:
            raise ValueError("V3 response field `cta_text` must be a non-empty string.")
    fmt = str(data.get("creative_format", "")).strip()
    if fmt != "ai_video_flex_15s":
        raise ValueError(f"V3 response creative_format must be 'ai_video_flex_15s', got '{fmt}'.")
    music = data.get("background_music")
    if not isinstance(music, dict):
        raise ValueError("V3 response must include `background_music` as an object.")
    if not str(music.get("description", "")).strip():
        raise ValueError("V3 background_music.description is required.")
    energy = str(music.get("energy_level", "")).strip()
    if energy not in ("low", "medium", "high"):
        raise ValueError(f"V3 background_music.energy_level must be low/medium/high, got '{energy}'.")
    strategy = data.get("strategy_metadata")
    if not isinstance(strategy, dict):
        raise ValueError("V3 response must include `strategy_metadata` as an object.")


_V4_CONTENT_MODES = ("educational", "entertaining", "satisfying")


def _validate_v4_extras(data: dict[str, Any]) -> None:
    """Validate V4-specific fields beyond the shared V3 shape."""
    viewer_takeaway = str(data.get("viewer_takeaway", "")).strip()
    if not viewer_takeaway:
        raise ValueError("V4 response field `viewer_takeaway` must be a non-empty string.")

    strategy = data.get("strategy_metadata")
    if isinstance(strategy, dict):
        content_mode = str(strategy.get("content_mode", "")).strip()
        if content_mode not in _V4_CONTENT_MODES:
            raise ValueError(
                f"V4 strategy_metadata.content_mode must be one of {_V4_CONTENT_MODES}, got '{content_mode}'."
            )


def _validate_v4_script_phase_extras(data: dict[str, Any]) -> None:
    """Validate V4-only fields present in script phase (before visuals merge)."""
    viewer_takeaway = str(data.get("viewer_takeaway", "")).strip()
    if not viewer_takeaway:
        raise ValueError("V4 script phase field `viewer_takeaway` must be a non-empty string.")
    strategy = data.get("strategy_metadata")
    if not isinstance(strategy, dict):
        raise ValueError("V4 script phase must include strategy_metadata as an object.")
    content_mode = str(strategy.get("content_mode", "")).strip()
    if content_mode not in _V4_CONTENT_MODES:
        raise ValueError(
            f"V4 script phase strategy_metadata.content_mode must be one of {_V4_CONTENT_MODES}, "
            f"got '{content_mode}'."
        )


def _validate_v3_script_phase_timeline(data: dict[str, Any]) -> None:
    """Validate V3/V4 script-only timeline: timing, scripts, word budgets (no scene_description)."""
    timeline = data.get("timeline", [])
    if not isinstance(timeline, list) or len(timeline) < 6 or len(timeline) > 8:
        raise ValueError(
            f"Script phase timeline must have 6-8 scenes, got {len(timeline) if isinstance(timeline, list) else 'non-list'}"
        )
    prev_end = 0.0
    total_duration = 0.0
    for i, scene in enumerate(timeline):
        if not isinstance(scene, dict):
            raise ValueError(f"Script phase timeline[{i}] must be an object")
        start = scene.get("start_seconds")
        end = scene.get("end_seconds")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise ValueError(f"Script phase timeline[{i}] start_seconds and end_seconds must be numbers")
        start_f, end_f = float(start), float(end)
        if abs(start_f - prev_end) > 0.05:
            raise ValueError(
                f"Script phase timeline[{i}] start_seconds={start_f} must equal previous end_seconds={prev_end}"
            )
        duration = end_f - start_f
        if duration < 1.4 or duration > 2.6:
            raise ValueError(
                f"Script phase timeline[{i}] duration {duration:.1f}s outside allowed range 1.5-2.5s"
            )
        script = scene.get("script")
        if not isinstance(script, str) or not script.strip():
            raise ValueError(f"Script phase timeline[{i}] must have a non-empty script")
        scene["script"] = script.strip()
        scene_word_count = len(scene["script"].split())
        scene_word_max = int(duration * TTS_WORDS_PER_SECOND_MAX)
        if scene_word_count > scene_word_max:
            raise ValueError(
                f"Script phase timeline[{i}] script has {scene_word_count} words but the {duration:.1f}s scene "
                f"allows at most {scene_word_max} at TTS pace."
            )
        tone = (scene.get("tone") or "").strip()
        if not tone:
            raise ValueError(f"Script phase timeline[{i}] must include a non-empty tone")
        scene["tone"] = tone
        if "scene_description" in scene:
            scene.pop("scene_description", None)
        prev_end = end_f
        total_duration += duration

    if total_duration < 12.9 or total_duration > 15.1:
        raise ValueError(
            f"Script phase total duration {total_duration:.1f}s outside allowed range 13-15s"
        )
    total_words = sum(len(str(s.get("script", "")).split()) for s in timeline if isinstance(s, dict))
    total_word_max = int(total_duration * TTS_WORDS_PER_SECOND_MAX)
    if total_words > total_word_max:
        raise ValueError(
            f"Script phase total word count is {total_words} but {total_duration:.1f}s allows at most {total_word_max}."
        )


def _validate_v3_script_phase_response(
    data: dict[str, Any],
    theme: str | None,
    v3_cta_enabled: bool,
    video_v4: bool,
) -> None:
    """Validate V3/V4 phase-1 JSON before visuals generation."""
    _validate_v3_response_shape(data, theme=theme, v3_cta_enabled=v3_cta_enabled)
    if video_v4:
        _validate_v4_script_phase_extras(data)
    _validate_v3_script_phase_timeline(data)


def _validate_v3_visuals_phase_response(
    script_data: dict[str, Any],
    visual_data: dict[str, Any],
) -> None:
    """Validate phase-2 visuals JSON and alignment with locked script plan."""
    sip = str(visual_data.get("starting_image_prompt", "") or "").strip()
    if not sip:
        raise ValueError("Visuals phase must include non-empty starting_image_prompt")
    visual_data["starting_image_prompt"] = sip

    v_timeline = visual_data.get("timeline")
    s_timeline = script_data.get("timeline")
    if not isinstance(v_timeline, list) or not isinstance(s_timeline, list):
        raise ValueError("Visuals phase timeline must be a list aligned with script phase")
    if len(v_timeline) != len(s_timeline):
        raise ValueError(
            f"Visuals phase timeline length {len(v_timeline)} must match script phase {len(s_timeline)}"
        )
    for i, row in enumerate(v_timeline):
        if not isinstance(row, dict):
            raise ValueError(f"Visuals phase timeline[{i}] must be an object")
        desc = str(row.get("scene_description", "") or "").strip()
        if not desc:
            raise ValueError(f"Visuals phase timeline[{i}].scene_description is required")
        if i >= 1 and not desc.upper().startswith("HARD CUT"):
            raise ValueError(
                f"Visuals phase timeline[{i}].scene_description must start with 'HARD CUT:' for scenes 2+"
            )


def _merge_v3_v4_script_and_visual_phases(
    script_data: dict[str, Any],
    visual_data: dict[str, Any],
) -> dict[str, Any]:
    """Merge locked script phase with visuals phase into a full V3/V4 creative dict."""
    merged = json.loads(json.dumps(script_data))
    merged["starting_image_prompt"] = str(visual_data["starting_image_prompt"]).strip()
    vt = visual_data["timeline"]
    st = merged["timeline"]
    new_timeline: list[dict[str, Any]] = []
    for i, srow in enumerate(st):
        if not isinstance(srow, dict):
            raise ValueError(f"script timeline[{i}] must be an object")
        if i >= len(vt) or not isinstance(vt[i], dict):
            raise ValueError("visuals timeline index out of range or invalid")
        desc = str(vt[i].get("scene_description", "") or "").strip()
        row = dict(srow)
        row["scene_description"] = desc
        new_timeline.append(row)
    merged["timeline"] = new_timeline
    return merged


def _build_v3_v4_visuals_user_message(base_user_msg: str, script_plan: dict[str, Any]) -> str:
    """User message for phase 2: original brief plus frozen script plan JSON."""
    try:
        locked = json.dumps(script_plan, indent=2)
    except (TypeError, ValueError):
        locked = str(script_plan)
    return (
        f"{base_user_msg}\n\n"
        "LOCKED SCRIPT PLAN (authoritative — do not modify any script, timestamp, or tone):\n"
        f"{locked}"
    )


def _sum_usage_token_counts(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    return (a[0] + b[0], a[1] + b[1])


def _split_voiceover_into_scenes(
    voiceover_script: str,
    scene_durations: list[float],
) -> list[str]:
    """Split one voiceover into per-scene scripts proportional to scene durations."""
    words = voiceover_script.split()
    if not words:
        return [""] * len(scene_durations)
    total_d = sum(scene_durations)
    if total_d <= 0:
        return [voiceover_script] + [""] * (len(scene_durations) - 1)
    n = len(words)
    out: list[str] = []
    idx = 0
    num_scenes = len(scene_durations)
    for i, d in enumerate(scene_durations):
        if i == num_scenes - 1:
            out.append(" ".join(words[idx:]))
            break
        share = d / total_d
        take = max(1, int(round(n * share)))
        remaining_scenes = num_scenes - i - 1
        if idx + take > n - remaining_scenes:
            take = max(1, n - idx - remaining_scenes)
        end = min(idx + take, n)
        out.append(" ".join(words[idx:end]))
        idx = end
    while len(out) < num_scenes:
        out.append("")
    return out[:num_scenes]


def _build_v5_voiceover_plan(
    voiceover_script: str,
    content_id: str,
    total_duration_seconds: float,
) -> dict[str, Any]:
    """Durable stitched voiceover plan for V5 horoscope reels."""
    vo = _sanitize_generated_text(voiceover_script.strip())
    word_count = len(vo.split())
    speech_rate = (
        word_count / total_duration_seconds
        if total_duration_seconds > 0
        else VOICEOVER_TARGET_WORDS_PER_SECOND
    )
    delivery_profile = {
        "tone": "best friend, playful, slightly dramatic, kind",
        "diction": "clean, crisp, conversational",
        "pace": "brisk but clear",
        "pause_style": "light conversational pauses only",
        "emphasis": "hook and CTA words",
        "target_duration_seconds": round(total_duration_seconds, 1),
    }
    provider_options = {
        "elevenlabs": {
            "language_code": "en",
            "apply_text_normalization": "auto",
            "voice_settings": {
                "speed": 1.03,
                "use_speaker_boost": True,
            },
        }
    }
    return {
        "script_template_id": "horoscope_v5_single",
        "voiceover_script": vo,
        "voice": _pick_voice(content_id),
        "voice_instructions": V5_TTS_VOICE_INSTRUCTIONS,
        "language": "english",
        "speech_rate_words_per_second": round(speech_rate, 1),
        "estimated_word_count": word_count,
        "delivery_profile": delivery_profile,
        "provider_options": provider_options,
    }


def _validate_and_normalize_v5_response(
    data: dict[str, Any],
    theme: str | None,
    hook_type: str | None,
) -> None:
    """Validate V5 horoscope JSON, word count, captions, and build video_plan."""
    vo = str(data.get("voiceover_script") or "").strip()
    if not vo:
        raise ValueError("V5 response field `voiceover_script` must be non-empty.")
    wc = len(vo.split())
    if wc < 30 or wc > 38:
        raise ValueError(
            f"V5 voiceover_script must be 30-38 words inclusive, got {wc}."
        )

    scenes_raw = data.get("scene_descriptions")
    if not isinstance(scenes_raw, list) or len(scenes_raw) != 4:
        raise ValueError("V5 scene_descriptions must be a list of exactly 4 strings.")
    scene_descs: list[str] = []
    for i, s in enumerate(scenes_raw):
        desc = str(s or "").strip()
        if not desc:
            raise ValueError(f"V5 scene_descriptions[{i}] must be non-empty.")
        scene_descs.append(desc)

    returned_theme = str(data.get("theme", "")).strip()
    returned_hook = str(data.get("hook_type", "")).strip()
    if returned_theme not in ZODIAC_SIGNS:
        raise ValueError(
            f"V5 theme must be a zodiac sign id, got {returned_theme!r}."
        )
    if returned_hook not in V5_NAMES:
        raise ValueError(
            f"V5 hook_type must be a presenter name id, got {returned_hook!r}."
        )
    if theme and returned_theme != theme:
        raise ValueError(
            f"V5 theme {returned_theme!r} did not match locked theme {theme!r}."
        )
    if hook_type and returned_hook != hook_type:
        raise ValueError(
            f"V5 hook_type {returned_hook!r} did not match locked hook_type {hook_type!r}."
        )

    fmt = str(data.get("creative_format", "")).strip()
    if fmt != "ai_video_flex_15s":
        raise ValueError(f"V5 creative_format must be 'ai_video_flex_15s', got {fmt!r}.")

    if not isinstance(data.get("platform_captions"), dict):
        raise ValueError("V5 response field `platform_captions` must be an object.")
    caption_keys = {"youtube", "instagram", "tiktok", "x"}
    missing_caption_keys = caption_keys.difference(data["platform_captions"])
    if missing_caption_keys:
        raise ValueError(
            "V5 response `platform_captions` missing keys: "
            f"{sorted(missing_caption_keys)}"
        )
    if not isinstance(data.get("hashtags"), list):
        raise ValueError("V5 response field `hashtags` must be a list.")

    # Four-beat timeline: 15.0s total (hook / roast / roast / cta).
    scene_durations = [3.5, 4.0, 4.0, 3.5]
    total_duration = float(sum(scene_durations))
    segment_scripts = _split_voiceover_into_scenes(vo, scene_durations)

    scenes: list[dict[str, Any]] = []
    for i in range(4):
        scenes.append({
            "duration_seconds": scene_durations[i],
            "scene_description": scene_descs[i],
            "script": segment_scripts[i],
        })

    data["video_plan"] = {
        "strategy_summary": "Horoscope V5 four-beat arc",
        "total_duration_seconds": total_duration,
        "style_family": "anamorphic",
        "style_rationale": "V5 horoscope reel: hook, roast or validation, CTA",
        "script_total_words": wc,
        "scenes": scenes,
    }


def _validate_and_normalize_v3_timeline(data: dict[str, Any]) -> None:
    """Validate V3 timeline (6-8 scenes, 1.5-2.5s each, 13-15s total) and normalize to video_plan."""
    timeline = data.get("timeline", [])
    if not isinstance(timeline, list) or len(timeline) < 6 or len(timeline) > 8:
        raise ValueError(
            f"Video V3 timeline must have 6-8 scenes, got {len(timeline) if isinstance(timeline, list) else 'non-list'}"
        )

    strategy = data.get("strategy_metadata") or {}
    if not isinstance(strategy, dict):
        strategy = {}
    style_family = strategy.get("style_family") or "anamorphic"
    style_rationale = strategy.get("style_angle") or "Video V3 theme-driven"

    scenes_normalized = []
    prev_end = 0.0
    total_duration = 0.0
    for i, scene in enumerate(timeline):
        if not isinstance(scene, dict):
            raise ValueError(f"V3 timeline[{i}] must be an object")
        start = scene.get("start_seconds")
        end = scene.get("end_seconds")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise ValueError(f"V3 timeline[{i}] start_seconds and end_seconds must be numbers")
        start_f, end_f = float(start), float(end)
        if abs(start_f - prev_end) > 0.05:
            raise ValueError(
                f"V3 timeline[{i}] start_seconds={start_f} must equal previous end_seconds={prev_end}"
            )
        duration = end_f - start_f
        if duration < 1.4 or duration > 2.6:
            raise ValueError(
                f"V3 timeline[{i}] duration {duration:.1f}s outside allowed range 1.5-2.5s"
            )
        desc = (scene.get("scene_description") or "").strip()
        if not desc:
            raise ValueError(f"V3 timeline[{i}].scene_description is required")
        if i >= 1 and not desc.upper().startswith("HARD CUT"):
            raise ValueError(
                f"V3 timeline[{i}].scene_description must start with 'HARD CUT:' for scenes 2+"
            )
        script = scene.get("script")
        if not isinstance(script, str) or not script.strip():
            raise ValueError(f"V3 timeline[{i}] must have a non-empty script (no visual-only beats)")
        scene["script"] = script.strip()
        scene_word_count = len(scene["script"].split())
        scene_word_max = int(duration * TTS_WORDS_PER_SECOND_MAX)
        if scene_word_count > scene_word_max:
            raise ValueError(
                f"V3 timeline[{i}] script has {scene_word_count} words but the {duration:.1f}s scene "
                f"allows at most {scene_word_max} at TTS pace. Shorten the line to fit."
            )
        tone = (scene.get("tone") or "").strip()

        scenes_normalized.append({
            "duration_seconds": round(duration, 1),
            "scene_description": desc,
            "script": scene["script"],
            "tone": tone if tone else None,
        })
        prev_end = end_f
        total_duration += duration

    if total_duration < 12.9 or total_duration > 15.1:
        raise ValueError(
            f"V3 timeline total duration {total_duration:.1f}s outside allowed range 13-15s"
        )

    total_words = sum(len(s["script"].split()) for s in scenes_normalized)
    total_word_max = int(total_duration * TTS_WORDS_PER_SECOND_MAX)
    if total_words > total_word_max:
        raise ValueError(
            f"V3 timeline total script word count is {total_words} but {total_duration:.1f}s "
            f"of video allows at most {total_word_max} words at TTS pace. Shorten the scripts."
        )

    video_plan = {
        "strategy_summary": strategy.get("style_angle") or "Video V3 theme-driven",
        "total_duration_seconds": round(total_duration, 1),
        "style_family": style_family,
        "style_rationale": style_rationale,
        "scenes": scenes_normalized,
    }
    data["video_plan"] = video_plan


def _validate_response_shape(
    data: dict[str, Any],
    theme: str | None = None,
    hook_type: str | None = None,
    cta_type: str = "see_product",
    proof_type: str = "none",
    script_style: str = "conversational",
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

    returned_cta = (data.get("cta_type") or "").strip()
    if returned_cta and returned_cta != cta_type:
        raise ValueError(
            f"OpenAI response cta_type '{returned_cta}' did not match locked cta_type '{cta_type}'."
        )
    returned_proof = (data.get("proof_type") or "").strip() if data.get("proof_type") else ""
    if returned_proof and returned_proof != proof_type:
        raise ValueError(
            f"OpenAI response proof_type '{returned_proof}' did not match locked proof_type '{proof_type}'."
        )
    returned_script = (data.get("script_style") or "").strip() if data.get("script_style") else ""
    if returned_script and returned_script != script_style:
        raise ValueError(
            f"OpenAI response script_style '{returned_script}' did not match locked script_style '{script_style}'."
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
    creative_format_val = (data.get("creative_format") or "").strip()
    if creative_format_val and creative_format_val not in CREATIVE_FORMATS:
        raise ValueError(
            f"OpenAI response creative_format '{creative_format_val}' not in whitelist. "
            f"Allowed: {', '.join(CREATIVE_FORMATS)}"
        )


def _response_text(response: Any) -> str:
    raw = getattr(response, "output_text", None)
    if raw is None and hasattr(response, "choices"):
        choice = response.choices[0]
        message = choice.message
        raw = message.content or ""
    if raw is None:
        raw = ""
    if not raw.strip():
        raise ValueError("OpenAI returned an empty response.")
    return raw.strip()


def _usage_token_counts(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    input_tokens = getattr(usage, "prompt_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage, "input_tokens", 0)
    output_tokens = getattr(usage, "completion_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(usage, "output_tokens", 0)
    return int(input_tokens or 0), int(output_tokens or 0)


# ---------------------------------------------------------------------------
# Phase 7: Paid variant caption generation
# ---------------------------------------------------------------------------

_PAID_VARIANT_SYSTEM_PROMPT = """\
You are an expert creative director for premium product ad copy.

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

    model = config.get("openai.model", "gpt-5.4")
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
    input_tokens, output_tokens = _usage_token_counts(response)
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
