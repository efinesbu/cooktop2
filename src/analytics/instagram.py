from __future__ import annotations

import logging

import httpx

from src import config
from src.analytics.base import BaseAnalyticsPuller
from src.models import Metric, Post

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


class InstagramAnalyticsPuller(BaseAnalyticsPuller):
    platform = "instagram"

    def __init__(self) -> None:
        self._access_token = config.get("instagram.access_token")
        if not self._access_token:
            raise ValueError("instagram.access_token not set in config")

    def fetch_metrics(self, post: Post) -> Metric | None:
        insights_resp = httpx.get(
            f"{GRAPH_API_BASE}/{post.post_id}/insights",
            params={
                "metric": "impressions,reach,saved,shares",
                "access_token": self._access_token,
            },
            timeout=15,
        )
        insights_resp.raise_for_status()
        insights_data = {
            item["name"]: item["values"][0]["value"]
            for item in insights_resp.json().get("data", [])
        }

        fields_resp = httpx.get(
            f"{GRAPH_API_BASE}/{post.post_id}",
            params={
                "fields": "like_count,comments_count",
                "access_token": self._access_token,
            },
            timeout=15,
        )
        fields_resp.raise_for_status()
        fields_data = fields_resp.json()

        return Metric(
            post_id=post.id,  # type: ignore[arg-type]
            platform=self.platform,
            views=insights_data.get("impressions", 0),
            likes=fields_data.get("like_count", 0),
            comments=fields_data.get("comments_count", 0),
            shares=insights_data.get("shares", 0),
            saves=insights_data.get("saved", 0),
        )
