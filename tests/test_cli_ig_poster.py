from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

from click.testing import CliRunner

import pytest

sys.modules.setdefault(
    "tweepy",
    types.SimpleNamespace(
        TooManyRequests=Exception,
        TwitterServerError=Exception,
        OAuth1UserHandler=object,
        API=object,
        Client=object,
    ),
)
sys.modules.setdefault(
    "src.shopify",
    types.SimpleNamespace(sync_products=lambda *args, **kwargs: []),
)
sys.modules.setdefault(
    "src.prompt_generator",
    types.SimpleNamespace(
        generate_content=lambda *args, **kwargs: (None, {}),
        generate_paid_variant_captions=lambda *args, **kwargs: [],
    ),
)
import cli as cli_module
from src.models import Content, PlatformPayload, Post, Product


def test_resolve_ig_poster_flag_true_when_explicit() -> None:
    assert cli_module._resolve_ig_poster_flag(True) is True


def test_resolve_ig_poster_flag_true_when_config_phone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(key: str, default=None):
        if key == "instagram.posting_method":
            return "phone"
        return default

    monkeypatch.setattr(cli_module.config, "get", fake_get)
    assert cli_module._resolve_ig_poster_flag(False) is True


def test_resolve_ig_poster_flag_false_without_flag_or_phone_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(key: str, default=None):
        if key == "instagram.posting_method":
            return None
        return default

    monkeypatch.setattr(cli_module.config, "get", fake_get)
    assert cli_module._resolve_ig_poster_flag(False) is False


def test_post_due_threads_use_ig_poster_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, bool] = {}

    def fake_init() -> None:
        return None

    def fake_wait(**kwargs: object) -> None:
        return None

    def fake_post_due(*, use_ig_poster: bool = False) -> None:
        captured["use_ig_poster"] = use_ig_poster

    monkeypatch.setattr(cli_module, "_init", fake_init)
    monkeypatch.setattr(cli_module, "_wait_until_post_window_start", fake_wait)
    monkeypatch.setattr(cli_module, "_post_due", fake_post_due)

    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["post-due", "--ig-poster"])
    assert result.exit_code == 0
    assert captured.get("use_ig_poster") is True

    captured.clear()
    monkeypatch.setattr(
        cli_module.config,
        "get",
        lambda key, default=None: "phone" if key == "instagram.posting_method" else default,
    )
    result2 = runner.invoke(cli_module.cli, ["post-due"])
    assert result2.exit_code == 0
    assert captured.get("use_ig_poster") is True


def test_post_platform_payload_instagram_uses_ig_phone_when_flag(
    mock_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = Path(mock_config["data_root"])
    videos_root = data_root / "videos"
    videos_root.mkdir(parents=True, exist_ok=True)
    video = videos_root / "ig.mp4"
    video.write_bytes(b"v")

    product = Product(sku="sku-1", name="P")
    content = Content(
        id="c-ig",
        product_sku="sku-1",
        theme="benefit_spotlight",
        hook_type="bold_claim",
        video_local_path=str(video),
    )
    payload = PlatformPayload(
        content_id=content.id,
        platform="instagram",
        caption="cap",
        hashtags="a,b",
    )

    ig_upload = MagicMock(return_value="ig_phone:queued1")

    class FakeIgPhonePoster:
        def __init__(self) -> None:
            pass

        def upload(self, *args, **kwargs):
            return ig_upload(*args, **kwargs)

    def boom_init(*_a, **_k):
        raise AssertionError("Instagram Graph API poster should not be used")

    monkeypatch.setattr("src.posters.ig_phone.IgPhonePoster", FakeIgPhonePoster)
    monkeypatch.setitem(
        cli_module.POSTERS,
        "instagram",
        type("BoomInstagramPoster", (), {"__init__": boom_init}),
    )

    inserted: list[Post] = []

    def fake_insert(p: Post) -> int:
        inserted.append(p)
        return 42

    monkeypatch.setattr(cli_module.db, "insert_post", fake_insert)

    post = cli_module._post_platform_payload(
        payload, content, product, use_ig_poster=True
    )

    assert post.post_id == "ig_phone:queued1"
    ig_upload.assert_called_once()
    assert inserted and inserted[0].post_id == "ig_phone:queued1"


def test_post_platform_payload_youtube_unchanged_when_ig_flag_true(
    mock_config: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = Path(mock_config["data_root"])
    videos_root = data_root / "videos"
    videos_root.mkdir(parents=True, exist_ok=True)
    video = videos_root / "yt.mp4"
    video.write_bytes(b"v")

    product = Product(sku="sku-1", name="P")
    content = Content(
        id="c-yt",
        product_sku="sku-1",
        theme="benefit_spotlight",
        hook_type="bold_claim",
        video_local_path=str(video),
    )
    payload = PlatformPayload(
        content_id=content.id,
        platform="youtube",
        caption="cap",
        hashtags="a,b",
    )

    yt_calls: list[bool] = []

    class FakeYouTubePoster:
        def __init__(self) -> None:
            pass

        def upload(self, *args, **kwargs):
            yt_calls.append(True)
            return "yt:remote"

    ig_instantiated = False

    class FakeIgPhonePoster:
        def __init__(self) -> None:
            nonlocal ig_instantiated
            ig_instantiated = True

        def upload(self, *args, **kwargs):
            raise AssertionError("IgPhonePoster must not run for YouTube")

    # POSTERS holds class references from import time; patch the mapping used at runtime.
    monkeypatch.setitem(cli_module.POSTERS, "youtube", FakeYouTubePoster)
    monkeypatch.setattr("src.posters.ig_phone.IgPhonePoster", FakeIgPhonePoster)

    monkeypatch.setattr(cli_module.db, "insert_post", lambda p: 1)

    post = cli_module._post_platform_payload(
        payload, content, product, use_ig_poster=True
    )

    assert post.post_id == "yt:remote"
    assert yt_calls == [True]
    assert ig_instantiated is False
