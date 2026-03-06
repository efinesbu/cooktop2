from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src import bandit, config, db, storage
from src.analytics import PULLERS
from src.creative_strategy import base_weight
from src.cost_tracker import check_budget, content_cost_summary
from src.image_generator import generate_starting_image
from src.models import Content, HOOK_TYPES, PLATFORMS, PlatformPayload, Post, Product, THEMES
from src.morning_briefing import display_briefing, email_briefing, generate_briefing
from src.posters.instagram import InstagramPoster
from src.posters.tiktok import TikTokPoster
from src.posters.x import XPoster
from src.posters.youtube import YouTubePoster
from src.product_images import register_images
from src.prompt_generator import generate_content
from src.video_generator import generate_video

console = Console()

POSTERS = {
    "youtube": YouTubePoster,
    "instagram": InstagramPoster,
    "tiktok": TikTokPoster,
    "x": XPoster,
}


def _init():
    db.init_db()
    storage.ensure_dirs()


def _post_content_to_all(content: Content, product: Product,
                         captions: dict[str, str], hashtags: list[str]):
    results = []
    enabled = config.enabled_platforms("posting")
    if not enabled:
        console.print(
            "[yellow]No posting platforms are configured.[/yellow] "
            "Add the required credentials or set `platforms.enabled` in config.yaml."
        )
        return results

    for platform in enabled:
        poster_cls = POSTERS[platform]
        try:
            poster = poster_cls()
            post = poster.post(content, product, captions, hashtags)
            console.print(f"  [green]✓[/green] Posted to {platform} (post_id={post.post_id})")
            results.append(post)
        except Exception as exc:
            console.print(f"  [red]✗[/red] Failed to post to {platform}: {exc}")
    return results


def _hashtags_to_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [tag.strip().lstrip("#") for tag in raw.split(",") if tag.strip()]


def _print_prompt(content: Content) -> None:
    """Print the generated prompt/script to the terminal for visibility."""
    lines = []
    if content.theme or content.hook_type:
        strategy = _format_strategy_label(content.theme or None, content.hook_type or None)
        if strategy != "prompt-selected":
            lines.append(f"[bold]Theme / Hook:[/bold] {strategy}")
    if content.hook_text:
        lines.append(f"[bold]Hook:[/bold] {content.hook_text}")
    if content.starting_image_prompt:
        lines.append(f"[bold]Starting image:[/bold] {content.starting_image_prompt}")
    if content.scene_1_desc:
        lines.append(f"[bold]Scene 1 (visual):[/bold] {content.scene_1_desc}")
    if content.scene_1_script:
        lines.append(f"[bold]Scene 1 (voiceover):[/bold] {content.scene_1_script}")
    if content.scene_2_desc:
        lines.append(f"[bold]Scene 2 (visual):[/bold] {content.scene_2_desc}")
    if content.scene_2_script:
        lines.append(f"[bold]Scene 2 (voiceover):[/bold] {content.scene_2_script}")
    if lines:
        console.print(Panel("\n".join(lines), title="Generated prompt", border_style="dim"))


def _normalize_product_url(url: str | None) -> str | None:
    if not url:
        return None
    normalized = url.strip().rstrip("/")
    if not normalized:
        return None
    if not normalized.startswith(("http://", "https://")):
        normalized = f"https://{normalized}"
    return normalized


def _schedule_payloads_for_content(content: Content) -> int:
    payloads = db.list_platform_payloads(content.id)
    if not payloads:
        console.print(
            f"[yellow]No platform payloads found for {content.id[:12]}. "
            "Generate and persist payloads before scheduling.[/yellow]"
        )
        return 0

    stagger_minutes = config.get("posting.stagger_minutes", {}) or {}
    enabled = set(config.enabled_platforms("posting"))
    now = datetime.utcnow()
    scheduled = 0
    skipped = 0
    for payload in payloads:
        if payload.status == "posted":
            continue
        if payload.platform not in enabled:
            payload.publish_at = None
            payload.status = "pending"
            payload.last_error = "Platform not enabled in config"
            payload.id = db.upsert_platform_payload(payload)
            skipped += 1
            continue
        delay_minutes = int(stagger_minutes.get(payload.platform, 0) or 0)
        payload.publish_at = (now + timedelta(minutes=delay_minutes)).strftime("%Y-%m-%d %H:%M:%S")
        payload.status = "scheduled"
        payload.last_error = None
        payload.id = db.upsert_platform_payload(payload)
        scheduled += 1
    if skipped:
        console.print(
            f"[yellow]Skipped {skipped}[/yellow] payloads for platforms not enabled in config."
        )
    return scheduled


def _post_platform_payload(payload: PlatformPayload, content: Content, product: Product) -> Post:
    if payload.platform not in POSTERS:
        raise ValueError(f"No poster configured for platform '{payload.platform}'")
    if not content.video_local_path:
        raise FileNotFoundError("Content has no video_local_path set")

    video_path = Path(content.video_local_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    poster = POSTERS[payload.platform]()
    hashtags = _hashtags_to_list(payload.hashtags)
    post_id = poster.upload(video_path, payload.caption or "", hashtags)

    post = Post(
        content_id=content.id,
        platform=payload.platform,
        post_id=post_id,
        caption=payload.caption,
        hashtags=",".join(hashtags),
        utm_url=payload.utm_url,
    )
    post.id = db.insert_post(post)
    return post


def _format_strategy_label(theme: str | None, hook_type: str | None) -> str:
    parts = []
    if theme:
        parts.append(theme)
    if hook_type:
        parts.append(hook_type)
    return " / ".join(parts) if parts else "prompt-selected"


def _generate_single(product: Product, theme: str | None, hook_type: str | None,
                     should_post: bool) -> Optional[Content]:
    spent, budget, within = check_budget()
    if not within:
        console.print(
            f"[red]Budget exhausted[/red] (${spent:.2f} / ${budget:.2f}). Skipping."
        )
        return None

    images = db.list_product_images(product.sku)
    if not images:
        console.print(f"[yellow]No images registered for {product.sku}, continuing anyway.[/yellow]")

    console.print(f"  Generating prompt … ({_format_strategy_label(theme, hook_type)})")
    content, extras = generate_content(product, theme, hook_type, images)
    _print_prompt(content)
    captions: dict[str, str] = extras["platform_captions"]
    hashtags: list[str] = extras["hashtags"]

    console.print("  Generating starting image …")
    starting_image_path = generate_starting_image(content, product)

    console.print("  Generating video …")
    generate_video(content, starting_image_path, product)

    db.update_last_content_date(product.sku)
    console.print(f"  [green]✓[/green] Content [bold]{content.id}[/bold] created")

    if should_post:
        _post_content_to_all(content, product, captions, hashtags)

    return content


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """Velura Content Automation System"""


# ---------------------------------------------------------------------------
# sync-products
# ---------------------------------------------------------------------------

@cli.command("sync-products")
def sync_products_cmd():
    """Sync product catalog from Shopify if configured."""
    _init()
    if not config.get("shopify.store_url") or not config.get("shopify.admin_api_token"):
        console.print(
            "[yellow]Shopify sync is not configured.[/yellow] "
            "Use `python cli.py add-product --sku <sku> --name <name>` for manual catalog setup."
        )
        return

    from src.shopify import sync_products

    try:
        products = sync_products()
    except Exception as exc:
        console.print(f"[red]Sync failed:[/red] {exc}")
        sys.exit(1)

    table = Table(title="Synced Products")
    table.add_column("SKU", style="cyan")
    table.add_column("Name")
    table.add_column("Price", justify="right")
    table.add_column("Active", justify="center")
    table.add_column("Ready", justify="center")
    for p in products:
        table.add_row(
            p.sku,
            p.name,
            f"${p.price:.2f}" if p.price else "—",
            "✓" if p.active else "✗",
            "✓" if p.generation_ready else "✗",
        )
    console.print(table)
    console.print(f"\n[green]{len(products)}[/green] products synced.")


@cli.command("add-product")
@click.option("--sku", required=True, help="Internal product SKU/slug used by the workflow")
@click.option("--name", required=True, help="Product display name")
@click.option("--category", default=None, help="Optional category")
@click.option("--price", type=float, default=None, help="Optional price")
@click.option("--url", "product_url", default=None, help="Optional full storefront product URL")
def add_product_cmd(
    sku: str,
    name: str,
    category: Optional[str],
    price: Optional[float],
    product_url: Optional[str],
):
    """Create or update a product without Shopify sync."""
    _init()

    product = Product(
        sku=sku.strip(),
        name=name.strip(),
        category=category.strip() if category else None,
        price=price,
        product_url=_normalize_product_url(product_url),
    )
    db.upsert_product(product)

    console.print(
        f"[green]Saved[/green] product [bold]{product.sku}[/bold] ({product.name}). "
        "Next step: place images in the product-images folder, then run "
        f"`python cli.py register-images --product {product.sku}`."
    )


# ---------------------------------------------------------------------------
# register-images
# ---------------------------------------------------------------------------

@cli.command("register-images")
@click.option("--product", "slug", required=True, help="Product SKU")
def register_images_cmd(slug: str):
    """Register local images for a product."""
    _init()
    try:
        images = register_images(slug)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    if not images:
        console.print(f"[yellow]No images found for {slug}.[/yellow]")
        return

    table = Table(title=f"Registered Images — {slug}")
    table.add_column("ID", justify="right")
    table.add_column("Type", style="cyan")
    table.add_column("Path")
    for img in images:
        table.add_row(str(img.id or "—"), img.image_type, img.file_path)
    console.print(table)
    console.print(f"\n[green]{len(images)}[/green] images registered.")


# ---------------------------------------------------------------------------
# morning-briefing
# ---------------------------------------------------------------------------

@cli.command("morning-briefing")
def morning_briefing_cmd():
    """Generate and send the daily morning briefing."""
    _init()
    try:
        briefing = generate_briefing()
        display_briefing(briefing)
        email_briefing(briefing)
        console.print("[green]Briefing sent.[/green]")
    except Exception as exc:
        console.print(f"[red]Briefing failed:[/red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--auto", "auto_mode", is_flag=True, help="Use bandit recommendations")
@click.option("--product", "slugs", multiple=True, help="Product SKU (repeatable)")
@click.option("--theme", "themes", multiple=True, type=click.Choice(THEMES), help="Theme (repeatable)")
@click.option("--hook", "hooks", multiple=True, type=click.Choice(HOOK_TYPES), help="Hook type (repeatable)")
@click.option("--count", default=4, show_default=True, help="Clips per product")
@click.option(
    "--rotate-theme-hook",
    is_flag=True,
    help="Manual mode: when count > 1, cycle through provided --theme/--hook values per clip.",
)
@click.option("--post", "should_post", is_flag=True, help="Deprecated: use preview, approve, schedule, and post-due")
def run(auto_mode: bool, slugs: tuple[str, ...], themes: tuple[str, ...],
        hooks: tuple[str, ...], count: int, rotate_theme_hook: bool, should_post: bool):
    """Generate content — manually or via bandit recommendations."""
    _init()

    if should_post:
        console.print(
            "[red]--post is deprecated for the approval-first workflow.[/red] "
            "Use `preview`, `approve`, `schedule`, and `post-due` instead."
        )
        sys.exit(1)

    if auto_mode and (slugs or themes or hooks or rotate_theme_hook):
        console.print(
            "[red]--auto cannot be combined with --product/--theme/--hook/--rotate-theme-hook[/red]"
        )
        sys.exit(1)

    if auto_mode:
        _run_auto(count, should_post)
    else:
        _run_manual(slugs, themes, hooks, count, should_post, rotate_theme_hook=rotate_theme_hook)


def _run_auto(count: int, should_post: bool):
    products = db.list_products(
        active_only=True,
        exclude_excluded=True,
        generation_ready_only=True,
    )
    if not products:
        console.print("[yellow]No eligible products found.[/yellow]")
        return

    total = 0
    for product in products:
        console.print(Panel(f"[bold]{product.name}[/bold] ({product.sku})", style="blue"))
        rec = bandit.recommend(product.sku, count)
        for alloc in rec.allocations:
            for _ in range(alloc.count):
                result = _generate_single(product, alloc.theme, alloc.hook_type, should_post)
                if result:
                    total += 1

    console.print(f"\n[green]{total}[/green] pieces of content generated across {len(products)} products.")


def _run_manual(slugs: tuple[str, ...], themes: tuple[str, ...],
                hooks: tuple[str, ...], count: int, should_post: bool,
                rotate_theme_hook: bool = False):
    if not slugs:
        console.print("[red]Provide at least one --product or use --auto.[/red]")
        sys.exit(1)

    total = 0
    for slug in slugs:
        product = db.get_product(slug)
        if not product:
            console.print(f"[red]Product {slug} not found — skipping.[/red]")
            continue

        console.print(Panel(f"[bold]{product.name}[/bold] ({product.sku})", style="blue"))
        for theme, hook in _manual_strategy_runs(themes, hooks, count, rotate_theme_hook):
            result = _generate_single(product, theme, hook, should_post)
            if result:
                total += 1

    console.print(f"\n[green]{total}[/green] pieces of content generated.")


def _manual_strategy_runs(
    themes: tuple[str, ...],
    hooks: tuple[str, ...],
    count: int,
    rotate_theme_hook: bool,
) -> list[tuple[str | None, str | None]]:
    theme_values: tuple[str | None, ...] = themes or (None,)
    hook_values: tuple[str | None, ...] = hooks or (None,)
    if not rotate_theme_hook or count <= 1:
        return [
            (theme, hook)
            for theme in theme_values
            for hook in hook_values
            for _ in range(count)
        ]

    return [
        (
            theme_values[index % len(theme_values)],
            hook_values[index % len(hook_values)],
        )
        for index in range(count)
    ]


# ---------------------------------------------------------------------------
# exclude / include
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--product", "slug", required=True, help="Product SKU")
@click.option("--reason", required=True, help="Reason for exclusion")
def exclude(slug: str, reason: str):
    """Exclude a product from content generation."""
    _init()
    db.exclude_product(slug, reason)
    console.print(f"[yellow]{slug}[/yellow] excluded: {reason}")


@cli.command()
@click.option("--product", "slug", required=True, help="Product SKU")
def include(slug: str):
    """Re-include a previously excluded product."""
    _init()
    db.include_product(slug)
    console.print(f"[green]{slug}[/green] re-included.")


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--today", is_flag=True, required=True, help="Show today's content")
def preview(today: bool):
    """Preview generated content."""
    _init()
    items = db.list_content_today()
    if not items:
        console.print("[yellow]No content generated today.[/yellow]")
        return

    table = Table(title="Today's Content")
    table.add_column("ID", style="cyan", max_width=12)
    table.add_column("Product")
    table.add_column("Theme")
    table.add_column("Hook Type")
    table.add_column("Review", justify="center")
    table.add_column("Payloads", justify="right")
    for c in items:
        payload_count = len(db.list_platform_payloads(c.id))
        table.add_row(
            c.id[:12],
            c.product_sku,
            c.theme,
            c.hook_type,
            c.review_status,
            str(payload_count),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# approve / reject / schedule / post
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--content-id", required=True, help="Approve a specific content piece")
def approve(content_id: str):
    """Approve a generated content item for scheduling/posting."""
    _init()
    content = db.get_content(content_id)
    if not content:
        console.print(f"[red]Content {content_id} not found.[/red]")
        sys.exit(1)
    db.approve_content(content_id)
    console.print(
        f"[green]Approved[/green] {content_id}. "
        "Next step: run `python cli.py schedule --content-id <id>`."
    )


@cli.command()
@click.option("--content-id", required=True, help="Reject a specific content piece")
@click.option("--reason", required=True, help="Reason for rejection")
def reject(content_id: str, reason: str):
    """Reject a generated content item."""
    _init()
    content = db.get_content(content_id)
    if not content:
        console.print(f"[red]Content {content_id} not found.[/red]")
        sys.exit(1)
    db.reject_content(content_id, reason)
    console.print(f"[yellow]Rejected[/yellow] {content_id}: {reason}")


@cli.command()
@click.option("--today", is_flag=True, help="Schedule all approved content from today")
@click.option("--content-id", "content_id", default=None, help="Schedule a specific approved content piece")
def schedule(today: bool, content_id: Optional[str]):
    """Schedule approved content for staggered posting."""
    _init()

    if not today and not content_id:
        console.print("[red]Provide either --today or --content-id.[/red]")
        sys.exit(1)

    if content_id:
        _schedule_single(content_id)
    elif today:
        _schedule_today()


@cli.command("post-due")
def post_due_cmd():
    """Post all payloads that are due based on publish_at."""
    _init()
    _post_due()

@cli.command("post")
@click.option("--today", is_flag=True, help="Post all approved content from today")
@click.option("--content-id", "content_id", default=None, help="Post a specific content piece")
def post_cmd(today: bool, content_id: Optional[str]):
    """Post content to all platforms."""
    _init()

    if not today and not content_id:
        console.print("[red]Provide either --today or --content-id.[/red]")
        sys.exit(1)

    if content_id:
        _post_single(content_id)
    elif today:
        console.print(
            "[yellow]`post --today` now acts as a convenience wrapper: "
            "schedule approved content from today, then post anything due.[/yellow]"
        )
        _schedule_today()
        _post_due()


def _schedule_single(content_id: str):
    content = db.get_content(content_id)
    if not content:
        console.print(f"[red]Content {content_id} not found.[/red]")
        sys.exit(1)
    if content.review_status != "approved":
        console.print(
            f"[red]Content {content_id} is not approved (status={content.review_status}).[/red]"
        )
        sys.exit(1)

    scheduled = _schedule_payloads_for_content(content)
    console.print(f"[green]Scheduled {scheduled}[/green] payloads for {content.id[:12]}.")


def _schedule_today():
    items = [c for c in db.list_content_today() if c.review_status == "approved"]
    if not items:
        console.print("[yellow]No approved content to schedule today.[/yellow]")
        return

    total = 0
    for content in items:
        total += _schedule_payloads_for_content(content)
    console.print(f"\n[green]Scheduled {total}[/green] payloads across {len(items)} approved items.")


def _post_single(content_id: str):
    content = db.get_content(content_id)
    if not content:
        console.print(f"[red]Content {content_id} not found.[/red]")
        sys.exit(1)

    product = db.get_product(content.product_sku)
    if not product:
        console.print(f"[red]Product {content.product_sku} not found.[/red]")
        sys.exit(1)

    payloads = db.list_platform_payloads(content.id)
    if payloads:
        enabled = set(config.enabled_platforms("posting"))
        posted = 0
        skipped = 0
        console.print(Panel(f"Posting [bold]{content.id}[/bold]", style="blue"))
        for payload in payloads:
            if payload.status == "posted":
                continue
            if payload.platform not in enabled:
                console.print(
                    f"  [yellow]↷[/yellow] Skipping {payload.platform}: platform not enabled in config"
                )
                skipped += 1
                continue
            try:
                post = _post_platform_payload(payload, content, product)
                if payload.id is not None:
                    db.update_platform_payload_status(payload.id, "posted")
                console.print(
                    f"  [green]✓[/green] Posted to {payload.platform} (post_id={post.post_id})"
                )
                posted += 1
            except Exception as exc:
                if payload.id is not None:
                    db.update_platform_payload_status(payload.id, "failed", str(exc))
                console.print(f"  [red]✗[/red] Failed to post to {payload.platform}: {exc}")
        console.print(
            f"\n[green]Posted {posted}[/green] payloads for {content.id[:12]}. "
            f"[yellow]Skipped {skipped}[/yellow]."
        )
        return

    captions, hashtags = _load_post_metadata(content)
    console.print(
        "[yellow]No persisted payloads found; falling back to reconstructed post metadata.[/yellow]"
    )
    console.print(Panel(f"Posting [bold]{content.id}[/bold]", style="blue"))
    _post_content_to_all(content, product, captions, hashtags)


def _post_due():
    payloads = db.list_due_platform_payloads()
    if not payloads:
        console.print("[yellow]No scheduled payloads are due.[/yellow]")
        return

    enabled = set(config.enabled_platforms("posting"))
    posted = 0
    skipped = 0
    for payload in payloads:
        if payload.platform not in enabled:
            payload.publish_at = None
            payload.status = "pending"
            payload.last_error = "Platform not enabled in config"
            payload.id = db.upsert_platform_payload(payload)
            console.print(
                f"[yellow]Skipping {payload.platform} for {payload.content_id[:12]}:[/yellow] "
                "platform not enabled in config"
            )
            skipped += 1
            continue
        content = db.get_content(payload.content_id)
        if not content:
            if payload.id is not None:
                db.update_platform_payload_status(payload.id, "failed", "Content not found")
            console.print(f"[red]Content {payload.content_id} not found — skipping.[/red]")
            continue
        product = db.get_product(content.product_sku)
        if not product:
            if payload.id is not None:
                db.update_platform_payload_status(payload.id, "failed", "Product not found")
            console.print(f"[red]Product {content.product_sku} not found — skipping.[/red]")
            continue
        try:
            console.print(
                Panel(
                    f"Posting [bold]{content.id[:12]}[/bold] to {payload.platform} ({product.name})",
                    style="blue",
                )
            )
            post = _post_platform_payload(payload, content, product)
            if payload.id is not None:
                db.update_platform_payload_status(payload.id, "posted")
            console.print(
                f"  [green]✓[/green] Posted to {payload.platform} (post_id={post.post_id})"
            )
            posted += 1
        except Exception as exc:
            if payload.id is not None:
                db.update_platform_payload_status(payload.id, "failed", str(exc))
            console.print(f"  [red]✗[/red] Failed to post to {payload.platform}: {exc}")

    console.print(
        f"\n[green]Posted {posted}[/green] scheduled payloads. "
        f"[yellow]Skipped {skipped}[/yellow]."
    )


def _load_post_metadata(content: Content) -> tuple[dict[str, str], list[str]]:
    """Build captions/hashtags for already-generated content being posted later.

    When posting outside of the `run` flow, the original prompt_generator
    extras aren't available, so we construct minimal captions from stored data.
    """
    existing_posts = db.list_posts_for_content(content.id)
    posted_platforms = {p.platform for p in existing_posts}

    captions: dict[str, str] = {}
    hashtags: list[str] = []
    enabled = config.enabled_platforms("posting")
    if existing_posts:
        first = existing_posts[0]
        for plat in enabled:
            if plat not in posted_platforms:
                captions[plat] = first.caption or ""
        hashtags = (first.hashtags or "").split(",") if first.hashtags else []
    else:
        for plat in enabled:
            captions[plat] = content.hook_text or content.product_sku
    return captions, hashtags


# ---------------------------------------------------------------------------
# pull-analytics
# ---------------------------------------------------------------------------

@cli.command("pull-analytics")
def pull_analytics_cmd():
    """Pull analytics from all platforms and update bandit model."""
    _init()
    total_pulled = 0
    enabled = config.enabled_platforms("analytics")
    if not enabled:
        console.print(
            "[yellow]No analytics platforms are configured.[/yellow] "
            "Add the required credentials or set `platforms.enabled` in config.yaml."
        )
        return

    for name in enabled:
        puller_cls = PULLERS[name]
        try:
            puller = puller_cls()
            puller.pull()
            console.print(f"  [green]✓[/green] {name}")
            total_pulled += 1
        except Exception as exc:
            console.print(f"  [red]✗[/red] {name}: {exc}")

    updated = bandit.update_from_metrics()
    console.print(f"\n[green]{total_pulled}[/green] platforms pulled, [green]{updated}[/green] bandit arms updated.")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--product", "slug", required=True, help="Product SKU")
def report(slug: str):
    """Show performance report for a product."""
    _init()

    product = db.get_product(slug)
    if not product:
        console.print(f"[red]Product {slug} not found.[/red]")
        sys.exit(1)

    console.print(Panel(f"[bold]{product.name}[/bold] ({product.sku})", style="blue"))

    content_list = db.list_content_for_product(slug)
    if not content_list:
        console.print("[yellow]No content found for this product.[/yellow]")
        return

    _report_content_summary(content_list)
    _report_platform_metrics(content_list)
    _report_bandit_weights(slug)
    _report_costs(content_list)


def _report_content_summary(content_list: list[Content]):
    theme_counts: dict[str, int] = {}
    hook_counts: dict[str, int] = {}
    for c in content_list:
        theme_counts[c.theme] = theme_counts.get(c.theme, 0) + 1
        hook_counts[c.hook_type] = hook_counts.get(c.hook_type, 0) + 1

    table = Table(title="Content by Theme / Hook")
    table.add_column("Category", style="cyan")
    table.add_column("Value")
    table.add_column("Count", justify="right")
    for theme, cnt in sorted(theme_counts.items(), key=lambda x: -x[1]):
        table.add_row("Theme", theme, str(cnt))
    for hook, cnt in sorted(hook_counts.items(), key=lambda x: -x[1]):
        table.add_row("Hook", hook, str(cnt))
    console.print(table)


def _report_platform_metrics(content_list: list[Content]):
    table = Table(title="Platform Metrics (latest per post)")
    table.add_column("Platform", style="cyan")
    table.add_column("Posts", justify="right")
    table.add_column("Views", justify="right")
    table.add_column("Likes", justify="right")
    table.add_column("Shares", justify="right")
    table.add_column("Eng. Rate", justify="right")

    platform_agg: dict[str, dict] = {}
    for content in content_list:
        posts = db.list_posts_for_content(content.id)
        for post in posts:
            m = db.latest_metrics_for_post(post.id)
            if not m:
                continue
            agg = platform_agg.setdefault(post.platform, {
                "posts": 0, "views": 0, "likes": 0, "shares": 0, "engagement_sum": 0.0,
            })
            agg["posts"] += 1
            agg["views"] += m.views
            agg["likes"] += m.likes
            agg["shares"] += m.shares
            rate = (m.likes + m.comments + m.shares + m.saves) / max(m.views, 1)
            agg["engagement_sum"] += rate

    for plat in PLATFORMS:
        agg = platform_agg.get(plat)
        if not agg:
            table.add_row(plat, "0", "—", "—", "—", "—")
            continue
        avg_rate = agg["engagement_sum"] / max(agg["posts"], 1) * 100
        table.add_row(
            plat,
            str(agg["posts"]),
            f"{agg['views']:,}",
            f"{agg['likes']:,}",
            f"{agg['shares']:,}",
            f"{avg_rate:.1f}%",
        )
    console.print(table)


def _report_bandit_weights(slug: str):
    arms = db.get_bandit_arms(slug)
    if not arms:
        console.print("[dim]No bandit arms initialized yet.[/dim]")
        return

    recommendation_count = min(5, len(THEMES) * len(HOOK_TYPES))
    rec = bandit.recommend(slug, recommendation_count)

    rec_table = Table(title="Current Recommendations")
    rec_table.add_column("Theme", style="cyan")
    rec_table.add_column("Hook Type")
    rec_table.add_column("Base Weight", justify="right")
    rec_table.add_column("Sampled Score", justify="right")
    rec_table.add_column("Mode")
    for alloc in rec.allocations:
        arm = next((item for item in arms if item.theme == alloc.theme and item.hook_type == alloc.hook_type), None)
        trials = max((arm.successes + arm.failures - 2), 0) if arm else 0
        mode = "explore" if trials < 5 else "exploit"
        rec_table.add_row(
            alloc.theme,
            alloc.hook_type,
            f"{base_weight(alloc.theme, alloc.hook_type):.2f}",
            f"{alloc.score:.3f}",
            mode,
        )
    console.print(rec_table)

    arms.sort(
        key=lambda a: (
            a.successes / max(a.successes + a.failures, 1),
            base_weight(a.theme, a.hook_type),
        ),
        reverse=True,
    )
    table = Table(title="Learned Strategy Performance (top 10)")
    table.add_column("Theme", style="cyan")
    table.add_column("Hook Type")
    table.add_column("Base Weight", justify="right")
    table.add_column("Trials", justify="right")
    table.add_column("Win Rate", justify="right")
    for arm in arms[:10]:
        trials = max(arm.successes + arm.failures - 2, 0)
        rate = arm.successes / max(arm.successes + arm.failures, 1) * 100
        table.add_row(
            arm.theme,
            arm.hook_type,
            f"{base_weight(arm.theme, arm.hook_type):.2f}",
            str(trials),
            f"{rate:.0f}%",
        )
    console.print(table)


def _report_costs(content_list: list[Content]):
    total = 0.0
    for c in content_list:
        summary = content_cost_summary(c.id)
        total += summary["total"]

    console.print(Panel(f"Total spend for this product: [bold]${total:.2f}[/bold]", style="green"))


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------

@cli.command()
def archive():
    """Archive old generated videos."""
    _init()
    storage.archive_old_videos()
    console.print("[green]Archive complete.[/green]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
