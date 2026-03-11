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
    url = utm.build_utm_url("https://store.com/products/widget-pro", content, "youtube")

    assert url.startswith("https://store.com/products/widget-pro?")
    assert "utm_source=youtube" in url
    assert "utm_medium=social" in url
    assert "utm_campaign=benefit_bold_claim" in url
    assert "utm_content=c-001" in url


def test_parse_utm_params() -> None:
    content = Content(
        id="c-rt",
        product_sku="roundtrip",
        theme="urgency",
        hook_type="question",
    )
    original = utm.build_utm_url("https://example.com/products/roundtrip", content, "instagram")
    parsed = utm.parse_utm_params(original)

    assert parsed["utm_source"] == "instagram"
    assert parsed["utm_medium"] == "bio"
    assert parsed["utm_campaign"] == "urgency_question"
    assert parsed["utm_content"] == "c-rt"


def test_build_attribution_data_direct() -> None:
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
        data = utm.build_attribution_data(content, product, "youtube")

    assert "veluraesthetics.com/products/serum-x" in data["destination_url"]
    assert data["link_mode"] == "direct"
    assert "utm_source=youtube" in data["utm_url"]
    assert "utm_campaign=curiosity_question" in data["utm_url"]


def test_build_attribution_data_redirect() -> None:
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
        data = utm.build_attribution_data(content, product, "instagram")

    assert data["destination_url"] == "https://fallback.example.com/go/ig"
    assert data["link_mode"] == "redirect"
    assert data["utm_url"] == ""
    assert data["utm_source"] == "instagram"
    assert data["utm_medium"] == "bio"
