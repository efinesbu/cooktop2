from __future__ import annotations

from pathlib import Path

from src.posters.instagram import InstagramPoster
from src.posters.youtube import YouTubePoster


class _FakeYouTubeRequest:
    def next_chunk(self):
        return None, {"id": "yt-video-123"}


class _FakeYouTubeVideos:
    def insert(self, part: str, body: dict, media_body) -> _FakeYouTubeRequest:
        return _FakeYouTubeRequest()


class _FakeYouTubeService:
    def videos(self) -> _FakeYouTubeVideos:
        return _FakeYouTubeVideos()


def test_youtube_upload_regression_for_retry_decorator(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")

    poster = YouTubePoster()
    monkeypatch.setattr(poster, "_get_service", lambda: _FakeYouTubeService())

    assert poster.upload(video_path, "Caption line", ["Velura"]) == "yt-video-123"


def test_instagram_upload_regression_for_retry_decorator(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")

    poster = InstagramPoster()
    monkeypatch.setattr(poster, "_get_public_url", lambda _: "https://example.com/clip.mp4")
    monkeypatch.setattr(poster, "_create_container", lambda client, video_url, caption: "container-123")
    monkeypatch.setattr(poster, "_wait_for_container", lambda client, container_id: None)
    monkeypatch.setattr(poster, "_publish_container", lambda client, container_id: "ig-media-123")

    assert poster.upload(video_path, "Caption line", ["Velura"]) == "ig-media-123"
