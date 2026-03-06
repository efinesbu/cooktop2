from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src import bandit, db
from src.creative_strategy import base_weight
from src.models import HOOK_TYPES, HOOK_DEFINITIONS, Product, THEMES, THEME_DEFINITIONS


def test_recommend_flat_priors(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="flat-test", name="Flat"))

    with patch("numpy.random.beta", return_value=0.5):
        rec = bandit.recommend("flat-test", count=1)

    assert rec.product_sku == "flat-test"
    assert len(rec.allocations) == 1
    assert rec.allocations[0].count == 1


def test_recommend_returns_correct_count(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="count-test", name="Count"))

    requested = 5
    with patch("numpy.random.beta", return_value=0.5):
        rec = bandit.recommend("count-test", count=requested)

    total_allocated = sum(a.count for a in rec.allocations)
    assert total_allocated == requested


def test_initialize_arms(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="init-test", name="Init"))
    bandit.initialize_arms("init-test")

    arms = db.get_bandit_arms("init-test")
    expected_count = len(THEMES) * len(HOOK_TYPES)
    assert len(arms) == expected_count

    themes_seen = {a.theme for a in arms}
    hooks_seen = {a.hook_type for a in arms}
    assert themes_seen == set(THEMES)
    assert hooks_seen == set(HOOK_TYPES)

    for arm in arms:
        assert arm.successes == 1
        assert arm.failures == 1


def test_whitelist_is_meaningful_and_distinct() -> None:
    assert THEMES == [theme.id for theme in THEME_DEFINITIONS if theme.enabled]
    assert HOOK_TYPES == [hook.id for hook in HOOK_DEFINITIONS if hook.enabled]
    assert len(THEMES) == len(set(THEMES))
    assert len(HOOK_TYPES) == len(set(HOOK_TYPES))
    assert "problem_solution" in THEMES
    assert "routine" in THEMES
    assert "quick_tip" in HOOK_TYPES
    assert "visual_surprise" in HOOK_TYPES


def test_recommend_can_filter_by_theme(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="theme-filter", name="Theme Filter"))

    with patch("numpy.random.random", return_value=0.5):
        rec = bandit.recommend("theme-filter", count=3, theme="benefit")

    assert len(rec.allocations) == 3
    assert {alloc.theme for alloc in rec.allocations} == {"benefit"}
    assert {alloc.hook_type for alloc in rec.allocations}.issubset(set(HOOK_TYPES))


def test_recommend_uses_cold_start_weights(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="weighted-test", name="Weighted"))

    with patch("numpy.random.random", return_value=1.0):
        rec = bandit.recommend("weighted-test", count=1)

    top = rec.allocations[0]
    winning_weight = base_weight(top.theme, top.hook_type)

    all_weights = [
        base_weight(theme, hook_type)
        for theme in THEMES
        for hook_type in HOOK_TYPES
    ]
    assert winning_weight == max(all_weights)
