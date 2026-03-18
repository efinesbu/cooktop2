from __future__ import annotations

from datetime import date as real_date
from datetime import timedelta

from src import morning_briefing
from src.models import (
    BanditRecommendation,
    Content,
    Metric,
    Post,
    Product,
    ThemeHookAllocation,
)


class FakeDate(real_date):
    @classmethod
    def today(cls) -> "FakeDate":
        return cls(2026, 3, 9)


def _stub_common_dependencies(monkeypatch, products: list[Product]) -> None:
    monkeypatch.setattr(morning_briefing, "date", FakeDate)
    monkeypatch.setattr(
        morning_briefing.config,
        "get",
        lambda key, default=None: 6 if key == "bandit.daily_slots" else default,
    )
    monkeypatch.setattr(
        morning_briefing,
        "check_budget",
        lambda: (0.0, 20.0, True),
    )
    monkeypatch.setattr(morning_briefing, "_cost_yesterday", lambda: 5.0)
    monkeypatch.setattr(morning_briefing, "_clips_yesterday", lambda: 4)
    monkeypatch.setattr(morning_briefing, "_cost_7day_average", lambda: 4.0)
    monkeypatch.setattr(
        morning_briefing,
        "_product_health",
        lambda _products: {
            "no_content": [],
            "declining": [],
            "should_pause": [],
            "awaiting_setup": [],
        },
    )
    monkeypatch.setattr(
        morning_briefing.db,
        "list_products",
        lambda active_only=True, exclude_excluded=True: products,
    )
    monkeypatch.setattr(
        morning_briefing.bandit,
        "recommend",
        lambda total_slots: BanditRecommendation(
            allocations=[
                ThemeHookAllocation("benefit_spotlight", "question", 3, 0.91),
                ThemeHookAllocation("benefit_spotlight", "quick_tip", 3, 0.44),
            ]
        ),
    )
    monkeypatch.setattr(morning_briefing.db, "list_bandit_arms", lambda: [])


def test_generate_briefing_includes_7day_trends_and_creative_insights(monkeypatch) -> None:
    products = [
        Product(sku="serum-a", name="Serum A"),
        Product(sku="serum-b", name="Serum B"),
    ]
    _stub_common_dependencies(monkeypatch, products)

    posts = [
        Post(id=1, content_id="c1", platform="youtube", published_at="2026-03-08 10:00:00"),
        Post(id=2, content_id="c2", platform="instagram", published_at="2026-03-07 10:00:00"),
        Post(id=3, content_id="c3", platform="tiktok", published_at="2026-03-06 10:00:00"),
        Post(id=4, content_id="c4", platform="x", published_at="2026-03-04 10:00:00"),
        Post(id=5, content_id="c5", platform="youtube", published_at="2026-02-28 10:00:00"),
        Post(id=6, content_id="c6", platform="instagram", published_at="2026-02-26 10:00:00"),
    ]
    contents = {
        "c1": Content(id="c1", product_sku="serum-a", theme="benefit_spotlight", hook_type="question"),
        "c2": Content(id="c2", product_sku="serum-a", theme="benefit_spotlight", hook_type="question"),
        "c3": Content(id="c3", product_sku="serum-b", theme="benefit_spotlight", hook_type="quick_tip"),
        "c4": Content(id="c4", product_sku="serum-b", theme="benefit_spotlight", hook_type="quick_tip"),
        "c5": Content(id="c5", product_sku="serum-a", theme="identity_tribe", hook_type="bold_claim"),
        "c6": Content(id="c6", product_sku="serum-b", theme="hidden_knowledge", hook_type="question"),
    }
    metrics = {
        1: Metric(post_id=1, platform="youtube", views=1000, likes=80, comments=20, shares=10, saves=10, watch_through_rate=0.45),
        2: Metric(post_id=2, platform="instagram", views=800, likes=60, comments=10, shares=5, saves=5, watch_through_rate=0.40),
        3: Metric(post_id=3, platform="tiktok", views=1500, likes=10, comments=3, shares=1, saves=1, watch_through_rate=0.25),
        4: Metric(post_id=4, platform="x", views=1200, likes=8, comments=4, shares=1, saves=1, watch_through_rate=0.20),
        5: Metric(post_id=5, platform="youtube", views=3000, likes=120, comments=30, shares=20, saves=10),
        6: Metric(post_id=6, platform="instagram", views=3000, likes=140, comments=30, shares=20, saves=20),
    }
    products_by_sku = {product.sku: product for product in products}

    def fake_list_recent_posts(days: int = 30) -> list[Post]:
        cutoff = FakeDate.today() - timedelta(days=days)
        return [
            post for post in posts
            if real_date.fromisoformat(post.published_at[:10]) >= cutoff
        ]

    monkeypatch.setattr(morning_briefing.db, "list_recent_posts", fake_list_recent_posts)
    monkeypatch.setattr(morning_briefing.db, "latest_metrics_for_post", lambda post_id: metrics.get(post_id))
    monkeypatch.setattr(morning_briefing.db, "get_content", lambda content_id: contents.get(content_id))
    monkeypatch.setattr(
        morning_briefing.db,
        "get_product",
        lambda sku: products_by_sku.get(sku),
    )

    briefing = morning_briefing.generate_briefing()

    assert "POSTS PUBLISHED YESTERDAY" in briefing
    assert "7-DAY PERFORMANCE" in briefing
    assert "Window: 2026-03-03 to 2026-03-09" in briefing
    assert "Posts tracked: 4 │ Avg/post: 1125 views │ Engagement: 5.1%" in briefing
    assert "Avg watch-through rate: 32.5%" in briefing
    assert "Vs prior 7 days: views ↓25% │ engagement ↓22% │ posts +2" in briefing
    assert "Best platform: YouTube — 12.0% engagement across 1 post" in briefing
    assert "Most viewed product: Serum B — 2,700 views across 2 posts" in briefing
    assert "30-DAY PERFORMANCE" in briefing
    assert "Window: 2026-02-08 to 2026-03-09" in briefing
    assert "Posts tracked: 6 │ Avg/post: 1750 views │ Engagement: 5.9%" in briefing
    assert "Vs prior 30 days: not enough historical data yet." in briefing
    assert "Best platform: Instagram — 7.6% engagement across 2 posts" in briefing
    assert "Most viewed product: Serum B — 5,700 views across 3 posts" in briefing
    assert "Top repeated combos (7d):" in briefing
    assert "1. benefit_spotlight/question — 11.1% engagement (2 posts, 1,800 views)" in briefing
    assert "1. benefit_spotlight/quick_tip — 1.1% engagement (2 posts)" in briefing
    assert "7-day views down 25% vs prior week" in briefing
    assert "7-day engagement down 22% vs prior week" in briefing
    assert "Retest benefit_spotlight/quick_tip creative (7-day engagement 1.1%)" in briefing
    assert "ORGANIC EVALUATION (12-CREATIVE MATRIX)" in briefing
    assert "Winners (repeat or promote):" in briefing or "Middle (consider remixing):" in briefing or "Losers (retire or refresh):" in briefing


def test_generate_briefing_includes_today_in_rolling_7day_window(monkeypatch) -> None:
    products = [Product(sku="serum-a", name="Serum A")]
    _stub_common_dependencies(monkeypatch, products)

    posts = [
        Post(id=21, content_id="c21", platform="instagram", published_at="2026-03-09 09:00:00"),
    ]
    contents = {
        "c21": Content(id="c21", product_sku="serum-a", theme="benefit_spotlight", hook_type="question"),
    }
    metrics = {
        21: Metric(
            post_id=21,
            platform="instagram",
            views=700,
            likes=70,
            comments=0,
            shares=0,
            saves=0,
            watch_through_rate=0.35,
        ),
    }

    def fake_list_recent_posts(days: int = 30) -> list[Post]:
        cutoff = FakeDate.today() - timedelta(days=days)
        return [
            post for post in posts
            if real_date.fromisoformat(post.published_at[:10]) >= cutoff
        ]

    monkeypatch.setattr(morning_briefing.db, "list_recent_posts", fake_list_recent_posts)
    monkeypatch.setattr(morning_briefing.db, "latest_metrics_for_post", lambda post_id: metrics.get(post_id))
    monkeypatch.setattr(morning_briefing.db, "get_content", lambda content_id: contents.get(content_id))
    monkeypatch.setattr(
        morning_briefing.db,
        "get_product",
        lambda sku: products[0] if sku == "serum-a" else None,
    )

    briefing = morning_briefing.generate_briefing()

    assert "No posts published yesterday with metrics." in briefing
    assert "Window: 2026-03-03 to 2026-03-09" in briefing
    assert "Posts tracked: 1 │ Avg/post: 700 views │ Engagement: 10.0%" in briefing
    assert "30-DAY PERFORMANCE" in briefing
    assert "Window: 2026-02-08 to 2026-03-09" in briefing
    assert "Vs prior 30 days: not enough historical data yet." in briefing
    assert "Most viewed product: Serum A — 700 views across 1 post" in briefing


def test_generate_briefing_handles_sparse_7day_data(monkeypatch) -> None:
    products = [Product(sku="serum-a", name="Serum A")]
    _stub_common_dependencies(monkeypatch, products)

    posts = [
        Post(id=11, content_id="c11", platform="instagram", published_at="2026-03-08 09:00:00"),
    ]
    contents = {
        "c11": Content(id="c11", product_sku="serum-a", theme="benefit_spotlight", hook_type="question"),
    }
    metrics = {
        11: Metric(
            post_id=11,
            platform="instagram",
            views=900,
            likes=72,
            comments=9,
            shares=9,
            saves=0,
            watch_through_rate=0.38,
        ),
    }

    def fake_list_recent_posts(days: int = 30) -> list[Post]:
        cutoff = FakeDate.today() - timedelta(days=days)
        return [
            post for post in posts
            if real_date.fromisoformat(post.published_at[:10]) >= cutoff
        ]

    monkeypatch.setattr(morning_briefing.db, "list_recent_posts", fake_list_recent_posts)
    monkeypatch.setattr(morning_briefing.db, "latest_metrics_for_post", lambda post_id: metrics.get(post_id))
    monkeypatch.setattr(morning_briefing.db, "get_content", lambda content_id: contents.get(content_id))
    monkeypatch.setattr(
        morning_briefing.db,
        "get_product",
        lambda sku: products[0] if sku == "serum-a" else None,
    )

    briefing = morning_briefing.generate_briefing()

    assert "POSTS PUBLISHED YESTERDAY" in briefing
    assert "Vs prior 7 days: not enough historical data yet." in briefing
    assert "Need more repeated posts to compare creative combos confidently." in briefing
    assert "Current leader: benefit_spotlight/question — 10.0% engagement (1 post)" in briefing
    assert "No urgent actions. Systems nominal." in briefing


def test_ascii_safe_text_replaces_unicode_markers() -> None:
    text = "Views │ likes — trend ↓10% • note ◆ arm ✓ done ⚠"

    converted = morning_briefing._ascii_safe_text(text)

    assert converted == "Views | likes - trend down 10% * note * arm OK done WARNING"
