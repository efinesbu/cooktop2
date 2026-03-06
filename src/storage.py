from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from src import config

logger = logging.getLogger(__name__)


def ensure_dirs() -> None:
    config.videos_dir().mkdir(parents=True, exist_ok=True)
    config.product_images_dir().mkdir(parents=True, exist_ok=True)


def video_path(product_sku: str, content_id: str) -> Path:
    parent = config.videos_dir() / product_sku
    parent.mkdir(parents=True, exist_ok=True)
    return parent / f"{content_id}.mp4"


def _file_age_days(path: Path) -> float:
    mtime = path.stat().st_mtime
    age_seconds = time.time() - mtime
    return age_seconds / 86400


def _upload_to_gcs(local_path: Path, bucket_name: str) -> None:
    from google.cloud import storage as gcs

    client = gcs.Client()
    bucket = client.bucket(bucket_name)

    blob_name = f"archived-videos/{local_path.parent.name}/{local_path.name}"
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path))
    logger.info("Uploaded %s to gs://%s/%s", local_path.name, bucket_name, blob_name)


def archive_old_videos(
    days: int = 7,
    top_performer_ids: set[str] | None = None,
) -> list[str]:
    top_performer_ids = top_performer_ids or set()
    videos_root = config.videos_dir()
    if not videos_root.exists():
        return []

    bucket_name = config.get("gcs.bucket_name")
    archived: list[str] = []

    for mp4 in videos_root.rglob("*.mp4"):
        content_id = mp4.stem
        if content_id in top_performer_ids:
            continue
        if _file_age_days(mp4) < days:
            continue

        if bucket_name:
            try:
                _upload_to_gcs(mp4, bucket_name)
            except Exception:
                logger.exception("Failed to upload %s to GCS — skipping deletion", mp4)
                continue

        mp4.unlink()
        archived.append(str(mp4))
        logger.debug("Archived video: %s", mp4)

    if archived:
        logger.info("Archived %d old videos (threshold=%d days)", len(archived), days)

    return archived
