from __future__ import annotations

from pathlib import Path

from src.posters.instagram import InstagramPoster
from src.posters.youtube import YouTubePoster


class _FakeYouTubeRequest:
    def next_chunk(self):
        return None, {"id": "yt-video-123"}


class _FakeYouTubeVideos:
    def __init__(self, captured_body: list | None = None):
        self._captured_body = captured_body

    def insert(self, part: str, body: dict, media_body) -> _FakeYouTubeRequest:
        if self._captured_body is not None:
            self._captured_body.append(body)
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


def test_youtube_caption_ends_with_link_in_bio(monkeypatch, tmp_path: Path) -> None:
    """YouTube captions must end with 'Link in bio'."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")

    captured_body: list = []
    fake_videos = _FakeYouTubeVideos(captured_body)
    fake_service = type("_FakeService", (), {"videos": lambda self: fake_videos})()

    poster = YouTubePoster()
    monkeypatch.setattr(poster, "_get_service", lambda: fake_service)

    poster.upload(video_path, "Glow faster with Serum X", ["Velura"])

    assert len(captured_body) == 1
    description = captured_body[0]["snippet"]["description"]
    # Caption ends with "Link in bio" (description also appends "\n\n#Shorts")
    assert "link in bio" in description.lower()
    assert description.lower().split("#shorts")[0].rstrip().endswith("link in bio")
    assert "Glow faster with Serum X" in description


def test_youtube_caption_link_in_bio_not_duplicated(monkeypatch, tmp_path: Path) -> None:
    """When caption already ends with 'Link in bio', do not duplicate it."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")

    captured_body: list = []
    fake_videos = _FakeYouTubeVideos(captured_body)
    fake_service = type("_FakeService", (), {"videos": lambda self: fake_videos})()

    poster = YouTubePoster()
    monkeypatch.setattr(poster, "_get_service", lambda: fake_service)

    poster.upload(video_path, "Get it now. Link in bio", ["Velura"])

    assert len(captured_body) == 1
    description = captured_body[0]["snippet"]["description"]
    assert description.count("Link in bio") == 1


def test_instagram_upload_regression_for_retry_decorator(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")

    poster = InstagramPoster()
    poster._use_make_bridge = False
    monkeypatch.setattr(poster, "_get_public_url", lambda _: "https://example.com/clip.mp4")
    monkeypatch.setattr(poster, "_create_container", lambda client, video_url, caption: "container-123")
    monkeypatch.setattr(poster, "_wait_for_container", lambda client, container_id: None)
    monkeypatch.setattr(poster, "_publish_container", lambda client, container_id: "ig-media-123")

    assert poster.upload(video_path, "Caption line", ["Velura"]) == "ig-media-123"
