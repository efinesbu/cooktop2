"""TTS voiceover generation for image-motion and ai_video_flex (OpenAI or ElevenLabs)."""

from __future__ import annotations

import importlib
import json
import logging
import subprocess
import time
import wave
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src import config, db
from src.models import Cost

logger = logging.getLogger(__name__)

TTS_PROVIDER_DEFAULT = "elevenlabs"

# OpenAI defaults (when tts.provider is openai)
TTS_MODEL_OPENAI = "gpt-4o-mini-tts"
TTS_RESPONSE_FORMAT = "wav"
TTS_LANGUAGE = "english"

# ElevenLabs defaults
ELEVENLABS_MODEL_DEFAULT = "eleven_multilingual_v2"
# pcm_44100 is Pro+ only; mp3_44100_128 works on typical paid/free API tiers (see ElevenLabs error output_format_not_allowed).
ELEVENLABS_OUTPUT_PCM = "pcm_44100"
ELEVENLABS_OUTPUT_DEFAULT = "mp3_44100_128"

MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0
# Approximate cost per 1K chars (provider-specific; used for ledger only)
TTS_COST_OPENAI_PER_1K_CHARS_USD = 0.015
TTS_COST_ELEVENLABS_PER_1K_CHARS_USD = 0.18

_ELEVENLABS_VOICE_SETTING_FLOAT_KEYS = (
    "stability",
    "similarity_boost",
    "style",
    "speed",
)
_ELEVENLABS_VOICE_SETTING_BOOL_KEYS = ("use_speaker_boost",)

# Unicode punctuation -> ASCII-safe (aligned with prompt_generator._UNICODE_TEXT_REPLACEMENTS)
_UNICODE_TTS_REPLACEMENTS = {
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


def _normalize_for_tts(text: str) -> str:
    """Normalize Unicode punctuation to ASCII-safe text before TTS API call."""
    result = text
    for source, target in _UNICODE_TTS_REPLACEMENTS.items():
        result = result.replace(source, target)
    return result


def _load_openai_module() -> Any:
    try:
        return importlib.import_module("openai")
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI SDK is not installed. Run `pip install -r requirements.txt`."
        ) from exc


def _tts_provider() -> str:
    raw = config.get("tts.provider", TTS_PROVIDER_DEFAULT)
    if not isinstance(raw, str):
        return TTS_PROVIDER_DEFAULT
    p = raw.strip().lower()
    if p in ("openai", "elevenlabs"):
        return p
    return TTS_PROVIDER_DEFAULT


def _resolve_voice_id(voice_from_plan: str) -> str:
    """Use configured ElevenLabs voice id when provider is ElevenLabs; else plan voice."""
    if _tts_provider() == "elevenlabs":
        vid = config.get("elevenlabs.voice_id")
        if isinstance(vid, str) and vid.strip():
            return vid.strip()
    return voice_from_plan


def _openai_api_key_configured() -> bool:
    k = config.get("openai.api_key")
    return isinstance(k, str) and bool(k.strip())


def _fallback_openai_voice_name() -> str:
    """OpenAI voice when falling back from failed ElevenLabs (plan voice may be an ElevenLabs id)."""
    v = config.get("tts.fallback_openai_voice")
    if isinstance(v, str) and v.strip():
        return v.strip()
    cycle = config.get("openai.tts_voice_cycle")
    if isinstance(cycle, list) and cycle:
        return str(cycle[0]).strip()
    return "marin"


def _get_openai_tts_config() -> dict[str, Any]:
    api_key = config.get("openai.api_key")
    if not api_key:
        raise ValueError(
            "Missing `openai.api_key` in config.yaml. "
            "Copy config.example.yaml to config.yaml and add your OpenAI credentials."
        )
    model = config.get("openai.tts_model", TTS_MODEL_OPENAI)
    response_format = config.get("openai.tts_response_format", TTS_RESPONSE_FORMAT)
    language = config.get("openai.tts_language", TTS_LANGUAGE)
    return {
        "api_key": api_key,
        "model": model,
        "response_format": response_format,
        "language": language,
    }


def _get_elevenlabs_tts_config() -> dict[str, Any]:
    api_key = config.get("elevenlabs.api_key")
    if not api_key:
        raise ValueError(
            "Missing `elevenlabs.api_key` in config.yaml (or ELEVENLABS_API_KEY in .env). "
            "Copy config.example.yaml to config.yaml and add your ElevenLabs API key."
        )
    model = config.get("elevenlabs.model", ELEVENLABS_MODEL_DEFAULT)
    return {
        "api_key": api_key,
        "model": model,
    }


def elevenlabs_v5_voice_settings() -> dict[str, float]:
    """ElevenLabs ``voice_settings`` for V5 horoscope reels (config-backed).

    Keys: ``elevenlabs.v5_stability`` (default 0.38), ``elevenlabs.v5_similarity_boost``
    (default 0.82), ``elevenlabs.v5_style`` (default 0.70).
    """
    def _f(key: str, default: float) -> float:
        raw = config.get(key, default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    return {
        "stability": _f("elevenlabs.v5_stability", 0.38),
        "similarity_boost": _f("elevenlabs.v5_similarity_boost", 0.82),
        "style": _f("elevenlabs.v5_style", 0.70),
    }


def _sanitize_elevenlabs_request_options(options: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only ElevenLabs request fields this adapter knows how to send safely."""
    if not isinstance(options, dict):
        return {}

    sanitized: dict[str, Any] = {}
    raw_voice_settings = options.get("voice_settings")
    if isinstance(raw_voice_settings, dict):
        voice_settings: dict[str, Any] = {}
        for key in _ELEVENLABS_VOICE_SETTING_FLOAT_KEYS:
            raw_value = raw_voice_settings.get(key)
            if raw_value is None:
                continue
            try:
                voice_settings[key] = float(raw_value)
            except (TypeError, ValueError):
                continue
        for key in _ELEVENLABS_VOICE_SETTING_BOOL_KEYS:
            raw_value = raw_voice_settings.get(key)
            if isinstance(raw_value, bool):
                voice_settings[key] = raw_value
        if voice_settings:
            sanitized["voice_settings"] = voice_settings

    for key in ("language_code", "apply_text_normalization", "previous_text", "next_text"):
        raw_value = options.get(key)
        if isinstance(raw_value, str) and raw_value.strip():
            sanitized[key] = raw_value.strip()

    raw_seed = options.get("seed")
    if isinstance(raw_seed, int) and 0 <= raw_seed <= 4294967295:
        sanitized["seed"] = raw_seed

    raw_locators = options.get("pronunciation_dictionary_locators")
    if isinstance(raw_locators, list):
        locators: list[dict[str, str]] = []
        for locator in raw_locators:
            if not isinstance(locator, dict):
                continue
            pronunciation_dictionary_id = locator.get("pronunciation_dictionary_id")
            if not isinstance(pronunciation_dictionary_id, str) or not pronunciation_dictionary_id.strip():
                continue
            item = {"pronunciation_dictionary_id": pronunciation_dictionary_id.strip()}
            version_id = locator.get("version_id")
            if isinstance(version_id, str) and version_id.strip():
                item["version_id"] = version_id.strip()
            locators.append(item)
        if locators:
            sanitized["pronunciation_dictionary_locators"] = locators

    return sanitized


def _build_tts_instructions(voice_instructions: str, language: str | None) -> str:
    """Build SDK-compatible instructions, including language guidance (OpenAI)."""
    parts: list[str] = []
    if language:
        parts.append(f"Speak in {language}.")
    if voice_instructions.strip():
        parts.append(voice_instructions.strip())
    return " ".join(parts)


def _elevenlabs_text_for_request(script: str) -> str:
    """Return only the script for ElevenLabs `text` (the API speaks the entire string).

    Do not prepend tone or language hints: unlike OpenAI TTS, ElevenLabs has no separate
    instructions field on this endpoint, so any leading text would be read aloud.
    """
    return _normalize_for_tts(script)


def _write_pcm16_mono_wav(pcm: bytes, path: Path, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def _elevenlabs_output_format() -> str:
    raw = config.get("elevenlabs.output_format", ELEVENLABS_OUTPUT_DEFAULT)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return ELEVENLABS_OUTPUT_DEFAULT


def _elevenlabs_accept_header(output_format: str) -> str:
    f = output_format.lower()
    if f.startswith("pcm"):
        return "audio/pcm"
    if "mp3" in f or f.startswith("mp3"):
        return "audio/mpeg"
    return "*/*"


def _mp3_bytes_to_wav_via_ffmpeg(audio_bytes: bytes, output_path: Path) -> None:
    """Decode compressed ElevenLabs output to mono 44.1kHz PCM WAV (same as mux pipeline expects)."""
    from src.renderers.ffmpeg_utils import find_ffmpeg

    ffmpeg = find_ffmpeg()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "44100",
                "-f",
                "wav",
                str(output_path),
            ],
            input=audio_bytes,
            capture_output=True,
            timeout=120,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            "ffmpeg could not convert ElevenLabs audio to WAV. "
            "Install ffmpeg and ensure it is on PATH, or set `elevenlabs.output_format` to "
            f"`{ELEVENLABS_OUTPUT_PCM}` on a Pro+ ElevenLabs plan. "
            f"ffmpeg stderr: {err[:800]}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffmpeg not found; required to decode ElevenLabs MP3 to WAV. "
            "Install ffmpeg or set `elevenlabs.output_format` to "
            f"`{ELEVENLABS_OUTPUT_PCM}` on a Pro+ ElevenLabs plan."
        ) from exc


def _elevenlabs_response_bytes_to_wav(
    audio_bytes: bytes, output_path: Path, *, output_format: str
) -> None:
    f = output_format.lower()
    if f.startswith("pcm"):
        _write_pcm16_mono_wav(audio_bytes, output_path, sample_rate=44100)
        return
    _mp3_bytes_to_wav_via_ffmpeg(audio_bytes, output_path)


def _elevenlabs_http_error_body(exc: HTTPError) -> str:
    """Read JSON/text error body from urllib HTTPError (single read)."""
    try:
        raw = exc.read()
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _generate_elevenlabs_speech(
    text: str,
    voice_id: str,
    output_path: Path,
    *,
    model_id: str,
    api_key: str,
    voice_settings: dict[str, Any] | None = None,
    request_options: dict[str, Any] | None = None,
) -> None:
    """Call ElevenLabs text-to-speech; write mono 44.1kHz WAV for muxing."""
    output_format = _elevenlabs_output_format()
    q = f"output_format={output_format}"
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?{q}"
    body: dict[str, Any] = {
        "text": text,
        "model_id": model_id,
    }
    if voice_settings:
        body["voice_settings"] = voice_settings
    if request_options:
        for key, value in request_options.items():
            if key != "voice_settings":
                body[key] = value
    payload = json.dumps(body).encode("utf-8")
    accept = _elevenlabs_accept_header(output_format)
    last_exc: Exception | None = None
    delay = INITIAL_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        req = Request(
            url,
            data=payload,
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": accept,
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=120) as resp:
                body = resp.read()
            _elevenlabs_response_bytes_to_wav(
                body, output_path, output_format=output_format
            )
            return
        except HTTPError as exc:
            last_exc = exc
            detail = _elevenlabs_http_error_body(exc)
            if detail:
                logger.warning(
                    "ElevenLabs TTS HTTP %s: %s",
                    exc.code,
                    detail[:800] + ("..." if len(detail) > 800 else ""),
                )
            if exc.code in (429, 500, 502, 503) and attempt < MAX_RETRIES:
                logger.warning(
                    "ElevenLabs TTS attempt %d/%d failed: %s",
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except URLError as exc:
            last_exc = exc
            if attempt == MAX_RETRIES:
                raise
            logger.warning(
                "ElevenLabs TTS attempt %d/%d failed: %s",
                attempt,
                MAX_RETRIES,
                exc,
            )
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"ElevenLabs TTS failed after {MAX_RETRIES} attempts") from last_exc


def _generate_openai_voiceover(
    script: str,
    voice: str,
    voice_instructions: str,
    output_path: Path,
    content_id: str,
    *,
    language: str | None = None,
) -> Path:
    """Call OpenAI speech API, write WAV, record tts_gen cost as openai."""
    cfg = _get_openai_tts_config()
    openai_module = _load_openai_module()
    client = openai_module.OpenAI(api_key=cfg["api_key"])

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lang = language or cfg.get("language", TTS_LANGUAGE)
    response_format = cfg.get("response_format", TTS_RESPONSE_FORMAT)
    instructions = _build_tts_instructions(voice_instructions, lang)

    normalized_script = _normalize_for_tts(script)
    last_exc: Exception | None = None
    delay = INITIAL_BACKOFF
    response = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.audio.speech.create(
                model=cfg["model"],
                voice=voice,
                input=normalized_script,
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

    if response is None:
        raise RuntimeError("OpenAI TTS returned no response")
    audio_bytes = response.content if hasattr(response, "content") else response.read()
    output_path.write_bytes(audio_bytes)
    logger.info("Saved OpenAI TTS audio to %s", output_path)

    char_count = len(script)
    cost_usd = (char_count / 1000) * TTS_COST_OPENAI_PER_1K_CHARS_USD
    db.insert_cost(Cost(
        content_id=content_id,
        step="tts_gen",
        api_provider="openai",
        tokens_or_units=char_count,
        cost_usd=cost_usd,
    ))

    return output_path


def generate_voiceover(
    script: str,
    voice: str,
    voice_instructions: str,
    output_path: Path,
    content_id: str,
    *,
    language: str | None = None,
    elevenlabs_voice_settings: dict[str, Any] | None = None,
    elevenlabs_request_options: dict[str, Any] | None = None,
) -> Path:
    """Generate TTS audio from script and save to output_path (WAV).

    Args:
        script: Text to speak.
        voice: Voice id from the content manifest (overridden by ``elevenlabs.voice_id`` when
            ``tts.provider`` is ``elevenlabs``).
        voice_instructions: Tone/pacing hints (OpenAI: ``instructions`` API field only; ignored for
            ElevenLabs on the standard REST endpoint so hints are not spoken as part of ``text``).
        output_path: Path to save WAV file.
        content_id: Content id for cost tracking.
        language: Override language (default from config for OpenAI).
        elevenlabs_voice_settings: Optional ElevenLabs ``voice_settings`` object (e.g. from
            :func:`elevenlabs_v5_voice_settings` for V5 horoscope reels). Ignored when provider is
            OpenAI.
        elevenlabs_request_options: Optional request metadata for ElevenLabs fields such as
            ``language_code``, ``apply_text_normalization``, pronunciation dictionaries, or extra
            ``voice_settings`` like ``speed`` and ``use_speaker_boost``.

    When ``tts.provider`` is ``elevenlabs``, any failure from the ElevenLabs API falls back to
    OpenAI TTS if ``openai.api_key`` is set (voice from ``tts.fallback_openai_voice``,
    ``openai.tts_voice_cycle``, or ``marin``).

    Returns:
        Path to the saved WAV file.
    """
    output_path = Path(output_path)
    provider = _tts_provider()
    voice_resolved = _resolve_voice_id(voice)

    if provider == "elevenlabs":
        if not isinstance(voice_resolved, str) or not voice_resolved.strip():
            raise ValueError(
                "Missing ElevenLabs voice id. Set `elevenlabs.voice_id` in config.yaml "
                "or ensure the voiceover plan includes a valid `voice` (ElevenLabs voice id)."
            )
        el = _get_elevenlabs_tts_config()
        text = _elevenlabs_text_for_request(script)
        request_options = _sanitize_elevenlabs_request_options(elevenlabs_request_options)
        merged_voice_settings = dict(elevenlabs_voice_settings or {})
        extra_voice_settings = request_options.pop("voice_settings", None)
        if isinstance(extra_voice_settings, dict):
            merged_voice_settings.update(extra_voice_settings)
        try:
            _generate_elevenlabs_speech(
                text,
                voice_resolved,
                output_path,
                model_id=el["model"],
                api_key=el["api_key"],
                voice_settings=merged_voice_settings or None,
                request_options=request_options or None,
            )
        except Exception as exc:
            if not _openai_api_key_configured():
                raise
            logger.warning(
                "ElevenLabs TTS failed; falling back to OpenAI: %s",
                exc,
            )
            return _generate_openai_voiceover(
                script=script,
                voice=_fallback_openai_voice_name(),
                voice_instructions=voice_instructions,
                output_path=output_path,
                content_id=content_id,
                language=language,
            )
        logger.info("Saved ElevenLabs TTS audio to %s", output_path)
        char_count = len(script)
        cost_usd = (char_count / 1000) * TTS_COST_ELEVENLABS_PER_1K_CHARS_USD
        db.insert_cost(Cost(
            content_id=content_id,
            step="tts_gen",
            api_provider="elevenlabs",
            tokens_or_units=char_count,
            cost_usd=cost_usd,
        ))
        return output_path

    return _generate_openai_voiceover(
        script=script,
        voice=voice_resolved,
        voice_instructions=voice_instructions,
        output_path=output_path,
        content_id=content_id,
        language=language,
    )
