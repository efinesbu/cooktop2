from __future__ import annotations

from unittest.mock import patch

from src import utm
from src.models import Content, Product


def test_build_utm_url() -> None:
    content = Content(
        id="c-001",
        product_sku="widget-pro",
        theme="benefit",
        hook_type="bold_claim",
    )
    url = utm.build_utm_url("https://store.com/products/widget-pro", content)

    assert url.startswith("https://store.com/products/widget-pro?")
    assert "utm_source=widget-pro" in url
    assert "utm_medium=reel" in url
    assert "utm_campaign=benefit_bold_claim" in url
    assert "utm_content=c-001" in url


def test_parse_utm_params() -> None:
    content = Content(
        id="c-rt",
        product_sku="roundtrip",
        theme="urgency",
        hook_type="question",
    )
    original = utm.build_utm_url("https://example.com/products/roundtrip", content)
    parsed = utm.parse_utm_params(original)

    assert parsed["utm_source"] == "roundtrip"
    assert parsed["utm_medium"] == "reel"
    assert parsed["utm_campaign"] == "urgency_question"
    assert parsed["utm_content"] == "c-rt"


def test_build_full_utm_link() -> None:
    content = Content(
        id="c-002",
        product_sku="serum-x",
        theme="curiosity",
        hook_type="question",
    )
    product = Product(sku="serum-x", name="Serum X")

    with patch(
        "src.config._config",
        {"site_url": "https://veluraesthetics.com"},
    ):
        link = utm.build_full_utm_link(content, product)

    assert "veluraesthetics.com/products/serum-x" in link
    assert "utm_source=serum-x" in link
    assert "utm_campaign=curiosity_question" in link
    assert "utm_content=c-002" in link


def test_build_full_utm_link_prefers_product_url() -> None:
    content = Content(
        id="c-003",
        product_sku="eye-cream",
        theme="benefit",
        hook_type="bold_claim",
    )
    product = Product(
        sku="eye-cream",
        name="Eye Cream",
        product_url="https://veluraesthetics.com/products/eye-cream-special",
    )

    with patch("src.config._config", {"site_url": "https://fallback.example.com"}):
        link = utm.build_full_utm_link(content, product)

    assert "veluraesthetics.com/products/eye-cream-special" in link
    assert "utm_source=eye-cream" in link
