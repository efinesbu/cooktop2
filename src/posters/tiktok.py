from __future__ import annotations

import logging
import math
from pathlib import Path

import httpx

from src import config
from src.posters.base import (
    BasePoster,
    TransientAPIError,
    TRANSIENT_STATUS_CODES,
    retry,
)

logger = logging.getLogger(__name__)

_API_BASE = "https://open.tiktokapis.com/v2"
_CHUNK_SIZE = 10_000_000  # ~10 MB per TikTok docs

_TRANSIENT = (TransientAPIError, httpx.ConnectError, httpx.TimeoutException)


class TikTokPoster(BasePoster):
    platform = "tiktok"

    def __init__(self) -> None:
        self._client_key: str = config.get("tiktok.client_key", "")
        self._client_secret: str = config.get("tiktok.client_secret", "")
        self._access_token: str = config.get("tiktok.access_token", "")
        self._refresh_token: str = config.get("tiktok.refresh_token", "")

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code in TRANSIENT_STATUS_CODES:
            raise TransientAPIError(resp.status_code, resp.text)
        resp.raise_for_status()

    # -- OAuth2 token refresh ------------------------------------------------

    @retry(transient_exceptions=_TRANSIENT)
    def _refresh_access_token(self) -> None:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{_API_BASE}/oauth/token/",
                json={
                    "client_key": self._client_key,
                    "client_secret": self._client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                },
            )
        self._raise_for_status(resp)
        data = resp.json()
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)

    # -- Upload steps --------------------------------------------------------

    @retry(transient_exceptions=_TRANSIENT)
    def _init_upload(self, video_size: int, title: str) -> tuple[str, str]:
        """Returns (publish_id, upload_url)."""
        total_chunks = max(1, math.ceil(video_size / _CHUNK_SIZE))
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{_API_BASE}/post/publish/video/init/",
                headers=self._auth_headers(),
                json={
                    "post_info": {
                        "title": title[:150],
                        "privacy_level": "PUBLIC_TO_EVERYONE",
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": video_size,
                        "chunk_size": _CHUNK_SIZE,
                        "total_chunk_count": total_chunks,
                    },
                },
            )
        self._raise_for_status(resp)
        body = resp.json()

        err = body.get("error", {})
        if err.get("code", "ok") != "ok":
            raise RuntimeError(
                f"TikTok init error {err['code']}: {err.get('message')}"
            )
        return body["data"]["publish_id"], body["data"]["upload_url"]

    @retry(transient_exceptions=_TRANSIENT)
    def _upload_chunk(
        self,
        upload_url: str,
        chunk: bytes,
        offset: int,
        total_size: int,
    ) -> None:
        end = offset + len(chunk) - 1
        with httpx.Client(timeout=120) as client:
            resp = client.put(
                upload_url,
                content=chunk,
                headers={
                    "Content-Range": f"bytes {offset}-{end}/{total_size}",
                    "Content-Type": "video/mp4",
                },
            )
        self._raise_for_status(resp)

    def _upload_video_file(
        self, upload_url: str, video_path: Path, video_size: int
    ) -> None:
        with open(video_path, "rb") as fh:
            offset = 0
            while offset < video_size:
                chunk = fh.read(_CHUNK_SIZE)
                if not chunk:
                    break
                self._upload_chunk(upload_url, chunk, offset, video_size)
                offset += len(chunk)

    # -- Public interface ----------------------------------------------------

    def upload(self, video_path: Path, caption: str, hashtags: list[str]) -> str:
        video_size = video_path.stat().st_size

        title = caption
        if hashtags:
            title = f"{caption} {' '.join(f'#{t}' for t in hashtags)}"

        try:
            publish_id, upload_url = self._init_upload(video_size, title)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            self._refresh_access_token()
            publish_id, upload_url = self._init_upload(video_size, title)

        self._upload_video_file(upload_url, video_path, video_size)
        logger.info("TikTok video published — publish_id=%s", publish_id)
        return publish_id
