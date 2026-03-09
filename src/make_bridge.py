from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import yaml
from dotenv import load_dotenv

from src import config

PRESIGNED_URL_TTL_SECONDS = 30 * 60


@dataclass(frozen=True)
class BridgeSettings:
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str
    make_webhook_url: str


@dataclass(frozen=True)
class BridgeResult:
    object_key: str
    video_url: str
    webhook_status_code: int
    webhook_response_text: str


def load_bridge_settings(config_path: Path | None = None) -> BridgeSettings:
    load_dotenv()
    yaml_config = _load_yaml_config(config_path) if config_path else None

    values = {
        "r2_account_id": _first_non_empty(
            os.getenv("R2_ACCOUNT_ID"),
            _get_config_value("make_bridge.r2.account_id", yaml_config),
        ),
        "r2_access_key_id": _first_non_empty(
            os.getenv("R2_ACCESS_KEY_ID"),
            os.getenv("AWS_ACCESS_KEY_ID"),
            _get_config_value("make_bridge.r2.access_key_id", yaml_config),
        ),
        "r2_secret_access_key": _first_non_empty(
            os.getenv("R2_SECRET_ACCESS_KEY"),
            os.getenv("AWS_SECRET_ACCESS_KEY"),
            _get_config_value("make_bridge.r2.secret_access_key", yaml_config),
        ),
        "r2_bucket_name": _first_non_empty(
            os.getenv("R2_BUCKET_NAME"),
            _get_config_value("make_bridge.r2.bucket_name", yaml_config),
        ),
        "make_webhook_url": _first_non_empty(
            os.getenv("MAKE_WEBHOOK_URL"),
            _get_config_value("make_bridge.webhook_url", yaml_config),
        ),
    }
    missing = [
        ("R2_ACCOUNT_ID", "make_bridge.r2.account_id", values["r2_account_id"]),
        ("R2_ACCESS_KEY_ID", "make_bridge.r2.access_key_id", values["r2_access_key_id"]),
        (
            "R2_SECRET_ACCESS_KEY",
            "make_bridge.r2.secret_access_key",
            values["r2_secret_access_key"],
        ),
        ("R2_BUCKET_NAME", "make_bridge.r2.bucket_name", values["r2_bucket_name"]),
        ("MAKE_WEBHOOK_URL", "make_bridge.webhook_url", values["make_webhook_url"]),
    ]
    missing_keys = [f"{env_name} / {yaml_key}" for env_name, yaml_key, value in missing if not value]
    if missing_keys:
        joined = ", ".join(missing_keys)
        raise ValueError(
            "Missing required Make/R2 configuration. Set these in .env or config.yaml: "
            f"{joined}"
        )

    return BridgeSettings(
        r2_account_id=values["r2_account_id"],
        r2_access_key_id=values["r2_access_key_id"],
        r2_secret_access_key=values["r2_secret_access_key"],
        r2_bucket_name=values["r2_bucket_name"],
        make_webhook_url=values["make_webhook_url"],
    )


def is_bridge_configured(config_path: Path | None = None) -> bool:
    try:
        load_bridge_settings(config_path=config_path)
    except ValueError:
        return False
    return True


def validate_video_path(video_path: Path) -> None:
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not video_path.is_file():
        raise ValueError(f"Video path is not a file: {video_path}")
    if video_path.suffix.lower() != ".mp4":
        raise ValueError(f"Expected an .mp4 file, got: {video_path.name}")


def default_object_key(video_path: Path, prefix: str = "videos") -> str:
    timestamp = int(time.time())
    return f"{prefix}/{video_path.stem}-{timestamp}.mp4"


def bridge_video_to_make(
    video_path: Path,
    caption: str,
    *,
    object_key: str | None = None,
    content_id: str | None = None,
    platform: str | None = None,
    config_path: Path | None = None,
) -> BridgeResult:
    validate_video_path(video_path)
    settings = load_bridge_settings(config_path=config_path)
    resolved_object_key = object_key or default_object_key(video_path)
    video_url = upload_video_to_r2(video_path, resolved_object_key, settings)
    response = send_to_make(
        video_url,
        caption,
        settings.make_webhook_url,
        content_id=content_id,
        platform=platform,
        handoff_object_key=resolved_object_key,
        handoff_id=f"make:{resolved_object_key}",
    )
    return BridgeResult(
        object_key=resolved_object_key,
        video_url=video_url,
        webhook_status_code=response.status_code,
        webhook_response_text=response.text,
    )


def upload_video_to_r2(video_path: Path, object_key: str, settings: BridgeSettings) -> str:
    import boto3
    from botocore.client import Config

    client = boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    with video_path.open("rb") as handle:
        client.upload_fileobj(
            handle,
            settings.r2_bucket_name,
            object_key,
            ExtraArgs={"ContentType": "video/mp4"},
        )

    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.r2_bucket_name, "Key": object_key},
        ExpiresIn=PRESIGNED_URL_TTL_SECONDS,
    )


def send_to_make(
    video_url: str,
    caption: str,
    webhook_url: str,
    *,
    content_id: str | None = None,
    platform: str | None = None,
    handoff_object_key: str | None = None,
    handoff_id: str | None = None,
) -> requests.Response:
    payload: dict[str, str] = {"video_url": video_url, "caption": caption}
    if content_id:
        payload["content_id"] = content_id
    if platform:
        payload["platform"] = platform
    if handoff_object_key:
        payload["handoff_object_key"] = handoff_object_key
    if handoff_id:
        payload["handoff_id"] = handoff_id

    response = requests.post(
        webhook_url,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response


def _load_yaml_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    if not isinstance(loaded, dict):
        raise ValueError(f"Expected {config_path} to contain a YAML mapping at the top level.")

    return loaded


def _get_config_value(dotted_key: str, yaml_config: dict[str, Any] | None) -> Any:
    if yaml_config is None:
        return config.get(dotted_key)
    current: Any = yaml_config
    for part in dotted_key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        elif value:
            return str(value)
    return None
