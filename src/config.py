from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from src.models import PLATFORMS

_DEFAULT_CONFIG_PATH = Path("config.yaml")
_config: dict[str, Any] | None = None

# Dotted config keys that can be overridden by environment variables.
# Env vars take precedence over config.yaml when set.
_ENV_OVERRIDES: dict[str, str] = {
    "openai.api_key": "OPENAI_API_KEY",
    "gemini.api_key": "GEMINI_API_KEY",
    "xai.api_key": "XAI_API_KEY",
    "youtube.api_key": "YOUTUBE_API_KEY",
    "tiktok.client_key": "TIKTOK_CLIENT_KEY",
    "tiktok.client_secret": "TIKTOK_CLIENT_SECRET",
    "tiktok.access_token": "TIKTOK_ACCESS_TOKEN",
    "tiktok.refresh_token": "TIKTOK_REFRESH_TOKEN",
    "tiktok-sandbox.client_key": "TIKTOK_SANDBOX_CLIENT_KEY",
    "tiktok-sandbox.client_secret": "TIKTOK_SANDBOX_CLIENT_SECRET",
    "x.api_key": "X_API_KEY",
    "x.api_secret": "X_API_SECRET",
    "x.access_token": "X_ACCESS_TOKEN",
    "x.access_token_secret": "X_ACCESS_TOKEN_SECRET",
    "instagram.access_token": "INSTAGRAM_ACCESS_TOKEN",
    "instagram.instagram_account_id": "INSTAGRAM_ACCOUNT_ID",
    "shopify.store_url": "SHOPIFY_STORE_URL",
    "shopify.client_id": "SHOPIFY_CLIENT_ID",
    "shopify.client_secret": "SHOPIFY_CLIENT_SECRET",
    "make_bridge.webhook_url": "MAKE_WEBHOOK_URL",
    "make_bridge.r2.account_id": "R2_ACCOUNT_ID",
    "make_bridge.r2.access_key_id": "R2_ACCESS_KEY_ID",
    "make_bridge.r2.secret_access_key": "R2_SECRET_ACCESS_KEY",
    "make_bridge.r2.bucket_name": "R2_BUCKET_NAME",
    "briefing.email": "BRIEFING_EMAIL",
    "briefing.smtp_host": "SMTP_HOST",
    "briefing.smtp_port": "SMTP_PORT",
    "briefing.smtp_user": "SMTP_USER",
    "briefing.smtp_pass": "SMTP_PASS",
    "briefing.from_email": "BRIEFING_FROM_EMAIL",
}

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
    load_dotenv()
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
    """Dot-notation lookup, e.g. get('shopify.store_url').
    For keys in _ENV_OVERRIDES, env vars take precedence over config.yaml."""
    load_dotenv()
    env_var = _ENV_OVERRIDES.get(key)
    if env_var:
        val = os.getenv(env_var)
        if val is not None and str(val).strip():
            return val.strip()
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


def brand_dir() -> Path:
    """Brand-kit reference images for image_motion_15s Gemini generation."""
    return data_root() / "brand"


def models_dir() -> Path:
    """Human-model reference images for lifestyle frames in image_motion_15s."""
    return data_root() / "models"


def db_path() -> Path:
    return Path("db/velura.db")


def enabled_platforms(purpose: str = "posting") -> list[str]:
    requirements = _platform_requirements(purpose)
    return [
        platform
        for platform in _requested_platforms()
        if _is_platform_configured(platform, requirements.get(platform, ()), purpose)
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


def _is_platform_configured(
    platform: str,
    requirements: tuple[str, ...],
    purpose: str,
) -> bool:
    if purpose == "posting" and platform == "instagram":
        return _is_instagram_posting_configured()
    return all(_is_configured(key) for key in requirements)


def _is_instagram_posting_configured() -> bool:
    direct_instagram_keys = (
        "instagram.access_token",
        "instagram.instagram_account_id",
        "instagram.gcs_bucket",
    )
    if all(_is_configured(key) for key in direct_instagram_keys):
        return True

    load_dotenv()
    return all(
        _has_non_empty_value(value)
        for value in (
            os.getenv("R2_ACCOUNT_ID") or get("make_bridge.r2.account_id"),
            os.getenv("R2_ACCESS_KEY_ID")
            or os.getenv("AWS_ACCESS_KEY_ID")
            or get("make_bridge.r2.access_key_id"),
            os.getenv("R2_SECRET_ACCESS_KEY")
            or os.getenv("AWS_SECRET_ACCESS_KEY")
            or get("make_bridge.r2.secret_access_key"),
            os.getenv("R2_BUCKET_NAME") or get("make_bridge.r2.bucket_name"),
            os.getenv("MAKE_WEBHOOK_URL") or get("make_bridge.webhook_url"),
        )
    )


def _has_non_empty_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)
