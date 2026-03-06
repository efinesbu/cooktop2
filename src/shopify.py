from __future__ import annotations

import logging
import time

import httpx

from src import config, db
from src.models import Product

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.5
_API_VERSION = "2024-01"
_PRODUCTS_LIMIT = 250


def _build_client() -> httpx.Client:
    store_url = config.get("shopify.store_url", "").rstrip("/")
    token = config.get("shopify.admin_api_token", "")
    if not store_url or not token:
        raise RuntimeError(
            "shopify.store_url and shopify.admin_api_token must be set in config.yaml"
        )
    return httpx.Client(
        base_url=f"{store_url}/admin/api/{_API_VERSION}",
        headers={"X-Shopify-Access-Token": token},
        timeout=30.0,
    )


def _request_with_retry(client: httpx.Client, url: str) -> httpx.Response:
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if attempt == _MAX_RETRIES:
                raise
            wait = _BACKOFF_BASE ** attempt
            logger.warning(
                "Shopify request failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt, _MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            url = part.split(";")[0].strip().strip("<>")
            return url
    return None


def _product_from_shopify(item: dict) -> Product:
    variants = item.get("variants") or [{}]
    first_variant = variants[0]
    sku = first_variant.get("sku") or item.get("handle", "")

    image = item.get("image") or {}
    image_url = image.get("src")

    return Product(
        sku=sku,
        name=item.get("title", ""),
        category=item.get("product_type") or None,
        price=float(first_variant["price"]) if first_variant.get("price") else None,
        shopify_image_url=image_url,
    )


def sync_products() -> list[Product]:
    client = _build_client()
    products: list[Product] = []
    url = f"/products.json?limit={_PRODUCTS_LIMIT}"

    try:
        while url:
            resp = _request_with_retry(client, url)
            data = resp.json()

            for item in data.get("products", []):
                product = _product_from_shopify(item)
                if not product.sku:
                    logger.warning("Skipping product '%s' — no SKU or handle", product.name)
                    continue
                db.upsert_product(product)
                products.append(product)

            url = _parse_next_link(resp.headers.get("link"))

        logger.info("Synced %d products from Shopify", len(products))
    finally:
        client.close()

    return products
