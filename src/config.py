from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from src.models import PLATFORMS

_DEFAULT_CONFIG_PATH = Path("config.yaml")
_config: dict[str, Any] | None = None

_POSTING_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "youtube": ("youtube.client_secrets_file",),
    "instagram": (
        "instagram.access_token",
        "instagram.instagram_account_id",
        "instagram.gcs_bucket",
    ),
    "tiktok": (
        "tiktok.client_key",
        "tiktok.client_secret",
        "tiktok.access_token",
        "tiktok.refresh_token",
    ),
    "x": (
        "x.api_key",
        "x.api_secret",
        "x.access_token",
        "x.access_token_secret",
    ),
}

_ANALYTICS_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "youtube": ("youtube.api_key",),
    "instagram": ("instagram.access_token",),
    "tiktok": ("tiktok.client_key", "tiktok.client_secret"),
    "x": (
        "x.api_key",
        "x.api_secret",
        "x.access_token",
        "x.access_token_secret",
    ),
}


def _resolve_data_root(raw: str) -> Path:
    if raw.startswith("~"):
        return Path(os.path.expanduser(raw))
    return Path(raw)


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    global _config
    if _config is not None:
        return _config
    cfg_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {cfg_path}. "
            "Copy config.example.yaml to config.yaml and fill in your keys."
        )
    with open(cfg_path, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)
    return _config


def get(key: str, default: Any = None) -> Any:
    """Dot-notation lookup, e.g. get('shopify.store_url')."""
    cfg = load_config()
    parts = key.split(".")
    node: Any = cfg
    for part in parts:
        if isinstance(node, dict):
            node = node.get(part)
        else:
            return default
        if node is None:
            return default
    return node


def data_root() -> Path:
    cfg = load_config()
    return _resolve_data_root(cfg.get("data_root", "~/.velura"))


def videos_dir() -> Path:
    return data_root() / "videos"


def product_images_dir() -> Path:
    return data_root() / "product-images"


def db_path() -> Path:
    return Path("db/velura.db")


def enabled_platforms(purpose: str = "posting") -> list[str]:
    requirements = _platform_requirements(purpose)
    return [
        platform
        for platform in _requested_platforms()
        if all(_is_configured(key) for key in requirements.get(platform, ()))
    ]


def reload() -> dict[str, Any]:
    global _config
    _config = None
    return load_config()


def _requested_platforms() -> list[str]:
    raw = get("platforms.enabled")
    if raw is None:
        return list(PLATFORMS)
    if not isinstance(raw, list):
        raise ValueError("config.yaml `platforms.enabled` must be a list of platform names")

    requested: list[str] = []
    invalid: list[str] = []
    for item in raw:
        platform = str(item).strip().lower()
        if not platform:
            continue
        if platform not in PLATFORMS:
            invalid.append(platform)
            continue
        if platform not in requested:
            requested.append(platform)

    if invalid:
        supported = ", ".join(PLATFORMS)
        invalid_values = ", ".join(invalid)
        raise ValueError(
            f"Unsupported platform(s) in config.yaml `platforms.enabled`: {invalid_values}. "
            f"Supported values: {supported}."
        )
    return requested


def _platform_requirements(purpose: str) -> dict[str, tuple[str, ...]]:
    if purpose == "posting":
        return _POSTING_REQUIREMENTS
    if purpose == "analytics":
        return _ANALYTICS_REQUIREMENTS
    raise ValueError(f"Unknown platform purpose: {purpose}")


def _is_configured(key: str) -> bool:
    value = get(key)
    if isinstance(value, str):
        value = value.strip()
    if not value:
        return False
    if key.endswith("_file"):
        return Path(str(value)).expanduser().exists()
    return True
