from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src import bandit, db
from src.models import HOOK_DEFINITIONS, HOOK_TYPES, Product, THEMES, THEME_DEFINITIONS


def test_recommend_flat_priors(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="flat-test", name="Flat"))

    with patch("numpy.random.beta", return_value=0.5):
        rec = bandit.recommend(total_slots=1)

    assert len(rec.allocations) == 1
    assert rec.allocations[0].count == 1


def test_recommend_returns_correct_count(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="count-test", name="Count"))

    requested = 5
    with patch("numpy.random.beta", return_value=0.5):
        rec = bandit.recommend(total_slots=requested)

    total_allocated = sum(a.count for a in rec.allocations)
    assert total_allocated == requested


def test_initialize_arms(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="init-test", name="Init"))
    bandit.initialize_arms("init-test")

    arms = db.list_bandit_arms()
    expected_count = len(bandit.starter_arm_keys())
    assert len(arms) == expected_count

    themes_seen = {a.theme for a in arms}
    hooks_seen = {a.hook_type for a in arms}
    assert themes_seen.issubset(set(THEMES))
    assert hooks_seen.issubset(set(HOOK_TYPES))

    for arm in arms:
        assert arm.alpha == 1.0
        assert arm.beta == 1.0


def test_whitelist_and_starter_arms_are_meaningful() -> None:
    assert THEMES == [theme.id for theme in THEME_DEFINITIONS if theme.enabled]
    assert HOOK_TYPES == [hook.id for hook in HOOK_DEFINITIONS if hook.enabled]
    assert len(THEMES) == len(set(THEMES))
    assert len(HOOK_TYPES) == len(set(HOOK_TYPES))
    assert "problem_solution" in THEMES
    assert "benefit_spotlight" in THEMES
    assert "stakes_cost_of_inaction" in THEMES
    assert "hidden_knowledge" in THEMES
    assert "identity_tribe" in THEMES
    assert "quick_tip" in HOOK_TYPES
    assert "visual_surprise" in HOOK_TYPES

    starter_keys = bandit.starter_arm_keys()
    assert len(starter_keys) == 4
    assert starter_keys == [
        "stakes_cost_of_inaction__relatable_pain",
        "problem_solution__relatable_pain",
        "hidden_knowledge__question",
        "identity_tribe__bold_claim",
    ]
    for key in starter_keys:
        theme, hook_type = bandit.parse_arm_key(key)
        assert theme in THEMES
        assert hook_type in HOOK_TYPES


def test_recommend_gives_top_k_a_minimum_slot(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="topk-test", name="Top K"))

    with patch("numpy.random.beta", side_effect=[0.9, 0.8, 0.7, 0.1]):
        rec = bandit.recommend(total_slots=4)

    assert sum(item.count for item in rec.allocations) == 4
    assert len(rec.allocations) >= 3
    assert all(item.count >= 1 for item in rec.allocations[:3])


def test_recommend_enforces_allocation_ceiling(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="ceiling-test", name="Ceiling"))

    with patch("numpy.random.beta", side_effect=[0.99, 0.01, 0.01, 0.01]):
        rec = bandit.recommend(total_slots=8)

    assert sum(item.count for item in rec.allocations) == 8
    assert max(item.count for item in rec.allocations) <= 5
