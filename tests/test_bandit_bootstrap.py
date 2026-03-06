from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src import bandit, db, product_images
from src.models import HOOK_TYPES, Product, THEMES


def test_recommend_initializes_missing_arms(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="auto-init", name="Auto Init"))

    with patch("numpy.random.beta", return_value=0.5):
        rec = bandit.recommend("auto-init", count=2)

    assert rec.product_sku == "auto-init"
    assert sum(item.count for item in rec.allocations) == 2
    assert len(db.get_bandit_arms("auto-init")) == len(THEMES) * len(HOOK_TYPES)


def test_increment_bandit_records_first_observation(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="first-obs", name="First Observation"))

    db.increment_bandit("first-obs", "fear", "question", success=True)
    arms = db.get_bandit_arms("first-obs")
    assert len(arms) == 1
    assert arms[0].successes == 2
    assert arms[0].failures == 1

    db.increment_bandit("first-obs", "fear", "question", success=False)
    arms = db.get_bandit_arms("first-obs")
    assert arms[0].successes == 2
    assert arms[0].failures == 2


def test_recommend_can_filter_by_hook_type(tmp_db: Path) -> None:
    db.upsert_product(Product(sku="hook-filter", name="Hook Filter"))

    with patch("numpy.random.random", return_value=0.5):
        rec = bandit.recommend("hook-filter", count=2, hook_type="question")

    assert sum(item.count for item in rec.allocations) == 2
    assert {item.hook_type for item in rec.allocations} == {"question"}
    assert {item.theme for item in rec.allocations}.issubset(set(THEMES))


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
