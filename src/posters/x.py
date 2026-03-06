from __future__ import annotations

import logging
from pathlib import Path

import tweepy

from src import config
from src.posters.base import BasePoster, retry

logger = logging.getLogger(__name__)

_TRANSIENT = (
    tweepy.TooManyRequests,
    tweepy.TwitterServerError,
    ConnectionError,
    TimeoutError,
)


class XPoster(BasePoster):
    platform = "x"

    def __init__(self) -> None:
        api_key = config.get("x.api_key", "")
        api_secret = config.get("x.api_secret", "")
        access_token = config.get("x.access_token", "")
        access_token_secret = config.get("x.access_token_secret", "")

        auth = tweepy.OAuth1UserHandler(
            api_key, api_secret, access_token, access_token_secret,
        )
        self._api = tweepy.API(auth)
        self._client = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
        )

    @retry(transient_exceptions=_TRANSIENT)
    def _upload_media(self, video_path: Path) -> int:
        media = self._api.media_upload(
            filename=str(video_path),
            media_category="tweet_video",
            chunked=True,
            wait_for_async_finalize=True,
        )
        return media.media_id

    @retry(transient_exceptions=_TRANSIENT)
    def _create_tweet(self, text: str, media_id: int) -> str:
        resp = self._client.create_tweet(text=text, media_ids=[media_id])
        return str(resp.data["id"])

    def upload(self, video_path: Path, caption: str, hashtags: list[str]) -> str:
        media_id = self._upload_media(video_path)

        tag_suffix = " ".join(f"#{h}" for h in hashtags) if hashtags else ""
        text = f"{caption}\n\n{tag_suffix}".strip() if tag_suffix else caption

        tweet_id = self._create_tweet(text, media_id)
        logger.info("Tweet published — id=%s", tweet_id)
        return tweet_id
