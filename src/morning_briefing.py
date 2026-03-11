from __future__ import annotations

import logging
import smtplib
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from typing import NamedTuple

from src import bandit, config, db
from src.cost_tracker import check_budget
from src.organic_evaluation import (
    classify_winners_middles_losers,
    format_cohort_label,
    gather_cohort_performances,
)
from src.models import Metric, Post, Product, PLATFORMS

logger = logging.getLogger(__name__)


class _PostPerformance(NamedTuple):
    post: Post
    published_on: date
    product_name: str
    theme: str
    hook_type: str
    metrics: Metric
    engagement_rate: float


@dataclass(frozen=True)
class _PerformanceSummary:
    performances: list[_PostPerformance]
    platform_totals: dict[str, dict[str, int]]
    total_views: int
    total_likes: int
    total_shares: int
    total_comments: int
    total_saves: int
    total_engagements: int
    engagement_rate: float
    avg_views_per_post: float
    avg_watch_through_rate: float | None


def _engagement_rate(m: Metric) -> float:
    return (m.likes + m.comments + m.shares + m.saves) / max(m.views, 1)


def _empty_metric_totals() -> dict[str, int]:
    return {"views": 0, "likes": 0, "shares": 0, "comments": 0, "saves": 0}


def _published_on(post: Post) -> date | None:
    if not post.published_at:
        return None
    try:
        return date.fromisoformat(post.published_at[:10])
    except ValueError:
        return None


def _gather_recent_performance(days: int) -> list[_PostPerformance]:
    posts = db.list_recent_posts(days=days)
    performances: list[_PostPerformance] = []

    for post in posts:
        if post.id is None:
            continue

        published_on = _published_on(post)
        if published_on is None:
            continue

        metrics = db.latest_metrics_for_post(post.id)
        if not metrics:
            continue

        content = db.get_content(post.content_id)
        if not content:
            continue

        product = db.get_product(content.product_sku)
        product_name = product.name if product else content.product_sku
        rate = _engagement_rate(metrics)

        performances.append(_PostPerformance(
            post=post,
            published_on=published_on,
            product_name=product_name,
            theme=content.theme,
            hook_type=content.hook_type,
            metrics=metrics,
            engagement_rate=rate,
        ))

    return performances


def _summarize_performance(performances: list[_PostPerformance]) -> _PerformanceSummary:
    platform_totals = {platform: _empty_metric_totals() for platform in PLATFORMS}
    total_views = total_likes = total_shares = total_comments = total_saves = 0
    watch_through_rates: list[float] = []

    for perf in performances:
        metrics = perf.metrics
        total_views += metrics.views
        total_likes += metrics.likes
        total_shares += metrics.shares
        total_comments += metrics.comments
        total_saves += metrics.saves

        if metrics.watch_through_rate is not None:
            watch_through_rates.append(metrics.watch_through_rate)

        if perf.post.platform in platform_totals:
            totals = platform_totals[perf.post.platform]
            totals["views"] += metrics.views
            totals["likes"] += metrics.likes
            totals["shares"] += metrics.shares
            totals["comments"] += metrics.comments
            totals["saves"] += metrics.saves

    total_engagements = total_likes + total_shares + total_comments + total_saves
    avg_watch_through_rate = (
        sum(watch_through_rates) / len(watch_through_rates)
        if watch_through_rates else None
    )

    return _PerformanceSummary(
        performances=performances,
        platform_totals=platform_totals,
        total_views=total_views,
        total_likes=total_likes,
        total_shares=total_shares,
        total_comments=total_comments,
        total_saves=total_saves,
        total_engagements=total_engagements,
        engagement_rate=total_engagements / max(total_views, 1),
        avg_views_per_post=(total_views / len(performances)) if performances else 0.0,
        avg_watch_through_rate=avg_watch_through_rate,
    )


def _filter_performance_window(
    performances: list[_PostPerformance],
    start_on: date,
    end_on: date,
) -> list[_PostPerformance]:
    return [p for p in performances if start_on <= p.published_on <= end_on]


def _pct_change(current: float, prior: float) -> float | None:
    if prior <= 0:
        return None
    return ((current - prior) / prior) * 100


def _post_count_label(count: int) -> str:
    return f"{count} post" if count == 1 else f"{count} posts"


def _group_performance(
    performances: list[_PostPerformance],
    key_fn,
) -> list[tuple[object, _PerformanceSummary]]:
    grouped: defaultdict[object, list[_PostPerformance]] = defaultdict(list)
    for perf in performances:
        grouped[key_fn(perf)].append(perf)

    ranked = [
        (key, _summarize_performance(items))
        for key, items in grouped.items()
    ]
    ranked.sort(
        key=lambda item: (
            item[1].engagement_rate,
            item[1].total_views,
            len(item[1].performances),
        ),
        reverse=True,
    )
    return ranked


def _gather_yesterday_performance() -> tuple[list[_PostPerformance], dict[str, dict[str, int]]]:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    performances = [
        perf for perf in _gather_recent_performance(days=2)
        if perf.published_on.isoformat() == yesterday
    ]
    return performances, _summarize_performance(performances).platform_totals


def _cost_yesterday() -> float:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with db._connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS total FROM costs WHERE date(created_at)=?",
            (yesterday,),
        ).fetchone()
    return float(row["total"])


def _cost_7day_average() -> float:
    with db._connect() as conn:
        row = conn.execute(
            """SELECT COALESCE(AVG(daily), 0) AS avg_cost FROM (
                SELECT SUM(cost_usd) AS daily
                FROM costs
                WHERE created_at >= datetime('now', '-7 days')
                GROUP BY date(created_at)
            )""",
        ).fetchone()
    return float(row["avg_cost"])


def _clips_yesterday() -> int:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with db._connect() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT content_id) AS cnt FROM costs WHERE date(created_at)=?",
            (yesterday,),
        ).fetchone()
    return int(row["cnt"])


def _product_health(products: list[Product]) -> dict:
    today = date.today()
    seven_days_ago = (today - timedelta(days=7)).isoformat()
    now = datetime.utcnow()

    no_content: list[tuple[str, str]] = []
    declining: list[tuple[str, float]] = []
    should_pause: list[tuple[str, float]] = []
    awaiting_setup: list[str] = []

    for product in products:
        if not product.generation_ready or not product.last_content_date:
            awaiting_setup.append(product.name)
            continue

        if product.last_content_date < seven_days_ago:
            no_content.append((product.name, product.last_content_date))

        recent_rates: list[float] = []
        prior_rates: list[float] = []
        all_rates: list[float] = []

        for content in db.list_content_for_product(product.sku, limit=50):
            for post in db.list_posts_for_content(content.id):
                if post.id is None:
                    continue
                m = db.latest_metrics_for_post(post.id)
                if not m:
                    continue
                rate = _engagement_rate(m)
                all_rates.append(rate)

                if post.published_at:
                    try:
                        pub = datetime.fromisoformat(post.published_at[:19])
                        age = (now - pub).days
                        if age <= 7:
                            recent_rates.append(rate)
                        elif age <= 14:
                            prior_rates.append(rate)
                    except ValueError:
                        pass

        if recent_rates and prior_rates:
            recent_avg = sum(recent_rates) / len(recent_rates)
            prior_avg = sum(prior_rates) / len(prior_rates)
            if prior_avg > 0:
                trend = ((recent_avg - prior_avg) / prior_avg) * 100
                if trend < -20:
                    declining.append((product.name, trend))

        if len(all_rates) >= 5:
            avg = sum(all_rates) / len(all_rates)
            if avg < 0.01:
                should_pause.append((product.name, avg))

    return {
        "no_content": no_content,
        "declining": declining,
        "should_pause": should_pause,
        "awaiting_setup": awaiting_setup,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_briefing() -> str:
    today = date.today()
    lines: list[str] = []

    def heading(title: str) -> None:
        lines.append("")
        lines.append(f"\u2500\u2500 {title} \u2500\u2500")

    lines.append("=" * 52)
    lines.append(f"  VELURA MORNING BRIEFING \u2014 {today.isoformat()}")
    lines.append("=" * 52)

    # ── Section 1: Posts Published Yesterday ───────────────────────────────

    heading("POSTS PUBLISHED YESTERDAY")
    performances, platform_totals = _gather_yesterday_performance()
    yesterday_summary = _summarize_performance(performances)

    lines.append(
        f"Total: {yesterday_summary.total_views:,} views \u2502 {yesterday_summary.total_likes:,} likes \u2502 "
        f"{yesterday_summary.total_shares:,} shares \u2502 {yesterday_summary.total_comments:,} comments \u2502 "
        f"{yesterday_summary.total_saves:,} saves"
    )
    lines.append(f"Posts tracked: {len(performances)}")

    if performances:
        ranked = sorted(performances, key=lambda p: p.engagement_rate, reverse=True)

        lines.append("")
        lines.append("Top Performers:")
        for i, p in enumerate(ranked[:3], 1):
            lines.append(
                f"  {i}. [{p.product_name}] {p.theme}/{p.hook_type} on {p.post.platform}"
                f" \u2014 {p.engagement_rate:.1%} engagement ({p.metrics.views:,} views)"
            )

        if len(ranked) > 3:
            worst_start = max(3, len(ranked) - 3)
            worst = ranked[worst_start:]
            lines.append("")
            lines.append("Worst Performers:")
            for i, p in enumerate(worst, 1):
                lines.append(
                    f"  {i}. [{p.product_name}] {p.theme}/{p.hook_type} on {p.post.platform}"
                    f" \u2014 {p.engagement_rate:.1%} engagement ({p.metrics.views:,} views)"
                )
    else:
        lines.append("No posts published yesterday with metrics.")

    lines.append("")
    lines.append("Platform Breakdown:")
    plat_labels = {
        "youtube": "YouTube", "instagram": "Instagram",
        "tiktok": "TikTok", "x": "X",
    }
    for plat in PLATFORMS:
        t = platform_totals.get(plat, {})
        v = t.get("views", 0)
        lk = t.get("likes", 0)
        sh = t.get("shares", 0)
        cm = t.get("comments", 0)
        label = plat_labels.get(plat, plat)
        lines.append(
            f"  {label:>10}: {v:>8,} views \u2502 {lk:>6,} likes \u2502 "
            f"{sh:>6,} shares \u2502 {cm:>6,} comments"
        )

    # ── Section 2: 7-Day Performance ───────────────────────────────────────

    heading("7-DAY PERFORMANCE")
    current_start = today - timedelta(days=6)
    current_end = today
    prior_start = today - timedelta(days=13)
    prior_end = today - timedelta(days=7)

    recent_performances = _gather_recent_performance(days=15)
    current_window = _filter_performance_window(recent_performances, current_start, current_end)
    prior_window = _filter_performance_window(recent_performances, prior_start, prior_end)
    current_summary = _summarize_performance(current_window)
    prior_summary = _summarize_performance(prior_window)

    lines.append(f"Window: {current_start.isoformat()} to {current_end.isoformat()}")
    lines.append(
        f"Total: {current_summary.total_views:,} views \u2502 {current_summary.total_likes:,} likes \u2502 "
        f"{current_summary.total_shares:,} shares \u2502 {current_summary.total_comments:,} comments \u2502 "
        f"{current_summary.total_saves:,} saves"
    )
    lines.append(
        f"Posts tracked: {len(current_window)} \u2502 Avg/post: {current_summary.avg_views_per_post:.0f} views"
        f" \u2502 Engagement: {current_summary.engagement_rate:.1%}"
    )
    if current_summary.avg_watch_through_rate is not None:
        lines.append(f"Avg watch-through rate: {current_summary.avg_watch_through_rate:.1%}")

    if current_window:
        ranked = sorted(current_window, key=lambda p: p.engagement_rate, reverse=True)
        lines.append("")
        lines.append("Top Performers:")
        for i, p in enumerate(ranked[:3], 1):
            lines.append(
                f"  {i}. [{p.product_name}] {p.theme}/{p.hook_type} on {p.post.platform}"
                f" \u2014 {p.engagement_rate:.1%} engagement ({p.metrics.views:,} views)"
            )
        if len(ranked) > 3:
            worst_start = max(3, len(ranked) - 3)
            worst = ranked[worst_start:]
            lines.append("")
            lines.append("Worst Performers:")
            for i, p in enumerate(worst, 1):
                lines.append(
                    f"  {i}. [{p.product_name}] {p.theme}/{p.hook_type} on {p.post.platform}"
                    f" \u2014 {p.engagement_rate:.1%} engagement ({p.metrics.views:,} views)"
                )

    lines.append("")
    lines.append("Platform Breakdown:")
    for plat in PLATFORMS:
        t = current_summary.platform_totals.get(plat, {})
        v = t.get("views", 0)
        lk = t.get("likes", 0)
        sh = t.get("shares", 0)
        cm = t.get("comments", 0)
        label = plat_labels.get(plat, plat)
        lines.append(
            f"  {label:>10}: {v:>8,} views \u2502 {lk:>6,} likes \u2502 "
            f"{sh:>6,} shares \u2502 {cm:>6,} comments"
        )

    if prior_window:
        views_change = _pct_change(current_summary.total_views, prior_summary.total_views)
        engagement_change = _pct_change(
            current_summary.engagement_rate,
            prior_summary.engagement_rate,
        )
        post_delta = len(current_window) - len(prior_window)
        post_delta_label = f"{post_delta:+d}" if post_delta else "0"
        views_change_label = (
            f"views {'↑' if views_change >= 0 else '↓'}{abs(views_change):.0f}%"
            if views_change is not None else "views n/a"
        )
        engagement_change_label = (
            f"engagement {'↑' if engagement_change >= 0 else '↓'}{abs(engagement_change):.0f}%"
            if engagement_change is not None else "engagement n/a"
        )
        change_bits = [
            views_change_label,
            engagement_change_label,
            f"posts {post_delta_label}",
        ]
        change_summary = " \u2502 ".join(change_bits)
        lines.append(f"Vs prior 7 days: {change_summary}")
    else:
        lines.append("Vs prior 7 days: not enough historical data yet.")

    platform_rankings = [
        (platform, summary)
        for platform, summary in _group_performance(current_window, lambda p: p.post.platform)
        if summary.performances
    ]
    if platform_rankings:
        top_platform, top_platform_summary = platform_rankings[0]
        lines.append(
            f"Best platform: {plat_labels.get(str(top_platform), str(top_platform))}"
            f" \u2014 {top_platform_summary.engagement_rate:.1%} engagement across"
            f" {_post_count_label(len(top_platform_summary.performances))}"
        )

    product_rankings = _group_performance(current_window, lambda p: p.product_name)
    if product_rankings:
        most_viewed_product, most_viewed_summary = max(
            product_rankings,
            key=lambda item: (item[1].total_views, item[1].engagement_rate),
        )
        lines.append(
            f"Most viewed product: {most_viewed_product}"
            f" \u2014 {most_viewed_summary.total_views:,} views across"
            f" {_post_count_label(len(most_viewed_summary.performances))}"
        )

    # ── Section 3: Creative Insights ───────────────────────────────────────

    heading("CREATIVE INSIGHTS")
    combo_rankings = _group_performance(current_window, lambda p: (p.theme, p.hook_type))
    repeated_combos = [
        (combo, summary)
        for combo, summary in combo_rankings
        if len(summary.performances) >= 2
    ]

    if repeated_combos:
        lines.append("Top repeated combos (7d):")
        for i, (combo, summary) in enumerate(repeated_combos[:3], 1):
            theme, hook_type = combo
            lines.append(
                f"  {i}. {theme}/{hook_type} \u2014 {summary.engagement_rate:.1%} engagement"
                f" ({len(summary.performances)} posts, {summary.total_views:,} views)"
            )

        if len(repeated_combos) > 1:
            weakest = sorted(
                repeated_combos,
                key=lambda item: (
                    item[1].engagement_rate,
                    item[1].total_views,
                    len(item[1].performances),
                ),
            )[:min(2, len(repeated_combos) - 1)]
            if weakest:
                lines.append("")
                lines.append("Needs refresh:")
                for i, (combo, summary) in enumerate(weakest, 1):
                    theme, hook_type = combo
                    lines.append(
                        f"  {i}. {theme}/{hook_type} \u2014 {summary.engagement_rate:.1%} engagement"
                        f" ({_post_count_label(len(summary.performances))})"
                    )
    elif combo_rankings:
        theme, hook_type = combo_rankings[0][0]
        summary = combo_rankings[0][1]
        lines.append("Need more repeated posts to compare creative combos confidently.")
        lines.append(
            f"Current leader: {theme}/{hook_type} \u2014 {summary.engagement_rate:.1%} engagement"
            f" ({_post_count_label(len(summary.performances))})"
        )
    else:
        lines.append("No 7-day creative data available yet.")

    # ── Section 3b: Organic Evaluation (12-Creative Matrix) ───────────────────

    heading("ORGANIC EVALUATION (12-CREATIVE MATRIX)")
    cohort_perfs = gather_cohort_performances(
        days=15,
        start_on=current_start,
        end_on=current_end,
    )
    rank_by = str(config.get("bandit.ranking_objective", "engagement_rate"))
    if rank_by not in ("engagement_rate", "views", "composite", "revenue", "sessions", "purchases"):
        rank_by = "engagement_rate"
    winners, middles, losers = classify_winners_middles_losers(
        cohort_perfs,
        winner_pct=0.25,
        loser_pct=0.25,
        rank_by=rank_by,
    )

    if cohort_perfs:
        lines.append(
            f"Cohorts: {len(cohort_perfs)} (product × platform × format × hook × CTA)"
        )
        lines.append(f"Ranked by {rank_by}. Top 25% = winners, bottom 25% = losers.")
        lines.append("")
        total_revenue = sum(p.revenue for p in cohort_perfs)
        if total_revenue > 0:
            lines.append(f"Commerce (7d): ${total_revenue:,.2f} revenue across {sum(p.purchases for p in cohort_perfs)} purchases")
        if winners:
            lines.append("")
            lines.append("Winners (repeat or promote):")
            for p in winners[:5]:
                wtr = f" WTR {p.avg_watch_through_rate:.0%}" if p.avg_watch_through_rate else ""
                commerce = f" ${p.revenue:.0f}" if p.revenue > 0 else ""
                lines.append(
                    f"  \u2713 {format_cohort_label(p)}"
                    f" \u2014 {p.engagement_rate:.1%} ({p.total_views:,} views{wtr}{commerce})"
                )
        if middles:
            lines.append("")
            lines.append("Middle (consider remixing):")
            for p in middles[:3]:
                lines.append(
                    f"  \u25cb {format_cohort_label(p)}"
                    f" \u2014 {p.engagement_rate:.1%} ({p.total_views:,} views)"
                )
        if losers:
            lines.append("")
            lines.append("Losers (retire or refresh):")
            for p in losers[:5]:
                lines.append(
                    f"  \u2717 {format_cohort_label(p)}"
                    f" \u2014 {p.engagement_rate:.1%} ({p.total_views:,} views)"
                )
    else:
        lines.append("No cohort data in the 7-day window yet.")
        lines.append("Publish creatives across the matrix to see winner/middle/loser labels.")

    # ── Section 4: Bandit Recommendations ──────────────────────────────────

    heading("BANDIT RECOMMENDATIONS")
    products = db.list_products(active_only=True, exclude_excluded=True)
    daily_slots = int(config.get("bandit.daily_slots", 8))

    if products:
        rec = bandit.recommend(daily_slots)
        arms = {(arm.theme, arm.hook_type): arm for arm in db.list_bandit_arms()}
        lines.append(f"Recommended allocation ({daily_slots} posts):")
        for alloc in rec.allocations:
            arm = arms.get((alloc.theme, alloc.hook_type))
            trials = max(int((arm.alpha + arm.beta) - 2), 0) if arm else 0
            mode = "explore" if trials < 5 else "exploit"
            marker = "\u25c7" if mode == "explore" else "\u25c6"
            learned_rate = (bandit.posterior_mean(arm) * 100) if arm else 50.0
            lines.append(
                f"  {marker} {alloc.theme}/{alloc.hook_type} ({mode})"
                f" \u2014 mean {learned_rate:.0f}%, score {alloc.score:.3f}, clips {alloc.count}"
            )

        suggested_per_product = max(daily_slots // max(len(products), 1), 1)
        lines.append("")
        lines.append(
            f"Suggested split: about {suggested_per_product} per product across {len(products)} active products"
        )
    else:
        lines.append("No active products to recommend for.")

    # ── Section 5: Product Health ──────────────────────────────────────────

    heading("PRODUCT HEALTH")
    health = _product_health(products)

    if health["no_content"]:
        lines.append("No content in 7 days:")
        for name, last_dt in health["no_content"]:
            lines.append(f"  \u2022 {name} (last: {last_dt})")
    else:
        lines.append("All products have recent content \u2713")

    if health["declining"]:
        lines.append("Declining engagement (consider refreshing images):")
        for name, trend in health["declining"]:
            lines.append(f"  \u2022 {name} (\u2193{abs(trend):.0f}%)")

    if health["should_pause"]:
        lines.append("Consider pausing (avg engagement <1%):")
        for name, avg in health["should_pause"]:
            lines.append(f"  \u2022 {name} (avg {avg:.2%})")

    if health["awaiting_setup"]:
        lines.append("Awaiting setup:")
        for name in health["awaiting_setup"]:
            lines.append(f"  \u2022 {name}")

    # ── Section 6: Budget ──────────────────────────────────────────────────

    heading("BUDGET")
    spent_today, daily_budget, within_budget = check_budget()
    yesterday_cost = _cost_yesterday()
    clips = _clips_yesterday()
    cost_per_clip = yesterday_cost / clips if clips else 0.0
    avg_7d = _cost_7day_average()

    remaining = daily_budget - yesterday_cost
    trend_arrow = "\u2193" if yesterday_cost <= avg_7d else "\u2191"

    lines.append(
        f"Yesterday: ${yesterday_cost:.2f} / ${daily_budget:.2f} daily budget"
        f" (${max(remaining, 0):.2f} remaining)"
    )
    lines.append(f"Cost per clip: ${cost_per_clip:.2f} avg ({clips} clips)")
    lines.append(f"Trend: {trend_arrow} vs 7-day avg (${avg_7d:.2f}/day)")

    if not within_budget:
        lines.append("\u26a0 TODAY'S BUDGET ALREADY EXCEEDED")

    # ── Section 7: Action Items ────────────────────────────────────────────

    heading("ACTION ITEMS")
    actions: list[str] = []

    for name, last_dt in health["no_content"]:
        days_stale = (today - date.fromisoformat(last_dt)).days
        actions.append(f"Prioritize {name} (no content in {days_stale} days)")

    for name, _ in health["declining"]:
        actions.append(f"Consider adding new images for {name}")

    if not current_window:
        actions.append("No posts in the past 7 days \u2014 check scheduling and posting flow")
    elif prior_window:
        views_change = _pct_change(current_summary.total_views, prior_summary.total_views)
        engagement_change = _pct_change(
            current_summary.engagement_rate,
            prior_summary.engagement_rate,
        )
        if views_change is not None and views_change < -20:
            actions.append(f"7-day views down {abs(views_change):.0f}% vs prior week")
        if engagement_change is not None and engagement_change < -15:
            actions.append(
                f"7-day engagement down {abs(engagement_change):.0f}% vs prior week"
            )

    if repeated_combos:
        weakest_combo, weakest_summary = min(
            repeated_combos,
            key=lambda item: (
                item[1].engagement_rate,
                item[1].total_views,
                len(item[1].performances),
            ),
        )
        if weakest_summary.engagement_rate < 0.015:
            theme, hook_type = weakest_combo
            actions.append(
                f"Retest {theme}/{hook_type} creative"
                f" (7-day engagement {weakest_summary.engagement_rate:.1%})"
            )

    if daily_budget > 0:
        usage_pct = yesterday_cost / daily_budget * 100
        if usage_pct > 80:
            actions.append(f"\u26a0 Budget usage at {usage_pct:.0f}% of daily limit")

    if not within_budget:
        actions.append("\u26a0 Budget exceeded today \u2014 generation paused")

    if not actions:
        actions.append("No urgent actions. Systems nominal.")

    for action in actions:
        lines.append(f"  \u2022 {action}")

    lines.append("")
    lines.append("\u2500" * 52)

    return "\n".join(lines)


def display_briefing(briefing: str) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    if not _supports_unicode_output():
        print(_ascii_safe_text(briefing))
        return

    console = Console()

    section_styles = {
        "YESTERDAY'S PERFORMANCE": "bright_cyan",
        "7-DAY PERFORMANCE": "cyan",
        "CREATIVE INSIGHTS": "bright_blue",
        "ORGANIC EVALUATION (12-CREATIVE MATRIX)": "bright_blue",
        "BANDIT RECOMMENDATIONS": "bright_magenta",
        "PRODUCT HEALTH": "bright_yellow",
        "BUDGET": "bright_green",
        "ACTION ITEMS": "bright_red",
    }

    sections: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = []

    for line in briefing.split("\n"):
        if line.startswith("\u2500\u2500 ") and line.endswith(" \u2500\u2500"):
            if current_title or current_lines:
                sections.append((current_title, current_lines))
            current_title = line[3:-3]
            current_lines = []
        elif line.startswith("=" * 10) or line.startswith("\u2500" * 10):
            if not current_title and not sections:
                current_lines.append(line)
        else:
            current_lines.append(line)

    if current_title or current_lines:
        sections.append((current_title, current_lines))

    console.print()

    if sections and not sections[0][0]:
        header_text = "\n".join(
            ln for ln in sections[0][1] if ln.strip() and not ln.startswith("=")
        )
        console.print(Panel(
            Text(header_text, justify="center", style="bold bright_white"),
            style="bold blue", padding=(1, 4),
        ))
        sections = sections[1:]

    for title, content_lines in sections:
        body = "\n".join(content_lines).strip()
        if not body:
            continue
        style = section_styles.get(title, "white")
        console.print(Panel(
            body,
            title=f"[bold]{title}[/bold]",
            title_align="left",
            border_style=style,
            padding=(0, 2),
        ))

    console.print()


def _supports_unicode_output() -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or ""
    if not encoding:
        return True
    try:
        "│".encode(encoding)
    except UnicodeEncodeError:
        return False
    except LookupError:
        return False
    return True


def _ascii_safe_text(text: str) -> str:
    replacements = {
        "│": "|",
        "─": "-",
        "—": "-",
        "•": "*",
        "◆": "*",
        "◇": "*",
        "✓": "OK",
        "⚠": "WARNING",
        "↑": "up ",
        "↓": "down ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def email_briefing(briefing: str) -> None:
    email_to = config.get("briefing.email")
    if not email_to:
        return

    smtp_host = config.get("briefing.smtp_host", "localhost")
    smtp_port = int(config.get("briefing.smtp_port", 587))
    smtp_user = config.get("briefing.smtp_user", "")
    smtp_pass = config.get("briefing.smtp_pass", "")
    from_addr = config.get("briefing.from_email", smtp_user or "velura@localhost")
    use_tls = config.get("briefing.smtp_tls", True)

    msg = MIMEText(briefing, "plain", "utf-8")
    msg["Subject"] = f"Velura Morning Briefing \u2014 {date.today().isoformat()}"
    msg["From"] = from_addr
    msg["To"] = email_to

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if use_tls:
                server.starttls()
            if smtp_user:
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, [email_to], msg.as_string())
    except Exception:
        logger.exception("Failed to send morning briefing email to %s", email_to)
