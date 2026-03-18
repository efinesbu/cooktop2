from __future__ import annotations

from pathlib import Path

from src import db
from src.models import BanditObservation, Content, HOOK_TYPES, Product


def _seed_legacy_phase_1_rows() -> None:
    db.upsert_product(Product(sku="legacy-sku", name="Legacy Product"))

    for content_id, theme, hook_type in (
        ("content-problem", "problem_solution", "relatable_pain"),
        ("content-benefit", "benefit", "bold_claim"),
        ("content-fear", "fear", "question"),
        ("content-curiosity", "curiosity", "quick_tip"),
        ("content-social", "social_proof", "visual_surprise"),
        ("content-urgency", "urgency", "relatable_pain"),
        ("content-routine", "routine", "bold_claim"),
    ):
        db.insert_content(
            Content(
                id=content_id,
                product_sku="legacy-sku",
                theme=theme,
                hook_type=hook_type,
            )
        )

    with db._connect() as conn:
        conn.executemany(
            """
            INSERT INTO bandit_state (arm_key, theme, hook_type, alpha, beta, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "urgency__relatable_pain",
                    "urgency",
                    "relatable_pain",
                    2.0,
                    3.0,
                    "2026-03-01 10:00:00",
                ),
                (
                    "fear__relatable_pain",
                    "fear",
                    "relatable_pain",
                    4.0,
                    5.0,
                    "2026-03-02 10:00:00",
                ),
                (
                    "routine__bold_claim",
                    "routine",
                    "bold_claim",
                    6.0,
                    7.0,
                    "2026-03-01 11:00:00",
                ),
                (
                    "benefit__bold_claim",
                    "benefit",
                    "bold_claim",
                    8.0,
                    9.0,
                    "2026-03-03 11:00:00",
                ),
            ],
        )

    for content_id, arm_key, theme, hook_type, rate, success in (
        (
            "content-urgency",
            "urgency__relatable_pain",
            "urgency",
            "relatable_pain",
            0.31,
            False,
        ),
        (
            "content-routine",
            "routine__bold_claim",
            "routine",
            "bold_claim",
            0.62,
            True,
        ),
    ):
        db.insert_bandit_observation(
            BanditObservation(
                content_id=content_id,
                product_sku="legacy-sku",
                arm_key=arm_key,
                theme=theme,
                hook_type=hook_type,
                aggregated_engagement_rate=rate,
                success=success,
            )
        )


def test_phase_1_theme_migration_remaps_legacy_rows_and_seeds_new_themes(
    tmp_db: Path,
) -> None:
    _seed_legacy_phase_1_rows()

    db.init_db()

    expected_content_themes = {
        "content-problem": "problem_solution",
        "content-benefit": "benefit_spotlight",
        "content-fear": "stakes_cost_of_inaction",
        "content-curiosity": "hidden_knowledge",
        "content-social": "identity_tribe",
        "content-urgency": "stakes_cost_of_inaction",
        "content-routine": "benefit_spotlight",
    }
    for content_id, expected_theme in expected_content_themes.items():
        content = db.get_content(content_id)
        assert content is not None
        assert content.theme == expected_theme

    arms_by_key = {arm.arm_key: arm for arm in db.list_bandit_arms()}

    assert "urgency__relatable_pain" not in arms_by_key
    assert "fear__relatable_pain" not in arms_by_key
    assert "routine__bold_claim" not in arms_by_key
    assert "benefit__bold_claim" not in arms_by_key

    stakes_arm = arms_by_key["stakes_cost_of_inaction__relatable_pain"]
    assert stakes_arm.theme == "stakes_cost_of_inaction"
    assert stakes_arm.hook_type == "relatable_pain"
    assert stakes_arm.alpha == 6.0
    assert stakes_arm.beta == 8.0
    assert stakes_arm.last_updated == "2026-03-02 10:00:00"

    benefit_arm = arms_by_key["benefit_spotlight__bold_claim"]
    assert benefit_arm.theme == "benefit_spotlight"
    assert benefit_arm.hook_type == "bold_claim"
    assert benefit_arm.alpha == 14.0
    assert benefit_arm.beta == 16.0
    assert benefit_arm.last_updated == "2026-03-03 11:00:00"

    urgency_obs = db.get_bandit_observation_for_content("content-urgency")
    assert urgency_obs is not None
    assert urgency_obs.arm_key == "stakes_cost_of_inaction__relatable_pain"
    assert urgency_obs.theme == "stakes_cost_of_inaction"
    assert urgency_obs.hook_type == "relatable_pain"

    routine_obs = db.get_bandit_observation_for_content("content-routine")
    assert routine_obs is not None
    assert routine_obs.arm_key == "benefit_spotlight__bold_claim"
    assert routine_obs.theme == "benefit_spotlight"
    assert routine_obs.hook_type == "bold_claim"

    seeded_new_theme_keys = {
        f"{theme}__{hook_type}"
        for theme in ("mechanism_reveal", "mythbust", "contrast_versus")
        for hook_type in HOOK_TYPES
    }
    assert seeded_new_theme_keys.issubset(arms_by_key)

    for key in seeded_new_theme_keys:
        arm = arms_by_key[key]
        theme, hook_type = key.split("__")
        assert arm.theme == theme
        assert arm.hook_type == hook_type
        assert arm.alpha == 1.0
        assert arm.beta == 1.0
