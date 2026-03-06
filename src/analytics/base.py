from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from src import db
from src.models import Metric, Post

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE = 2.0


class BaseAnalyticsPuller(ABC):
    platform: str

    @abstractmethod
    def fetch_metrics(self, post: Post) -> Metric | None:
        """Fetch latest metrics for a post from the platform API."""

    def _fetch_with_retry(self, post: Post) -> Metric | None:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return self.fetch_metrics(post)
            except Exception:
                if attempt == MAX_RETRIES:
                    logger.exception(
                        "Failed to fetch metrics for post %s on %s after %d attempts",
                        post.post_id, self.platform, MAX_RETRIES,
                    )
                    return None
                delay = BACKOFF_BASE ** attempt
                logger.warning(
                    "Attempt %d/%d failed for post %s on %s, retrying in %.1fs",
                    attempt, MAX_RETRIES, post.post_id, self.platform, delay,
                )
                time.sleep(delay)
        return None

    def pull(self, posts: list[Post] | None = None) -> int:
        if posts is None:
            posts = db.list_recent_posts()

        platform_posts = [p for p in posts if p.platform == self.platform]
        count = 0

        for post in platform_posts:
            metric = self._fetch_with_retry(post)
            if metric is not None:
                db.insert_metric(metric)
                count += 1

        logger.info("Pulled %d metrics for %s", count, self.platform)
        return count
