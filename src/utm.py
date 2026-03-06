from __future__ import annotations

from urllib.parse import urlencode, urlparse, parse_qs

from src import config
from src.models import Content, Product


def build_utm_url(base_url: str, content: Content) -> str:
    params = {
        "utm_source": content.product_sku,
        "utm_medium": "reel",
        "utm_campaign": f"{content.theme}_{content.hook_type}",
        "utm_content": content.id,
    }
    separator = "&" if "?" in base_url else "?"
    return base_url + separator + urlencode(params)


def parse_utm_params(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return {
        key: values[0]
        for key, values in qs.items()
        if key.startswith("utm_")
    }


def get_product_url(product: Product) -> str:
    if product.product_url:
        return product.product_url

    site_url = config.get("site_url") or config.get("shopify.store_url", "")
    site_url = site_url.rstrip("/")
    if not site_url:
        raise RuntimeError(
            "Set product.product_url or site_url in config.yaml to build product links."
        )
    if not site_url.startswith(("http://", "https://")):
        site_url = f"https://{site_url}"
    return f"{site_url}/products/{product.sku}"


def build_full_utm_link(content: Content, product: Product) -> str:
    return build_utm_url(get_product_url(product), content)
