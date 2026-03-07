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
    assert config.get("openai.api_key") == "from-yaml"
