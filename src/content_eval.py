from __future__ import annotations

import json
import logging
import time
from typing import Any

from src import config, db
from src.models import Content, ContentEval, Cost, EVAL_CRITERIA

log = logging.getLogger(__name__)

EVAL_SYSTEM_PROMPT = """\
You are a creative quality evaluator for short-form social video content.

You will receive a creative payload (hook, visuals, voiceover, captions, strategy metadata) and must answer seven yes/no questions.

Criteria:
1. HOOK: Does the hook_text create a specific curiosity gap or name a specific frustration -- not just introduce the product?
2. FIRST_FRAME: Would the opening visual description produce a scroll-stopping thumbnail?
3. NARRATIVE_ARC: Does the full voiceover build a single coherent arc with a turning point -- not a list of benefits?
4. SPECIFICITY: Is the problem_angle specific enough that a viewer would think "that's exactly my situation"?
5. CAPTION: Does the Instagram caption read like something a real person would type?
6. SCENE_PROGRESSION: Does every scene/frame introduce a genuinely new idea that advances the story?
7. STANDALONE_VALUE: Would a viewer with zero purchase intent still watch this to the end because it taught them something, surprised them, or was satisfying to watch?

Respond with ONLY a JSON object mapping each criterion name (lowercase) to true or false:
{"hook": true, "first_frame": false, "narrative_arc": true, "specificity": false, "caption": true, "scene_progression": true, "standalone_value": false}

No commentary, no markdown fences. Just the JSON object.
"""


def _build_eval_prompt(content: Content) -> str:
    parts = [f"Content ID: {content.id}"]
    parts.append(f"Creative format: {content.creative_format}")
    parts.append(f"Hook text: {content.hook_text or '(none)'}")
    parts.append(f"Problem angle: {content.problem_angle or '(none)'}")

    manifest: dict[str, Any] = {}
    if content.asset_manifest_json:
        try:
            manifest = json.loads(content.asset_manifest_json)
        except json.JSONDecodeError:
            pass

    if content.creative_format == "ai_video_flex_15s":
        timeline = manifest.get("timeline", [])
        parts.append("Format: V3 (ai_video_flex_15s)")
        for i, scene in enumerate(timeline):
            if isinstance(scene, dict):
                parts.append(f"  Scene {i+1}:")
                parts.append(f"    Visual: {scene.get('scene_description', '(none)')}")
                parts.append(f"    Script: {scene.get('script', '(none)')}")
                parts.append(f"    Tone: {scene.get('tone', '(none)')}")
        strategy = manifest.get("strategy_metadata", {})
        if isinstance(strategy, dict):
            parts.append(f"Expression arc: {strategy.get('expression_arc', '(none)')}")
        parts.append(f"Starting image prompt: {content.starting_image_prompt or '(none)'}")
    elif content.creative_format == "image_motion_15s":
        image_plan = manifest.get("image_plan", {})
        voiceover_plan = manifest.get("voiceover_plan", {})
        parts.append("Format: Image motion (image_motion_15s)")
        frames = image_plan.get("frames", []) if isinstance(image_plan, dict) else []
        for i, frame in enumerate(frames):
            if isinstance(frame, dict):
                parts.append(f"  Frame {i+1}:")
                parts.append(f"    Intent: {frame.get('frame_intent', '(none)')}")
                parts.append(f"    Image prompt: {frame.get('image_prompt', '(none)')}")
        if isinstance(voiceover_plan, dict):
            parts.append(f"Voiceover script: {voiceover_plan.get('voiceover_script', '(none)')}")
    else:
        parts.append(f"Format: {content.creative_format}")
        parts.append(f"Asset manifest: {content.asset_manifest_json or '(none)'}")

    captions = manifest.get("platform_captions", {})
    if isinstance(captions, dict):
        for plat, cap in captions.items():
            parts.append(f"Caption ({plat}): {cap}")

    return "\n".join(parts)


def _parse_eval_response(raw: str) -> dict[str, bool]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    result = json.loads(cleaned)
    parsed: dict[str, bool] = {}
    for criterion in EVAL_CRITERIA:
        parsed[criterion] = bool(result.get(criterion, False))
    return parsed


def score_content(content: Content) -> int:
    prompt = _build_eval_prompt(content)
    model = config.get("eval.model", "gpt-4.1-mini")
    api_key = config.get("openai.api_key", "")

    import openai
    client = openai.OpenAI(api_key=api_key)

    start = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": EVAL_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
    )
    elapsed = time.time() - start

    raw = response.choices[0].message.content or ""
    parsed = _parse_eval_response(raw)

    evals = [
        ContentEval(content_id=content.id, criterion=c, passed=parsed.get(c, False))
        for c in EVAL_CRITERIA
    ]
    db.insert_content_evals(content.id, evals)

    score = sum(1 for v in parsed.values() if v)
    db.update_content_eval_score(content.id, score)

    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    cost = Cost(
        content_id=content.id,
        step="eval",
        api_provider=model,
        tokens_or_units=(input_tokens + output_tokens),
    )
    db.insert_cost(cost)

    log.info("Scored content %s: %d/%d", content.id, score, len(EVAL_CRITERIA))
    return score


def score_batch(lookback_days: int = 7) -> int:
    items = db.list_unscored_content(lookback_days=lookback_days)
    log.info("Found %d unscored content items", len(items))
    scored = 0
    for content in items:
        try:
            score_content(content)
            scored += 1
        except Exception:
            log.exception("Failed to score content %s", content.id)
    return scored
