from __future__ import annotations

import logging

import httpx

from src import config
from src.analytics.base import BaseAnalyticsPuller
from src.models import Metric, Post

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeAnalyticsPuller(BaseAnalyticsPuller):
    platform = "youtube"

    def __init__(self) -> None:
        self._api_key = config.get("youtube.api_key")
        if not self._api_key:
            raise ValueError("youtube.api_key not set in config")

    def fetch_metrics(self, post: Post) -> Metric | None:
        resp = httpx.get(
            f"{YOUTUBE_API_BASE}/videos",
            params={
                "part": "statistics",
                "id": post.post_id,
                "key": self._api_key,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        if not items:
            logger.warning("No YouTube data for video %s", post.post_id)
            return None

        stats = items[0]["statistics"]
        return Metric(
            post_id=post.id,  # type: ignore[arg-type]
            platform=self.platform,
            views=int(stats.get("viewCount", 0)),
            likes=int(stats.get("likeCount", 0)),
            comments=int(stats.get("commentCount", 0)),
        )
