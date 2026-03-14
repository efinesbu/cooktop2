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


def _list_image_files(image_dir: Path) -> list[Path]:
    if not image_dir.is_dir():
        return []
    return sorted(
        f for f in image_dir.iterdir()
        if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS
    )


def _update_product_image_state(existing: Product, image_dir: Path, has_hero: bool) -> None:
    existing.image_dir = str(image_dir)
    existing.generation_ready = has_hero
    db.upsert_product(existing)


def register_images(product_sku: str) -> list[ProductImage]:
    existing = db.get_product(product_sku)
    if not existing:
        raise ValueError(
            f"Product '{product_sku}' not found. Add it first with "
            "`python cli.py add-product --sku ... --name ...`."
        )

    image_dir = config.product_images_dir() / product_sku
    files = _list_image_files(image_dir)

    db.clear_product_images(product_sku)

    if not image_dir.is_dir():
        _update_product_image_state(existing, image_dir, has_hero=False)
        logger.warning("Image directory not found: %s", image_dir)
        return []

    if not files:
        _update_product_image_state(existing, image_dir, has_hero=False)
        logger.info("No image files found in %s", image_dir)
        return []

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

    _update_product_image_state(existing, image_dir, has_hero)
    if has_hero:
        bandit.initialize_arms(product_sku)

    logger.info(
        "Registered %d images for SKU %s (hero=%s)",
        len(images), product_sku, has_hero,
    )
    return images


def refresh_images_if_changed(product_sku: str) -> tuple[list[ProductImage], bool]:
    image_dir = config.product_images_dir() / product_sku
    disk_snapshot = [
        (_classify_image_type(path.name), str(path))
        for path in _list_image_files(image_dir)
    ]
    registered_images = db.list_product_images(product_sku)
    registered_snapshot = [
        (img.image_type, img.file_path)
        for img in registered_images
    ]
    if disk_snapshot != registered_snapshot:
        logger.info("Detected product image changes for %s; refreshing registration", product_sku)
        return register_images(product_sku), True
    return registered_images, False


def get_hero_image(product_sku: str) -> Path | None:
    images = db.list_product_images(product_sku)
    for img in images:
        if img.image_type == "hero":
            p = Path(img.file_path)
            if p.exists():
                return p
    return None
