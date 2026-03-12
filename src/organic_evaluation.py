"""Phase 5: Cohort-level organic evaluation reporting.

Groups creatives by product_sku, platform, creative_format, hook_type, cta_type
and classifies them as winners (top ~25%), losers (bottom ~25%), or middle.
Decisioning remains human-reviewed; no auto-promote or auto-retire.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from src import db
from src.models import Content, Metric, PLATFORMS


@dataclass
class CohortPerformance:
    """Aggregated performance for one cohort (product × platform × format × hook × cta)."""

    product_sku: str
    product_name: str
    platform: str
    creative_format: str
    hook_type: str
    cta_type: str
    theme: str  # kept for display; cohort key uses hook_type
    post_count: int
    total_views: int
    total_engagements: int
    engagement_rate: float
    avg_watch_through_rate: float | None
    content_ids: list[str]
    # Phase 6: commerce metrics (from commerce_facts, attributed by content_id)
    sessions: int = 0
    purchases: int = 0
    revenue: float = 0.0


def _engagement_rate(views: int, engagements: int) -> float:
    return engagements / max(views, 1)


def _published_on(published_at: str | None) -> date | None:
    if not published_at:
        return None
    try:
        return date.fromisoformat(published_at[:10])
    except ValueError:
        return None


def gather_cohort_performances(
    days: int = 7,
    start_on: date | None = None,
    end_on: date | None = None,
) -> list[CohortPerformance]:
    """Aggregate post metrics into cohort-level performance.

    Cohorts are grouped by product_sku, platform, creative_format, hook_type, cta_type.
    Handles sparse data and zero-view creatives (engagement_rate = 0).
    """
    posts = db.list_recent_posts(days=days)
    cohort_agg: dict[tuple[str, str, str, str, str], dict] = {}

    for post in posts:
        if post.id is None:
            continue

        pub_date = _published_on(post.published_at)
        if pub_date is None:
            continue

        if start_on is not None and pub_date < start_on:
            continue
        if end_on is not None and pub_date > end_on:
            continue

        metrics = db.latest_metrics_for_post(post.id)
        if metrics is None:
            metrics = _empty_metric()

        content = db.get_content(post.content_id)
        if content is None:
            continue

        product = db.get_product(content.product_sku)
        product_name = product.name if product else content.product_sku

        key = (
            content.product_sku,
            post.platform,
            content.creative_format or "ai_video_15s",
            content.hook_type,
            content.cta_type or "see_product",
        )
        agg = cohort_agg.setdefault(
            key,
            {
                "product_sku": content.product_sku,
                "product_name": product_name,
                "platform": post.platform,
                "creative_format": content.creative_format or "ai_video_15s",
                "hook_type": content.hook_type,
                "cta_type": content.cta_type or "see_product",
                "theme": content.theme,
                "post_count": 0,
                "total_views": 0,
                "total_engagements": 0,
                "watch_rates": [],
                "content_ids": [],
                "sessions": 0,
                "purchases": 0,
                "revenue": 0.0,
            },
        )

        engagements = metrics.likes + metrics.comments + metrics.shares + metrics.saves
        agg["post_count"] += 1
        agg["total_views"] += metrics.views
        agg["total_engagements"] += engagements
        if metrics.watch_through_rate is not None:
            agg["watch_rates"].append(metrics.watch_through_rate)
        if post.content_id not in agg["content_ids"]:
            agg["content_ids"].append(post.content_id)

    # Phase 6: aggregate commerce facts per cohort (by content_ids, platform, date range)
    start_str = start_on.isoformat() if start_on else None
    end_str = end_on.isoformat() if end_on else None
    for agg in cohort_agg.values():
        plat = agg["platform"]
        for cid in agg["content_ids"]:
            commerce = db.aggregate_commerce_for_content(
                cid, days=0, start_date=start_str, end_date=end_str, platform=plat
            )
            agg["sessions"] += commerce["sessions"]
            agg["purchases"] += commerce["purchases"]
            agg["revenue"] += commerce["revenue"]

    result: list[CohortPerformance] = []
    for key, agg in cohort_agg.items():
        watch_rates = agg["watch_rates"]
        avg_wtr = sum(watch_rates) / len(watch_rates) if watch_rates else None
        result.append(
            CohortPerformance(
                product_sku=agg["product_sku"],
                product_name=agg["product_name"],
                platform=agg["platform"],
                creative_format=agg["creative_format"],
                hook_type=agg["hook_type"],
                cta_type=agg["cta_type"],
                theme=agg["theme"],
                post_count=agg["post_count"],
                total_views=agg["total_views"],
                total_engagements=agg["total_engagements"],
                engagement_rate=_engagement_rate(agg["total_views"], agg["total_engagements"]),
                avg_watch_through_rate=avg_wtr,
                content_ids=agg["content_ids"],
                sessions=agg["sessions"],
                purchases=agg["purchases"],
                revenue=agg["revenue"],
            )
        )

    return result


def _empty_metric() -> Metric:
    return Metric(post_id=0, platform="", views=0, likes=0, shares=0, comments=0, saves=0)


def classify_winners_middles_losers(
    performances: list[CohortPerformance],
    winner_pct: float = 0.25,
    loser_pct: float = 0.25,
    rank_by: Literal[
        "engagement_rate", "views", "composite",
        "revenue", "sessions", "purchases",
    ] = "engagement_rate",
) -> tuple[list[CohortPerformance], list[CohortPerformance], list[CohortPerformance]]:
    """Classify cohorts into winners (top %), losers (bottom %), and middle.

    No auto-promote or auto-retire; this is for human review.
    rank_by: engagement_rate (default), views, composite, revenue, sessions, purchases.
    """
    if not performances:
        return [], [], []

    import math
    if rank_by == "engagement_rate":
        key_fn = lambda p: (p.engagement_rate, p.total_views)
    elif rank_by == "views":
        key_fn = lambda p: (p.total_views, p.engagement_rate)
    elif rank_by == "revenue":
        key_fn = lambda p: (p.revenue, p.purchases, p.sessions)
    elif rank_by == "sessions":
        key_fn = lambda p: (p.sessions, p.revenue, p.total_views)
    elif rank_by == "purchases":
        key_fn = lambda p: (p.purchases, p.revenue, p.sessions)
    else:
        # composite: engagement_rate * log(views+1) for balance
        key_fn = lambda p: (p.engagement_rate * math.log(p.total_views + 1), p.total_views)

    sorted_perf = sorted(performances, key=key_fn, reverse=True)
    n = len(sorted_perf)
    winner_count = max(1, int(n * winner_pct))
    loser_count = max(1, int(n * loser_pct))
    if winner_count + loser_count > n:
        loser_count = max(0, n - winner_count)

    winners = sorted_perf[:winner_count]
    losers = sorted_perf[-loser_count:] if loser_count > 0 else []
    middle = sorted_perf[winner_count : n - loser_count]

    return winners, middle, losers


def format_cohort_label(p: CohortPerformance) -> str:
    """Short label for a cohort in reports."""
    return (
        f"{p.product_name}/{p.platform}/{p.creative_format}/{p.theme}/{p.hook_type}"
    )


def get_image_motion_performance_summary(
    product_sku: str,
    days: int = 30,
    rank_by: Literal[
        "engagement_rate", "views", "composite",
        "revenue", "sessions", "purchases",
    ] = "engagement_rate",
) -> tuple[str, str]:
    """Product-first then global historical performance summary for image_motion_15s.

    Returns (summary_text, rationale) for the image-motion planner.
    rationale indicates source: "product_winners", "global_winners", or "default".
    """
    performances = gather_cohort_performances(days=days)
    image_motion = [p for p in performances if p.creative_format == "image_motion_15s"]
    if not image_motion:
        return (
            "Use a balanced mix of hero and lifestyle frames. Default style_family: realistic_cinematic. "
            "Vary at most 1–2 axes per creative. Require at least 1 hero-led frame.",
            "default",
        )

    product_specific = [p for p in image_motion if p.product_sku == product_sku]
    candidates = product_specific if product_specific else image_motion
    rationale = "product_winners" if product_specific else "global_winners"

    winners, _, _ = classify_winners_middles_losers(
        candidates, winner_pct=0.25, loser_pct=0.25, rank_by=rank_by
    )
    if not winners:
        return (
            "Use a balanced mix of hero and lifestyle frames. Default style_family: realistic_cinematic.",
            rationale,
        )

    parts = []
    for w in winners[:3]:  # Top 3 winning cohorts
        parts.append(
            f"{w.theme}/{w.hook_type}: engagement {w.engagement_rate:.2%}, "
            f"{w.total_views} views, {w.post_count} posts"
        )
    summary = (
        "Historical winners for image_motion_15s: " + "; ".join(parts) + ". "
        "Bias your style/role mix toward these themes and hooks when planning frames."
    )
    return summary, rationale
