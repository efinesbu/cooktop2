from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from src import config
from src.make_bridge import bridge_video_to_make, is_bridge_configured
from src.posters.base import BasePoster, TransientError, retry_transient

logger = logging.getLogger(__name__)

_GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
_CONTAINER_POLL_INTERVAL = 5
_CONTAINER_POLL_MAX_ATTEMPTS = 60
_TRANSIENT_STATUSES = {429, 500, 502, 503}


class InstagramPoster(BasePoster):
    platform = "instagram"

    def __init__(self) -> None:
        self._access_token: str = config.get("instagram.access_token", "")
        self._account_id: str = config.get("instagram.instagram_account_id", "")
        self._gcs_bucket: str | None = config.get("instagram.gcs_bucket")
        self._use_make_bridge = is_bridge_configured()

    def _get_public_url(self, video_path: Path) -> str:
        """Return a publicly-accessible URL for the local video file.

        The Instagram Graph API requires a public URL to ingest video.
        If ``instagram.gcs_bucket`` is set, the file is uploaded to GCS
        and a public URL is returned.  Otherwise a NotImplementedError
        is raised so callers know they must configure a hosting provider.
        """
        if self._gcs_bucket:
            from google.cloud import storage

            client = storage.Client()
            bucket = client.bucket(self._gcs_bucket)
            blob_name = f"ig-uploads/{video_path.stem}_{int(time.time())}.mp4"
            blob = bucket.blob(blob_name)
            blob.upload_from_filename(str(video_path), content_type="video/mp4")
            blob.make_public()
            return blob.public_url

        raise NotImplementedError(
            "Instagram Graph API requires a publicly-accessible video URL. "
            "Set 'instagram.gcs_bucket' in config.yaml to enable automatic "
            "upload to Google Cloud Storage, or subclass InstagramPoster and "
            "override _get_public_url() for your hosting provider."
        )

    @retry_transient()
    def upload(self, video_path: Path, caption: str, hashtags: list[str]) -> str:
        full_caption = f"{caption}\n\n{' '.join(f'#{h}' for h in hashtags)}"

        if self._use_make_bridge:
            result = bridge_video_to_make(video_path, full_caption)
            handoff_id = f"make:{result.object_key}"
            logger.info("Handed Instagram payload to Make bridge: %s", handoff_id)
            return handoff_id

        video_url = self._get_public_url(video_path)

        try:
            with httpx.Client(timeout=120) as client:
                container_id = self._create_container(client, video_url, full_caption)
                self._wait_for_container(client, container_id)
                media_id = self._publish_container(client, container_id)
        except httpx.TransportError as exc:
            raise TransientError(str(exc)) from exc

        logger.info("Published Instagram Reel: %s", media_id)
        return media_id

    # ------------------------------------------------------------------
    # Graph API helpers
    # ------------------------------------------------------------------

    def _create_container(
        self, client: httpx.Client, video_url: str, caption: str
    ) -> str:
        resp = client.post(
            f"{_GRAPH_API_BASE}/{self._account_id}/media",
            params={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "access_token": self._access_token,
            },
        )
        _check_response(resp)
        return resp.json()["id"]

    def _wait_for_container(self, client: httpx.Client, container_id: str) -> None:
        for _ in range(_CONTAINER_POLL_MAX_ATTEMPTS):
            resp = client.get(
                f"{_GRAPH_API_BASE}/{container_id}",
                params={
                    "fields": "status_code",
                    "access_token": self._access_token,
                },
            )
            _check_response(resp)
            status = resp.json().get("status_code")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise RuntimeError(
                    f"Instagram container {container_id} failed processing"
                )
            time.sleep(_CONTAINER_POLL_INTERVAL)

        raise TimeoutError(
            f"Instagram container {container_id} did not finish "
            f"within {_CONTAINER_POLL_INTERVAL * _CONTAINER_POLL_MAX_ATTEMPTS}s"
        )

    def _publish_container(self, client: httpx.Client, container_id: str) -> str:
        resp = client.post(
            f"{_GRAPH_API_BASE}/{self._account_id}/media_publish",
            params={
                "creation_id": container_id,
                "access_token": self._access_token,
            },
        )
        _check_response(resp)
        return resp.json()["id"]


def _check_response(resp: httpx.Response) -> None:
    if resp.status_code in _TRANSIENT_STATUSES:
        raise TransientError(f"Instagram API {resp.status_code}: {resp.text}")
    resp.raise_for_status()
