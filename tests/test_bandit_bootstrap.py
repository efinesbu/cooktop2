from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src import bandit, db, product_images
from src.models import Product, V5_NAMES, ZODIAC_SIGNS


def test_recommend_initializes_missing_arms(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="auto-init", name="Auto Init"))

    with patch("numpy.random.beta", return_value=0.5):
        rec = bandit.recommend(total_slots=2)

    assert sum(item.count for item in rec.allocations) == 2
    assert len(db.list_bandit_arms()) == len(bandit.starter_arm_keys())
    assert bandit.starter_arm_keys() == [
        "stakes_cost_of_inaction__relatable_pain",
        "problem_solution__relatable_pain",
        "hidden_knowledge__question",
        "identity_tribe__bold_claim",
    ]


def test_increment_bandit_records_first_observation(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="first-obs", name="First Observation"))
    bandit.initialize_arms()
    starter_key = bandit.starter_arm_keys()[0]

    db.increment_bandit(starter_key, success=True)
    arm = db.get_bandit_arm(starter_key)
    assert arm is not None
    assert arm.alpha == 2.0
    assert arm.beta == 1.0

    db.increment_bandit(starter_key, success=False)
    arm = db.get_bandit_arm(starter_key)
    assert arm is not None
    assert arm.alpha == 2.0
    assert arm.beta == 2.0


def test_register_images_initializes_bandit_for_ready_product(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    db.upsert_product(Product(sku="hero-product", name="Hero Product"))
    image_root = tmp_path / "velura-data" / "product-images" / "hero-product"
    image_root.mkdir(parents=True)
    (image_root / "hero-main.png").write_bytes(b"image")

    monkeypatch.setattr(
        "src.config._config",
        {"data_root": str(tmp_path / "velura-data")},
    )

    with patch("src.product_images.bandit.initialize_arms") as init_mock:
        images = product_images.register_images("hero-product")

    assert len(images) == 1
    init_mock.assert_called_once_with("hero-product")


def test_recommend_v5_seeds_horoscope_name_arms(tmp_db: Path) -> None:
    """First recommend_v5 initializes the full zodiac x presenter arm grid."""
    db.upsert_product(Product(sku="v5-boot", name="V5 Boot"))
    with patch("numpy.random.beta", return_value=0.5):
        bandit.recommend_v5(total_slots=1)

    arms = db.list_bandit_arms()
    v5_keys = {
        a.arm_key for a in arms
        if a.theme in ZODIAC_SIGNS and a.hook_type in V5_NAMES
    }
    assert len(v5_keys) == len(ZODIAC_SIGNS) * len(V5_NAMES)
