"""OpenAI TTS voiceover generation for image-motion and other formats."""

from __future__ import annotations

import importlib
import logging
import time
from pathlib import Path
from typing import Any

from src import config, db
from src.models import Content, Cost

logger = logging.getLogger(__name__)

TTS_MODEL = "gpt-4o-mini-tts"
TTS_RESPONSE_FORMAT = "wav"
TTS_LANGUAGE = "english"
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0
# Approximate cost per 1K chars for TTS (gpt-4o-mini-tts pricing)
TTS_COST_PER_1K_CHARS_USD = 0.015


def _load_openai_module() -> Any:
    try:
        return importlib.import_module("openai")
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI SDK is not installed. Run `pip install -r requirements.txt`."
        ) from exc


def _get_tts_config() -> dict[str, Any]:
    """Read TTS config with defaults."""
    api_key = config.get("openai.api_key")
    if not api_key:
        raise ValueError(
            "Missing `openai.api_key` in config.yaml. "
            "Copy config.example.yaml to config.yaml and add your OpenAI credentials."
        )
    model = config.get("openai.tts_model", TTS_MODEL)
    response_format = config.get("openai.tts_response_format", TTS_RESPONSE_FORMAT)
    language = config.get("openai.tts_language", TTS_LANGUAGE)
    return {
        "api_key": api_key,
        "model": model,
        "response_format": response_format,
        "language": language,
    }


def _build_tts_instructions(voice_instructions: str, language: str | None) -> str:
    """Build SDK-compatible instructions, including language guidance."""
    parts: list[str] = []
    if language:
        parts.append(f"Speak in {language}.")
    if voice_instructions.strip():
        parts.append(voice_instructions.strip())
    return " ".join(parts)


def generate_voiceover(
    script: str,
    voice: str,
    voice_instructions: str,
    output_path: Path,
    content_id: str,
    *,
    language: str | None = None,
) -> Path:
    """Generate TTS audio from script and save to output_path.

    Args:
        script: Text to speak.
        voice: OpenAI voice id (e.g. marin, cedar).
        voice_instructions: Instructions for tone/pacing.
        output_path: Path to save WAV file.
        content_id: Content id for cost tracking.
        language: Override language (default from config).

    Returns:
        Path to the saved WAV file.
    """
    cfg = _get_tts_config()
    openai_module = _load_openai_module()
    client = openai_module.OpenAI(api_key=cfg["api_key"])

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lang = language or cfg.get("language", TTS_LANGUAGE)
    response_format = cfg.get("response_format", TTS_RESPONSE_FORMAT)
    instructions = _build_tts_instructions(voice_instructions, lang)

    last_exc: Exception | None = None
    delay = INITIAL_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.audio.speech.create(
                model=cfg["model"],
                voice=voice,
                input=script,
                instructions=instructions,
                response_format=response_format,
            )
            break
        except (
            openai_module.APIConnectionError,
            openai_module.RateLimitError,
            openai_module.APIStatusError,
        ) as exc:
            last_exc = exc
            if attempt == MAX_RETRIES:
                raise
            logger.warning("OpenAI TTS attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            time.sleep(delay)
            delay *= 2
    else:
        raise RuntimeError(
            f"OpenAI TTS failed after {MAX_RETRIES} attempts"
        ) from last_exc

    # Response is a binary stream
    audio_bytes = response.content if hasattr(response, "content") else response.read()
    output_path.write_bytes(audio_bytes)
    logger.info("Saved TTS audio to %s", output_path)

    # Record cost (approximate)
    char_count = len(script)
    cost_usd = (char_count / 1000) * TTS_COST_PER_1K_CHARS_USD
    db.insert_cost(Cost(
        content_id=content_id,
        step="tts_gen",
        api_provider="openai",
        tokens_or_units=char_count,
        cost_usd=cost_usd,
    ))

    return output_path
