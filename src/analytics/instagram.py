from __future__ import annotations

import logging

import httpx

from src import config
from src.analytics.base import BaseAnalyticsPuller
from src.models import Metric, Post

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
REEL_INSIGHT_METRICS = "views,reach,saved,shares"


class InstagramAnalyticsPuller(BaseAnalyticsPuller):
    platform = "instagram"

    def __init__(self) -> None:
        self._access_token = config.get("instagram.access_token")
        if not self._access_token:
            raise ValueError("instagram.access_token not set in config")

    def fetch_metrics(self, post: Post) -> Metric | None:
        if not post.post_id:
            logger.warning("Skipping Instagram analytics for post %s with no media id", post.id)
            return None
        if post.post_id.startswith("make:"):
            logger.warning(
                "Skipping Instagram analytics for handoff id %s. Persist the final Instagram media id "
                "from Make back into posts.post_id to enable metrics pulls.",
                post.post_id,
            )
            return None

        insights_resp = httpx.get(
            f"{GRAPH_API_BASE}/{post.post_id}/insights",
            params={
                "metric": REEL_INSIGHT_METRICS,
                "access_token": self._access_token,
            },
            timeout=15,
        )
        if 400 <= insights_resp.status_code < 500 and insights_resp.status_code != 429:
            logger.warning(
                "Skipping Instagram analytics for media %s due to permanent Graph API error %s: %s",
                post.post_id,
                insights_resp.status_code,
                insights_resp.text,
            )
            return None
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
        if 400 <= fields_resp.status_code < 500 and fields_resp.status_code != 429:
            logger.warning(
                "Skipping Instagram analytics fields for media %s due to permanent Graph API error %s: %s",
                post.post_id,
                fields_resp.status_code,
                fields_resp.text,
            )
            return None
        fields_resp.raise_for_status()
        fields_data = fields_resp.json()

        return Metric(
            post_id=post.id,  # type: ignore[arg-type]
            platform=self.platform,
            views=insights_data.get("views", 0),
            likes=fields_data.get("like_count", 0),
            comments=fields_data.get("comments_count", 0),
            shares=insights_data.get("shares", 0),
            saves=insights_data.get("saved", 0),
        )
