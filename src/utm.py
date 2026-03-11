from __future__ import annotations

from urllib.parse import urlencode, urlparse, parse_qs

from src import config
from src.models import Content, Product


def build_utm_url(base_url: str, content: Content, platform: str) -> str:
    params = {
        "utm_source": platform,
        "utm_medium": "bio" if platform in ("instagram", "tiktok") else "social",
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


def build_attribution_data(content: Content, product: Product, platform: str) -> dict[str, str]:
    if platform in ("instagram", "tiktok"):
        redirect_path = "/go/ig" if platform == "instagram" else "/go/tt"
        site_url = config.get("site_url") or config.get("shopify.store_url", "")
        site_url = site_url.rstrip("/")
        if not site_url.startswith(("http://", "https://")) and site_url:
            site_url = f"https://{site_url}"
        
        destination_url = f"{site_url}{redirect_path}" if site_url else redirect_path

        return {
            "destination_url": destination_url,
            "utm_url": "",
            "link_mode": "redirect",
            "utm_source": platform,
            "utm_medium": "bio",
            "utm_campaign": f"{content.theme}_{content.hook_type}",
            "utm_content": content.id,
        }
    else:
        base_url = get_product_url(product)
        utm_url = build_utm_url(base_url, content, platform)
        return {
            "destination_url": base_url,
            "utm_url": utm_url,
            "link_mode": "direct",
            "utm_source": platform,
            "utm_medium": "social",
            "utm_campaign": f"{content.theme}_{content.hook_type}",
            "utm_content": content.id,
        }
