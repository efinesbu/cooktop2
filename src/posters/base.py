from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

from src import config, db
from src.models import Content, Post, Product
from src.utm import build_full_utm_link

logger = logging.getLogger(__name__)

T = TypeVar("T")

TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class TransientAPIError(Exception):
    """Wraps a transient HTTP error (429 / 5xx) so the retry decorator can catch it."""

    def __init__(self, status_code: int, detail: str = ""):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {detail}")


def retry(
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    transient_exceptions: tuple[type[Exception], ...] = (
        ConnectionError,
        TimeoutError,
        TransientAPIError,
    ),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Exponential-backoff retry for transient failures."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except transient_exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts - 1:
                        break
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        "%s attempt %d/%d failed (%s) — retrying in %.1fs",
                        func.__name__, attempt + 1, max_attempts, exc, delay,
                    )
                    time.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


class BasePoster(ABC):
    platform: str

    @abstractmethod
    def upload(self, video_path: Path, caption: str, hashtags: list[str]) -> str:
        """Upload video and return the platform-specific post ID."""

    def post(
        self,
        content: Content,
        product: Product,
        captions: dict[str, str],
        hashtags: list[str],
    ) -> Post:
        """Full workflow: build UTM link, format caption, upload, persist to DB."""
        utm_url = build_full_utm_link(content, product)
        caption = captions.get(self.platform, "")

        if not content.video_local_path:
            raise FileNotFoundError("Content has no video_local_path set")
        video_path = Path(content.video_local_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        post_id = self.upload(video_path, caption, hashtags)

        post = Post(
            content_id=content.id,
            platform=self.platform,
            post_id=post_id,
            caption=caption,
            hashtags=",".join(hashtags),
            utm_url=utm_url,
        )
        post.id = db.insert_post(post)
        return post


TransientError = TransientAPIError
retry_transient = retry
