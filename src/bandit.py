from __future__ import annotations

from math import floor
from statistics import median

import numpy as np

from src import config, db
from src.models import (
    BanditArm,
    BanditObservation,
    BanditRecommendation,
    ThemeHookAllocation,
    V5_NAMES,
    ZODIAC_SIGNS,
)

ARM_KEY_SEPARATOR = "__"
DEFAULT_STARTER_ARMS: list[tuple[str, str]] = [
    ("stakes_cost_of_inaction", "relatable_pain"),
    ("problem_solution", "relatable_pain"),
    ("hidden_knowledge", "question"),
    ("identity_tribe", "bold_claim"),
]


def arm_key(theme: str, hook_type: str) -> str:
    """Build arm key from the canonical learnable strategy pair."""
    return f"{theme}{ARM_KEY_SEPARATOR}{hook_type}"


def parse_arm_key(key: str) -> tuple[str, str]:
    """Parse arm key into its canonical strategy pair."""
    parts = key.split(ARM_KEY_SEPARATOR)
    if len(parts) == 2:
        return parts[0], parts[1]
    raise ValueError(f"Invalid arm_key format: {key!r}")


def starter_arm_keys() -> list[str]:
    configured = config.get("bandit.starter_arms")
    if configured is None:
        return [arm_key(theme, hook_type) for theme, hook_type in DEFAULT_STARTER_ARMS]
    if not isinstance(configured, list):
        raise ValueError("config.yaml `bandit.starter_arms` must be a list of arm keys")
    return [str(value).strip() for value in configured if str(value).strip()]


def initialize_arms(product_sku: str | None = None) -> None:
    del product_sku
    existing = {arm.arm_key for arm in db.list_bandit_arms()}
    starter_arms = []
    for key in starter_arm_keys():
        if key in existing:
            continue
        theme, hook_type = parse_arm_key(key)
        starter_arms.append(
            BanditArm(
                arm_key=key,
                theme=theme,
                hook_type=hook_type,
                alpha=1.0,
                beta=1.0,
            )
        )
    if starter_arms:
        db.seed_bandit_arms(starter_arms)


def initialize_v5_arms(product_sku: str | None = None) -> None:
    """Seed bandit arms for V5 horoscope reels (zodiac sign × presenter name)."""
    del product_sku
    existing = {arm.arm_key for arm in db.list_bandit_arms()}
    starter_arms: list[BanditArm] = []
    for horoscope in ZODIAC_SIGNS:
        for name in V5_NAMES:
            key = arm_key(horoscope, name)
            if key in existing:
                continue
            starter_arms.append(
                BanditArm(
                    arm_key=key,
                    theme=horoscope,
                    hook_type=name,
                    alpha=1.0,
                    beta=1.0,
                )
            )
    if starter_arms:
        db.seed_bandit_arms(starter_arms)


def _recommend_from_arms(arms: list[BanditArm], total_slots: int) -> BanditRecommendation:
    """Thompson sampling allocation over the given arm set (shared by recommend / recommend_v5)."""
    if total_slots <= 0:
        raise ValueError("total_slots must be positive")
    if not arms:
        raise ValueError("No bandit arms are available for recommendation.")

    sampled_scores = {
        arm.arm_key: float(np.random.beta(arm.alpha, arm.beta))
        for arm in arms
    }
    ranked_arms = sorted(
        arms,
        key=lambda arm: sampled_scores[arm.arm_key],
        reverse=True,
    )

    top_k = min(_min_top_k(), len(ranked_arms), total_slots)
    max_per_arm = _max_slots_per_arm(total_slots)
    allocation_map = {arm.arm_key: 0 for arm in ranked_arms}

    for arm in ranked_arms[:top_k]:
        allocation_map[arm.arm_key] += 1

    remaining_slots = total_slots - top_k
    if remaining_slots > 0:
        _allocate_remaining_slots(
            ranked_arms,
            sampled_scores,
            allocation_map,
            remaining_slots,
            max_per_arm,
        )

    allocations = [
        ThemeHookAllocation(
            theme=arm.theme,
            hook_type=arm.hook_type,
            count=allocation_map[arm.arm_key],
            score=sampled_scores[arm.arm_key],
            arm_key=arm.arm_key,
        )
        for arm in ranked_arms
        if allocation_map[arm.arm_key] > 0
    ]
    return BanditRecommendation(allocations=allocations)


def _is_v5_arm(arm: BanditArm) -> bool:
    """True if this arm is a V5 horoscope × name pair (see :func:`initialize_v5_arms`)."""
    return arm.theme in ZODIAC_SIGNS and arm.hook_type in V5_NAMES


def recommend(total_slots: int = 8) -> BanditRecommendation:
    initialize_arms()
    # Exclude V5-only arms so standard runs are not diluted after V5 has been seeded.
    arms = [arm for arm in db.list_bandit_arms() if not _is_v5_arm(arm)]
    return _recommend_from_arms(arms, total_slots)


def recommend_v5(total_slots: int = 8) -> BanditRecommendation:
    """Recommend allocations over V5 horoscope × name arms only."""
    initialize_v5_arms()
    arms = [
        arm
        for arm in db.list_bandit_arms()
        if arm.theme in ZODIAC_SIGNS and arm.hook_type in V5_NAMES
    ]
    return _recommend_from_arms(arms, total_slots)


def ranked_arm_summaries() -> list[tuple[BanditArm, float]]:
    initialize_arms()
    arms = db.list_bandit_arms()
    return sorted(
        [(arm, posterior_mean(arm)) for arm in arms],
        key=lambda item: item[1],
        reverse=True,
    )


def posterior_mean(arm: BanditArm) -> float:
    return arm.alpha / max(arm.alpha + arm.beta, 1.0)


def _min_top_k() -> int:
    return max(int(config.get("bandit.min_top_k", 3)), 0)


def _max_slots_per_arm(total_slots: int) -> int:
    ceiling = float(config.get("bandit.allocation_ceiling", 0.7))
    return max(1, floor(total_slots * ceiling))


def _allocate_remaining_slots(
    ranked_arms: list[BanditArm],
    sampled_scores: dict[str, float],
    allocation_map: dict[str, int],
    remaining_slots: int,
    max_per_arm: int,
) -> None:
    eligible_arms = [
        arm for arm in ranked_arms
        if allocation_map[arm.arm_key] < max_per_arm
    ]
    if not eligible_arms:
        return

    total_score = sum(sampled_scores[arm.arm_key] for arm in eligible_arms)
    if total_score <= 0:
        total_score = float(len(eligible_arms))

    desired_extras = {
        arm.arm_key: remaining_slots * (
            sampled_scores[arm.arm_key] / total_score
            if total_score > 0 else 1.0 / len(eligible_arms)
        )
        for arm in eligible_arms
    }
    extra_allocations = {arm.arm_key: 0 for arm in eligible_arms}

    for _ in range(remaining_slots):
        candidates = [
            arm for arm in eligible_arms
            if allocation_map[arm.arm_key] < max_per_arm
        ]
        if not candidates:
            break
        chosen = max(
            candidates,
            key=lambda arm: (
                desired_extras[arm.arm_key] - extra_allocations[arm.arm_key],
                sampled_scores[arm.arm_key],
            ),
        )
        allocation_map[chosen.arm_key] += 1
        extra_allocations[chosen.arm_key] += 1

def _ranking_objective() -> str:
    """Return configured ranking objective, validated against supported values."""
    obj = str(config.get("bandit.ranking_objective", "engagement_rate")).strip().lower()
    supported = {"engagement_rate", "views", "revenue", "sessions", "purchases"}
    return obj if obj in supported else "engagement_rate"


def update_from_metrics() -> int:
    """Update bandit arms from post metrics and commerce facts.

    Uses ranking_objective from config: engagement_rate (default), views,
    revenue, sessions, purchases. Falls back to engagement when commerce
    data is sparse.
    """
    posts = db.list_recent_posts(days=30)
    creative_metrics: dict[str, dict] = {}

    for post in posts:
        if post.id is None:
            continue
        metrics = db.latest_metrics_for_post(post.id)
        if metrics is None:
            continue
        content = db.get_content(post.content_id)
        if content is None:
            continue

        aggregate = creative_metrics.setdefault(
            content.id,
            {
                "product_sku": content.product_sku,
                "theme": content.theme,
                "hook_type": content.hook_type,
                "views": 0,
                "engagements": 0,
                "sessions": 0,
                "purchases": 0,
                "revenue": 0.0,
            },
        )
        aggregate["views"] = int(aggregate["views"]) + metrics.views
        aggregate["engagements"] = int(aggregate["engagements"]) + (
            metrics.likes + metrics.comments + metrics.shares + metrics.saves
        )

        # Phase 6: aggregate commerce per content (across platforms for this post)
        commerce = db.aggregate_commerce_for_content(
            content.id, days=30, platform=post.platform
        )
        aggregate["sessions"] += commerce["sessions"]
        aggregate["purchases"] += commerce["purchases"]
        aggregate["revenue"] += commerce["revenue"]

    objective = _ranking_objective()
    updated = 0

    creative_rates = [
        int(item["engagements"]) / max(int(item["views"]), 1)
        for item in creative_metrics.values()
    ]
    engagement_median = median(creative_rates) if creative_rates else 0.0

    if objective in ("revenue", "sessions", "purchases"):
        commerce_scores = []
        for agg in creative_metrics.values():
            if objective == "revenue":
                commerce_scores.append(float(agg["revenue"]))
            elif objective == "sessions":
                commerce_scores.append(int(agg["sessions"]))
            else:
                commerce_scores.append(int(agg["purchases"]))
        commerce_median = median(commerce_scores) if commerce_scores else 0.0
        has_commerce = any(s > 0 for s in commerce_scores)
    else:
        commerce_median = 0.0
        has_commerce = False

    for content_id, aggregate in creative_metrics.items():
        if db.has_bandit_observation_for_content(content_id):
            continue
        theme = str(aggregate["theme"])
        hook_type = str(aggregate["hook_type"])
        key = arm_key(theme, hook_type)

        arm = db.get_bandit_arm(key)
        if arm is None:
            continue

        rate = int(aggregate["engagements"]) / max(int(aggregate["views"]), 1)

        if objective == "revenue" and has_commerce:
            score = float(aggregate["revenue"])
            success = score > commerce_median
        elif objective == "sessions" and has_commerce:
            score = int(aggregate["sessions"])
            success = score > commerce_median
        elif objective == "purchases" and has_commerce:
            score = int(aggregate["purchases"])
            success = score > commerce_median
        elif objective == "views":
            score = int(aggregate["views"])
            views_median = median([int(a["views"]) for a in creative_metrics.values()])
            success = score > views_median
        else:
            success = rate > engagement_median

        db.increment_bandit(key, success)
        db.insert_bandit_observation(
            BanditObservation(
                content_id=content_id,
                product_sku=str(aggregate["product_sku"]),
                arm_key=key,
                theme=theme,
                hook_type=hook_type,
                aggregated_engagement_rate=rate,
                success=success,
            )
        )
        updated += 1

    return updated
