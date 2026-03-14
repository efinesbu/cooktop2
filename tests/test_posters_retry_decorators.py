from __future__ import annotations

from pathlib import Path

from google.auth.exceptions import RefreshError

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


def test_youtube_reauthorizes_when_cached_refresh_token_is_revoked(
    monkeypatch, tmp_path: Path
) -> None:
    secrets_path = tmp_path / "youtube_client_secrets.json"
    secrets_path.write_text('{"installed": {"client_id": "id"}}', encoding="utf-8")
    token_path = tmp_path / "youtube_token.json"
    token_path.write_text('{"refresh_token": "stale"}', encoding="utf-8")

    class _RevokedCreds:
        expired = True
        refresh_token = "stale-refresh-token"
        valid = False

        def refresh(self, request) -> None:
            raise RefreshError(
                "invalid_grant: Token has been expired or revoked.",
                {"error": "invalid_grant"},
            )

    class _FreshCreds:
        expired = False
        refresh_token = "fresh-refresh-token"
        valid = True

        def to_json(self) -> str:
            return '{"refresh_token": "fresh"}'

    class _FakeFlow:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def run_local_server(self, **kwargs):
            self.calls.append(kwargs)
            return _FreshCreds()

    fake_flow = _FakeFlow()

    def fake_get(key: str, default=None):
        values = {
            "youtube.client_secrets_file": str(secrets_path),
            "youtube.token_file": str(token_path),
            "youtube.login_hint": "team@example.com",
        }
        return values.get(key, default)

    monkeypatch.setattr("src.posters.youtube.config.get", fake_get)
    monkeypatch.setattr(
        "src.posters.youtube.Credentials.from_authorized_user_file",
        lambda path, scopes: _RevokedCreds(),
    )

    poster = YouTubePoster()
    monkeypatch.setattr(poster, "_build_oauth_flow", lambda _: fake_flow)

    creds = poster._load_credentials()

    assert isinstance(creds, _FreshCreds)
    assert fake_flow.calls == [
        {
            "port": 0,
            "redirect_uri_trailing_slash": False,
            "prompt": "select_account",
            "login_hint": "team@example.com",
        }
    ]
    assert token_path.read_text(encoding="utf-8") == '{"refresh_token": "fresh"}'


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
