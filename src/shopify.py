from __future__ import annotations

import logging
import re
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
    client_id = config.get("shopify.client_id", "")
    client_secret = config.get("shopify.client_secret", "")
    
    if not store_url or not client_id or not client_secret:
        raise RuntimeError(
            "shopify.store_url, shopify.client_id, and shopify.client_secret must be set in config.yaml"
        )
        
    token_url = f"{store_url}/admin/oauth/access_token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    with httpx.Client(timeout=30.0, follow_redirects=True) as temp_client:
        resp = temp_client.post(token_url, json=payload)
        resp.raise_for_status()
        token = resp.json()["access_token"]

    return httpx.Client(
        base_url=f"{store_url}/admin/api/{_API_VERSION}",
        headers={"X-Shopify-Access-Token": token},
        timeout=30.0,
        follow_redirects=True,
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


def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    if not html or not html.strip():
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = " ".join(text.split())
    return text.strip()


def _product_url_from_handle(handle: str | None) -> str | None:
    cleaned_handle = (handle or "").strip().strip("/")
    store_url = str(config.get("shopify.store_url", "")).strip().rstrip("/")
    if not cleaned_handle or not store_url:
        return None
    return f"{store_url}/products/{cleaned_handle}"


def _product_from_shopify(item: dict) -> Product:
    variants = item.get("variants") or [{}]
    first_variant = variants[0]
    handle = item.get("handle", "")
    sku = first_variant.get("sku") or handle

    image = item.get("image") or {}
    image_url = image.get("src")

    body_html = item.get("body_html") or ""
    description = _strip_html(body_html) if body_html else None

    return Product(
        sku=sku,
        name=item.get("title", ""),
        category=item.get("product_type") or None,
        price=float(first_variant["price"]) if first_variant.get("price") else None,
        description=description or None,
        product_url=_product_url_from_handle(handle),
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
                db.upsert_shopify_product(product)
                products.append(product)

            url = _parse_next_link(resp.headers.get("link"))

        logger.info("Synced %d products from Shopify", len(products))
    finally:
        client.close()

    return products
