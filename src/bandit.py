from __future__ import annotations

from collections import defaultdict
from statistics import median

import numpy as np

from src import db
from src.creative_strategy import base_weight
from src.models import (
    BanditArm,
    BanditObservation,
    BanditRecommendation,
    ThemeHookAllocation,
    THEMES,
    HOOK_TYPES,
)


def recommend(
    product_sku: str,
    count: int,
    theme: str | None = None,
    hook_type: str | None = None,
) -> BanditRecommendation:
    existing_arms = db.get_bandit_arms(product_sku)
    if not existing_arms:
        initialize_arms(product_sku)
        existing_arms = db.get_bandit_arms(product_sku)
    existing = {(a.theme, a.hook_type): a for a in existing_arms}

    scored: list[tuple[str, str, float]] = []
    for theme_id in THEMES:
        if theme and theme_id != theme:
            continue
        for hook_id in HOOK_TYPES:
            if hook_type and hook_id != hook_type:
                continue
            arm = existing.get((theme_id, hook_id))
            score = _score_arm(theme_id, hook_id, arm)
            scored.append((theme_id, hook_id, score))

    if not scored:
        raise ValueError("No eligible theme/hook combinations are available for recommendation.")

    scored.sort(key=lambda x: x[2], reverse=True)

    allocation_map: dict[tuple[str, str], int] = defaultdict(int)
    for i in range(count):
        idx = i % len(scored)
        key = (scored[idx][0], scored[idx][1])
        allocation_map[key] += 1

    allocations = [
        ThemeHookAllocation(
            theme=theme,
            hook_type=hook_type,
            count=allocation_map[(theme, hook_type)],
            score=score,
        )
        for theme, hook_type, score in scored
        if (theme, hook_type) in allocation_map
    ]

    return BanditRecommendation(product_sku=product_sku, allocations=allocations)


def _score_arm(theme: str, hook_type: str, arm: BanditArm | None) -> float:
    weight = base_weight(theme, hook_type)
    if arm is None or _arm_trials(arm) == 0:
        return float(np.random.random() * weight)
    alpha = arm.successes
    beta_param = arm.failures
    return float(np.random.beta(alpha, beta_param) * weight)


def _arm_trials(arm: BanditArm) -> int:
    return max(arm.successes + arm.failures - 2, 0)


def update_from_metrics() -> int:
    posts = db.list_recent_posts(days=30)

    post_data: list[tuple[int, int, str, str, str, float]] = []
    product_rates: dict[str, list[float]] = defaultdict(list)

    for post in posts:
        if post.id is None:
            continue
        metrics = db.latest_metrics_for_post(post.id)
        if metrics is None:
            continue
        content = db.get_content(post.content_id)
        if content is None:
            continue

        engagement_rate = (
            (metrics.likes + metrics.comments + metrics.shares + metrics.saves)
            / max(metrics.views, 1)
        )
        post_data.append((
            post.id,
            metrics.id or 0,
            content.product_sku,
            content.theme,
            content.hook_type,
            engagement_rate,
        ))
        product_rates[content.product_sku].append(engagement_rate)

    updated = 0
    for post_id, metric_id, product_sku, theme, hook_type, rate in post_data:
        if db.has_bandit_observation(post_id):
            continue
        med = median(product_rates[product_sku])
        success = rate > med
        db.increment_bandit(product_sku, theme, hook_type, success)
        db.insert_bandit_observation(BanditObservation(
            post_id=post_id,
            metric_id=metric_id,
            product_sku=product_sku,
            theme=theme,
            hook_type=hook_type,
            engagement_rate=rate,
            success=success,
        ))
        updated += 1

    return updated


def initialize_arms(product_sku: str) -> None:
    existing = {(a.theme, a.hook_type) for a in db.get_bandit_arms(product_sku)}
    for theme in THEMES:
        for hook_type in HOOK_TYPES:
            if (theme, hook_type) not in existing:
                db.upsert_bandit_arm(BanditArm(
                    product_sku=product_sku,
                    theme=theme,
                    hook_type=hook_type,
                    successes=1,
                    failures=1,
                ))
