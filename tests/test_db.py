from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from src import db
from src.models import (
    BanditArm, BanditObservation, Content, Cost, Metric, PlatformPayload, Post,
    Product, ResearchSnapshot, TextInsight,
)


def test_init_db(tmp_db: Path) -> None:
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    table_names = {r["name"] for r in rows}
    for expected in ("products", "content", "posts", "metrics", "bandit_state", "costs", "research_snapshots"):
        assert expected in table_names


def test_upsert_and_get_product(tmp_db: Path) -> None:
    product = Product(
        sku="sku-1",
        name="Widget",
        category="tech",
        price=19.99,
        product_url="https://veluraesthetics.com/products/widget",
    )
    db.upsert_product(product)

    fetched = db.get_product("sku-1")
    assert fetched is not None
    assert fetched.sku == "sku-1"
    assert fetched.name == "Widget"
    assert fetched.category == "tech"
    assert fetched.price == 19.99
    assert fetched.product_url == "https://veluraesthetics.com/products/widget"


def test_list_products_active_only(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="active-1", name="Active", active=True))
    db.upsert_product(Product(sku="inactive-1", name="Inactive", active=False))
    db.upsert_product(
        Product(sku="excluded-1", name="Excluded", active=True, excluded=True)
    )

    products = db.list_products(active_only=True, exclude_excluded=True)
    skus = {p.sku for p in products}
    assert "active-1" in skus
    assert "inactive-1" not in skus
    assert "excluded-1" not in skus


def test_upsert_shopify_product_preserves_local_registration_state(tmp_db: Path) -> None:
    db.upsert_product(
        Product(
            sku="eye-cream",
            name="Eye Cream",
            description="Manual description",
            product_url="https://veluraesthetics.com/products/active-eye-cream",
            image_dir="C:/tmp/eye-cream",
            generation_ready=True,
            active=False,
            excluded=True,
            exclude_reason="manual hold",
            last_content_date="2026-03-01",
        )
    )

    db.upsert_shopify_product(
        Product(
            sku="eye-cream",
            name="Eye Cream",
            description="Synced website description",
            product_url="https://veluraesthetics.com/products/active-eye-cream/",
            shopify_image_url="https://cdn.example.com/eye-cream.jpg",
        )
    )

    fetched = db.get_product("eye-cream")
    assert fetched is not None
    assert fetched.description == "Synced website description"
    assert fetched.shopify_image_url == "https://cdn.example.com/eye-cream.jpg"
    assert fetched.product_url == "https://veluraesthetics.com/products/active-eye-cream"
    assert fetched.image_dir == "C:/tmp/eye-cream"
    assert fetched.generation_ready is True
    assert fetched.active is False
    assert fetched.excluded is True
    assert fetched.exclude_reason == "manual hold"
    assert fetched.last_content_date == "2026-03-01"


def test_upsert_shopify_product_merges_exact_product_url_match(tmp_db: Path) -> None:
    db.upsert_product(
        Product(
            sku="eye-cream",
            name="Eye Cream",
            product_url="https://veluraesthetics.com/products/active-eye-cream",
            generation_ready=True,
            image_dir="C:/tmp/eye-cream",
        )
    )

    db.upsert_shopify_product(
        Product(
            sku="92852-BLNK-PC-03-04-CR-AEC",
            name="Eye Cream",
            description="Featherweight under-eye hydration",
            product_url="https://veluraesthetics.com/products/active-eye-cream/",
            shopify_image_url="https://cdn.example.com/eye-cream.jpg",
        )
    )

    fetched = db.get_product("eye-cream")
    assert fetched is not None
    assert fetched.description == "Featherweight under-eye hydration"
    assert fetched.shopify_image_url == "https://cdn.example.com/eye-cream.jpg"
    assert fetched.generation_ready is True
    assert fetched.image_dir == "C:/tmp/eye-cream"
    assert db.get_product("92852-BLNK-PC-03-04-CR-AEC") is None


def test_upsert_shopify_product_keeps_similar_products_separate_when_urls_differ(tmp_db: Path) -> None:
    db.upsert_product(
        Product(
            sku="eye-cream",
            name="Active Retinol Eye Cream",
            description="Retinol eye cream description",
            product_url="https://veluraesthetics.com/products/active-retinol-eye-cream",
            generation_ready=True,
            image_dir="C:/tmp/eye-cream",
        )
    )

    db.upsert_shopify_product(
        Product(
            sku="92852-BLNK-PC-03-04-CR-AEC",
            name="Eye Cream",
            description="Featherweight eye cream description",
            product_url="https://veluraesthetics.com/products/active-eye-cream",
            shopify_image_url="https://cdn.example.com/eye-cream.jpg",
        )
    )

    registered = db.get_product("eye-cream")
    synced = db.get_product("92852-BLNK-PC-03-04-CR-AEC")
    assert registered is not None
    assert synced is not None
    assert registered.description == "Retinol eye cream description"
    assert synced.description == "Featherweight eye cream description"
    assert registered.product_url == "https://veluraesthetics.com/products/active-retinol-eye-cream"
    assert synced.product_url == "https://veluraesthetics.com/products/active-eye-cream"


def test_exclude_include_product(
    db_with_product: Path, sample_product: Product
) -> None:
    db.exclude_product(sample_product.sku, "low stock")
    product = db.get_product(sample_product.sku)
    assert product is not None
    assert product.excluded is True
    assert product.exclude_reason == "low stock"

    db.include_product(sample_product.sku)
    product = db.get_product(sample_product.sku)
    assert product is not None
    assert product.excluded is False
    assert product.exclude_reason is None


def test_insert_and_get_content(
    db_with_product: Path, sample_content: Content
) -> None:
    db.insert_content(sample_content)

    fetched = db.get_content(sample_content.id)
    assert fetched is not None
    assert fetched.id == sample_content.id
    assert fetched.product_sku == sample_content.product_sku
    assert fetched.theme == "benefit_spotlight"
    assert fetched.hook_type == "bold_claim"
    assert fetched.hook_text == "You won't believe this!"
    assert fetched.review_status == "pending"
    assert fetched.creative_format == "ai_video_15s"
    assert fetched.cta_type == "see_product"


def test_insert_and_get_content_with_phase2_metadata(
    db_with_product: Path, sample_product: Product
) -> None:
    """Phase 2: metadata fields round-trip correctly."""
    content = Content(
        id="meta-001",
        product_sku=sample_product.sku,
        theme="problem_solution",
        hook_type="relatable_pain",
        hook_text="Tired of dull skin?",
        creative_format="ai_video_15s",
        cta_type="shop_now",
        cta_text="Try me today",
        problem_angle="dull skin visibility",
        proof_type="ingredient",
        script_style="conversational",
    )
    db.insert_content(content)

    fetched = db.get_content("meta-001")
    assert fetched is not None
    assert fetched.creative_format == "ai_video_15s"
    assert fetched.cta_type == "shop_now"
    assert fetched.cta_text == "Try me today"
    assert fetched.problem_angle == "dull skin visibility"
    assert fetched.proof_type == "ingredient"
    assert fetched.script_style == "conversational"


def test_insert_and_get_content_preserves_strategy_metadata_json(
    db_with_product: Path, sample_product: Product
) -> None:
    """Video V2: strategy_metadata_json round-trips via insert_content and get_content."""
    import json

    strategy = {"style_family": "anamorphic", "style_angle": "Luxury product hero"}
    content = Content(
        id="strategy-001",
        product_sku=sample_product.sku,
        theme="benefit_spotlight",
        hook_type="bold_claim",
        hook_text="Test hook",
        strategy_metadata_json=json.dumps(strategy),
    )
    db.insert_content(content)

    fetched = db.get_content("strategy-001")
    assert fetched is not None
    assert fetched.strategy_metadata_json is not None
    parsed = json.loads(fetched.strategy_metadata_json)
    assert parsed["style_family"] == "anamorphic"
    assert parsed["style_angle"] == "Luxury product hero"


def test_list_content_today_uses_sqlite_local_date(
    db_with_product: Path, sample_content: Content, monkeypatch
) -> None:
    db.insert_content(sample_content)

    class _FakeDate:
        @staticmethod
        def today():
            return date(2099, 1, 1)

    monkeypatch.setattr(db, "date", _FakeDate, raising=False)

    items = db.list_content_today()
    assert any(item.id == sample_content.id for item in items)


def test_approve_and_reject_content(
    db_with_product: Path, sample_content: Content
) -> None:
    db.insert_content(sample_content)

    db.approve_content(sample_content.id)
    approved = db.get_content(sample_content.id)
    assert approved is not None
    assert approved.approved is True
    assert approved.review_status == "approved"
    assert approved.approved_at is not None

    db.reject_content(sample_content.id, "needs a better hook")
    rejected = db.get_content(sample_content.id)
    assert rejected is not None
    assert rejected.approved is False
    assert rejected.review_status == "rejected"
    assert rejected.review_notes == "needs a better hook"
    assert rejected.rejected_at is not None


def test_approve_all_pending_content_today(
    db_with_product: Path, sample_product: Product
) -> None:
    today_row = Content(
        id="today-pending",
        product_sku=sample_product.sku,
        theme="benefit_spotlight",
        hook_type="bold_claim",
        hook_text="x",
    )
    yesterday_row = Content(
        id="yesterday-pending",
        product_sku=sample_product.sku,
        theme="benefit_spotlight",
        hook_type="bold_claim",
        hook_text="y",
    )
    db.insert_content(today_row)
    db.insert_content(yesterday_row)
    with db._connect() as conn:
        conn.execute(
            """UPDATE content SET created_at = datetime('now', 'localtime', '-1 day')
               WHERE id=?""",
            (yesterday_row.id,),
        )

    assert db.approve_all_pending_content_today() == 1
    assert db.get_content(today_row.id).review_status == "approved"
    assert db.get_content(yesterday_row.id).review_status == "pending"


def test_bandit_state_roundtrip(
    db_with_product: Path, sample_product: Product
) -> None:
    arm = BanditArm(
        arm_key="stakes_cost_of_inaction__question",
        theme="stakes_cost_of_inaction",
        hook_type="question",
        alpha=5,
        beta=3,
    )
    db.upsert_bandit_arm(arm)

    arms = db.list_bandit_arms()
    assert len(arms) == 1
    assert arms[0].arm_key == "stakes_cost_of_inaction__question"
    assert arms[0].theme == "stakes_cost_of_inaction"
    assert arms[0].hook_type == "question"
    assert arms[0].alpha == 5
    assert arms[0].beta == 3

    db.increment_bandit("stakes_cost_of_inaction__question", success=True)
    fetched = db.get_bandit_arm("stakes_cost_of_inaction__question")
    assert fetched is not None
    assert fetched.alpha == 6
    assert fetched.beta == 3


def test_cost_tracking(
    db_with_product: Path, sample_content: Content
) -> None:
    db.insert_content(sample_content)

    cost = Cost(
        content_id=sample_content.id,
        step="prompt_gen",
        api_provider="openai",
        tokens_or_units=1500,
        cost_usd=0.03,
    )
    db.insert_cost(cost)

    # SQLite datetime('now') is UTC; total_cost_today uses local date.today().
    # Align the stored timestamp so the filter matches regardless of timezone.
    today_str = date.today().isoformat()
    with db._connect() as conn:
        conn.execute(
            "UPDATE costs SET created_at = ? || ' 12:00:00' WHERE content_id = ?",
            (today_str, sample_content.id),
        )

    today_total = db.total_cost_today()
    assert today_total >= 0.03

    costs = db.costs_for_content(sample_content.id)
    assert len(costs) == 1
    assert costs[0].step == "prompt_gen"
    assert costs[0].cost_usd == 0.03


def test_init_db_migrates_legacy_cost_step_check_and_allows_tts(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    old_path = db._DB_PATH
    db.set_db_path(db_file)
    try:
        with sqlite3.connect(db_file) as conn:
            conn.executescript(
                """
                CREATE TABLE products (
                    sku TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                );
                CREATE TABLE content (
                    id TEXT PRIMARY KEY,
                    product_sku TEXT NOT NULL REFERENCES products(sku),
                    theme TEXT NOT NULL,
                    hook_type TEXT NOT NULL,
                    review_status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE costs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_id TEXT NOT NULL REFERENCES content(id),
                    step TEXT NOT NULL CHECK(step IN ('prompt_gen', 'image_gen', 'video_gen', 'slideshow_render', 'image_motion_render')),
                    api_provider TEXT NOT NULL,
                    tokens_or_units INTEGER,
                    cost_usd REAL,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                """
            )
            conn.execute("INSERT INTO products (sku, name) VALUES (?, ?)", ("legacy-sku", "Legacy Product"))
            conn.execute(
                "INSERT INTO content (id, product_sku, theme, hook_type) VALUES (?, ?, ?, ?)",
                ("legacy-content", "legacy-sku", "benefit", "question"),
            )

        db.init_db()

        db.insert_cost(
            Cost(
                content_id="legacy-content",
                step="tts_gen",
                api_provider="openai",
                tokens_or_units=42,
                cost_usd=0.01,
            )
        )

        with db._connect() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='costs'"
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM costs WHERE content_id=? AND step='tts_gen'",
                ("legacy-content",),
            ).fetchone()

        assert row is not None
        assert "CHECK(step IN" not in row["sql"]
        assert count["count"] == 1
    finally:
        db._DB_PATH = old_path


def test_platform_payload_roundtrip(
    db_with_product: Path, sample_content: Content
) -> None:
    db.insert_content(sample_content)

    payload = PlatformPayload(
        content_id=sample_content.id,
        platform="youtube",
        caption="A short caption",
        hashtags="one,two",
        utm_url="https://example.com?p=1",
        publish_at="2026-03-05 08:00:00",
        status="scheduled",
    )
    payload.id = db.upsert_platform_payload(payload)

    fetched = db.get_platform_payload(sample_content.id, "youtube")
    assert fetched is not None
    assert fetched.caption == "A short caption"
    assert fetched.hashtags == "one,two"
    assert fetched.status == "scheduled"

    due = db.list_due_platform_payloads("2026-03-05 08:00:00")
    assert any(item.content_id == sample_content.id and item.platform == "youtube" for item in due)


def test_text_insight_roundtrip_and_scope_matching(tmp_db: Path) -> None:
    db.insert_text_insight(
        TextInsight(
            id="text-generic",
            product_sku=None,
            platform=None,
            creative_format=None,
            insight_text="Generic insight.",
            source_post_count=2,
        )
    )
    db.insert_text_insight(
        TextInsight(
            id="text-scoped",
            product_sku="test-product",
            platform="instagram",
            creative_format="ai_video_15s",
            insight_text="Scoped insight.",
            source_post_count=7,
        )
    )

    fetched = db.get_latest_text_insight(
        product_sku="test-product",
        platform="instagram",
        creative_format="ai_video_15s",
    )
    assert fetched is not None
    assert fetched.id == "text-scoped"
    assert fetched.insight_text == "Scoped insight."
    assert fetched.source_post_count == 7
    assert fetched.product_sku == "test-product"
    assert fetched.platform == "instagram"
    assert fetched.creative_format == "ai_video_15s"

    fallback = db.get_latest_text_insight()
    assert fallback is not None
    assert fallback.id == "text-generic"
    assert fallback.insight_text == "Generic insight."
    assert fallback.source_post_count == 2


def test_latest_metrics_for_post_prefers_newest_metric_row(
    db_with_product: Path, sample_content: Content
) -> None:
    db.insert_content(sample_content)
    post_id = db.insert_post(
        Post(content_id=sample_content.id, platform="youtube", post_id="yt-1")
    )
    db.insert_metric(Metric(post_id=post_id, platform="youtube", views=100, likes=5))
    latest_metric_id = db.insert_metric(
        Metric(post_id=post_id, platform="youtube", views=100, likes=25)
    )

    latest = db.latest_metrics_for_post(post_id)

    assert latest is not None
    assert latest.id == latest_metric_id
    assert latest.likes == 25


def test_metric_roundtrip_preserves_avg_watch_time(
    db_with_product: Path, sample_content: Content
) -> None:
    db.insert_content(sample_content)
    post_id = db.insert_post(
        Post(content_id=sample_content.id, platform="instagram", post_id="ig-1")
    )

    db.insert_metric(
        Metric(post_id=post_id, platform="instagram", views=100, avg_watch_time=7.5)
    )

    latest = db.latest_metrics_for_post(post_id)

    assert latest is not None
    assert latest.avg_watch_time == 7.5


def test_insert_post_roundtrip_preserves_published_at(
    db_with_product: Path, sample_content: Content
) -> None:
    db.insert_content(sample_content)
    published_at = f"{date.today().isoformat()} 12:00:00"

    post_id = db.insert_post(
        Post(
            content_id=sample_content.id,
            platform="instagram",
            post_id="ig-1",
            published_at=published_at,
        )
    )

    fetched = db.get_post(post_id)
    assert fetched is not None
    assert fetched.published_at == published_at
    assert fetched.content_id == sample_content.id
    assert fetched.platform == "instagram"


def test_bandit_observation_helpers(
    db_with_product: Path, sample_content: Content
) -> None:
    db.insert_content(sample_content)
    assert db.has_bandit_observation_for_content(sample_content.id) is False

    db.insert_bandit_observation(
        BanditObservation(
            content_id=sample_content.id,
            product_sku=sample_content.product_sku,
            arm_key="benefit_spotlight__bold_claim",
            theme="benefit_spotlight",
            hook_type=sample_content.hook_type,
            aggregated_engagement_rate=0.3,
            success=True,
        )
    )

    assert db.has_bandit_observation_for_content(sample_content.id) is True


def test_bandit_migration_preserves_legacy_tables(tmp_db: Path) -> None:
    with db._connect() as conn:
        conn.execute("ALTER TABLE bandit_state RENAME TO bandit_state_new")
        conn.execute("ALTER TABLE bandit_observations RENAME TO bandit_observations_new")
        conn.executescript(
            """
            CREATE TABLE bandit_state (
                product_sku     TEXT NOT NULL,
                theme           TEXT NOT NULL,
                hook_type       TEXT NOT NULL,
                successes       INTEGER DEFAULT 1,
                failures        INTEGER DEFAULT 1,
                last_updated    TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (product_sku, theme, hook_type)
            );
            CREATE TABLE bandit_observations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id         INTEGER NOT NULL UNIQUE,
                metric_id       INTEGER NOT NULL,
                product_sku     TEXT NOT NULL,
                theme           TEXT NOT NULL,
                hook_type       TEXT NOT NULL,
                engagement_rate REAL NOT NULL,
                success         INTEGER NOT NULL,
                observed_at     TEXT DEFAULT (datetime('now'))
            );
            """
        )

        db._migrate_bandit_tables(conn)

        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "bandit_state_legacy" in tables
        assert "bandit_observations_legacy" in tables

        bandit_state_columns = set(db._table_columns(conn, "bandit_state"))
        observation_columns = set(db._table_columns(conn, "bandit_observations"))
        assert {"arm_key", "alpha", "beta"}.issubset(bandit_state_columns)
        assert {"content_id", "arm_key", "aggregated_engagement_rate"}.issubset(observation_columns)


# ---------------------------------------------------------------------------
# Phase 3: Research Snapshots
# ---------------------------------------------------------------------------

def test_insert_and_get_research_snapshot(tmp_db: Path) -> None:
    snap = ResearchSnapshot(
        id="rs-001",
        product_sku="moisturizer",
        platform="instagram",
        creative_format="ai_video_15s",
        summary="Instagram users respond well to before/after framing.",
        source_type="manual",
    )
    db.insert_research_snapshot(snap)

    fetched = db.get_research_snapshot("rs-001")
    assert fetched is not None
    assert fetched.id == "rs-001"
    assert fetched.product_sku == "moisturizer"
    assert fetched.platform == "instagram"
    assert fetched.creative_format == "ai_video_15s"
    assert fetched.summary == "Instagram users respond well to before/after framing."
    assert fetched.source_type == "manual"
    assert fetched.created_at is not None


def test_get_best_matching_snapshot_precedence(
    db_with_product: Path, sample_product: Product
) -> None:
    """Product+platform+format beats product-only; generic (NULL) matches any."""
    db.insert_research_snapshot(ResearchSnapshot(
        id="generic",
        product_sku=None,
        platform=None,
        creative_format=None,
        summary="Generic insight for all.",
        source_type="manual",
    ))
    db.insert_research_snapshot(ResearchSnapshot(
        id="product-only",
        product_sku=sample_product.sku,
        platform=None,
        creative_format=None,
        summary="Product-specific insight.",
        source_type="manual",
    ))
    db.insert_research_snapshot(ResearchSnapshot(
        id="product-format",
        product_sku=sample_product.sku,
        platform=None,
        creative_format="ai_video_15s",
        summary="Product + format insight.",
        source_type="manual",
    ))

    best = db.get_best_matching_snapshot(
        product_sku=sample_product.sku,
        platform=None,
        creative_format="ai_video_15s",
    )
    assert best is not None
    assert best.id == "product-format"
    assert "Product + format" in best.summary


def test_get_best_matching_snapshot_returns_none_when_empty(tmp_db: Path) -> None:
    best = db.get_best_matching_snapshot(
        product_sku="nonexistent",
        platform=None,
        creative_format="ai_video_15s",
    )
    assert best is None


def test_list_research_snapshots(
    db_with_product: Path, sample_product: Product
) -> None:
    db.insert_research_snapshot(ResearchSnapshot(
        id="rs-1",
        product_sku=sample_product.sku,
        summary="First",
        source_type="manual",
    ))
    db.insert_research_snapshot(ResearchSnapshot(
        id="rs-2",
        product_sku=sample_product.sku,
        summary="Second",
        source_type="manual",
    ))
    db.insert_research_snapshot(ResearchSnapshot(
        id="rs-3",
        product_sku="other-product",
        summary="Other",
        source_type="manual",
    ))

    # Filter by product: includes product-specific and generic (NULL) snapshots
    by_product = db.list_research_snapshots(product_sku=sample_product.sku, limit=10)
    ids = {s.id for s in by_product}
    assert "rs-1" in ids
    assert "rs-2" in ids
    assert "rs-3" not in ids

    all_snapshots = db.list_research_snapshots(limit=10)
    assert len(all_snapshots) >= 3


def test_clone_content_for_repost_requires_video_path(tmp_db: Path, sample_product: Product) -> None:
    db.upsert_product(sample_product)
    db.insert_content(
        Content(
            id="orig-no-video",
            product_sku=sample_product.sku,
            theme="benefit_spotlight",
            hook_type="question",
            video_local_path=None,
        )
    )
    with pytest.raises(ValueError, match="video_local_path"):
        db.clone_content_for_repost("orig-no-video")


def test_clone_content_for_repost_requires_existing_video_file(
    tmp_db: Path, sample_product: Product, tmp_path: Path
) -> None:
    db.upsert_product(sample_product)
    missing = tmp_path / "nope.mp4"
    db.insert_content(
        Content(
            id="orig-missing-file",
            product_sku=sample_product.sku,
            theme="benefit_spotlight",
            hook_type="question",
            video_local_path=str(missing),
        )
    )
    with pytest.raises(ValueError, match="video file not found"):
        db.clone_content_for_repost("orig-missing-file")


def test_clone_content_for_repost_linked_row_fresh_utm_and_repeat_distinct(
    tmp_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_product: Product,
) -> None:
    monkeypatch.setattr(
        "src.config._config",
        {
            "posting": {"stagger_minutes": {"youtube": 0}},
            "platforms": {"enabled": ["youtube"]},
            "site_url": "https://example.com",
            "shopify": {"store_url": "https://example.com"},
            "youtube": {"client_secrets_file": str(tmp_path / "yt.json")},
            "data_root": str(tmp_path / "velura-data"),
        },
    )
    (tmp_path / "yt.json").write_text("{}", encoding="utf-8")

    db.upsert_product(sample_product)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"v")
    orig_id = "original-content-aa"
    db.insert_content(
        Content(
            id=orig_id,
            product_sku=sample_product.sku,
            theme="benefit_spotlight",
            hook_type="question",
            video_local_path=str(video_path),
        )
    )
    db.upsert_platform_payload(
        PlatformPayload(
            content_id=orig_id,
            platform="youtube",
            caption="Original caption",
            hashtags="a,b",
            utm_content=orig_id,
            utm_campaign="benefit_spotlight_question",
        )
    )

    r1 = db.clone_content_for_repost(orig_id)
    r2 = db.clone_content_for_repost(orig_id)

    assert r1.id != r2.id != orig_id
    assert r1.source_content_id == orig_id
    assert r2.source_content_id == orig_id

    orig_payload = db.get_platform_payload(orig_id, "youtube")
    assert orig_payload is not None
    assert orig_payload.utm_content == orig_id

    p1 = db.get_platform_payload(r1.id, "youtube")
    p2 = db.get_platform_payload(r2.id, "youtube")
    assert p1 is not None and p2 is not None
    assert p1.utm_content == r1.id
    assert p2.utm_content == r2.id
    assert p1.caption == "Original caption"


def test_clone_content_for_repost_pending_review(
    tmp_db: Path,
    tmp_path: Path,
    sample_product: Product,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.config._config",
        {
            "posting": {"stagger_minutes": {"youtube": 0}},
            "platforms": {"enabled": ["youtube"]},
            "site_url": "https://example.com",
            "shopify": {"store_url": "https://example.com"},
            "youtube": {"client_secrets_file": str(tmp_path / "yt.json")},
            "data_root": str(tmp_path / "velura-data"),
        },
    )
    (tmp_path / "yt.json").write_text("{}", encoding="utf-8")

    db.upsert_product(sample_product)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"v")
    orig_id = "original-pending"
    db.insert_content(
        Content(
            id=orig_id,
            product_sku=sample_product.sku,
            theme="benefit_spotlight",
            hook_type="question",
            video_local_path=str(video_path),
        )
    )

    r = db.clone_content_for_repost(orig_id, auto_approve=False)
    assert r.review_status == "pending"
    assert not r.approved


def test_mark_platform_payload_delivery_submitted_for_ig_phone_remote_id(
    db_with_product: Path, sample_content: Content
) -> None:
    db.insert_content(sample_content)
    payload = PlatformPayload(
        content_id=sample_content.id,
        platform="instagram",
        caption="c",
        hashtags="h",
        status="scheduled",
    )
    payload.id = db.upsert_platform_payload(payload)
    assert payload.id is not None

    status = db.mark_platform_payload_delivery(payload.id, "ig_phone:a1b2c3d4")

    assert status == "submitted"
    updated = db.get_platform_payload(sample_content.id, "instagram")
    assert updated is not None
    assert updated.status == "submitted"
    assert updated.last_error is None
