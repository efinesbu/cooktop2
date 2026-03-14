from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.image_generator import _build_prompt, _extract_image_bytes, _first_hero_image_path
from src.models import Content, Product


def test_first_hero_image_path_prefers_hero_named_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "velura-data"
    product_dir = data_root / "product-images" / "eye-cream"
    product_dir.mkdir(parents=True)

    (product_dir / "detail-texture-eyecream.jpeg").write_bytes(b"detail")
    expected = product_dir / "hero-alt-eyecream.jpeg"
    expected.write_bytes(b"hero")
    (product_dir / "hero-eyecream.png").write_bytes(b"hero-2")
    (product_dir / "lifestyle-eyecream.jpeg").write_bytes(b"life")

    monkeypatch.setattr("src.config._config", {"data_root": str(data_root)})

    product = Product(sku="eye-cream", name="Eye Cream")

    assert _first_hero_image_path(product) == expected


def test_build_prompt_requires_preserving_reference_branding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "velura-data"
    product_dir = data_root / "product-images" / "eye-cream"
    product_dir.mkdir(parents=True)
    (product_dir / "hero-eyecream.png").write_bytes(b"hero")

    monkeypatch.setattr("src.config._config", {"data_root": str(data_root)})

    content = Content(
        id="content-1",
        product_sku="eye-cream",
        starting_image_prompt="Soft minimal premium hero shot.",
    )
    product = Product(sku="eye-cream", name="Eye Cream")

    prompt = _build_prompt(content, product)

    assert "visible brand wordmark" in prompt
    assert "Do not replace, omit, or genericize" in prompt


def test_extract_image_bytes_returns_inline_data() -> None:
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(inline_data=SimpleNamespace(data=b"png-bytes")),
                    ]
                )
            )
        ]
    )

    assert _extract_image_bytes(response) == b"png-bytes"


def test_extract_image_bytes_reports_blocked_response() -> None:
    response = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=None))],
        prompt_feedback=SimpleNamespace(
            block_reason="SAFETY",
            block_reason_message="Blocked by safety filters.",
        ),
    )

    with pytest.raises(RuntimeError, match="block_reason='SAFETY'"):
        _extract_image_bytes(response)
