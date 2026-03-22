"""Tests for config loading and env var overrides."""

from __future__ import annotations

import pytest

from src import config


def test_env_var_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env vars take precedence over config.yaml for secret keys."""
    config.reload()
    monkeypatch.setattr(
        "src.config._config",
        {"openai": {"api_key": "from-yaml"}, "platforms": {"enabled": []}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert config.get("openai.api_key") == "from-env"


def test_config_fallback_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """When env var is unset, config.yaml value is used."""
    config.reload()
    monkeypatch.setattr(
        "src.config._config",
        {"openai": {"api_key": "from-yaml"}, "platforms": {"enabled": []}},
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)  # prevent .env from repopulating
    assert config.get("openai.api_key") == "from-yaml"


def test_tts_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTS config keys have sensible defaults when not set."""
    config.reload()
    monkeypatch.setattr(
        "src.config._config",
        {"openai": {"api_key": "test-key"}, "platforms": {"enabled": []}},
    )
    # Defaults when keys absent
    assert config.get("openai.tts_model") is None
    assert config.get("openai.tts_voice_cycle") is None
    assert config.get("openai.tts_response_format") is None
    assert config.get("openai.tts_language") is None
    assert config.get("openai.tts_enabled_formats") is None


def test_tts_config_from_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTS config keys are read from config when set."""
    config.reload()
    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {
                "api_key": "test-key",
                "tts_model": "gpt-4o-mini-tts",
                "tts_voice_cycle": ["marin"],
                "tts_response_format": "wav",
                "tts_language": "english",
                "tts_enabled_formats": ["image_motion_15s"],
            },
            "platforms": {"enabled": []},
        },
    )
    assert config.get("openai.tts_model") == "gpt-4o-mini-tts"
    assert config.get("openai.tts_voice_cycle") == ["marin"]
    assert config.get("openai.tts_response_format") == "wav"
    assert config.get("openai.tts_language") == "english"
    assert config.get("openai.tts_enabled_formats") == ["image_motion_15s"]


def test_tts_enabled_formats_top_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """tts.enabled_formats overrides legacy openai.tts_enabled_formats when set."""
    config.reload()
    monkeypatch.setattr(
        "src.config._config",
        {
            "tts": {"enabled_formats": ["ai_video_flex_15s"]},
            "openai": {
                "api_key": "test-key",
                "tts_enabled_formats": ["image_motion_15s"],
            },
            "platforms": {"enabled": []},
        },
    )
    assert config.get("tts.enabled_formats") == ["ai_video_flex_15s"]
