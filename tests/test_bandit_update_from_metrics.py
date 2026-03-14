from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import bandit, db
from src.models import BanditArm, Content, Metric, Post, Product


def _create_post_with_metric(
    *,
    product_sku: str,
    content_id: str,
    theme: str,
    hook_type: str,
    platform: str = "youtube",
    views: int,
    likes: int,
    comments: int = 0,
    shares: int = 0,
    saves: int = 0,
) -> int:
    if db.get_content(content_id) is None:
        db.insert_content(
            Content(
                id=content_id,
                product_sku=product_sku,
                theme=theme,
                hook_type=hook_type,
            )
        )
    post_id = db.insert_post(
        Post(
            content_id=content_id,
            platform=platform,
            post_id=f"{platform}-{content_id}",
        )
    )
    db.insert_metric(
        Metric(
            post_id=post_id,
            platform=platform,
            views=views,
            likes=likes,
            comments=comments,
            shares=shares,
            saves=saves,
        )
    )
    return post_id


def test_update_from_metrics_is_idempotent(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="serum-x", name="Serum X"))
    bandit.initialize_arms()

    _create_post_with_metric(
        product_sku="serum-x",
        content_id="content-a",
        theme="problem_solution",
        hook_type="relatable_pain",
        views=100,
        likes=40,
    )
    _create_post_with_metric(
        product_sku="serum-x",
        content_id="content-b",
        theme="benefit",
        hook_type="bold_claim",
        views=100,
        likes=10,
    )

    updated = bandit.update_from_metrics()
    assert updated == 2

    first_pass = {
        arm.arm_key: (arm.alpha, arm.beta)
        for arm in db.list_bandit_arms()
    }

    updated = bandit.update_from_metrics()
    assert updated == 0

    second_pass = {
        arm.arm_key: (arm.alpha, arm.beta)
        for arm in db.list_bandit_arms()
    }
    assert second_pass == first_pass


def test_update_from_metrics_aggregates_by_creative_and_uses_latest_metric_per_post(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="serum-y", name="Serum Y"))
    bandit.initialize_arms()

    post_id = _create_post_with_metric(
        product_sku="serum-y",
        content_id="content-c",
        theme="benefit",
        hook_type="bold_claim",
        views=100,
        likes=10,
    )
    db.insert_metric(
        Metric(
            post_id=post_id,
            platform="youtube",
            views=100,
            likes=60,
        )
    )
    _create_post_with_metric(
        product_sku="serum-y",
        content_id="content-c",
        theme="benefit",
        hook_type="bold_claim",
        platform="instagram",
        views=100,
        likes=20,
    )
    _create_post_with_metric(
        product_sku="serum-y",
        content_id="content-d",
        theme="problem_solution",
        hook_type="relatable_pain",
        views=100,
        likes=20,
    )

    updated = bandit.update_from_metrics()
    assert updated == 2

    winning_arm = db.get_bandit_arm(bandit.arm_key("benefit", "bold_claim"))
    losing_arm = db.get_bandit_arm(bandit.arm_key("problem_solution", "relatable_pain"))

    assert winning_arm is not None
    assert winning_arm.alpha == 2.0
    assert winning_arm.beta == 1.0

    assert losing_arm is not None
    assert losing_arm.alpha == 1.0
    assert losing_arm.beta == 2.0

    assert db.has_bandit_observation_for_content("content-c") is True
    assert db.has_bandit_observation_for_content("content-d") is True


def test_update_from_metrics_falls_back_to_engagement_when_no_commerce(monkeypatch, tmp_db: Path) -> None:
    """When ranking_objective is revenue but no commerce data, uses engagement."""
    monkeypatch.setattr("src.bandit._ranking_objective", lambda: "revenue")
    db.upsert_product(Product(sku="serum-z", name="Serum Z"))
    bandit.initialize_arms()

    _create_post_with_metric(
        product_sku="serum-z",
        content_id="content-e",
        theme="problem_solution",
        hook_type="relatable_pain",
        views=100,
        likes=50,
    )
    _create_post_with_metric(
        product_sku="serum-z",
        content_id="content-f",
        theme="benefit",
        hook_type="bold_claim",
        views=100,
        likes=5,
    )

    updated = bandit.update_from_metrics()
    assert updated == 2

    winning_arm = db.get_bandit_arm(bandit.arm_key("problem_solution", "relatable_pain"))
    losing_arm = db.get_bandit_arm(bandit.arm_key("benefit", "bold_claim"))
    assert winning_arm is not None and winning_arm.alpha == 2.0
    assert losing_arm is not None and losing_arm.beta == 2.0


def test_update_from_metrics_legacy_content_produces_observation_with_canonical_arm_key(
    tmp_db: Path,
) -> None:
    """Legacy content still produces observations keyed only by theme and hook_type."""
    db.upsert_product(Product(sku="serum-legacy", name="Serum Legacy"))
    bandit.initialize_arms()

    _create_post_with_metric(
        product_sku="serum-legacy",
        content_id="content-legacy",
        theme="benefit",
        hook_type="bold_claim",
        views=100,
        likes=30,
    )

    updated = bandit.update_from_metrics()
    assert updated == 1

    obs = db.get_bandit_observation_for_content("content-legacy")
    assert obs is not None
    assert obs.arm_key == "benefit__bold_claim"


def test_update_from_metrics_ignores_non_arm_strategy_metadata(
    tmp_db: Path,
) -> None:
    """Stored strategy metadata should not create extra bandit arms."""
    db.upsert_product(Product(sku="serum-v2", name="Serum V2"))
    arm_key = "benefit__bold_claim"
    db.upsert_bandit_arm(
        BanditArm(
            arm_key=arm_key,
            theme="benefit",
            hook_type="bold_claim",
        )
    )
    bandit.initialize_arms()

    # Content with strategy_metadata_json
    strategy = {"style_family": "anamorphic", "style_angle": "Luxury closeup"}
    db.insert_content(
        Content(
            id="content-v2",
            product_sku="serum-v2",
            theme="benefit",
            hook_type="bold_claim",
            strategy_metadata_json=json.dumps(strategy),
        )
    )
    _create_post_with_metric(
        product_sku="serum-v2",
        content_id="content-v2",
        theme="benefit",
        hook_type="bold_claim",
        views=100,
        likes=50,
    )

    updated = bandit.update_from_metrics()
    assert updated == 1

    obs = db.get_bandit_observation_for_content("content-v2")
    assert obs is not None
    assert obs.arm_key == arm_key
