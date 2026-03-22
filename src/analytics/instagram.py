from __future__ import annotations

import logging

import httpx

from src import config
from src.analytics.base import BaseAnalyticsPuller
from src.models import Metric, Post

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
BASE_INSIGHT_METRICS = "views,reach,saved,shares"
WATCH_TIME_INSIGHT_METRIC = "ig_reels_avg_watch_time"


def _parse_insights(payload: dict) -> dict[str, int | float]:
    insights: dict[str, int | float] = {}
    for item in payload.get("data", []):
        name = item.get("name")
        values = item.get("values") or []
        if not name or not values:
            continue
        first_value = values[0]
        if not isinstance(first_value, dict):
            continue
        value = first_value.get("value")
        if value is None:
            continue
        insights[name] = value
    return insights


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
                "metric": BASE_INSIGHT_METRICS,
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
        insights_data = _parse_insights(insights_resp.json())

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

        avg_watch_time = self._fetch_avg_watch_time(post.post_id)

        return Metric(
            post_id=post.id,  # type: ignore[arg-type]
            platform=self.platform,
            views=insights_data.get("views", 0),
            likes=fields_data.get("like_count", 0),
            comments=fields_data.get("comments_count", 0),
            shares=insights_data.get("shares", 0),
            saves=insights_data.get("saved", 0),
            avg_watch_time=avg_watch_time,
        )

    def _fetch_avg_watch_time(self, media_id: str) -> float | None:
        try:
            watch_resp = httpx.get(
                f"{GRAPH_API_BASE}/{media_id}/insights",
                params={
                    "metric": WATCH_TIME_INSIGHT_METRIC,
                    "access_token": self._access_token,
                },
                timeout=15,
            )
            if 400 <= watch_resp.status_code < 500 and watch_resp.status_code != 429:
                logger.info(
                    "Instagram watch-time insight unavailable for media %s (%s): %s",
                    media_id,
                    watch_resp.status_code,
                    watch_resp.text,
                )
                return None
            watch_resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Skipping Instagram watch-time insight for media %s: %s", media_id, exc)
            return None

        watch_data = _parse_insights(watch_resp.json())
        value = watch_data.get(WATCH_TIME_INSIGHT_METRIC)
        if value is None:
            return None
        return float(value)
