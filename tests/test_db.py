from __future__ import annotations

from datetime import date
from pathlib import Path

from src import db
from src.models import BanditArm, BanditObservation, Content, Cost, Metric, PlatformPayload, Post, Product


def test_init_db(tmp_db: Path) -> None:
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    table_names = {r["name"] for r in rows}
    for expected in ("products", "content", "posts", "metrics", "bandit_state", "costs"):
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
        product_sku=sample_product.sku,
        theme="fear",
        hook_type="question",
        successes=5,
        failures=3,
    )
    db.upsert_bandit_arm(arm)

    arms = db.get_bandit_arms(sample_product.sku)
    assert len(arms) == 1
    assert arms[0].theme == "fear"
    assert arms[0].hook_type == "question"
    assert arms[0].successes == 5
    assert arms[0].failures == 3

    db.increment_bandit(sample_product.sku, "fear", "question", success=True)
    arms = db.get_bandit_arms(sample_product.sku)
    assert arms[0].successes == 6
    assert arms[0].failures == 3


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
    post_id = db.insert_post(
        Post(content_id=sample_content.id, platform="youtube", post_id="yt-obs")
    )
    metric_id = db.insert_metric(
        Metric(post_id=post_id, platform="youtube", views=100, likes=30)
    )

    assert db.has_bandit_observation(post_id) is False

    db.insert_bandit_observation(
        BanditObservation(
            post_id=post_id,
            metric_id=metric_id,
            product_sku=sample_content.product_sku,
            theme=sample_content.theme,
            hook_type=sample_content.hook_type,
            engagement_rate=0.3,
            success=True,
        )
    )

    assert db.has_bandit_observation(post_id) is True
