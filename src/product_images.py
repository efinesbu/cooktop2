from __future__ import annotations

import logging
from pathlib import Path

from src import bandit, config, db
from src.models import Product, ProductImage

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _classify_image_type(filename: str) -> str:
    lower = filename.lower()
    if "hero" in lower:
        return "hero"
    if "lifestyle" in lower:
        return "lifestyle"
    return "detail"


def register_images(product_sku: str) -> list[ProductImage]:
    existing = db.get_product(product_sku)
    if not existing:
        raise ValueError(
            f"Product '{product_sku}' not found. Add it first with "
            "`python cli.py add-product --sku ... --name ...`."
        )

    image_dir = config.product_images_dir() / product_sku
    if not image_dir.is_dir():
        logger.warning("Image directory not found: %s", image_dir)
        return []

    files = sorted(
        f for f in image_dir.iterdir()
        if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS
    )
    if not files:
        logger.info("No image files found in %s", image_dir)
        return []

    db.clear_product_images(product_sku)

    images: list[ProductImage] = []
    has_hero = False

    for f in files:
        img_type = _classify_image_type(f.name)
        if img_type == "hero":
            has_hero = True

        img = ProductImage(
            product_sku=product_sku,
            file_path=str(f),
            image_type=img_type,
        )
        img.id = db.insert_product_image(img)
        images.append(img)

    existing.image_dir = str(image_dir)
    existing.generation_ready = has_hero
    db.upsert_product(existing)
    if has_hero:
        bandit.initialize_arms(product_sku)

    logger.info(
        "Registered %d images for SKU %s (hero=%s)",
        len(images), product_sku, has_hero,
    )
    return images


def get_hero_image(product_sku: str) -> Path | None:
    images = db.list_product_images(product_sku)
    for img in images:
        if img.image_type == "hero":
            p = Path(img.file_path)
            if p.exists():
                return p
    return None
