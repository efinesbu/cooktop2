from __future__ import annotations

from pathlib import Path
import sys
import types

from click.testing import CliRunner


class _UnusedPoster:
    def __init__(self, *args, **kwargs) -> None:
        pass


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
sys.modules.setdefault("src.analytics", types.SimpleNamespace(PULLERS={}))
sys.modules.setdefault(
    "src.image_generator",
    types.SimpleNamespace(generate_starting_image=lambda *args, **kwargs: None),
)
sys.modules.setdefault(
    "src.shopify",
    types.SimpleNamespace(sync_products=lambda *args, **kwargs: []),
)
sys.modules.setdefault(
    "src.prompt_generator",
    types.SimpleNamespace(generate_content=lambda *args, **kwargs: (None, {})),
)
sys.modules.setdefault(
    "src.posters.youtube",
    types.SimpleNamespace(YouTubePoster=_UnusedPoster),
)
sys.modules.setdefault(
    "src.posters.instagram",
    types.SimpleNamespace(InstagramPoster=_UnusedPoster),
)
sys.modules.setdefault(
    "src.posters.tiktok",
    types.SimpleNamespace(TikTokPoster=_UnusedPoster),
)
sys.modules.setdefault(
    "src.posters.x",
    types.SimpleNamespace(XPoster=_UnusedPoster),
)

import cli as cli_module
from src import db
from src.models import Content, PlatformPayload, Product


class FakeYouTubePoster:
    def upload(self, video_path: Path, caption: str, hashtags: list[str]) -> str:
        assert video_path.exists()
        assert caption == "Launch caption"
        assert hashtags == ["launch", "velura"]
        return "yt-123"


def test_approve_schedule_and_post_due(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "posting": {"stagger_minutes": {"youtube": 0}},
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
            "data_root": str(tmp_path / "velura-data"),
        },
    )

    product = Product(sku="serum-x", name="Serum X")
    db.upsert_product(product)

    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    content = Content(
        id="content-123",
        product_sku=product.sku,
        theme="benefit",
        hook_type="question",
        video_local_path=str(video_path),
    )
    db.insert_content(content)
    db.upsert_platform_payload(
        PlatformPayload(
            content_id=content.id,
            platform="youtube",
            caption="Launch caption",
            hashtags="launch,velura",
            utm_url="https://example.com/products/serum-x?utm_content=content-123",
        )
    )

    monkeypatch.setitem(cli_module.POSTERS, "youtube", FakeYouTubePoster)
    runner = CliRunner()

    approved = runner.invoke(cli_module.cli, ["approve", "--content-id", content.id])
    assert approved.exit_code == 0

    scheduled = runner.invoke(cli_module.cli, ["schedule", "--content-id", content.id])
    assert scheduled.exit_code == 0
    payload = db.get_platform_payload(content.id, "youtube")
    assert payload is not None
    assert payload.status == "scheduled"
    assert payload.publish_at is not None

    posted = runner.invoke(cli_module.cli, ["post-due"])
    assert posted.exit_code == 0

    payload = db.get_platform_payload(content.id, "youtube")
    assert payload is not None
    assert payload.status == "posted"

    posts = db.list_posts_for_content(content.id)
    assert len(posts) == 1
    assert posts[0].platform == "youtube"
    assert posts[0].caption == "Launch caption"


def test_schedule_and_post_due_skip_disabled_platform_payloads(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "posting": {"stagger_minutes": {"youtube": 0, "instagram": 0}},
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
            "data_root": str(tmp_path / "velura-data"),
        },
    )

    product = Product(sku="serum-x", name="Serum X")
    db.upsert_product(product)

    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    content = Content(
        id="content-456",
        product_sku=product.sku,
        theme="benefit",
        hook_type="question",
        video_local_path=str(video_path),
    )
    db.insert_content(content)
    db.approve_content(content.id)
    db.upsert_platform_payload(
        PlatformPayload(
            content_id=content.id,
            platform="youtube",
            caption="Launch caption",
            hashtags="launch,velura",
            utm_url="https://example.com/products/serum-x?utm_content=content-456",
        )
    )
    db.upsert_platform_payload(
        PlatformPayload(
            content_id=content.id,
            platform="instagram",
            caption="IG caption",
            hashtags="launch,velura",
            utm_url="https://example.com/products/serum-x?utm_content=content-456",
        )
    )

    monkeypatch.setitem(cli_module.POSTERS, "youtube", FakeYouTubePoster)
    runner = CliRunner()

    scheduled = runner.invoke(cli_module.cli, ["schedule", "--content-id", content.id])
    assert scheduled.exit_code == 0

    youtube_payload = db.get_platform_payload(content.id, "youtube")
    instagram_payload = db.get_platform_payload(content.id, "instagram")
    assert youtube_payload is not None
    assert youtube_payload.status == "scheduled"
    assert youtube_payload.publish_at is not None
    assert instagram_payload is not None
    assert instagram_payload.status == "pending"
    assert instagram_payload.publish_at is None
    assert instagram_payload.last_error == "Platform not enabled in config"

    posted = runner.invoke(cli_module.cli, ["post-due"])
    assert posted.exit_code == 0

    youtube_payload = db.get_platform_payload(content.id, "youtube")
    instagram_payload = db.get_platform_payload(content.id, "instagram")
    assert youtube_payload is not None
    assert youtube_payload.status == "posted"
    assert instagram_payload is not None
    assert instagram_payload.status == "pending"
    assert instagram_payload.publish_at is None
    assert instagram_payload.last_error == "Platform not enabled in config"

    posts = db.list_posts_for_content(content.id)
    assert len(posts) == 1
    assert posts[0].platform == "youtube"


def test_add_product_command_saves_manual_catalog_entry(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "src.config._config",
        {
            "site_url": "https://veluraesthetics.com",
            "data_root": str(tmp_path / "velura-data"),
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.cli,
        [
            "add-product",
            "--sku",
            "brow-pomade",
            "--name",
            "Brow Pomade",
            "--category",
            "beauty",
            "--price",
            "29.0",
            "--url",
            "veluraesthetics.com/products/brow-pomade",
        ],
    )

    assert result.exit_code == 0
    product = db.get_product("brow-pomade")
    assert product is not None
    assert product.name == "Brow Pomade"
    assert product.product_url == "https://veluraesthetics.com/products/brow-pomade"
    assert product.generation_ready is False
