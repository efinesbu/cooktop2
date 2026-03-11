"""Tests for Phase 5 organic evaluation: cohort grouping and winner/middle/loser classification."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.models import Content, Metric, Post, Product
from src.organic_evaluation import (
    CohortPerformance,
    classify_winners_middles_losers,
    format_cohort_label,
    gather_cohort_performances,
)


def test_gather_cohort_performances_groups_by_matrix_dimensions(monkeypatch) -> None:
    """Cohorts are grouped by product_sku, platform, creative_format, hook_type, cta_type."""
    posts = [
        Post(id=1, content_id="c1", platform="youtube", published_at="2026-03-08 10:00:00"),
        Post(id=2, content_id="c2", platform="instagram", published_at="2026-03-07 10:00:00"),
    ]
    contents = {
        "c1": Content(
            id="c1",
            product_sku="serum-a",
            theme="benefit",
            hook_type="question",
            creative_format="ai_video_15s",
            cta_type="see_product",
        ),
        "c2": Content(
            id="c2",
            product_sku="serum-b",
            theme="routine",
            hook_type="quick_tip",
            creative_format="slideshow_15s",
            cta_type="see_product",
        ),
    }
    metrics = {
        1: Metric(post_id=1, platform="youtube", views=1000, likes=80, comments=20, shares=10, saves=10),
        2: Metric(post_id=2, platform="instagram", views=800, likes=60, comments=10, shares=5, saves=5),
    }
    products = {"serum-a": Product(sku="serum-a", name="Serum A"), "serum-b": Product(sku="serum-b", name="Serum B")}

    monkeypatch.setattr(
        "src.organic_evaluation.db.list_recent_posts",
        lambda days=30: posts,
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.latest_metrics_for_post",
        lambda post_id: metrics.get(post_id),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.get_content",
        lambda content_id: contents.get(content_id),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.get_product",
        lambda sku: products.get(sku),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.aggregate_commerce_for_content",
        lambda *a, **kw: {"sessions": 0, "add_to_cart": 0, "checkout_started": 0, "purchases": 0, "revenue": 0.0},
    )

    result = gather_cohort_performances(days=7)

    assert len(result) == 2
    by_platform = {p.platform: p for p in result}
    assert by_platform["youtube"].product_sku == "serum-a"
    assert by_platform["youtube"].creative_format == "ai_video_15s"
    assert by_platform["youtube"].hook_type == "question"
    assert by_platform["youtube"].engagement_rate == pytest.approx(0.12, rel=0.01)
    assert by_platform["instagram"].creative_format == "slideshow_15s"


def test_gather_cohort_performances_handles_zero_view_creatives(monkeypatch) -> None:
    """Zero-view creatives get engagement_rate=0 without division errors."""
    posts = [
        Post(id=1, content_id="c1", platform="youtube", published_at="2026-03-08 10:00:00"),
    ]
    contents = {
        "c1": Content(
            id="c1",
            product_sku="serum-a",
            theme="benefit",
            hook_type="question",
            creative_format="ai_video_15s",
            cta_type="see_product",
        ),
    }
    metrics = {
        1: Metric(post_id=1, platform="youtube", views=0, likes=0, comments=0, shares=0, saves=0),
    }
    products = {"serum-a": Product(sku="serum-a", name="Serum A")}

    monkeypatch.setattr(
        "src.organic_evaluation.db.list_recent_posts",
        lambda days=30: posts,
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.latest_metrics_for_post",
        lambda post_id: metrics.get(post_id),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.get_content",
        lambda content_id: contents.get(content_id),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.get_product",
        lambda sku: products.get(sku),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.aggregate_commerce_for_content",
        lambda *a, **kw: {"sessions": 0, "add_to_cart": 0, "checkout_started": 0, "purchases": 0, "revenue": 0.0},
    )

    result = gather_cohort_performances(days=7)

    assert len(result) == 1
    assert result[0].total_views == 0
    assert result[0].engagement_rate == 0.0


def test_gather_cohort_performances_handles_sparse_data(monkeypatch) -> None:
    """Sparse data: few posts, some with missing metrics or content."""
    posts = [
        Post(id=1, content_id="c1", platform="youtube", published_at="2026-03-08 10:00:00"),
        Post(id=2, content_id="c99", platform="instagram", published_at="2026-03-07 10:00:00"),
    ]
    contents = {"c1": Content(id="c1", product_sku="serum-a", theme="benefit", hook_type="question")}
    metrics = {1: Metric(post_id=1, platform="youtube", views=100, likes=5, comments=0, shares=0, saves=0)}
    products = {"serum-a": Product(sku="serum-a", name="Serum A")}

    monkeypatch.setattr(
        "src.organic_evaluation.db.list_recent_posts",
        lambda days=30: posts,
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.latest_metrics_for_post",
        lambda post_id: metrics.get(post_id),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.get_content",
        lambda content_id: contents.get(content_id),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.get_product",
        lambda sku: products.get(sku),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.aggregate_commerce_for_content",
        lambda *a, **kw: {"sessions": 0, "add_to_cart": 0, "checkout_started": 0, "purchases": 0, "revenue": 0.0},
    )

    result = gather_cohort_performances(days=7)

    assert len(result) == 1
    assert result[0].product_sku == "serum-a"
    assert result[0].engagement_rate == pytest.approx(0.05, rel=0.01)


def test_gather_cohort_performances_partial_platform_cohorts(monkeypatch) -> None:
    """Partial platforms: data for some platforms but not others."""
    posts = [
        Post(id=1, content_id="c1", platform="youtube", published_at="2026-03-08 10:00:00"),
        Post(id=2, content_id="c1", platform="instagram", published_at="2026-03-08 11:00:00"),
    ]
    contents = {
        "c1": Content(
            id="c1",
            product_sku="serum-a",
            theme="benefit",
            hook_type="question",
            creative_format="ai_video_15s",
            cta_type="see_product",
        ),
    }
    metrics = {
        1: Metric(post_id=1, platform="youtube", views=500, likes=50, comments=5, shares=5, saves=5),
        2: Metric(post_id=2, platform="instagram", views=300, likes=20, comments=2, shares=1, saves=2),
    }
    products = {"serum-a": Product(sku="serum-a", name="Serum A")}

    monkeypatch.setattr(
        "src.organic_evaluation.db.list_recent_posts",
        lambda days=30: posts,
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.latest_metrics_for_post",
        lambda post_id: metrics.get(post_id),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.get_content",
        lambda content_id: contents.get(content_id),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.get_product",
        lambda sku: products.get(sku),
    )
    monkeypatch.setattr(
        "src.organic_evaluation.db.aggregate_commerce_for_content",
        lambda *a, **kw: {"sessions": 0, "add_to_cart": 0, "checkout_started": 0, "purchases": 0, "revenue": 0.0},
    )

    result = gather_cohort_performances(days=7)

    assert len(result) == 2
    by_platform = {p.platform: p for p in result}
    assert "youtube" in by_platform
    assert "instagram" in by_platform
    assert by_platform["youtube"].total_views == 500
    assert by_platform["instagram"].total_views == 300


def test_classify_winners_middles_losers_empty() -> None:
    """Empty input returns three empty lists."""
    w, m, l = classify_winners_middles_losers([])
    assert w == []
    assert m == []
    assert l == []


def test_classify_winners_middles_losers_single_cohort() -> None:
    """Single cohort is classified as winner."""
    p = CohortPerformance(
        product_sku="a",
        product_name="A",
        platform="ig",
        creative_format="ai_video_15s",
        hook_type="q",
        cta_type="see_product",
        theme="benefit",
        post_count=1,
        total_views=100,
        total_engagements=10,
        engagement_rate=0.1,
        avg_watch_through_rate=0.5,
        content_ids=["c1"],
    )
    w, m, l = classify_winners_middles_losers([p])
    assert len(w) == 1
    assert len(m) == 0
    assert len(l) == 0


def test_classify_winners_middles_losers_splits_by_percentile() -> None:
    """Top 25% winners, bottom 25% losers, rest middle."""
    perfs = [
        CohortPerformance("a", "A", "ig", "ai", "q", "see", "benefit", 1, 1000, 100, 0.10, 0.5, ["c1"]),
        CohortPerformance("b", "B", "ig", "ai", "r", "see", "benefit", 1, 800, 60, 0.075, 0.4, ["c2"]),
        CohortPerformance("c", "C", "ig", "ai", "s", "see", "benefit", 1, 600, 30, 0.05, 0.3, ["c3"]),
        CohortPerformance("d", "D", "ig", "ai", "t", "see", "benefit", 1, 400, 10, 0.025, 0.2, ["c4"]),
    ]
    w, m, l = classify_winners_middles_losers(perfs, winner_pct=0.25, loser_pct=0.25)
    assert len(w) == 1
    assert len(m) == 2
    assert len(l) == 1
    assert w[0].engagement_rate == 0.10
    assert l[0].engagement_rate == 0.025


def test_classify_winners_middles_losers_rank_by_revenue() -> None:
    """When rank_by=revenue, cohorts are ranked by revenue."""
    perfs = [
        CohortPerformance("a", "A", "ig", "ai", "q", "see", "benefit", 1, 100, 10, 0.10, None, ["c1"], sessions=50, purchases=2, revenue=80.0),
        CohortPerformance("b", "B", "ig", "ai", "r", "see", "benefit", 1, 200, 20, 0.10, None, ["c2"], sessions=100, purchases=1, revenue=30.0),
        CohortPerformance("c", "C", "ig", "ai", "s", "see", "benefit", 1, 300, 30, 0.10, None, ["c3"], sessions=80, purchases=0, revenue=0.0),
    ]
    w, m, l = classify_winners_middles_losers(perfs, rank_by="revenue", winner_pct=0.33, loser_pct=0.33)
    assert len(w) == 1
    assert len(l) == 1
    assert w[0].revenue == 80.0
    assert l[0].revenue == 0.0


def test_format_cohort_label() -> None:
    p = CohortPerformance(
        product_sku="serum-a",
        product_name="Serum A",
        platform="instagram",
        creative_format="slideshow_15s",
        hook_type="question",
        cta_type="see_product",
        theme="benefit",
        post_count=1,
        total_views=100,
        total_engagements=10,
        engagement_rate=0.1,
        avg_watch_through_rate=None,
        content_ids=[],
    )
    assert "Serum A" in format_cohort_label(p)
    assert "instagram" in format_cohort_label(p)
    assert "slideshow_15s" in format_cohort_label(p)
    assert "benefit" in format_cohort_label(p)
    assert "question" in format_cohort_label(p)
