"""Tests for OpenAI TTS voiceover generation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import db
from src.models import Content, Product
from src.voiceover_generator import generate_voiceover


def test_generate_voiceover_raises_when_api_key_missing(monkeypatch, tmp_path: Path) -> None:
    """Missing OpenAI API key raises ValueError with setup message."""
    monkeypatch.setattr(
        "src.config._config",
        {"openai": {}},
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)

    with pytest.raises(ValueError, match="Missing.*openai.api_key"):
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
        {"openai": {"api_key": "test-key"}},
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
        {"openai": {"api_key": "test-key"}},
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
