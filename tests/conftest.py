from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

from src import db
from src.models import Content, Product


@pytest.fixture
def tmp_db(tmp_path: Path) -> Generator[Path, None, None]:
    db_file = tmp_path / "test.db"
    old_path = db._DB_PATH
    db.set_db_path(db_file)
    db.init_db()
    yield db_file
    db._DB_PATH = old_path


# Env vars that override config.yaml; cleared in tests so patched config wins.
from src.config import _ENV_OVERRIDES


@pytest.fixture(autouse=True)
def _clear_config_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear env override vars so tests using patched config get deterministic values."""
    for env_var in _ENV_OVERRIDES.values():
        monkeypatch.delenv(env_var, raising=False)


@pytest.fixture
def mock_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    test_cfg = {
        "site_url": "https://test-store.example.com",
        "shopify": {},
        "openai": {"api_key": "test-key"},
        "runway": {"api_key": "test-key"},
        "youtube": {"api_key": "test-key"},
        "instagram": {"api_key": "test-key"},
        "tiktok": {"api_key": "test-key"},
        "x": {"api_key": "test-key"},
        "data_root": str(tmp_path / "velura-data"),
    }
    monkeypatch.setattr("src.config._config", test_cfg)
    return test_cfg


@pytest.fixture
def sample_product() -> Product:
    return Product(
        sku="test-product",
        name="Test Product",
        category="beauty",
        price=29.99,
    )


@pytest.fixture
def sample_content() -> Content:
    return Content(
        id="test-content-001",
        product_sku="test-product",
        theme="benefit_spotlight",
        hook_type="bold_claim",
        hook_text="You won't believe this!",
    )


@pytest.fixture
def db_with_product(
    tmp_db: Path, sample_product: Product
) -> Generator[Path, None, None]:
    db.upsert_product(sample_product)
    yield tmp_db
