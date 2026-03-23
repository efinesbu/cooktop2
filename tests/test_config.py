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


def test_gemini_image_model_remaps_legacy_gemini_2_0_flash(monkeypatch: pytest.MonkeyPatch) -> None:
    """gemini-2.0-flash does not support IMAGE modalities; remap to an image-capable id."""
    config.reload()
    monkeypatch.setattr(
        "src.config._config",
        {
            "gemini": {"api_key": "k", "model": "gemini-2.0-flash"},
            "platforms": {"enabled": []},
        },
    )
    assert config.gemini_image_model() == "gemini-2.5-flash-image"


def test_gemini_image_model_prefers_image_model_key(monkeypatch: pytest.MonkeyPatch) -> None:
    config.reload()
    monkeypatch.setattr(
        "src.config._config",
        {
            "gemini": {
                "api_key": "k",
                "model": "gemini-2.0-flash",
                "image_model": "custom-image-model",
            },
            "platforms": {"enabled": []},
        },
    )
    assert config.gemini_image_model() == "custom-image-model"


def test_gemini_v5_model_defaults_to_gemini_image_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """V5 starting image uses gemini_image_model when v5_model is unset."""
    config.reload()
    monkeypatch.setattr(
        "src.config._config",
        {
            "gemini": {"api_key": "k", "model": "gemini-2.0-flash"},
            "platforms": {"enabled": []},
        },
    )
    assert config.gemini_v5_model() == "gemini-2.5-flash-image"


def test_gemini_v5_model_legacy_nano_banana_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy placeholder nano-banana-2 maps to gemini.model."""
    config.reload()
    monkeypatch.setattr(
        "src.config._config",
        {
            "gemini": {
                "api_key": "k",
                "model": "gemini-2.0-flash",
                "v5_model": "nano-banana-2",
            },
            "platforms": {"enabled": []},
        },
    )
    assert config.gemini_v5_model() == "gemini-2.5-flash-image"


def test_gemini_v5_model_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """When gemini.v5_model is set to a real id, it is used."""
    config.reload()
    monkeypatch.setattr(
        "src.config._config",
        {
            "gemini": {
                "api_key": "k",
                "model": "gemini-2.0-flash",
                "v5_model": "gemini-2.5-flash-image",
            },
            "platforms": {"enabled": []},
        },
    )
    assert config.gemini_v5_model() == "gemini-2.5-flash-image"


def test_enabled_platforms_keeps_youtube_when_cached_token_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """YouTube posting stays enabled when a cached OAuth token exists."""
    config.reload()
    token_path = tmp_path / "youtube_token.json"
    token_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "src.config._config",
        {
            "platforms": {"enabled": ["youtube"]},
            "youtube": {
                "client_secrets_file": str(tmp_path / "missing-client-secrets.json"),
                "token_file": str(token_path),
            },
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)

    assert config.enabled_platforms("posting") == ["youtube"]


def test_enabled_platforms_rejects_instagram_make_bridge_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Instagram is not enabled when Make bridge values are still placeholders."""
    config.reload()
    monkeypatch.setattr(
        "src.config._config",
        {
            "platforms": {"enabled": ["instagram"]},
            "make_bridge": {
                "webhook_url": "https://hook.us2.make.com/your-webhook-id",
                "r2": {
                    "account_id": "YOUR_CLOUDFLARE_R2_ACCOUNT_ID",
                    "access_key_id": "YOUR_CLOUDFLARE_R2_ACCESS_KEY_ID",
                    "secret_access_key": "YOUR_CLOUDFLARE_R2_SECRET_ACCESS_KEY",
                    "bucket_name": "your-r2-bucket-name",
                },
            },
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("R2_BUCKET_NAME", raising=False)
    monkeypatch.delenv("MAKE_WEBHOOK_URL", raising=False)

    assert config.enabled_platforms("posting") == []
