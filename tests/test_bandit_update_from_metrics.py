from __future__ import annotations

from pathlib import Path

from src import bandit, db
from src.models import Content, Metric, Post, Product


def _create_post_with_metric(
    *,
    product_sku: str,
    content_id: str,
    theme: str,
    hook_type: str,
    views: int,
    likes: int,
    comments: int = 0,
    shares: int = 0,
    saves: int = 0,
) -> int:
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
            platform="youtube",
            post_id=f"yt-{content_id}",
        )
    )
    db.insert_metric(
        Metric(
            post_id=post_id,
            platform="youtube",
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

    _create_post_with_metric(
        product_sku="serum-x",
        content_id="content-a",
        theme="benefit",
        hook_type="question",
        views=100,
        likes=40,
    )
    _create_post_with_metric(
        product_sku="serum-x",
        content_id="content-b",
        theme="curiosity",
        hook_type="quick_tip",
        views=100,
        likes=10,
    )

    updated = bandit.update_from_metrics()
    assert updated == 2

    first_pass = {
        (arm.theme, arm.hook_type): (arm.successes, arm.failures)
        for arm in db.get_bandit_arms("serum-x")
    }

    updated = bandit.update_from_metrics()
    assert updated == 0

    second_pass = {
        (arm.theme, arm.hook_type): (arm.successes, arm.failures)
        for arm in db.get_bandit_arms("serum-x")
    }
    assert second_pass == first_pass


def test_update_from_metrics_uses_latest_metric_for_each_post(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="serum-y", name="Serum Y"))

    post_id = _create_post_with_metric(
        product_sku="serum-y",
        content_id="content-c",
        theme="benefit",
        hook_type="question",
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
        content_id="content-d",
        theme="social_proof",
        hook_type="relatable_pain",
        views=100,
        likes=20,
    )

    updated = bandit.update_from_metrics()
    assert updated == 2

    arms = {(arm.theme, arm.hook_type): arm for arm in db.get_bandit_arms("serum-y")}
    assert arms[("benefit", "question")].successes == 2
    assert arms[("benefit", "question")].failures == 1
    assert arms[("social_proof", "relatable_pain")].successes == 1
    assert arms[("social_proof", "relatable_pain")].failures == 2
