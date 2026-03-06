from __future__ import annotations

import logging

import tweepy

from src import config
from src.analytics.base import BaseAnalyticsPuller
from src.models import Metric, Post

logger = logging.getLogger(__name__)


class XAnalyticsPuller(BaseAnalyticsPuller):
    platform = "x"

    def __init__(self) -> None:
        api_key = config.get("x.api_key")
        api_secret = config.get("x.api_secret")
        access_token = config.get("x.access_token")
        access_token_secret = config.get("x.access_token_secret")

        if not all([api_key, api_secret, access_token, access_token_secret]):
            raise ValueError("x.api_key, x.api_secret, x.access_token, x.access_token_secret must be set")

        self._client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
        )

    def fetch_metrics(self, post: Post) -> Metric | None:
        response = self._client.get_tweet(
            post.post_id,
            tweet_fields=["public_metrics"],
        )

        if not response.data:
            logger.warning("No X data for tweet %s", post.post_id)
            return None

        metrics = response.data.public_metrics
        return Metric(
            post_id=post.id,  # type: ignore[arg-type]
            platform=self.platform,
            views=metrics.get("impression_count", 0),
            likes=metrics.get("like_count", 0),
            shares=metrics.get("retweet_count", 0),
            comments=metrics.get("reply_count", 0),
        )
