from __future__ import annotations

import logging

import httpx

from src import config
from src.analytics.base import BaseAnalyticsPuller
from src.models import Metric, Post

logger = logging.getLogger(__name__)

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"


class TikTokAnalyticsPuller(BaseAnalyticsPuller):
    platform = "tiktok"

    def __init__(self) -> None:
        self._client_key = config.get("tiktok.client_key")
        self._client_secret = config.get("tiktok.client_secret")
        if not self._client_key or not self._client_secret:
            raise ValueError("tiktok.client_key and tiktok.client_secret must be set in config")
        self._access_token: str | None = None

    def _ensure_token(self) -> str:
        if self._access_token:
            return self._access_token

        resp = httpx.post(
            f"{TIKTOK_API_BASE}/oauth/token/",
            data={
                "client_key": self._client_key,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
            timeout=15,
        )
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]
        return self._access_token

    def fetch_metrics(self, post: Post) -> Metric | None:
        token = self._ensure_token()
        resp = httpx.post(
            f"{TIKTOK_API_BASE}/video/query/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "filters": {"video_ids": [post.post_id]},
                "fields": ["id", "view_count", "like_count", "comment_count", "share_count"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        videos = data.get("videos", [])

        if not videos:
            logger.warning("No TikTok data for video %s", post.post_id)
            return None

        video = videos[0]
        return Metric(
            post_id=post.id,  # type: ignore[arg-type]
            platform=self.platform,
            views=video.get("view_count", 0),
            likes=video.get("like_count", 0),
            comments=video.get("comment_count", 0),
            shares=video.get("share_count", 0),
        )
