from __future__ import annotations

import importlib
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
    types.SimpleNamespace(
        generate_content=lambda *args, **kwargs: (None, {}),
        generate_paid_variant_captions=lambda *args, **kwargs: [],
    ),
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
from src.make_bridge import BridgeResult
from src.models import Content, PlatformPayload, Product


class FakeYouTubePoster:
    def upload(self, video_path: Path, caption: str, hashtags: list[str]) -> str:
        assert video_path.exists()
        assert caption == "Launch caption"
        assert hashtags == ["launch", "velura"]
        return "yt-123"


def _seed_approved_youtube_content(
    content_id: str,
    product: Product,
    video_path: Path,
) -> None:
    content = Content(
        id=content_id,
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
            utm_url=f"https://example.com/products/{product.sku}?utm_content={content.id}",
        )
    )


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
            destination_url="https://example.com/products/serum-x",
            utm_source="youtube",
            utm_medium="social",
            utm_campaign="benefit_question",
            utm_content="content-123",
            link_mode="direct",
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
    assert posts[0].link_mode == "direct"
    assert posts[0].destination_url == "https://example.com/products/serum-x"


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


def test_approve_schedule_and_post_due_instagram_via_make_bridge(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "src.config._config",
        {
            "posting": {"stagger_minutes": {"instagram": 0}},
            "platforms": {"enabled": ["instagram"]},
            "make_bridge": {
                "webhook_url": "https://example.make.test/webhook",
                "r2": {
                    "account_id": "account-123",
                    "access_key_id": "access-key",
                    "secret_access_key": "secret-key",
                    "bucket_name": "velura-r2",
                },
            },
            "data_root": str(tmp_path / "velura-data"),
        },
    )

    instagram_module = _load_real_instagram_module()

    product = Product(sku="serum-ig", name="Serum IG")
    db.upsert_product(product)

    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video")
    content = Content(
        id="content-ig-123",
        product_sku=product.sku,
        theme="benefit",
        hook_type="question",
        video_local_path=str(video_path),
    )
    db.insert_content(content)
    db.upsert_platform_payload(
        PlatformPayload(
            content_id=content.id,
            platform="instagram",
            caption="Launch caption",
            hashtags="launch,velura",
            utm_url="https://example.com/products/serum-ig?utm_content=content-ig-123",
        )
    )

    calls: list[tuple[Path, str, dict[str, str]]] = []

    def fake_bridge(video_path_arg: Path, caption: str, **kwargs) -> BridgeResult:
        calls.append((video_path_arg, caption, kwargs))
        return BridgeResult(
            object_key="videos/clip-123.mp4",
            video_url="https://signed.example/video.mp4",
            webhook_status_code=200,
            webhook_response_text="accepted",
        )

    monkeypatch.setattr(instagram_module, "bridge_video_to_make", fake_bridge)
    monkeypatch.setitem(cli_module.POSTERS, "instagram", instagram_module.InstagramPoster)

    runner = CliRunner()

    approved = runner.invoke(cli_module.cli, ["approve", "--content-id", content.id])
    assert approved.exit_code == 0

    scheduled = runner.invoke(cli_module.cli, ["schedule", "--content-id", content.id])
    assert scheduled.exit_code == 0
    payload = db.get_platform_payload(content.id, "instagram")
    assert payload is not None
    assert payload.status == "scheduled"
    assert payload.publish_at is not None

    posted = runner.invoke(cli_module.cli, ["post-due"])
    assert posted.exit_code == 0

    payload = db.get_platform_payload(content.id, "instagram")
    assert payload is not None
    assert payload.status == "posted"

    assert calls == [(
        video_path,
        "Launch caption\n\n#launch #velura",
        {"platform": "instagram"},
    )]
    posts = db.list_posts_for_content(content.id)
    assert len(posts) == 1
    assert posts[0].platform == "instagram"
    assert posts[0].post_id == "make:videos/clip-123.mp4"


def test_post_command_delays_repeated_platform_posts(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "src.config._config",
        {
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
            "data_root": str(tmp_path / "velura-data"),
        },
    )

    product = Product(sku="serum-delay", name="Serum Delay")
    db.upsert_product(product)
    video_path = tmp_path / "clip-delay.mp4"
    video_path.write_bytes(b"video")
    _seed_approved_youtube_content("content-delay-1", product, video_path)
    _seed_approved_youtube_content("content-delay-2", product, video_path)

    sleep_calls: list[int] = []

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(cli_module.time, "sleep", fake_sleep)
    # Disable variance so test assertions on exact delay remain deterministic
    monkeypatch.setattr(cli_module.random, "uniform", lambda a, b: 1.0)
    monkeypatch.setitem(cli_module.POSTERS, "youtube", FakeYouTubePoster)

    runner = CliRunner()
    posted = runner.invoke(
        cli_module.cli,
        [
            "post",
            "--content-id",
            "content-delay-1",
            "--content-id",
            "content-delay-2",
            "--delay-1",
        ],
    )

    assert posted.exit_code == 0
    assert sleep_calls == [30, 30]
    assert "Next youtube post in 60 seconds." in posted.output
    assert "Next youtube post in 30 seconds." in posted.output

    posts = db.list_posts_for_content("content-delay-1") + db.list_posts_for_content("content-delay-2")
    assert len(posts) == 2
    assert db.get_platform_payload("content-delay-1", "youtube").status == "posted"
    assert db.get_platform_payload("content-delay-2", "youtube").status == "posted"


def test_post_command_nodelay_skips_repeated_platform_waits(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "src.config._config",
        {
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
            "data_root": str(tmp_path / "velura-data"),
        },
    )

    product = Product(sku="serum-fast", name="Serum Fast")
    db.upsert_product(product)
    video_path = tmp_path / "clip-fast.mp4"
    video_path.write_bytes(b"video")
    _seed_approved_youtube_content("content-fast-1", product, video_path)
    _seed_approved_youtube_content("content-fast-2", product, video_path)

    sleep_calls: list[int] = []

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(cli_module.time, "sleep", fake_sleep)
    monkeypatch.setitem(cli_module.POSTERS, "youtube", FakeYouTubePoster)

    runner = CliRunner()
    posted = runner.invoke(
        cli_module.cli,
        [
            "post",
            "--content-id",
            "content-fast-1",
            "--content-id",
            "content-fast-2",
            "--nodelay",
        ],
    )

    assert posted.exit_code == 0
    assert sleep_calls == []
    assert "Next youtube post in" not in posted.output


def test_post_command_rejects_delay_above_999_minutes(tmp_db: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["post", "--today", "--delay-1000"])

    assert result.exit_code != 0
    assert "Use --delay-XXX with XXX between 0 and 999." in result.output


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


def _load_real_instagram_module():
    sys.modules.pop("src.posters.instagram", None)
    return importlib.import_module("src.posters.instagram")
