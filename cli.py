from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import random
import re
import sys
import time
from typing import Optional
from zoneinfo import ZoneInfo

import click
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.table import Table

from src import bandit, config, content_eval, db, storage
from src.analytics import PULLERS
from src.cost_tracker import check_budget, content_cost_summary
from src.instagram_sheet_sync import (
    inspect_instagram_post_ids_from_sheet,
    sync_instagram_post_ids_from_phone_queue,
    sync_instagram_post_ids_from_sheet,
)
from src.models import (
    Content, CREATIVE_FORMATS, CTA_TYPES, HOOK_TYPES, PLATFORMS, PlatformPayload,
    Post, Product, PROOF_TYPES, ResearchSnapshot, SCRIPT_STYLES, THEMES, V5_NAMES,
    ZODIAC_SIGNS,
)
from src.morning_briefing import display_briefing, email_briefing, generate_briefing
from src.posters.instagram import InstagramPoster
from src.posters.tiktok import TikTokPoster
from src.posters.x import XPoster
from src.posters.youtube import YouTubePoster
from src.creative_strategy import resolve_deterministic_fields, resolve_v5_fields
from src.product_images import refresh_images_if_changed, register_images
from src.prompt_generator import generate_content
from src.renderers import render_media
from src.text_review import run_text_review

console = Console()
PARALLEL_GENERATION_THRESHOLD = 10

POSTERS = {
    "youtube": YouTubePoster,
    "instagram": InstagramPoster,
    "tiktok": TikTokPoster,
    "x": XPoster,
}
POST_DELAY_PATTERN = re.compile(r"^--delay-(\d{1,3})$")


def _resolve_ig_poster_flag(flag: bool) -> bool:
    return bool(flag) or config.get("instagram.posting_method") == "phone"


def _console_safe_text(text: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def _print_debug_panel(text: str, title: str) -> None:
    console.print(Panel(_console_safe_text(text), title=title, border_style="dim"))


def _content_is_v5(content: Content) -> bool:
    """True when asset manifest declares schema_version 5 (V5 horoscope reels)."""
    raw = content.asset_manifest_json
    if not raw or not str(raw).strip():
        return False
    try:
        m = json.loads(raw)
        if not isinstance(m, dict):
            return False
        v = m.get("schema_version")
        if isinstance(v, int):
            return v == 5
        try:
            return int(str(v).strip()) == 5
        except (TypeError, ValueError):
            return False
    except json.JSONDecodeError:
        return False


# Posting quiet hours: never post between 10pm EST and 8am EST (Eastern Time)
EASTERN = ZoneInfo("America/New_York")
QUIET_START_HOUR = 22  # 10pm
QUIET_END_HOUR = 8     # 8am


def _is_quiet_hours_est() -> bool:
    """True if current Eastern time is between 10pm and 8am (exclusive of 8am)."""
    now_et = datetime.now(EASTERN)
    hour = now_et.hour
    if QUIET_START_HOUR <= hour or hour < QUIET_END_HOUR:
        return True
    return False


def _wait_until_post_window_start(*, allow_quiet_hours: bool = False) -> None:
    """If in quiet hours (10pm–8am EST), wait until 8am EST + random(0–5) minutes.
    Pass allow_quiet_hours=True to skip the wait and post immediately."""
    if allow_quiet_hours or not _is_quiet_hours_est():
        return

    now_et = datetime.now(EASTERN)
    # Target: 8am today (or tomorrow if we're past midnight but before 8am)
    target = now_et.replace(hour=QUIET_END_HOUR, minute=0, second=0, microsecond=0)
    if now_et >= target:
        target += timedelta(days=1)
    # Add random 0–5 minutes
    target += timedelta(minutes=random.randint(0, 5))

    wait_seconds = (target - now_et).total_seconds()
    if wait_seconds <= 0:
        return

    console.print(
        f"[yellow]Quiet hours (10pm–8am EST). Waiting until {target.strftime('%I:%M %p')} ET before first post.[/yellow]"
    )
    print_interval = 900 if wait_seconds > 900 else 60
    remaining = wait_seconds
    while remaining > 0:
        sleep_sec = min(print_interval, remaining)
        time.sleep(sleep_sec)
        remaining -= sleep_sec
        if remaining > 0:
            print(f"Posting window opens in {int(remaining)} seconds.")


def _init():
    db.init_db()
    storage.ensure_dirs()


def _post_content_to_all(content: Content, product: Product,
                         captions: dict[str, str], hashtags: list[str],
                         delay_state: dict[str, object] | None = None):
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
        _wait_for_next_platform_post(platform, delay_state)
        try:
            poster = poster_cls()
            post = poster.post(content, product, captions, hashtags)
            console.print(f"  [green]OK[/green] Posted to {platform} (post_id={post.post_id})")
            results.append(post)
        except Exception as exc:
            console.print(f"  [red]ERROR[/red] Failed to post to {platform}: {exc}")
        finally:
            _mark_platform_attempt(platform, delay_state)
    return results


def _hashtags_to_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [tag.strip().lstrip("#") for tag in raw.split(",") if tag.strip()]


def _parse_post_delay_args(extra_args: list[str]) -> tuple[int, bool]:
    delay_minutes = 5
    no_delay = False
    seen_delay = False

    for arg in extra_args:
        if arg == "--nodelay":
            no_delay = True
            continue

        match = POST_DELAY_PATTERN.fullmatch(arg)
        if match:
            if seen_delay:
                raise click.UsageError("Provide only one --delay-XXX option.")
            delay_minutes = int(match.group(1))
            seen_delay = True
            continue

        if arg.startswith("--delay-"):
            raise click.UsageError("Use --delay-XXX with XXX between 0 and 999.")

        if arg.startswith("-"):
            raise click.UsageError(f"No such option: {arg}")

        raise click.UsageError(f"Unexpected argument: {arg}")

    return delay_minutes, no_delay


def _build_post_delay_state(
    delay_minutes: int, no_delay: bool, allow_quiet_hours: bool = False
) -> dict[str, object]:
    return {
        "delay_minutes": delay_minutes,
        "no_delay": no_delay,
        "platform_attempts": {},
        "allow_quiet_hours": allow_quiet_hours,
    }


def _wait_for_next_platform_post(
    platform: str,
    delay_state: dict[str, object] | None,
) -> None:
    if not delay_state or delay_state["no_delay"]:
        return

    allow_quiet_hours = bool(delay_state.get("allow_quiet_hours", False))

    platform_attempts = delay_state["platform_attempts"]
    assert isinstance(platform_attempts, dict)
    previous_attempts = int(platform_attempts.get(platform, 0))
    if previous_attempts == 0:
        return

    base_seconds = int(delay_state["delay_minutes"]) * 60
    if base_seconds <= 0:
        return

    # Apply ±20% random variance to each delay between same-platform posts
    variance = random.uniform(0.8, 1.2)
    total_seconds = max(1, int(base_seconds * variance))

    remaining_seconds = total_seconds
    # When delay > 15 min, print every 15 min; otherwise every 30 seconds
    print_interval = 900 if total_seconds > 900 else 30
    while remaining_seconds > 0:
        # If we've entered quiet hours (10pm–8am EST), pause and wait until 8am
        if not allow_quiet_hours and _is_quiet_hours_est():
            console.print(
                "[yellow]Quiet hours (10pm–8am EST). Pausing until 8am ET before next post.[/yellow]"
            )
            _wait_until_post_window_start()
            return
        print(f"Next {platform} post in {remaining_seconds} seconds.")
        sleep_seconds = min(print_interval, remaining_seconds)
        time.sleep(sleep_seconds)
        remaining_seconds -= sleep_seconds


def _mark_platform_attempt(platform: str, delay_state: dict[str, object] | None) -> None:
    if not delay_state:
        return

    platform_attempts = delay_state["platform_attempts"]
    assert isinstance(platform_attempts, dict)
    platform_attempts[platform] = int(platform_attempts.get(platform, 0)) + 1


def _print_prompt(content: Content) -> None:
    """Print the generated prompt/script to the terminal for visibility."""
    lines = []
    if content.theme or content.hook_type:
        if _content_is_v5(content):
            lines.append(f"[bold]Horoscope:[/bold] {content.theme or '—'}")
            lines.append(f"[bold]Name:[/bold] {content.hook_type or '—'}")
        else:
            strategy = _format_strategy_label(content.theme or None, content.hook_type or None)
            if strategy != "prompt-selected":
                lines.append(f"[bold]Theme / Hook:[/bold] {strategy}")
    if content.hook_text:
        lines.append(f"[bold]Hook:[/bold] {content.hook_text}")
    if content.starting_image_prompt:
        label = "Starting image"
        if _content_is_v5(content):
            label = "Starting image (actual Gemini ref prompt)"
        lines.append(f"[bold]{label}:[/bold] {content.starting_image_prompt}")
    if content.scene_1_desc:
        lines.append(f"[bold]Scene 1 (visual):[/bold] {content.scene_1_desc}")
    if content.scene_1_script:
        lines.append(f"[bold]Scene 1 (voiceover):[/bold] {content.scene_1_script}")
    if content.scene_2_desc:
        lines.append(f"[bold]Scene 2 (visual):[/bold] {content.scene_2_desc}")
    if content.scene_2_script:
        lines.append(f"[bold]Scene 2 (voiceover):[/bold] {content.scene_2_script}")
    if content.asset_manifest_json:
        try:
            manifest = json.loads(content.asset_manifest_json)
            plan = manifest.get("image_plan") if isinstance(manifest, dict) else None
            voiceover_plan = manifest.get("voiceover_plan") if isinstance(manifest, dict) else None
            if (
                content.creative_format == "image_motion_15s"
                and plan
                and isinstance(plan, dict)
                and plan.get("strategy_summary")
            ):
                lines.append(f"[bold]Overall scene:[/bold] {plan['strategy_summary']}")
            strategy_metadata = plan.get("strategy_metadata") if isinstance(plan, dict) else None
            if content.creative_format == "image_motion_15s" and strategy_metadata and isinstance(strategy_metadata, dict):
                content_goal = (strategy_metadata.get("content_goal") or "").strip()
                if content_goal:
                    lines.append(f"[bold]Content goal:[/bold] {content_goal}")
                primary_intent = (strategy_metadata.get("primary_engagement_intent") or "").strip()
                if primary_intent:
                    lines.append(f"[bold]Primary intent:[/bold] {primary_intent}")
                audience_question = (strategy_metadata.get("audience_question_cluster") or "").strip()
                if audience_question:
                    lines.append(f"[bold]Audience question:[/bold] {audience_question}")
                audience_fear = (strategy_metadata.get("audience_fear_cluster") or "").strip()
                if audience_fear:
                    lines.append(f"[bold]Audience fear:[/bold] {audience_fear}")
            if (
                voiceover_plan
                and isinstance(voiceover_plan, dict)
                and voiceover_plan.get("voiceover_script")
            ):
                lines.append(
                    f"[bold]Voiceover:[/bold] {voiceover_plan['voiceover_script']}"
                )
            delivery_profile = (
                voiceover_plan.get("delivery_profile")
                if isinstance(voiceover_plan, dict)
                else None
            )
            if delivery_profile and isinstance(delivery_profile, dict):
                lines.append(
                    f"[bold]Speech metadata:[/bold] {json.dumps(delivery_profile, sort_keys=True)}"
                )
            elevenlabs_options = None
            if isinstance(voiceover_plan, dict):
                provider_options = voiceover_plan.get("provider_options")
                if isinstance(provider_options, dict):
                    elevenlabs_options = provider_options.get("elevenlabs")
            if elevenlabs_options and isinstance(elevenlabs_options, dict):
                lines.append(
                    f"[bold]ElevenLabs metadata:[/bold] {json.dumps(elevenlabs_options, sort_keys=True)}"
                )
        except json.JSONDecodeError:
            pass
    if lines:
        _print_debug_panel("\n".join(lines), "Generated prompt")


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


def _post_platform_payload(
    payload: PlatformPayload,
    content: Content,
    product: Product,
    *,
    use_ig_poster: bool = False,
) -> Post:
    if payload.platform == "instagram" and use_ig_poster:
        from src.posters.ig_phone import IgPhonePoster

        poster = IgPhonePoster()
    else:
        if payload.platform not in POSTERS:
            raise ValueError(f"No poster configured for platform '{payload.platform}'")
        poster = POSTERS[payload.platform]()
    if not content.video_local_path:
        raise FileNotFoundError("Content has no video_local_path set")

    video_path = Path(content.video_local_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    hashtags = _hashtags_to_list(payload.hashtags)
    post_id = poster.upload(video_path, payload.caption or "", hashtags)

    post = Post(
        content_id=content.id,
        platform=payload.platform,
        post_id=post_id,
        caption=payload.caption,
        hashtags=",".join(hashtags),
        utm_url=payload.utm_url,
        destination_url=payload.destination_url,
        utm_source=payload.utm_source,
        utm_medium=payload.utm_medium,
        utm_campaign=payload.utm_campaign,
        utm_content=payload.utm_content,
        link_mode=payload.link_mode,
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


def _run_generation_job(
    job: tuple[Product, str | None, str | None, int],
    should_post: bool,
    creative_format: str | None = None,
    video_v2: bool = False,
    video_v3: bool = False,
    video_v4: bool = False,
    video_v5: bool = False,
    cta_type: str | None = None,
    proof_type: str | None = None,
    script_style: str | None = None,
    velura_branding: bool = True,
    v5_include_text_insights: bool = False,
    v5_include_performance_summary: bool = False,
) -> Optional[Content]:
    product, theme, hook_type, generation_index = job
    kwargs = {
        "creative_format": creative_format,
        "video_v2": video_v2,
        "video_v3": video_v3,
        "video_v4": video_v4,
        "cta_type": cta_type,
        "proof_type": proof_type,
        "script_style": script_style,
    }
    if video_v5:
        kwargs["video_v5"] = True
    if not velura_branding:
        kwargs["velura_branding"] = velura_branding
    if video_v5:
        kwargs["v5_include_text_insights"] = v5_include_text_insights
        kwargs["v5_include_performance_summary"] = v5_include_performance_summary
    return _generate_single(
        product,
        theme,
        hook_type,
        generation_index,
        should_post,
        **kwargs,
    )


def _generate_batch(
    jobs: list[tuple[Product, str | None, str | None, int]],
    should_post: bool,
    requested_count: int,
    creative_format: str | None = None,
    video_v2: bool = False,
    video_v3: bool = False,
    video_v4: bool = False,
    video_v5: bool = False,
    cta_type: str | None = None,
    proof_type: str | None = None,
    script_style: str | None = None,
    velura_branding: bool = True,
    v5_include_text_insights: bool = False,
    v5_include_performance_summary: bool = False,
) -> int:
    if not jobs:
        return 0

    def run_job(job):
        return _run_generation_job(
            job,
            should_post,
            creative_format,
            video_v2,
            video_v3,
            video_v4,
            video_v5,
            cta_type,
            proof_type,
            script_style,
            velura_branding,
            v5_include_text_insights,
            v5_include_performance_summary,
        )

    if requested_count < PARALLEL_GENERATION_THRESHOLD and len(jobs) > 1:
        max_workers = min(len(jobs), requested_count)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = executor.map(run_job, jobs)
            return sum(1 for result in results if result)

    return sum(
        1
        for product, theme, hook_type, idx in jobs
        if _generate_single(
            product,
            theme,
            hook_type,
            idx,
            should_post,
            creative_format=creative_format,
            video_v2=video_v2,
            video_v3=video_v3,
            video_v4=video_v4,
            **({"video_v5": True} if video_v5 else {}),
            cta_type=cta_type,
            proof_type=proof_type,
            script_style=script_style,
            **({"velura_branding": velura_branding} if not velura_branding else {}),
            **(
                {
                    "v5_include_text_insights": v5_include_text_insights,
                    "v5_include_performance_summary": v5_include_performance_summary,
                }
                if video_v5
                else {}
            ),
        )
    )


def _generate_single(product: Product, theme: str | None, hook_type: str | None, generation_index: int,
                     should_post: bool, creative_format: str | None = None, video_v2: bool = False,
                     video_v3: bool = False, video_v4: bool = False, video_v5: bool = False,
                     cta_type: str | None = None, proof_type: str | None = None,
                     script_style: str | None = None, velura_branding: bool = True,
                     v5_include_text_insights: bool = False,
                     v5_include_performance_summary: bool = False) -> Optional[Content]:
    spent, budget, within = check_budget()
    if not within:
        console.print(
            f"[red]Budget exhausted[/red] (${spent:.2f} / ${budget:.2f}). Skipping."
        )
        return None

    images, refreshed_images = refresh_images_if_changed(product.sku)
    if refreshed_images:
        console.print(f"[dim]{product.sku}: refreshed registered images from disk.[/dim]")
    if not images:
        if video_v5:
            console.print(
                f"[dim]{product.sku}: no product images registered; "
                "V5 will use the horoscope reference image asset instead.[/dim]"
            )
        else:
            console.print(f"[yellow]{product.sku}: no images registered, continuing anyway.[/yellow]")

    if video_v5:
        # theme/hook_type slots carry horoscope/name from the job tuple
        resolved = resolve_v5_fields(hook_type, theme, generation_index)
        console.print(
            f"  {product.sku}: generating V5 prompt ... "
            f"(horoscope: {resolved['theme']}, name: {resolved['hook_type']})"
        )
    else:
        resolved = resolve_deterministic_fields(
            theme, hook_type, cta_type, proof_type, script_style, generation_index, video_v3=(video_v3 or video_v4)
        )
        if video_v4:
            console.print(f"  {product.sku}: generating V4 prompt ... (theme: {resolved['theme']})")
        elif video_v3:
            console.print(f"  {product.sku}: generating V3 prompt ... (theme: {resolved['theme']})")
        else:
            console.print(
                f"  {product.sku}: generating prompt ... ({_format_strategy_label(resolved['theme'], resolved['hook_type'])})"
            )
    content, extras = generate_content(
        product, resolved["theme"], resolved["hook_type"], images,
        creative_format, video_v2=video_v2, video_v3=video_v3, video_v4=video_v4,
        video_v5=video_v5,
        v5_vibe=resolved.get("vibe") if video_v5 else None,
        cta_type=resolved["cta_type"], proof_type=resolved["proof_type"], script_style=resolved["script_style"],
        velura_branding=velura_branding,
        v5_include_text_insights=v5_include_text_insights,
        v5_include_performance_summary=v5_include_performance_summary,
    )
    if "prompt_input" in extras:
        _print_debug_panel(extras["prompt_input"], "Prompt")
    if "prompt_output" in extras:
        try:
            output_json = json.loads(extras["prompt_output"])
            output_str = json.dumps(output_json, indent=2)
        except (json.JSONDecodeError, TypeError):
            output_str = extras["prompt_output"]
        _print_debug_panel(output_str, "Prompt output")
    if "voice_prompt_input" in extras:
        _print_debug_panel(extras["voice_prompt_input"], "Voice prompt")
    if "voice_prompt_output" in extras:
        try:
            output_json = json.loads(extras["voice_prompt_output"])
            output_str = json.dumps(output_json, indent=2)
        except (json.JSONDecodeError, TypeError):
            output_str = extras["voice_prompt_output"]
        _print_debug_panel(output_str, "Voice prompt output")
    if "v3_classification" in extras:
        cls = extras["v3_classification"]
        _print_debug_panel(
            f"hook_type: {cls.get('hook_type')}\nscript_style: {cls.get('script_style')}\nproof_type: {cls.get('proof_type')}",
            "V3 post-gen classification",
        )
    _print_prompt(content)
    captions: dict[str, str] = extras["platform_captions"]
    hashtags: list[str] = extras["hashtags"]

    console.print(f"  {product.sku}: rendering media ({content.creative_format}) ...")
    render_media(content, product, images)

    db.update_last_content_date(product.sku)
    console.print(f"  [green]OK[/green] {product.sku}: content [bold]{content.id}[/bold] created")

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
    if not config.get("shopify.store_url") or not config.get("shopify.client_id") or not config.get("shopify.client_secret"):
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
            "Y" if p.active else "N",
            "Y" if p.generation_ready else "N",
        )
    console.print(table)
    console.print(f"\n[green]{len(products)}[/green] products synced.")


@cli.command("add-product")
@click.option("--sku", required=True, help="Internal product SKU/slug used by the workflow")
@click.option("--name", required=True, help="Product display name")
@click.option("--category", default=None, help="Optional category")
@click.option("--price", type=float, default=None, help="Optional price")
@click.option("--url", "product_url", default=None, help="Optional full storefront product URL")
@click.option("--description", default=None, help="Product description for content generation prompts")
def add_product_cmd(
    sku: str,
    name: str,
    category: Optional[str],
    price: Optional[float],
    product_url: Optional[str],
    description: Optional[str],
):
    """Create or update a product without Shopify sync."""
    _init()

    product = Product(
        sku=sku.strip(),
        name=name.strip(),
        category=category.strip() if category else None,
        price=price,
        description=description.strip() if description else None,
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
# research (Phase 3: Research Memory)
# ---------------------------------------------------------------------------

@cli.command("research-add")
@click.option("--product", "product_sku", default=None, help="Product SKU (optional, for product-specific insight)")
@click.option("--platform", type=click.Choice(["youtube", "instagram", "tiktok", "x"]), default=None, help="Platform (optional)")
@click.option("--format", "creative_format", type=click.Choice(CREATIVE_FORMATS), default=None, help="Creative format (optional)")
@click.option("--source", "source_type", default="manual", help="Source type: manual, creatives, comments, platform_notes")
@click.option("--summary", required=True, help="Research summary text to inject into prompts")
def research_add_cmd(
    product_sku: str | None,
    platform: str | None,
    creative_format: str | None,
    source_type: str,
    summary: str,
):
    """Create a research snapshot for prompt injection."""
    _init()
    import uuid
    snap = ResearchSnapshot(
        id=uuid.uuid4().hex[:16],
        product_sku=product_sku,
        platform=platform,
        creative_format=creative_format,
        summary=summary.strip(),
        source_type=source_type,
    )
    db.insert_research_snapshot(snap)
    scope = []
    if product_sku:
        scope.append(f"product={product_sku}")
    if platform:
        scope.append(f"platform={platform}")
    if creative_format:
        scope.append(f"format={creative_format}")
    scope_str = ", ".join(scope) if scope else "all products/platforms/formats"
    console.print(
        f"[green]Created[/green] research snapshot [bold]{snap.id}[/bold] "
        f"({scope_str}). It will be injected into matching generation prompts."
    )


@cli.command("research-list")
@click.option("--product", "product_sku", default=None, help="Filter by product SKU")
@click.option("--platform", type=click.Choice(["youtube", "instagram", "tiktok", "x"]), default=None, help="Filter by platform")
@click.option("--format", "creative_format", type=click.Choice(CREATIVE_FORMATS), default=None, help="Filter by creative format")
@click.option("--limit", default=20, help="Max snapshots to show")
def research_list_cmd(
    product_sku: str | None,
    platform: str | None,
    creative_format: str | None,
    limit: int,
):
    """List research snapshots."""
    _init()
    snapshots = db.list_research_snapshots(
        product_sku=product_sku,
        platform=platform,
        creative_format=creative_format,
        limit=limit,
    )
    if not snapshots:
        console.print("[yellow]No research snapshots found.[/yellow]")
        return

    table = Table(title="Research Snapshots")
    table.add_column("ID", style="cyan", max_width=12)
    table.add_column("Product")
    table.add_column("Platform")
    table.add_column("Format")
    table.add_column("Source")
    table.add_column("Summary", max_width=50, overflow="ellipsis")
    for s in snapshots:
        table.add_row(
            s.id,
            s.product_sku or "—",
            s.platform or "—",
            s.creative_format or "—",
            s.source_type,
            (s.summary[:47] + "...") if len(s.summary) > 50 else s.summary,
        )
    console.print(table)
    console.print(f"\n[green]{len(snapshots)}[/green] snapshot(s).")


@cli.command("review-text")
@click.option("--product", "product_sku", default=None, help="Filter by product SKU")
@click.option("--platform", type=click.Choice(PLATFORMS), default=None, help="Filter by platform")
@click.option("--format", "creative_format", type=click.Choice(CREATIVE_FORMATS), default=None, help="Filter by creative format")
@click.option(
    "--min-posts",
    type=click.IntRange(min=1),
    default=None,
    help="Minimum eligible posts required before generating an insight",
)
@click.option(
    "--lookback-days",
    type=click.IntRange(min=1),
    default=None,
    help="Only analyze posts from the last N days",
)
def review_text_cmd(
    product_sku: str | None,
    platform: str | None,
    creative_format: str | None,
    min_posts: int | None,
    lookback_days: int | None,
):
    """Manually run the text-review worker and persist one scoped insight."""
    _init()

    min_posts = int(config.get("text_review.min_posts", 5)) if min_posts is None else min_posts
    lookback_days = (
        int(config.get("text_review.lookback_days", 30))
        if lookback_days is None
        else lookback_days
    )

    insight = run_text_review(
        min_posts=min_posts,
        product_sku=product_sku,
        platform=platform,
        creative_format=creative_format,
        lookback_days=lookback_days,
    )
    if insight is None:
        console.print(
            "[yellow]No text insight created.[/yellow] "
            "Not enough eligible posts were found for the requested scope."
        )
        return

    scope_parts = []
    if insight.product_sku:
        scope_parts.append(f"product={insight.product_sku}")
    if insight.platform:
        scope_parts.append(f"platform={insight.platform}")
    if insight.creative_format:
        scope_parts.append(f"format={insight.creative_format}")
    scope = ", ".join(scope_parts) if scope_parts else "all products/platforms/formats"

    console.print(
        f"[green]Created[/green] text insight [bold]{insight.id}[/bold] "
        f"from {insight.source_post_count} post(s)."
    )
    console.print(f"Scope: {scope}")
    console.print(insight.insight_text)


@cli.command("paid-seed-clone")
@click.option(
    "--content-id",
    "content_id",
    required=True,
    help="Content ID of organic winner (row number from preview or full ID)",
)
@click.option("--variants", default=5, help="Number of ad-safe variants to generate (3–5)", show_default=True)
def paid_seed_clone_cmd(content_id: str, variants: int):
    """Clone an organic winner into ad-safe variants for paid promotion.

    Creates 3–5 variants by varying CTA, opening hook, and captions while
    preserving the winning core concept and video asset. Lineage is stored
    (source_content_id) for attribution. Manual handoff to ad platforms.
    """
    _init()
    if variants < 1 or variants > 10:
        console.print("[red]--variants must be between 1 and 10.[/red]")
        sys.exit(1)

    content = _resolve_content_for_scope(content_id, "last-24h")
    if not content:
        console.print(f"[red]Content {content_id} not found.[/red]")
        sys.exit(1)

    from src.paid_variant import clone_for_paid

    try:
        created = clone_for_paid(content.id, variant_count=variants)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    console.print(
        f"[green]Created {len(created)}[/green] paid variant(s) from [bold]{content.id[:12]}[/bold]. "
        "Review with `preview --last-24h`, then approve and schedule as usual."
    )
    table = Table(title="Paid Variants")
    table.add_column("ID", style="cyan", max_width=12)
    table.add_column("Hook")
    table.add_column("CTA")
    for c in created:
        table.add_row(
            c.id[:12],
            (c.hook_text or "")[:40] + ("..." if len(c.hook_text or "") > 40 else ""),
            f"{c.cta_type} / {c.cta_text or '—'}",
        )
    console.print(table)


@cli.command("repost")
@click.option(
    "--content-id",
    "content_id",
    required=True,
    help="Source content ID (full id or preview row number)",
)
@click.option(
    "--pending",
    is_flag=True,
    help="Leave the repost in pending review instead of auto-approving",
)
@click.option(
    "--row-scope",
    type=click.Choice(["today", "last-24h", "all"]),
    default=None,
    help="Preview scope when --content-id is a numeric row number",
)
def repost_cmd(content_id: str, pending: bool, row_scope: str | None):
    """Queue a repost of an existing video as a new content row (separate payloads and metrics).

    Requires a persisted video file path on the source item. Creates a linked clone
    (source_content_id) with fresh platform payloads and new UTM attribution.
    """
    _init()
    content = (
        _resolve_content_for_scope(content_id, row_scope)
        if row_scope
        else _resolve_content(content_id)
    )
    if not content:
        console.print(f"[red]Content {content_id} not found.[/red]")
        sys.exit(1)

    try:
        clone = db.clone_content_for_repost(content.id, auto_approve=not pending)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    console.print(
        f"[green]Repost created[/green] from [bold]{content.id[:12]}[/bold] → "
        f"[bold]{clone.id[:12]}[/bold]. "
        + (
            "Pending review — run `approve` then `schedule` when ready."
            if pending
            else "Approved — run `schedule` then `post` / `post-due` as usual."
        )
    )


@cli.command("commerce-ingest")
@click.argument("csv_path", type=click.Path(exists=True, path_type=Path))
@click.option("--source", default="shopify_import", help="Source label for ingested rows")
def commerce_ingest_cmd(csv_path: Path, source: str):
    """Ingest commerce facts (sessions, purchases, revenue) from CSV.

    CSV must have: content_id, platform, event_date.
    Optional: sessions, add_to_cart, checkout_started, purchases, revenue.
    Produce from Shopify order export by parsing UTM (utm_content=content_id, utm_source=platform).
    """
    _init()
    from src.commerce_ingest import ingest_commerce_csv

    try:
        inserted, skipped = ingest_commerce_csv(csv_path, source=source)
        console.print(f"[green]Upserted {inserted}[/green] commerce fact(s), skipped {skipped}.")
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@cli.command("report")
def report_cmd():
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


@cli.command("briefing-diagnose")
def briefing_diagnose_cmd():
    """Diagnose why Yesterday's Performance shows zeros."""
    from datetime import date, timedelta

    _init()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    posts = db.list_recent_posts(days=2)
    enabled_analytics = config.enabled_platforms("analytics")

    table = Table(title="Yesterday's Performance Diagnostic")
    table.add_column("Check", style="cyan")
    table.add_column("Result", style="white")
    table.add_row("Today (local)", str(date.today()))
    table.add_row("Yesterday (filter target)", yesterday)
    table.add_row("Posts in last 2 days", str(len(posts)))
    table.add_row("Analytics platforms enabled", ", ".join(enabled_analytics) or "(none)")
    table.add_row("", "")

    with db._connect() as conn:
        metric_count = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    table.add_row("Total metrics in DB", str(metric_count))

    # Per-post breakdown
    with_metrics = 0
    with_content = 0
    matching_yesterday = 0
    for post in posts:
        if post.id and db.latest_metrics_for_post(post.id):
            with_metrics += 1
        if post.content_id and db.get_content(post.content_id):
            with_content += 1
        pub = post.published_at[:10] if post.published_at else None
        if pub == yesterday:
            m = db.latest_metrics_for_post(post.id) if post.id else None
            c = db.get_content(post.content_id) if post.content_id else None
            if m and c:
                matching_yesterday += 1

    table.add_row("Posts with metrics", f"{with_metrics} / {len(posts)}")
    table.add_row("Posts with content", f"{with_content} / {len(posts)}")
    table.add_row("Posts matching yesterday + metrics + content", str(matching_yesterday))

    console.print(table)

    if matching_yesterday == 0:
        console.print()
        if len(posts) == 0:
            console.print("[yellow]No posts in last 2 days.[/yellow] Post content first.")
        elif with_metrics == 0:
            console.print(
                "[yellow]No metrics for any recent post.[/yellow] Run [bold]pull-analytics[/bold] "
                "and ensure analytics platforms are configured."
            )
        elif not enabled_analytics:
            console.print(
                "[yellow]No analytics platforms configured.[/yellow] Add credentials for "
                "YouTube, Instagram, TikTok, or X in config.yaml."
            )
        else:
            console.print(
                "[yellow]No posts from yesterday have metrics.[/yellow] "
                "Run [bold]pull-analytics[/bold] before report."
            )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--auto", "auto_mode", is_flag=True, help="Use bandit recommendations")
@click.option("--product", "slugs", multiple=True, help="Product SKU (repeatable)")
@click.option("--theme", "themes", multiple=True, type=click.Choice(THEMES), help="Theme (repeatable)")
@click.option("--hook", "hooks", multiple=True, type=click.Choice(HOOK_TYPES), help="Hook type (repeatable)")
@click.option(
    "--format",
    "creative_format",
    type=click.Choice(CREATIVE_FORMATS),
    default=None,
    help="Creative format (ai_video_15s, ai_video_flex_15s, image_motion_15s). Default: ai_video_15s.",
)
@click.option(
    "--video-v2",
    "video_v2",
    is_flag=True,
    help="Use video-v2 prompts (forces ai_video_flex_15s). Incompatible with --format image_motion_15s.",
)
@click.option(
    "--video-v3",
    "video_v3",
    is_flag=True,
    help="Use video-v3 theme-driven prompts (forces ai_video_flex_15s, 6-8 scenes, third-person narrator, post-gen classification). Incompatible with --video-v2, --video-v4, and --format image_motion_15s.",
)
@click.option(
    "--video-v4",
    "video_v4",
    is_flag=True,
    help="Use video-v4 educational/entertaining prompts (forces ai_video_flex_15s, 6-8 scenes, product as context not hero, content_mode, viewer_takeaway). Incompatible with --video-v2, --video-v3, and --format image_motion_15s.",
)
@click.option(
    "--video-v5",
    "video_v5",
    is_flag=True,
    help="V5 horoscope reels (forces ai_video_flex_15s). Incompatible with --video-v2/v3/v4 and --format image_motion_15s.",
)
@click.option(
    "--horoscope",
    "v5_horoscopes",
    multiple=True,
    type=click.Choice(ZODIAC_SIGNS),
    help="V5 zodiac sign (repeatable). Requires --video-v5.",
)
@click.option(
    "--name",
    "v5_names",
    multiple=True,
    type=click.Choice(V5_NAMES),
    help="V5 presenter name (repeatable). Requires --video-v5.",
)
@click.option(
    "--v5-include-text-insights",
    "v5_include_text_insights",
    is_flag=True,
    default=False,
    help="Include latest text insights in the V5 user message (requires --video-v5).",
)
@click.option(
    "--v5-include-performance",
    "v5_include_performance_summary",
    is_flag=True,
    default=False,
    help="Include organic video performance summary in the V5 user message (requires --video-v5).",
)
@click.option("--count", default=8, show_default=True, help="Total clips across all products in --auto mode")
@click.option(
    "--rotate-theme-hook",
    is_flag=True,
    help="Manual mode: when count > 1, cycle through provided --theme/--hook values per clip.",
)
@click.option("--cta-type", "cta_type", type=click.Choice(CTA_TYPES), default=None, help="CTA type (see_product, shop_now)")
@click.option("--proof-type", "proof_type", type=click.Choice(PROOF_TYPES), default=None, help="Proof type (test_result, testimonial, before_after, ingredient, none)")
@click.option("--script-style", "script_style", type=click.Choice(SCRIPT_STYLES), default=None, help="Script style (conversational, direct, storytelling, tip_based)")
@click.option(
    "--velura-branding/--no-velura-branding",
    "velura_branding",
    default=True,
    show_default=True,
    help="Include or omit explicit Velura wordmark and brand-name guidance during generation.",
)
@click.option("--post", "should_post", is_flag=True, help="Deprecated: use preview, approve, schedule, and post-due")
def run(auto_mode: bool, slugs: tuple[str, ...], themes: tuple[str, ...],
        hooks: tuple[str, ...], creative_format: str | None, video_v2: bool,
        video_v3: bool, video_v4: bool, video_v5: bool,
        v5_horoscopes: tuple[str, ...], v5_names: tuple[str, ...],
        v5_include_text_insights: bool, v5_include_performance_summary: bool,
        count: int, rotate_theme_hook: bool, cta_type: str | None, proof_type: str | None,
        script_style: str | None, velura_branding: bool, should_post: bool):
    """Generate content — manually or via bandit recommendations."""
    _init()

    video_flags = sum([video_v2, video_v3, video_v4, video_v5])
    if video_flags > 1:
        console.print(
            "[red]Only one of --video-v2, --video-v3, --video-v4, and --video-v5 may be set.[/red]"
        )
        sys.exit(1)

    if (v5_horoscopes or v5_names) and not video_v5:
        console.print("[red]--horoscope and --name require --video-v5.[/red]")
        sys.exit(1)

    if video_v5 and (themes or hooks):
        console.print("[red]--video-v5 uses --horoscope and --name, not --theme/--hook.[/red]")
        sys.exit(1)

    if video_v2 and creative_format == "image_motion_15s":
        console.print(
            "[red]--video-v2 cannot be combined with --format image_motion_15s.[/red] "
            "Use --video-v2 alone (it forces ai_video_flex_15s) or omit --video-v2."
        )
        sys.exit(1)

    if video_v3 and creative_format == "image_motion_15s":
        console.print(
            "[red]--video-v3 cannot be combined with --format image_motion_15s.[/red] "
            "Use --video-v3 alone (it forces ai_video_flex_15s) or omit --video-v3."
        )
        sys.exit(1)

    if video_v4 and creative_format == "image_motion_15s":
        console.print(
            "[red]--video-v4 cannot be combined with --format image_motion_15s.[/red] "
            "Use --video-v4 alone (it forces ai_video_flex_15s) or omit --video-v4."
        )
        sys.exit(1)

    if video_v5 and creative_format == "image_motion_15s":
        console.print(
            "[red]--video-v5 cannot be combined with --format image_motion_15s.[/red] "
            "Use --video-v5 alone (it forces ai_video_flex_15s) or omit --video-v5."
        )
        sys.exit(1)

    if video_v2:
        creative_format = "ai_video_flex_15s"
    if video_v3:
        creative_format = "ai_video_flex_15s"
    if video_v4:
        creative_format = "ai_video_flex_15s"
    if video_v5:
        creative_format = "ai_video_flex_15s"

    if (v5_include_text_insights or v5_include_performance_summary) and not video_v5:
        console.print(
            "[red]--v5-include-text-insights and --v5-include-performance require --video-v5.[/red]"
        )
        sys.exit(1)

    if should_post:
        console.print(
            "[red]--post is deprecated for the approval-first workflow.[/red] "
            "Use `preview`, `approve`, `schedule`, and `post-due` instead."
        )
        sys.exit(1)

    if auto_mode and (
        slugs or themes or hooks or rotate_theme_hook or v5_horoscopes or v5_names
    ):
        console.print(
            "[red]--auto cannot be combined with --product/--theme/--hook/--rotate-theme-hook/"
            "--horoscope/--name[/red]"
        )
        sys.exit(1)

    if auto_mode:
        _run_auto(
            count,
            should_post,
            creative_format,
            video_v2,
            video_v3,
            video_v4,
            video_v5,
            cta_type,
            proof_type,
            script_style,
            velura_branding,
            v5_include_text_insights,
            v5_include_performance_summary,
        )
    else:
        _run_manual(
            slugs,
            themes,
            hooks,
            count,
            should_post,
            creative_format,
            rotate_theme_hook,
            video_v2,
            video_v3,
            video_v4,
            video_v5,
            v5_horoscopes,
            v5_names,
            cta_type,
            proof_type,
            script_style,
            velura_branding,
            v5_include_text_insights,
            v5_include_performance_summary,
        )


def _run_auto(count: int, should_post: bool, creative_format: str | None = None, video_v2: bool = False,
              video_v3: bool = False, video_v4: bool = False, video_v5: bool = False,
              cta_type: str | None = None, proof_type: str | None = None,
              script_style: str | None = None, velura_branding: bool = True,
              v5_include_text_insights: bool = False,
              v5_include_performance_summary: bool = False):
    products = db.list_products(
        active_only=True,
        exclude_excluded=True,
        generation_ready_only=True,
    )
    if not products:
        console.print("[yellow]No eligible products found.[/yellow]")
        return

    recommendation = (
        bandit.recommend_v5(total_slots=count)
        if video_v5
        else bandit.recommend(total_slots=count)
    )
    queued_runs: list[tuple[Product, str, str]] = []
    starting_product_index = random.randrange(len(products))
    for alloc in recommendation.allocations:
        for _ in range(alloc.count):
            product = products[(starting_product_index + len(queued_runs)) % len(products)]
            queued_runs.append((product, alloc.theme, alloc.hook_type))

    summary = Table(title="Global Bandit Allocation")
    if video_v5:
        summary.add_column("Horoscope", style="cyan")
        summary.add_column("Name")
        summary.add_column("Clips", justify="right")
        for alloc in recommendation.allocations:
            summary.add_row(alloc.theme, alloc.hook_type, str(alloc.count))
    else:
        summary.add_column("Theme", style="cyan")
        if not (video_v3 or video_v4):
            summary.add_column("Hook Type")
        summary.add_column("Clips", justify="right")
        for alloc in recommendation.allocations:
            if video_v3 or video_v4:
                summary.add_row(alloc.theme, str(alloc.count))
            else:
                summary.add_row(alloc.theme, alloc.hook_type, str(alloc.count))
    console.print(summary)

    jobs: list[tuple[Product, str | None, str | None, int]] = []
    grouped_runs: dict[str, list[tuple[str, str]]] = {}
    for product, theme, hook_type in queued_runs:
        grouped_runs.setdefault(product.sku, []).append((theme, hook_type))

    idx = 0
    ordered_products = products[starting_product_index:] + products[:starting_product_index]
    for product in ordered_products:
        product_runs = grouped_runs.get(product.sku, [])
        if not product_runs:
            continue
        console.print(Panel(f"[bold]{product.name}[/bold] ({product.sku})", style="blue"))
        for theme, hook_type in product_runs:
            jobs.append((product, theme, hook_type, idx))
            idx += 1

    total = _generate_batch(
        jobs,
        should_post,
        requested_count=count,
        creative_format=creative_format,
        video_v2=video_v2,
        video_v3=video_v3,
        video_v4=video_v4,
        video_v5=video_v5,
        cta_type=cta_type,
        proof_type=proof_type,
        script_style=script_style,
        velura_branding=velura_branding,
        v5_include_text_insights=v5_include_text_insights,
        v5_include_performance_summary=v5_include_performance_summary,
    )

    piece = "piece" if total == 1 else "pieces"
    console.print(f"\n[green]{total}[/green] {piece} of content generated ({len(products)} products eligible).")


def _run_manual(slugs: tuple[str, ...], themes: tuple[str, ...],
                hooks: tuple[str, ...], count: int, should_post: bool,
                creative_format: str | None = None, rotate_theme_hook: bool = False, video_v2: bool = False,
                video_v3: bool = False, video_v4: bool = False, video_v5: bool = False,
                v5_horoscopes: tuple[str, ...] = (),
                v5_names: tuple[str, ...] = (),
                cta_type: str | None = None, proof_type: str | None = None,
                script_style: str | None = None, velura_branding: bool = True,
                v5_include_text_insights: bool = False,
                v5_include_performance_summary: bool = False):
    if not slugs:
        if video_v5:
            slugs = ("nk",)
        else:
            console.print("[red]Provide at least one --product or use --auto.[/red]")
            sys.exit(1)

    jobs: list[tuple[Product, str | None, str | None, int]] = []
    if video_v5:
        if not v5_horoscopes and not v5_names:
            rec = bandit.recommend_v5(total_slots=count)
            strategy_pairs = [
                (alloc.theme, alloc.hook_type) for alloc in rec.allocations for _ in range(alloc.count)
            ]
        else:
            strategy_pairs = _manual_v5_strategy_runs(v5_horoscopes, v5_names, count, rotate_theme_hook)
    elif not themes and not hooks:
        rec = bandit.recommend(total_slots=count)
        strategy_pairs = [(alloc.theme, alloc.hook_type) for alloc in rec.allocations for _ in range(alloc.count)]
    else:
        strategy_pairs = _manual_strategy_runs(themes, hooks, count, rotate_theme_hook)

    idx = 0
    for slug in slugs:
        product = db.get_product(slug)
        if not product:
            console.print(f"[red]Product {slug} not found — skipping.[/red]")
            continue

        console.print(Panel(f"[bold]{product.name}[/bold] ({product.sku})", style="blue"))
        for theme, hook in strategy_pairs:
            jobs.append((product, theme, hook, idx))
            idx += 1

    total = _generate_batch(
        jobs,
        should_post,
        requested_count=count,
        creative_format=creative_format,
        video_v2=video_v2,
        video_v3=video_v3,
        video_v4=video_v4,
        video_v5=video_v5,
        cta_type=cta_type,
        proof_type=proof_type,
        script_style=script_style,
        velura_branding=velura_branding,
        v5_include_text_insights=v5_include_text_insights,
        v5_include_performance_summary=v5_include_performance_summary,
    )

    console.print(f"\n[green]{total}[/green] pieces of content generated.")


def _manual_v5_strategy_runs(
    horoscopes: tuple[str, ...],
    names: tuple[str, ...],
    count: int,
    rotate_theme_hook: bool,
) -> list[tuple[str | None, str | None]]:
    """Build (horoscope, name) pairs for manual V5 runs (mirrors _manual_strategy_runs)."""
    has_h = bool(horoscopes)
    has_n = bool(names)

    if has_h and not has_n:
        rec = bandit.recommend_v5(total_slots=count)
        allocs_flat = [(a.theme, a.hook_type) for a in rec.allocations for _ in range(a.count)]
        return [(horoscopes[i % len(horoscopes)], allocs_flat[i][1]) for i in range(count)]

    if has_n and not has_h:
        rec = bandit.recommend_v5(total_slots=count)
        allocs_flat = [(a.theme, a.hook_type) for a in rec.allocations for _ in range(a.count)]
        return [(allocs_flat[i][0], names[i % len(names)]) for i in range(count)]

    h_values: tuple[str | None, ...] = horoscopes or (None,)
    n_values: tuple[str | None, ...] = names or (None,)
    if not rotate_theme_hook or count <= 1:
        return [
            (h, n)
            for h in h_values
            for n in n_values
            for _ in range(count)
        ]

    return [
        (h_values[index % len(h_values)], n_values[index % len(n_values)])
        for index in range(count)
    ]


def _manual_strategy_runs(
    themes: tuple[str, ...],
    hooks: tuple[str, ...],
    count: int,
    rotate_theme_hook: bool,
) -> list[tuple[str | None, str | None]]:
    has_themes = bool(themes)
    has_hooks = bool(hooks)

    if has_themes and not has_hooks:
        rec = bandit.recommend(total_slots=count)
        allocs_flat = [(a.theme, a.hook_type) for a in rec.allocations for _ in range(a.count)]
        return [(themes[i % len(themes)], allocs_flat[i][1]) for i in range(count)]

    if has_hooks and not has_themes:
        rec = bandit.recommend(total_slots=count)
        allocs_flat = [(a.theme, a.hook_type) for a in rec.allocations for _ in range(a.count)]
        return [(allocs_flat[i][0], hooks[i % len(hooks)]) for i in range(count)]

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

def _media_type_from_format(creative_format: str | None) -> str:
    """Return 'video' for video formats, 'static' for motion image (image_motion_15s)."""
    if creative_format == "image_motion_15s":
        return "static"
    if creative_format in ("ai_video_15s", "ai_video_flex_15s"):
        return "video"
    return "—"


def _preview_print_caption_details(items: list[Content]) -> None:
    """After the preview table, print per-platform caption and hashtags for each row."""
    plat_order = {p: i for i, p in enumerate(PLATFORMS)}

    def _sort_key(p: PlatformPayload) -> tuple[int, str]:
        return (plat_order.get(p.platform, 999), p.platform)

    for row, c in enumerate(items, start=1):
        payloads = sorted(db.list_platform_payloads(c.id), key=_sort_key)
        header = f"Row {row} · {c.id[:12]} · {c.product_sku}"
        console.print()
        console.print(f"[bold]{header}[/bold]")
        if not payloads:
            console.print("  [dim]No platform payloads yet.[/dim]")
            continue
        for p in payloads:
            cap = (p.caption or "").strip()
            tags = (p.hashtags or "").strip()
            console.print(f"  [cyan]{p.platform}[/cyan]")
            if cap:
                for line in _console_safe_text(cap).splitlines():
                    console.print(f"    {rich_escape(line)}")
            else:
                console.print("    [dim](no caption)[/dim]")
            if tags:
                console.print(f"    [dim]hashtags[/dim] {rich_escape(_console_safe_text(tags))}")


def _review_display_status(content: Content, payloads: list[PlatformPayload]) -> str:
    """Derive Review column status from content and payloads."""
    if content.review_status == "rejected":
        return "rejected"
    if content.review_status == "pending":
        return "pending"
    # approved (or legacy posted/partial_failure): show actual workflow state from payloads
    if not payloads:
        return content.review_status
    statuses = {p.status for p in payloads}
    if statuses <= {"posted"}:
        return "posted"
    if "scheduled" in statuses:
        return "scheduled"
    if statuses <= {"submitted"}:
        return "submitted"
    if "posted" in statuses:
        return "partial"
    if "submitted" in statuses:
        return "partial"
    return "approved"


def _list_content_for_row_scope(row_scope: str) -> list[Content]:
    if row_scope == "today":
        return db.list_content_today()
    if row_scope == "last-24h":
        return db.list_content_last_24h()
    if row_scope == "all":
        return db.list_all_content()
    raise ValueError(f"Unsupported row scope: {row_scope}")


def _resolve_content_for_scope(content_id: str, row_scope: str):
    if content_id.isdigit() and int(content_id) >= 1:
        idx = int(content_id) - 1
        items = _list_content_for_row_scope(row_scope)
        if idx < len(items):
            return items[idx]
        return None
    return db.get_content(content_id)


@cli.command()
@click.option("--today", is_flag=True, help="Show today's content (00:00–23:59 local)")
@click.option("--last-24h", "last_24h", is_flag=True, help="Show content from the last 24 hours")
@click.option("--all", "show_all", is_flag=True, help="Show all saved content")
@click.option(
    "--captions",
    "--caption",
    "show_captions",
    is_flag=True,
    help="After the table, show each platform's caption and hashtags for every row",
)
def preview(today: bool, last_24h: bool, show_all: bool, show_captions: bool):
    """Preview generated content."""
    selected_scopes = sum([today, last_24h, show_all])
    if selected_scopes == 0:
        console.print("[red]Provide one of --today, --last-24h, or --all.[/red]")
        return
    if selected_scopes > 1:
        console.print("[red]Provide only one of --today, --last-24h, or --all.[/red]")
        return
    _init()
    if show_all:
        items = db.list_all_content()
        title = "All Content"
        empty_msg = "No content found."
    elif last_24h:
        items = db.list_content_last_24h()
        title = "Content (last 24 hours)"
        empty_msg = "No content in the last 24 hours."
    else:
        items = db.list_content_today()
        title = "Today's Content"
        empty_msg = "No content generated today."
    if not items:
        console.print(f"[yellow]{empty_msg}[/yellow]")
        return

    preview_all_v5 = all(_content_is_v5(c) for c in items)

    table = Table(title=title)
    table.add_column("Row", justify="right", style="dim")
    table.add_column("ID", style="cyan", max_width=12)
    table.add_column("Product")
    table.add_column("Type", justify="center")
    if preview_all_v5:
        table.add_column("Horoscope")
        table.add_column("Name")
    else:
        table.add_column("Theme")
        table.add_column("Hook Type")
    table.add_column("From", style="dim", max_width=10)
    table.add_column("Review", justify="center")
    table.add_column("Payloads", justify="right")
    for i, c in enumerate(items, start=1):
        payloads = db.list_platform_payloads(c.id)
        review_status = _review_display_status(c, payloads)
        media_type = _media_type_from_format(c.creative_format)
        if c.source_content_id:
            src = c.source_content_id
            source_hint = (src[:8] + "…") if len(src) > 8 else src
        else:
            source_hint = "—"
        table.add_row(
            str(i),
            c.id[:12],
            c.product_sku,
            media_type,
            c.theme,
            c.hook_type,
            source_hint,
            review_status,
            str(len(payloads)),
        )
    console.print(table)
    if show_captions:
        _preview_print_caption_details(items)


# ---------------------------------------------------------------------------
# approve / reject / schedule / post
# ---------------------------------------------------------------------------

def _resolve_content(content_id: str, use_last_24h: bool = True):
    """Resolve --content-id to a Content. Accepts row number (1-based) from preview (today or last-24h).
    Defaults to last-24h, but rejects ambiguous numeric rows unless a scope is explicit."""
    if content_id.isdigit() and int(content_id) >= 1:
        if not use_last_24h:
            return _resolve_content_for_scope(content_id, "today")

        candidates: list[tuple[str, Content]] = []
        for row_scope in ("today", "last-24h", "all"):
            content = _resolve_content_for_scope(content_id, row_scope)
            if content:
                candidates.append((row_scope, content))

        if not candidates:
            return None

        unique_matches = {content.id: content for _, content in candidates}
        if len(unique_matches) == 1:
            return next(iter(unique_matches.values()))

        scopes = ", ".join(row_scope for row_scope, _ in candidates)
        raise click.UsageError(
            f"Row {content_id} is ambiguous across preview scopes ({scopes}). "
            "Use the full content ID or pass `--row-scope today`, "
            "`--row-scope last-24h`, or `--row-scope all`."
        )
    return db.get_content(content_id)


@cli.command()
@click.option(
    "--content-id",
    "content_ids",
    required=True,
    multiple=True,
    help="Row number(s) from preview (e.g. --content-id 1 --content-id 2 --content-id 3)",
)
@click.option(
    "--row-scope",
    type=click.Choice(["today", "last-24h", "all"]),
    default=None,
    help="Preview scope to use when --content-id is a numeric row number.",
)
def approve(content_ids: tuple[str, ...], row_scope: str | None):
    """Approve generated content items for scheduling/posting."""
    _init()
    failed = []
    approved = []
    for cid in content_ids:
        content = _resolve_content_for_scope(cid, row_scope) if row_scope else _resolve_content(cid)
        if not content:
            failed.append(cid)
            continue
        db.approve_content(content.id)
        approved.append(cid)
    for cid in failed:
        console.print(f"[red]Content {cid} not found.[/red]")
    if failed and not approved:
        sys.exit(1)
    if approved:
        ids_str = ", ".join(approved)
        next_step = f"python cli.py schedule --content-id {' --content-id '.join(approved)}"
        if row_scope:
            next_step += f" --row-scope {row_scope}"
        console.print(
            f"[green]Approved[/green] row(s) {ids_str}. "
            f"Next step: run `{next_step}`."
        )


@cli.command("approve-all")
@click.option(
    "--today",
    is_flag=True,
    help="Only approve pending content created today (same window as preview --today).",
)
def approve_all_cmd(today: bool):
    """Set pending content to approved status (all pending, or only today's with --today)."""
    _init()
    count = (
        db.approve_all_pending_content_today()
        if today
        else db.approve_all_pending_content()
    )
    if count == 0:
        if today:
            console.print("[yellow]No pending content from today to approve.[/yellow]")
        else:
            console.print("[yellow]No pending content to approve.[/yellow]")
        return
    piece = "item" if count == 1 else "items"
    console.print(
        f"[green]Approved {count}[/green] {piece}. "
        "Next step: run `python cli.py schedule --today` or schedule by content-id."
    )


@cli.command("reject-all-approved")
@click.option("--reason", default=None, help="Optional reason for rejection")
def reject_all_approved_cmd(reason: str | None):
    """Set all approved content to rejected status."""
    _init()
    count = db.reject_all_approved_content(notes=reason)
    if count == 0:
        console.print("[yellow]No approved content to reject.[/yellow]")
        return
    piece = "item" if count == 1 else "items"
    console.print(f"[yellow]Rejected {count}[/yellow] {piece}.")


@cli.command()
@click.option(
    "--content-id",
    "content_ids",
    required=True,
    multiple=True,
    help="Row number(s) from preview (e.g. --content-id 1 --content-id 2)",
)
@click.option("--reason", required=True, help="Reason for rejection")
@click.option(
    "--row-scope",
    type=click.Choice(["today", "last-24h", "all"]),
    default=None,
    help="Preview scope to use when --content-id is a numeric row number.",
)
def reject(content_ids: tuple[str, ...], reason: str, row_scope: str | None):
    """Reject generated content items."""
    _init()
    failed = []
    rejected = []
    for cid in content_ids:
        content = _resolve_content_for_scope(cid, row_scope) if row_scope else _resolve_content(cid)
        if not content:
            failed.append(cid)
            continue
        db.reject_content(content.id, reason)
        rejected.append(cid)
    for cid in failed:
        console.print(f"[red]Content {cid} not found.[/red]")
    if failed and not rejected:
        sys.exit(1)
    if rejected:
        console.print(f"[yellow]Rejected[/yellow] row(s) {', '.join(rejected)}: {reason}")


@cli.command()
@click.option("--today", is_flag=True, help="Schedule all approved content from today")
@click.option(
    "--content-id",
    "content_ids",
    multiple=True,
    help="Row number(s) from preview (e.g. --content-id 1 --content-id 2)",
)
@click.option(
    "--row-scope",
    type=click.Choice(["today", "last-24h", "all"]),
    default=None,
    help="Preview scope to use when --content-id is a numeric row number.",
)
def schedule(today: bool, content_ids: tuple[str, ...], row_scope: str | None):
    """Schedule approved content for staggered posting."""
    _init()

    if not today and not content_ids:
        console.print("[red]Provide either --today or --content-id (one or more).[/red]")
        sys.exit(1)

    if content_ids:
        any_failed = False
        for cid in content_ids:
            if not _schedule_single(cid, row_scope=row_scope):
                any_failed = True
        if any_failed:
            sys.exit(1)
    elif today:
        _schedule_today()


@cli.command("post-due")
@click.option(
    "--allow-quiet-hours",
    is_flag=True,
    help="Allow posting between 10pm and 8am EST (bypass quiet hours)",
)
@click.option(
    "--ig-poster",
    "ig_poster",
    is_flag=True,
    help="Use the phone-based Instagram poster instead of the Graph API for Instagram payloads.",
)
def post_due_cmd(allow_quiet_hours: bool, ig_poster: bool):
    """Post all payloads that are due based on publish_at."""
    _init()
    _wait_until_post_window_start(allow_quiet_hours=allow_quiet_hours)
    _post_due(use_ig_poster=_resolve_ig_poster_flag(ig_poster))

@cli.command("post", context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
@click.option("--today", is_flag=True, help="Post all approved content from the last 24 hours immediately")
@click.option(
    "--content-id",
    "content_ids",
    multiple=True,
    help="Row number(s) from preview (e.g. --content-id 1 --content-id 2)",
)
@click.option(
    "--allow-quiet-hours",
    is_flag=True,
    help="Allow posting between 10pm and 8am EST (bypass quiet hours)",
)
@click.option(
    "--row-scope",
    type=click.Choice(["today", "last-24h", "all"]),
    default=None,
    help="Preview scope to use when --content-id is a numeric row number.",
)
@click.option(
    "--ig-poster",
    "ig_poster",
    is_flag=True,
    help="Use the phone-based Instagram poster instead of the Graph API for Instagram payloads.",
)
@click.pass_context
def post_cmd(
    ctx: click.Context,
    today: bool,
    content_ids: tuple[str, ...],
    allow_quiet_hours: bool,
    row_scope: str | None,
    ig_poster: bool,
):
    """Post content to all platforms. Supports `--delay-XXX` and `--nodelay`."""
    _init()
    delay_minutes, no_delay = _parse_post_delay_args(list(ctx.args))
    delay_state = _build_post_delay_state(delay_minutes, no_delay, allow_quiet_hours)

    if not today and not content_ids:
        console.print("[red]Provide either --today or --content-id (one or more).[/red]")
        sys.exit(1)

    _wait_until_post_window_start(allow_quiet_hours=allow_quiet_hours)

    use_ig_poster = _resolve_ig_poster_flag(ig_poster)
    if content_ids:
        any_failed = False
        for cid in content_ids:
            if not _post_single(
                cid,
                delay_state=delay_state,
                row_scope=row_scope,
                use_ig_poster=use_ig_poster,
            ):
                any_failed = True
        if any_failed:
            sys.exit(1)
    elif today:
        items = [c for c in db.list_content_last_24h() if c.review_status == "approved"]
        if not items:
            console.print("[yellow]No approved content in the last 24 hours.[/yellow]")
            return
        any_failed = False
        for content in items:
            if not _post_single(
                content.id,
                delay_state=delay_state,
                use_ig_poster=use_ig_poster,
            ):
                any_failed = True
        if any_failed:
            sys.exit(1)


def _schedule_single(content_id: str, row_scope: str | None = None) -> bool:
    content = _resolve_content_for_scope(content_id, row_scope) if row_scope else _resolve_content(content_id)
    if not content:
        console.print(f"[red]Content {content_id} not found.[/red]")
        return False
    if content.review_status != "approved":
        console.print(
            f"[red]Row {content_id} is not approved (status={content.review_status}).[/red]"
        )
        return False

    scheduled = _schedule_payloads_for_content(content)
    console.print(f"[green]Scheduled {scheduled}[/green] payloads for row {content_id}.")
    return True


def _schedule_today():
    items = [c for c in db.list_content_today() if c.review_status == "approved"]
    if not items:
        console.print("[yellow]No approved content to schedule today.[/yellow]")
        return

    total = 0
    for content in items:
        total += _schedule_payloads_for_content(content)
    console.print(f"\n[green]Scheduled {total}[/green] payloads across {len(items)} approved items.")


def _schedule_last_24h():
    """Schedule approved content from the last 24 hours (matches preview --last-24h)."""
    items = [c for c in db.list_content_last_24h() if c.review_status == "approved"]
    if not items:
        console.print("[yellow]No approved content to schedule in the last 24 hours.[/yellow]")
        return

    total = 0
    for content in items:
        total += _schedule_payloads_for_content(content)
    console.print(f"\n[green]Scheduled {total}[/green] payloads across {len(items)} approved items.")


def _post_single(
    content_id: str,
    delay_state: dict[str, object] | None = None,
    row_scope: str | None = None,
    *,
    use_ig_poster: bool = False,
) -> bool:
    content = _resolve_content_for_scope(content_id, row_scope) if row_scope else _resolve_content(content_id)
    if not content:
        console.print(f"[red]Content {content_id} not found.[/red]")
        return False

    product = db.get_product(content.product_sku)
    if not product:
        console.print(f"[red]Product {content.product_sku} not found.[/red]")
        return False

    payloads = db.list_platform_payloads(content.id)
    if payloads:
        enabled = set(config.enabled_platforms("posting"))
        posted = 0
        submitted = 0
        already_posted = 0
        already_submitted = 0
        skipped = 0
        console.print(Panel(f"Posting [bold]{content.id}[/bold]", style="blue"))
        for payload in payloads:
            if payload.status == "posted":
                already_posted += 1
                continue
            if payload.status == "submitted":
                already_submitted += 1
                continue
            if payload.platform not in enabled:
                console.print(
                    f"  [yellow]SKIP[/yellow] Skipping {payload.platform}: platform not enabled in config"
                )
                skipped += 1
                continue
            _wait_for_next_platform_post(payload.platform, delay_state)
            try:
                post = _post_platform_payload(
                    payload, content, product, use_ig_poster=use_ig_poster
                )
                if payload.id is not None:
                    payload_status = db.mark_platform_payload_delivery(payload.id, post.post_id or "")
                else:
                    pid = post.post_id or ""
                    payload_status = (
                        "submitted"
                        if pid.startswith("make:") or pid.startswith("ig_phone:")
                        else "posted"
                    )
                if payload_status == "submitted":
                    console.print(
                        f"  [green]OK[/green] Submitted {payload.platform} handoff "
                        f"(post_id={post.post_id})"
                    )
                    submitted += 1
                else:
                    console.print(
                        f"  [green]OK[/green] Posted to {payload.platform} (post_id={post.post_id})"
                    )
                    posted += 1
            except Exception as exc:
                if payload.id is not None:
                    db.update_platform_payload_status(payload.id, "failed", str(exc))
                console.print(f"  [red]ERROR[/red] Failed to post to {payload.platform}: {exc}")
            finally:
                _mark_platform_attempt(payload.platform, delay_state)
        console.print(
            f"\n[green]Posted {posted}[/green] payloads for {content.id[:12]}. "
            f"[cyan]Submitted {submitted}[/cyan]. "
            f"[yellow]Already posted {already_posted}[/yellow]. "
            f"[yellow]Already submitted {already_submitted}[/yellow]. "
            f"[yellow]Skipped {skipped}[/yellow]."
        )
        return True

    captions, hashtags = _load_post_metadata(content)
    console.print(
        "[yellow]No persisted payloads found; falling back to reconstructed post metadata.[/yellow]"
    )
    console.print(Panel(f"Posting [bold]{content.id}[/bold]", style="blue"))
    _post_content_to_all(content, product, captions, hashtags, delay_state=delay_state)
    return True


def _post_due(*, use_ig_poster: bool = False):
    # Use UTC now so due check matches publish_at (stored in UTC from scheduling)
    now_iso = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    payloads = db.list_due_platform_payloads(now_iso=now_iso)
    if not payloads:
        console.print("[yellow]No scheduled payloads are due.[/yellow]")
        return

    enabled = set(config.enabled_platforms("posting"))
    posted = 0
    submitted = 0
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
            post = _post_platform_payload(
                payload, content, product, use_ig_poster=use_ig_poster
            )
            if payload.id is not None:
                payload_status = db.mark_platform_payload_delivery(payload.id, post.post_id or "")
            else:
                pid = post.post_id or ""
                payload_status = (
                    "submitted"
                    if pid.startswith("make:") or pid.startswith("ig_phone:")
                    else "posted"
                )
            if payload_status == "submitted":
                console.print(
                    f"  [green]OK[/green] Submitted {payload.platform} handoff "
                    f"(post_id={post.post_id})"
                )
                submitted += 1
            else:
                console.print(
                    f"  [green]OK[/green] Posted to {payload.platform} (post_id={post.post_id})"
                )
                posted += 1
        except Exception as exc:
            if payload.id is not None:
                db.update_platform_payload_status(payload.id, "failed", str(exc))
            console.print(f"  [red]ERROR[/red] Failed to post to {payload.platform}: {exc}")

    console.print(
        f"\n[green]Posted {posted}[/green] scheduled payloads. "
        f"[cyan]Submitted {submitted}[/cyan]. "
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

@cli.command("sync-instagram-ids")
@click.argument("mappings", nargs=-1, required=True)
def sync_instagram_ids_cmd(mappings: tuple[str, ...]):
    """Update posts.post_id from handoff IDs to Instagram media IDs.

    Each mapping is handoff_id:ig_id (e.g. make:videos/foo.mp4:DVl619ikQzM).
    Extract ig_id from the reel URL: instagram.com/reel/SHORTCODE -> SHORTCODE.
    """
    _init()
    updated = 0
    for pair in mappings:
        if ":" not in pair:
            console.print(f"[yellow]Skipping invalid mapping:[/yellow] {pair}")
            continue
        # handoff_id contains colons (e.g. make:videos/foo.mp4), ig_id is after last colon
        last_colon = pair.rfind(":")
        handoff_id = pair[:last_colon].strip()
        ig_id = pair[last_colon + 1 :].strip()
        handoff_id = handoff_id.strip()
        ig_id = ig_id.strip()
        n = db.sync_instagram_post_id(ig_id, handoff_id=handoff_id)
        if n:
            updated += 1
            console.print(f"  [green]OK[/green] {handoff_id} -> {ig_id}")
        else:
            console.print(f"  [yellow]![/yellow] No match for {handoff_id}")
    console.print(f"\n[green]{updated}[/green] post(s) updated.")


@cli.command("diagnose-instagram-sync")
def diagnose_instagram_sync_cmd():
    """Inspect Google Sheet rows and show how they map to local Instagram posts."""
    _init()
    try:
        diagnostic = inspect_instagram_post_ids_from_sheet()
    except Exception as exc:
        console.print(f"[red]Instagram sync diagnose failed:[/red] {exc}")
        sys.exit(1)

    console.print(
        "[green]Instagram ID sync diagnostic:[/green] "
        f"{diagnostic.rows_considered} eligible rows from {diagnostic.rows_read} sheet rows, "
        f"{sum(1 for row in diagnostic.row_results if row.status == 'matched')} matched, "
        f"{sum(1 for row in diagnostic.row_results if row.status == 'already_synced')} already synced, "
        f"{sum(1 for row in diagnostic.row_results if row.status == 'no_match')} unmatched."
    )

    table = Table(title="Instagram Sheet Sync Diagnostic")
    table.add_column("Row", justify="right")
    table.add_column("Status", style="cyan")
    table.add_column("Match")
    table.add_column("Local Row", justify="right")
    table.add_column("Local ID Before")
    table.add_column("Sheet IG ID")
    table.add_column("Content ID")
    table.add_column("Handoff ID")
    table.add_column("Detail")

    for row in diagnostic.row_results:
        table.add_row(
            str(row.row_number),
            row.status,
            row.matched_by,
            str(row.local_post_row_id or ""),
            row.local_post_id_before,
            row.instagram_post_id,
            row.content_id,
            row.handoff_id,
            row.detail,
        )

    console.print(table)


@cli.command("pull-analytics")
def pull_analytics_cmd():
    """Pull analytics from all platforms and update bandit model."""
    _init()
    platforms_with_metrics = 0
    total_metric_rows = 0

    try:
        sheet_sync = sync_instagram_post_ids_from_sheet()
        if sheet_sync.rows_read:
            console.print(
                "[green]Instagram ID sync:[/green] "
                f"{sheet_sync.rows_updated}/{sheet_sync.rows_considered} eligible rows updated "
                f"from {sheet_sync.rows_read} sheet rows."
            )
    except Exception as exc:
        console.print(f"[yellow]Instagram ID sync skipped:[/yellow] {exc}")

    try:
        phone_sync = sync_instagram_post_ids_from_phone_queue()
        if phone_sync.posts_considered:
            console.print(
                "[green]Instagram phone queue sync:[/green] "
                f"{phone_sync.posts_updated}/{phone_sync.posts_considered} posts updated."
            )
    except Exception as exc:
        console.print(f"[yellow]Instagram phone queue sync skipped:[/yellow] {exc}")

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
            pulled_rows = puller.pull()
            total_metric_rows += pulled_rows
            if pulled_rows > 0:
                console.print(f"  [green]OK[/green] {name} ({pulled_rows} metrics)")
                platforms_with_metrics += 1
            else:
                console.print(f"  [yellow]![/yellow] {name}: no metric rows saved")
        except Exception as exc:
            console.print(f"  [red]ERROR[/red] {name}: {exc}")

    updated = bandit.update_from_metrics()
    console.print(
        f"\n[green]{platforms_with_metrics}[/green] platforms returned metrics, "
        f"[green]{total_metric_rows}[/green] metric rows saved, "
        f"[green]{updated}[/green] bandit arms updated."
    )


# ---------------------------------------------------------------------------
# report-product
# ---------------------------------------------------------------------------

@cli.command("report-product")
@click.option("--product", "slug", required=True, help="Product SKU")
def report_product(slug: str):
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
    del slug
    arms = db.list_bandit_arms()
    if not arms:
        console.print("[dim]No bandit arms initialized yet.[/dim]")
        return

    recommendation_count = int(config.get("bandit.daily_slots", 8))
    rec = bandit.recommend(recommendation_count)

    arms_by_key = {arm.arm_key: arm for arm in arms}
    rec_table = Table(title="Current Recommendations")
    rec_table.add_column("Theme", style="cyan")
    rec_table.add_column("Hook Type")
    rec_table.add_column("Posterior Mean", justify="right")
    rec_table.add_column("Sampled Score", justify="right")
    rec_table.add_column("Mode")
    for alloc in rec.allocations:
        arm = arms_by_key.get(alloc.arm_key) if alloc.arm_key else None
        if not arm:
            arm = next(
                (a for a in arms if a.theme == alloc.theme and a.hook_type == alloc.hook_type),
                None,
            )
        trials = max(int((arm.alpha + arm.beta) - 2), 0) if arm else 0
        mode = "explore" if trials < 5 else "exploit"
        rec_table.add_row(
            alloc.theme,
            alloc.hook_type,
            f"{bandit.posterior_mean(arm):.3f}" if arm else "0.500",
            f"{alloc.score:.3f}",
            mode,
        )
    console.print(rec_table)

    arms.sort(
        key=lambda a: (
            bandit.posterior_mean(a),
            a.alpha,
        ),
        reverse=True,
    )
    table = Table(title="Learned Strategy Performance (top 10)")
    table.add_column("Theme", style="cyan")
    table.add_column("Hook Type")
    table.add_column("Arm Key")
    table.add_column("Trials", justify="right")
    table.add_column("Posterior Mean", justify="right")
    for arm in arms[:10]:
        trials = max(int((arm.alpha + arm.beta) - 2), 0)
        rate = bandit.posterior_mean(arm) * 100
        table.add_row(
            arm.theme,
            arm.hook_type,
            arm.arm_key,
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
# eval-content / eval-batch / daily-loop
# ---------------------------------------------------------------------------


@cli.command("eval-content")
@click.option("--content-id", required=True, help="Content ID to evaluate")
def eval_content_cmd(content_id: str):
    """Score a single content item against the 6-criterion eval checklist."""
    _init()
    content = db.get_content(content_id)
    if content is None:
        console.print(f"[red]Content {content_id} not found.[/red]")
        sys.exit(1)
    score = content_eval.score_content(content)
    evals = db.get_content_evals(content_id)
    for ev in evals:
        status = "[green]PASS[/green]" if ev.passed else "[red]FAIL[/red]"
        console.print(f"  {ev.criterion}: {status}")
    console.print(f"\n[bold]Total: {score}/6[/bold]")


@cli.command("eval-batch")
@click.option("--lookback-days", type=int, default=7, help="Score content from last N days")
def eval_batch_cmd(lookback_days: int):
    """Score all unscored content from the lookback window."""
    _init()
    scored = content_eval.score_batch(lookback_days=lookback_days)
    console.print(f"[green]Scored {scored} content item(s).[/green]")


@cli.command("daily-loop")
@click.option("--lookback-days", type=int, default=7, help="Lookback window for eval and review")
def daily_loop_cmd(lookback_days: int):
    """Run the full daily analysis loop: pull-analytics, eval-batch, review-text, report."""
    _init()

    console.print("[bold]Step 1/4: pull-analytics[/bold]")
    try:
        try:
            sync_instagram_post_ids_from_sheet()
        except Exception:
            pass
        enabled = config.enabled_platforms("analytics")
        total_metrics = 0
        for name in enabled:
            puller = PULLERS[name]()
            total_metrics += puller.pull()
        updated = bandit.update_from_metrics()
        console.print(f"  {total_metrics} metric rows, {updated} bandit arms updated.")
    except Exception as exc:
        console.print(f"[yellow]pull-analytics warning:[/yellow] {exc}")

    console.print("\n[bold]Step 2/4: eval-batch[/bold]")
    try:
        scored = content_eval.score_batch(lookback_days=lookback_days)
        console.print(f"  Scored {scored} content item(s).")
    except Exception as exc:
        console.print(f"[yellow]eval-batch warning:[/yellow] {exc}")

    console.print("\n[bold]Step 3/4: review-text[/bold]")
    try:
        min_posts = int(config.get("text_review.min_posts", 5))
        review_lookback = int(config.get("text_review.lookback_days", 30))
        insight = run_text_review(
            min_posts=min_posts,
            lookback_days=review_lookback,
        )
        if insight:
            console.print(f"  Created text insight {insight.id} from {insight.source_post_count} post(s).")
            console.print()
            console.print(Panel(
                _console_safe_text(insight.insight_text),
                title="[bold]Text insight (review-text)[/bold]",
                border_style="cyan",
                padding=(0, 2),
            ))
        else:
            console.print("  [yellow]No text insight created (not enough posts).[/yellow]")
    except Exception as exc:
        console.print(f"[yellow]review-text warning:[/yellow] {exc}")

    console.print("\n[bold]Step 4/4: report[/bold]")
    try:
        briefing = generate_briefing()
        display_briefing(briefing)
        email_briefing(briefing)
        console.print("[green]Briefing sent.[/green]")
    except Exception as exc:
        console.print(f"[yellow]report warning:[/yellow] {exc}")

    console.print("\n[bold cyan]Bandit recommendations[/bold cyan]")
    try:
        _report_bandit_weights("")
    except Exception as exc:
        console.print(f"[yellow]Could not print recommendations:[/yellow] {exc}")

    console.print("\n[green]Daily loop complete.[/green]")


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
