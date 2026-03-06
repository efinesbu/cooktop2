from __future__ import annotations

import logging
import smtplib
from collections import defaultdict
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from typing import NamedTuple

from src import bandit, config, db
from src.creative_strategy import base_weight
from src.cost_tracker import check_budget
from src.models import Metric, Post, Product, PLATFORMS

logger = logging.getLogger(__name__)


class _PostPerformance(NamedTuple):
    post: Post
    product_name: str
    theme: str
    hook_type: str
    metrics: Metric
    engagement_rate: float


def _engagement_rate(m: Metric) -> float:
    return (m.likes + m.comments + m.shares + m.saves) / max(m.views, 1)


def _gather_yesterday_performance() -> tuple[list[_PostPerformance], dict[str, dict[str, int]]]:
    posts = db.list_recent_posts(days=2)
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    performances: list[_PostPerformance] = []
    platform_totals: dict[str, dict[str, int]] = {
        p: {"views": 0, "likes": 0, "shares": 0, "comments": 0, "saves": 0}
        for p in PLATFORMS
    }

    for post in posts:
        if not post.published_at or post.published_at[:10] != yesterday:
            continue
        if post.id is None:
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
            post=post, product_name=product_name, theme=content.theme,
            hook_type=content.hook_type, metrics=metrics, engagement_rate=rate,
        ))

        if post.platform in platform_totals:
            pt = platform_totals[post.platform]
            pt["views"] += metrics.views
            pt["likes"] += metrics.likes
            pt["shares"] += metrics.shares
            pt["comments"] += metrics.comments
            pt["saves"] += metrics.saves

    return performances, platform_totals


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

    # ── Section 1: Yesterday's Performance ─────────────────────────────────

    heading("YESTERDAY'S PERFORMANCE")
    performances, platform_totals = _gather_yesterday_performance()

    total_views = sum(p.metrics.views for p in performances)
    total_likes = sum(p.metrics.likes for p in performances)
    total_shares = sum(p.metrics.shares for p in performances)
    total_comments = sum(p.metrics.comments for p in performances)
    total_saves = sum(p.metrics.saves for p in performances)

    lines.append(
        f"Total: {total_views:,} views \u2502 {total_likes:,} likes \u2502 "
        f"{total_shares:,} shares \u2502 {total_comments:,} comments \u2502 "
        f"{total_saves:,} saves"
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
        lines.append("No post data available for yesterday.")

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

    # ── Section 2: Bandit Recommendations ──────────────────────────────────

    heading("BANDIT RECOMMENDATIONS")
    products = db.list_products(active_only=True, exclude_excluded=True)

    if products:
        for product in products:
            rec = bandit.recommend(product.sku, 4)
            lines.append(f"\n  {product.name} ({product.sku}):")

            arms = {(a.theme, a.hook_type): a for a in db.get_bandit_arms(product.sku)}
            for alloc in rec.allocations:
                arm = arms.get((alloc.theme, alloc.hook_type))
                trials = (arm.successes + arm.failures - 2) if arm else 0
                mode = "explore" if trials < 5 else "exploit"
                marker = "\u25c7" if mode == "explore" else "\u25c6"
                learned_rate = (
                    (arm.successes / max(arm.successes + arm.failures, 1)) * 100
                    if arm else 50.0
                )
                lines.append(
                    f"    {marker} {alloc.theme}/{alloc.hook_type} ({mode})"
                    f" \u2014 base {base_weight(alloc.theme, alloc.hook_type):.2f},"
                    f" learned {learned_rate:.0f}%, score {alloc.score:.3f}, clips {alloc.count}"
                )
    else:
        lines.append("No active products to recommend for.")

    # ── Section 3: Product Health ──────────────────────────────────────────

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

    # ── Section 4: Budget ──────────────────────────────────────────────────

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

    # ── Section 5: Action Items ────────────────────────────────────────────

    heading("ACTION ITEMS")
    actions: list[str] = []

    for name, last_dt in health["no_content"]:
        days_stale = (today - date.fromisoformat(last_dt)).days
        actions.append(f"Prioritize {name} (no content in {days_stale} days)")

    for name, _ in health["declining"]:
        actions.append(f"Consider adding new images for {name}")

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

    console = Console()

    section_styles = {
        "YESTERDAY'S PERFORMANCE": "bright_cyan",
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
