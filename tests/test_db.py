from __future__ import annotations

from datetime import date
from pathlib import Path

from src import db
from src.models import (
    BanditArm, BanditObservation, Content, Cost, Metric, PlatformPayload, Post,
    Product, ResearchSnapshot,
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
    assert fetched.theme == "benefit"
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


def test_bandit_state_roundtrip(
    db_with_product: Path, sample_product: Product
) -> None:
    arm = BanditArm(
        arm_key="fear__question",
        theme="fear",
        hook_type="question",
        alpha=5,
        beta=3,
    )
    db.upsert_bandit_arm(arm)

    arms = db.list_bandit_arms()
    assert len(arms) == 1
    assert arms[0].arm_key == "fear__question"
    assert arms[0].theme == "fear"
    assert arms[0].hook_type == "question"
    assert arms[0].alpha == 5
    assert arms[0].beta == 3

    db.increment_bandit("fear__question", success=True)
    fetched = db.get_bandit_arm("fear__question")
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


def test_bandit_observation_helpers(
    db_with_product: Path, sample_content: Content
) -> None:
    db.insert_content(sample_content)
    assert db.has_bandit_observation_for_content(sample_content.id) is False

    db.insert_bandit_observation(
        BanditObservation(
            content_id=sample_content.id,
            product_sku=sample_content.product_sku,
            arm_key="benefit__bold_claim",
            theme=sample_content.theme,
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
