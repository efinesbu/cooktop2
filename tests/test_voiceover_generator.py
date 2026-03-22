"""Tests for TTS voiceover generation (OpenAI and ElevenLabs)."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from src import db
from src.models import Content, Product
from src.voiceover_generator import generate_voiceover


def test_generate_voiceover_raises_when_elevenlabs_key_missing(monkeypatch, tmp_path: Path) -> None:
    """Default provider is ElevenLabs; missing key raises with setup message."""
    monkeypatch.setattr(
        "src.config._config",
        {"tts": {"provider": "elevenlabs"}, "elevenlabs": {}},
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)

    with pytest.raises(ValueError, match="Missing `elevenlabs.api_key`"):
        generate_voiceover(
            script="Test script.",
            voice="K7W7zLWeGoxU9YqWoB7A",
            voice_instructions="Calm tone.",
            output_path=tmp_path / "out.wav",
            content_id="test-001",
        )


def test_generate_voiceover_raises_when_openai_selected_but_key_missing(
    monkeypatch, tmp_path: Path
) -> None:
    """With tts.provider openai, missing OpenAI key raises."""
    monkeypatch.setattr(
        "src.config._config",
        {"tts": {"provider": "openai"}, "openai": {}},
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)

    with pytest.raises(ValueError, match="Missing `openai.api_key`"):
        generate_voiceover(
            script="Test script.",
            voice="marin",
            voice_instructions="Calm tone.",
            output_path=tmp_path / "out.wav",
            content_id="test-001",
        )


def test_generate_voiceover_calls_openai_and_saves_wav(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """generate_voiceover calls OpenAI TTS API and saves WAV, records cost."""
    product = Product(sku="serum-x", name="Serum X")
    content = Content(
        id="test-tts-001",
        product_sku=product.sku,
        theme="benefit_spotlight",
        hook_type="question",
    )
    db.upsert_product(product)
    db.insert_content(content)

    captured: dict = {}

    class FakeSpeechResponse:
        content = b"fake-wav-bytes"

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key

        @property
        def audio(self):
            return self

        @property
        def speech(self):
            return self

        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeSpeechResponse()

    fake_openai = SimpleNamespace(OpenAI=FakeOpenAIClient)

    monkeypatch.setattr(
        "src.config._config",
        {"tts": {"provider": "openai"}, "openai": {"api_key": "test-key"}},
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "src.voiceover_generator._load_openai_module",
        lambda: fake_openai,
    )

    output_path = tmp_path / "videos" / "serum-x" / "test-tts-001_voiceover.wav"
    result = generate_voiceover(
        script="Want fresher skin? Try me.",
        voice="marin",
        voice_instructions="Speak in a calm, premium tone.",
        output_path=output_path,
        content_id=content.id,
    )

    assert result == output_path
    assert output_path.read_bytes() == b"fake-wav-bytes"
    assert captured.get("voice") == "marin"
    assert captured.get("input") == "Want fresher skin? Try me."
    assert "gpt-4o-mini" in (captured.get("model") or "")
    assert captured.get("language") is None
    assert captured.get("instructions") == "Speak in english. Speak in a calm, premium tone."

    costs = db.costs_for_content(content.id)
    tts_costs = [c for c in costs if c.step == "tts_gen"]
    assert len(tts_costs) == 1
    assert tts_costs[0].api_provider == "openai"


def test_elevenlabs_uses_plan_voice_when_config_voice_id_missing(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """ElevenLabs accepts voice from the voiceover plan when elevenlabs.voice_id is unset."""
    product = Product(sku="serum-x", name="Serum X")
    content = Content(
        id="test-tts-el-plan-001",
        product_sku=product.sku,
        theme="benefit_spotlight",
        hook_type="question",
    )
    db.upsert_product(product)
    db.insert_content(content)

    pcm_silence = b"\x00\x00" * 8000

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return pcm_silence

    monkeypatch.setattr(
        "src.config._config",
        {
            "tts": {"provider": "elevenlabs"},
            "elevenlabs": {
                "api_key": "xi-test",
                "model": "eleven_multilingual_v2",
                "output_format": "pcm_44100",
            },
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "src.voiceover_generator.urlopen",
        lambda req, timeout=120: FakeResp(),
    )

    output_path = tmp_path / "out.wav"
    plan_voice = "K7W7zLWeGoxU9YqWoB7A"
    result = generate_voiceover(
        script="Hello world.",
        voice=plan_voice,
        voice_instructions="Warm tone.",
        output_path=output_path,
        content_id=content.id,
    )

    assert result == output_path
    assert output_path.exists()


def test_generate_voiceover_raises_when_elevenlabs_voice_unresolved(
    monkeypatch, tmp_path: Path
) -> None:
    """ElevenLabs requires either config voice_id or a non-empty plan voice."""
    monkeypatch.setattr(
        "src.config._config",
        {
            "tts": {"provider": "elevenlabs"},
            "elevenlabs": {"api_key": "xi-test", "model": "eleven_multilingual_v2"},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)

    with pytest.raises(ValueError, match="Missing ElevenLabs voice id"):
        generate_voiceover(
            script="Test.",
            voice="",
            voice_instructions="",
            output_path=tmp_path / "out.wav",
            content_id="test-001",
        )


def test_generate_voiceover_calls_elevenlabs_and_saves_wav(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """ElevenLabs path writes PCM WAV and records cost."""
    product = Product(sku="serum-x", name="Serum X")
    content = Content(
        id="test-tts-el-001",
        product_sku=product.sku,
        theme="benefit_spotlight",
        hook_type="question",
    )
    db.upsert_product(product)
    db.insert_content(content)

    pcm_silence = b"\x00\x00" * 8000  # brief mono s16 silence at 44.1kHz

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return pcm_silence

    monkeypatch.setattr(
        "src.config._config",
        {
            "tts": {"provider": "elevenlabs"},
            "elevenlabs": {
                "api_key": "xi-test",
                "model": "eleven_multilingual_v2",
                "voice_id": "K7W7zLWeGoxU9YqWoB7A",
                "output_format": "pcm_44100",
            },
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "src.voiceover_generator.urlopen",
        lambda req, timeout=120: FakeResp(),
    )

    output_path = tmp_path / "videos" / "serum-x" / "test-tts-el-001_voiceover.wav"
    result = generate_voiceover(
        script="Hello world.",
        voice="K7W7zLWeGoxU9YqWoB7A",
        voice_instructions="Warm tone.",
        output_path=output_path,
        content_id=content.id,
    )

    assert result == output_path
    assert output_path.exists() and output_path.stat().st_size > 0

    costs = db.costs_for_content(content.id)
    tts_costs = [c for c in costs if c.step == "tts_gen"]
    assert len(tts_costs) == 1
    assert tts_costs[0].api_provider == "elevenlabs"


def test_elevenlabs_request_text_is_script_only_not_tone_instructions(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """ElevenLabs speaks the full `text` field; do not prepend voice_instructions."""
    product = Product(sku="serum-x", name="Serum X")
    content = Content(
        id="test-tts-el-body-001",
        product_sku=product.sku,
        theme="benefit_spotlight",
        hook_type="question",
    )
    db.upsert_product(product)
    db.insert_content(content)

    pcm_silence = b"\x00\x00" * 8000
    captured: dict[str, str] = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return pcm_silence

    def capture_urlopen(req, timeout=120):
        captured["body"] = req.data.decode("utf-8") if req.data else ""
        return FakeResp()

    monkeypatch.setattr(
        "src.config._config",
        {
            "tts": {"provider": "elevenlabs"},
            "elevenlabs": {
                "api_key": "xi-test",
                "model": "eleven_multilingual_v2",
                "voice_id": "K7W7zLWeGoxU9YqWoB7A",
                "output_format": "pcm_44100",
            },
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "src.voiceover_generator.urlopen",
        capture_urlopen,
    )

    output_path = tmp_path / "out.wav"
    generate_voiceover(
        script="Only this line should be spoken.",
        voice="K7W7zLWeGoxU9YqWoB7A",
        voice_instructions="Speak in a calm, premium, reassuring tone for a premium consumer brand.",
        output_path=output_path,
        content_id=content.id,
    )

    payload = json.loads(captured["body"])
    assert payload["text"] == "Only this line should be spoken."
    assert "calm" not in payload["text"].lower()
    assert "premium" not in payload["text"].lower()


def test_elevenlabs_failure_falls_back_to_openai(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When ElevenLabs request fails, use OpenAI TTS if openai.api_key is configured."""
    product = Product(sku="serum-x", name="Serum X")
    content = Content(
        id="test-tts-fb-001",
        product_sku=product.sku,
        theme="benefit_spotlight",
        hook_type="question",
    )
    db.upsert_product(product)
    db.insert_content(content)

    captured: dict = {}

    class FakeSpeechResponse:
        content = b"fallback-openai-wav"

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key

        @property
        def audio(self):
            return self

        @property
        def speech(self):
            return self

        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeSpeechResponse()

    fake_openai = SimpleNamespace(OpenAI=FakeOpenAIClient)

    def failing_urlopen(req, timeout=120):
        raise HTTPError("https://api.elevenlabs.io/", 400, "Bad Request", None, BytesIO())

    monkeypatch.setattr(
        "src.config._config",
        {
            "tts": {"provider": "elevenlabs"},
            "elevenlabs": {
                "api_key": "xi-test",
                "model": "eleven_multilingual_v2",
                "voice_id": "K7W7zLWeGoxU9YqWoB7A",
                "output_format": "pcm_44100",
            },
            "openai": {"api_key": "sk-openai-fallback"},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr("src.voiceover_generator.urlopen", failing_urlopen)
    monkeypatch.setattr(
        "src.voiceover_generator._load_openai_module",
        lambda: fake_openai,
    )

    output_path = tmp_path / "videos" / "serum-x" / "test-tts-fb-001_voiceover.wav"
    result = generate_voiceover(
        script="Fallback test line.",
        voice="K7W7zLWeGoxU9YqWoB7A",
        voice_instructions="Neutral.",
        output_path=output_path,
        content_id=content.id,
    )

    assert result == output_path
    assert output_path.read_bytes() == b"fallback-openai-wav"
    assert captured.get("voice") == "marin"

    costs = db.costs_for_content(content.id)
    tts_costs = [c for c in costs if c.step == "tts_gen"]
    assert len(tts_costs) == 1
    assert tts_costs[0].api_provider == "openai"


def test_elevenlabs_failure_without_openai_key_raises(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """ElevenLabs failure propagates when no OpenAI key is available for fallback."""

    def failing_urlopen(req, timeout=120):
        raise HTTPError("https://api.elevenlabs.io/", 503, "Service Unavailable", None, BytesIO())

    monkeypatch.setattr(
        "src.config._config",
        {
            "tts": {"provider": "elevenlabs"},
            "elevenlabs": {
                "api_key": "xi-test",
                "model": "eleven_multilingual_v2",
                "voice_id": "K7W7zLWeGoxU9YqWoB7A",
                "output_format": "pcm_44100",
            },
            "openai": {},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr("src.voiceover_generator.urlopen", failing_urlopen)

    with pytest.raises(HTTPError) as exc_info:
        generate_voiceover(
            script="No fallback.",
            voice="K7W7zLWeGoxU9YqWoB7A",
            voice_instructions="",
            output_path=tmp_path / "out.wav",
            content_id="orphan-content",
        )
    assert exc_info.value.code == 503


def test_generate_voiceover_normalizes_unicode_punctuation_before_tts(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Regression: voiceover script with Unicode em dash is normalized to ASCII before TTS API."""
    product = Product(sku="serum-x", name="Serum X")
    content = Content(
        id="test-tts-002",
        product_sku=product.sku,
        theme="benefit_spotlight",
        hook_type="question",
    )
    db.upsert_product(product)
    db.insert_content(content)

    captured: dict = {}

    class FakeSpeechResponse:
        content = b"fake-wav-bytes"

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key

        @property
        def audio(self):
            return self

        @property
        def speech(self):
            return self

        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeSpeechResponse()

    fake_openai = SimpleNamespace(OpenAI=FakeOpenAIClient)

    monkeypatch.setattr(
        "src.config._config",
        {"tts": {"provider": "openai"}, "openai": {"api_key": "test-key"}},
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "src.voiceover_generator._load_openai_module",
        lambda: fake_openai,
    )

    script_with_em_dash = "Want fresher skin\u2014try me today."
    output_path = tmp_path / "videos" / "serum-x" / "test-tts-002_voiceover.wav"
    generate_voiceover(
        script=script_with_em_dash,
        voice="marin",
        voice_instructions="Calm tone.",
        output_path=output_path,
        content_id=content.id,
    )

    assert captured.get("input") == "Want fresher skin-try me today."
