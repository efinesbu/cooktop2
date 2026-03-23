from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import pytest

import src.image_generator as _img_gen
from src.image_generator import (
    _build_prompt,
    _extract_image_bytes,
    _first_hero_image_path,
    build_v5_starting_image_prompt,
    generate_frame_images_for_plan,
    generate_starting_image,
    generate_v5_starting_image,
)
from src import db
from src.models import Content, Product

# Content-aware hero selector; required for nolabel tests. Implementer adds this.
_hero_image_path_for_content = getattr(_img_gen, "_hero_image_path_for_content", None)


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


def test_build_prompt_omits_wordmark_instruction_when_branding_disabled(
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
        asset_manifest_json=json.dumps({"velura_branding": False}),
    )
    product = Product(sku="eye-cream", name="Eye Cream")

    prompt = _build_prompt(content, product)

    assert "visible brand wordmark" not in prompt
    assert "without forcing an added brand wordmark" in prompt


def test_first_hero_image_path_uses_product_image_dir_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "velura-data"
    default_dir = data_root / "product-images" / "92852-BLNK-PC-03-04-CR-AEC"
    default_dir.mkdir(parents=True)
    (default_dir / "hero-default.png").write_bytes(b"default")

    custom_dir = data_root / "product-images" / "eye-cream"
    custom_dir.mkdir(parents=True)
    expected = custom_dir / "hero-eyecream.png"
    expected.write_bytes(b"hero")

    monkeypatch.setattr("src.config._config", {"data_root": str(data_root)})

    product = Product(
        sku="92852-BLNK-PC-03-04-CR-AEC",
        name="Eye Cream",
        image_dir=str(custom_dir),
    )

    assert _first_hero_image_path(product) == expected


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


def test_build_v5_starting_image_prompt_updates_both_jessica_labels() -> None:
    prompt = build_v5_starting_image_prompt("pisces", "ashley")

    assert "The ONLY two changes:" in prompt
    assert "update the 'Jessica' text on the necklace pendant" in prompt
    assert "update the 'Jessica' text on the top left" in prompt
    assert "'ashley'" in prompt
    assert prompt.count("'ashley'") == 2


def test_generate_frame_images_omits_brand_refs_when_branding_disabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "velura-data"
    product_dir = data_root / "product-images" / "eye-cream"
    product_dir.mkdir(parents=True)
    hero_path = product_dir / "hero-eyecream.png"
    hero_path.write_bytes(b"hero")

    output_dir = tmp_path / "out"
    monkeypatch.setattr(
        "src.config._config",
        {
            "data_root": str(data_root),
            "gemini": {"api_key": "test-key", "model": "gemini-test", "aspect_ratio": "9:16"},
        },
    )
    monkeypatch.setattr("src.image_generator.genai.Client", lambda api_key: object())

    brand_path = tmp_path / "brand-ref.png"
    brand_path.write_bytes(b"brand")
    model_path = tmp_path / "model-ref.png"
    model_path.write_bytes(b"model")

    captured: dict[str, object] = {}

    monkeypatch.setattr("src.image_generator._brand_reference_paths", lambda: [brand_path])
    monkeypatch.setattr("src.image_generator._model_reference_paths", lambda: [model_path])
    monkeypatch.setattr("src.image_generator.db.insert_cost", lambda cost: 1)

    def fake_build_contents_multi(reference_paths, prompt):
        captured["reference_paths"] = [str(path) for path in reference_paths]
        captured["prompt"] = prompt
        return prompt

    monkeypatch.setattr("src.image_generator._build_contents_multi", fake_build_contents_multi)
    monkeypatch.setattr("src.image_generator._generate_with_retries", lambda *args, **kwargs: b"png")

    content = Content(
        id="content-1",
        product_sku="eye-cream",
        asset_manifest_json=json.dumps({"velura_branding": False}),
    )
    product = Product(sku="eye-cream", name="Eye Cream")
    plan = {
        "frames": [
            {
                "role": "lifestyle_portrait",
                "image_prompt": "Soft premium portrait.",
            }
        ]
    }

    result_paths = generate_frame_images_for_plan(content, product, plan, output_dir=output_dir)

    assert len(result_paths) == 1
    assert captured["reference_paths"] == [str(hero_path), str(model_path)]
    assert str(brand_path) not in captured["reference_paths"]
    assert "without forcing an explicit wordmark" in captured["prompt"]


# --- Nolabel reference selection (non-branded generation) ---


@pytest.mark.skipif(
    _hero_image_path_for_content is None,
    reason="Requires _hero_image_path_for_content(content, product) in image_generator",
)
def test_first_hero_image_path_prefers_nolabel_when_non_branded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When velura_branding is false, hero selection should prefer -nolabel- in filenames."""
    data_root = tmp_path / "velura-data"
    product_dir = data_root / "product-images" / "eye-cream"
    product_dir.mkdir(parents=True)

    hero_labeled = product_dir / "hero-eyecream.png"
    hero_labeled.write_bytes(b"labeled")
    hero_nolabel = product_dir / "hero-nolabel-eyecream.png"
    hero_nolabel.write_bytes(b"nolabel")

    monkeypatch.setattr("src.config._config", {"data_root": str(data_root)})

    content = Content(
        id="content-1",
        product_sku="eye-cream",
        asset_manifest_json=json.dumps({"velura_branding": False}),
    )
    product = Product(sku="eye-cream", name="Eye Cream")

    hero_path = _hero_image_path_for_content(content, product)

    assert hero_path == hero_nolabel
    assert "-nolabel-" in hero_path.stem


@pytest.mark.skipif(
    _hero_image_path_for_content is None,
    reason="Requires _hero_image_path_for_content(content, product) in image_generator",
)
def test_first_hero_image_path_ignores_nolabel_preference_when_branded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When velura_branding is true (default), hero selection should NOT prefer -nolabel-."""
    data_root = tmp_path / "velura-data"
    product_dir = data_root / "product-images" / "eye-cream"
    product_dir.mkdir(parents=True)

    hero_labeled = product_dir / "hero-eyecream.png"
    hero_labeled.write_bytes(b"labeled")
    hero_nolabel = product_dir / "hero-nolabel-eyecream.png"
    hero_nolabel.write_bytes(b"nolabel")

    monkeypatch.setattr("src.config._config", {"data_root": str(data_root)})

    content = Content(
        id="content-1",
        product_sku="eye-cream",
        asset_manifest_json=json.dumps({"velura_branding": True}),
    )
    product = Product(sku="eye-cream", name="Eye Cream")

    hero_path = _hero_image_path_for_content(content, product)

    assert hero_path == hero_labeled


@pytest.mark.skipif(
    _hero_image_path_for_content is None,
    reason="Requires _hero_image_path_for_content(content, product) in image_generator",
)
def test_hero_image_path_falls_back_to_labeled_when_no_nolabel_exists(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When non-branded but no -nolabel- hero exists, use the labeled hero."""
    data_root = tmp_path / "velura-data"
    product_dir = data_root / "product-images" / "eye-cream"
    product_dir.mkdir(parents=True)

    hero_labeled = product_dir / "hero-eyecream.png"
    hero_labeled.write_bytes(b"labeled")

    monkeypatch.setattr("src.config._config", {"data_root": str(data_root)})

    content = Content(
        id="content-1",
        product_sku="eye-cream",
        asset_manifest_json=json.dumps({"velura_branding": False}),
    )
    product = Product(sku="eye-cream", name="Eye Cream")

    hero_path = _hero_image_path_for_content(content, product)

    assert hero_path == hero_labeled


def test_generate_starting_image_uses_nolabel_hero_when_non_branded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Starting-image grounding should prefer -nolabel- hero when non-branded."""
    data_root = tmp_path / "velura-data"
    product_dir = data_root / "product-images" / "eye-cream"
    product_dir.mkdir(parents=True)

    hero_labeled = product_dir / "hero-eyecream.png"
    hero_labeled.write_bytes(b"labeled")
    hero_nolabel = product_dir / "hero-nolabel-eyecream.png"
    hero_nolabel.write_bytes(b"nolabel")

    monkeypatch.setattr(
        "src.config._config",
        {
            "data_root": str(data_root),
            "gemini": {"api_key": "test-key", "model": "gemini-test", "aspect_ratio": "9:16"},
        },
    )
    monkeypatch.setattr("src.image_generator.genai.Client", lambda api_key: object())
    monkeypatch.setattr("src.image_generator.db.insert_cost", lambda cost: 1)

    captured: dict[str, object] = {}

    def fake_build_contents(prompt: str, reference_image_path: Path | None):
        captured["reference_image_path"] = reference_image_path
        return prompt

    monkeypatch.setattr("src.image_generator._build_contents", fake_build_contents)
    monkeypatch.setattr("src.image_generator._generate_with_retries", lambda *args, **kwargs: b"png")

    content = Content(
        id="content-1",
        product_sku="eye-cream",
        starting_image_prompt="Soft minimal hero shot.",
        asset_manifest_json=json.dumps({"velura_branding": False}),
    )
    product = Product(sku="eye-cream", name="Eye Cream")

    generate_starting_image(content, product)

    assert captured["reference_image_path"] == hero_nolabel
    assert "-nolabel-" in str(captured["reference_image_path"])


@pytest.mark.parametrize("frame_role", ["hero_macro", "hero_tabletop", "texture_detail"])
def test_generate_frame_images_adds_nolabel_detail_for_detail_style_frames(
    monkeypatch,
    tmp_path: Path,
    frame_role: str,
) -> None:
    """Detail-style frames (hero_macro, hero_tabletop, texture_detail) get nolabel detail refs when non-branded."""
    data_root = tmp_path / "velura-data"
    product_dir = data_root / "product-images" / "eye-cream"
    product_dir.mkdir(parents=True)

    hero_nolabel = product_dir / "hero-nolabel-eyecream.png"
    hero_nolabel.write_bytes(b"hero")
    detail_nolabel = product_dir / "detail-nolabel-texture.jpeg"
    detail_nolabel.write_bytes(b"detail")

    output_dir = tmp_path / "out"
    monkeypatch.setattr(
        "src.config._config",
        {
            "data_root": str(data_root),
            "gemini": {"api_key": "test-key", "model": "gemini-test", "aspect_ratio": "9:16"},
        },
    )
    monkeypatch.setattr("src.image_generator.genai.Client", lambda api_key: object())
    monkeypatch.setattr("src.image_generator._brand_reference_paths", lambda: [])
    monkeypatch.setattr("src.image_generator._model_reference_paths", lambda: [])
    monkeypatch.setattr("src.image_generator.db.insert_cost", lambda cost: 1)

    captured: dict[str, list[list[str]]] = {"reference_paths_per_frame": []}

    def fake_build_contents_multi(reference_paths, prompt):
        captured["reference_paths_per_frame"].append([str(p) for p in reference_paths])
        return prompt

    monkeypatch.setattr("src.image_generator._build_contents_multi", fake_build_contents_multi)
    monkeypatch.setattr("src.image_generator._generate_with_retries", lambda *args, **kwargs: b"png")

    content = Content(
        id="content-1",
        product_sku="eye-cream",
        asset_manifest_json=json.dumps({"velura_branding": False}),
    )
    product = Product(sku="eye-cream", name="Eye Cream")
    plan = {"frames": [{"role": frame_role, "image_prompt": "Product close-up."}]}

    generate_frame_images_for_plan(content, product, plan, output_dir=output_dir)

    refs = captured["reference_paths_per_frame"][0]
    assert str(hero_nolabel) in refs
    assert str(detail_nolabel) in refs


def test_generate_frame_images_omits_nolabel_detail_for_lifestyle_frame_when_non_branded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Lifestyle frames should not get nolabel detail refs; only hero (nolabel when non-branded)."""
    data_root = tmp_path / "velura-data"
    product_dir = data_root / "product-images" / "eye-cream"
    product_dir.mkdir(parents=True)

    hero_nolabel = product_dir / "hero-nolabel-eyecream.png"
    hero_nolabel.write_bytes(b"hero")
    detail_nolabel = product_dir / "detail-nolabel-texture.jpeg"
    detail_nolabel.write_bytes(b"detail")

    model_path = tmp_path / "model.png"
    model_path.write_bytes(b"model")

    output_dir = tmp_path / "out"
    monkeypatch.setattr(
        "src.config._config",
        {
            "data_root": str(data_root),
            "gemini": {"api_key": "test-key", "model": "gemini-test", "aspect_ratio": "9:16"},
        },
    )
    monkeypatch.setattr("src.image_generator.genai.Client", lambda api_key: object())
    monkeypatch.setattr("src.image_generator._brand_reference_paths", lambda: [])
    monkeypatch.setattr("src.image_generator._model_reference_paths", lambda: [model_path])
    monkeypatch.setattr("src.image_generator.db.insert_cost", lambda cost: 1)

    captured: dict[str, list[list[str]]] = {"reference_paths_per_frame": []}

    def fake_build_contents_multi(reference_paths, prompt):
        captured["reference_paths_per_frame"].append([str(p) for p in reference_paths])
        return prompt

    monkeypatch.setattr("src.image_generator._build_contents_multi", fake_build_contents_multi)
    monkeypatch.setattr("src.image_generator._generate_with_retries", lambda *args, **kwargs: b"png")

    content = Content(
        id="content-1",
        product_sku="eye-cream",
        asset_manifest_json=json.dumps({"velura_branding": False}),
    )
    product = Product(sku="eye-cream", name="Eye Cream")
    plan = {
        "frames": [
            {"role": "lifestyle_portrait", "image_prompt": "Person with product."},
        ]
    }

    generate_frame_images_for_plan(content, product, plan, output_dir=output_dir)

    refs = captured["reference_paths_per_frame"][0]
    assert str(hero_nolabel) in refs
    assert str(detail_nolabel) not in refs


def test_generate_frame_images_branded_does_not_add_nolabel_detail_refs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When branded, texture_detail frames should NOT get nolabel detail refs; branded behavior unchanged."""
    data_root = tmp_path / "velura-data"
    product_dir = data_root / "product-images" / "eye-cream"
    product_dir.mkdir(parents=True)

    hero_labeled = product_dir / "hero-eyecream.png"
    hero_labeled.write_bytes(b"hero")
    detail_nolabel = product_dir / "detail-nolabel-texture.jpeg"
    detail_nolabel.write_bytes(b"detail")

    output_dir = tmp_path / "out"
    monkeypatch.setattr(
        "src.config._config",
        {
            "data_root": str(data_root),
            "gemini": {"api_key": "test-key", "model": "gemini-test", "aspect_ratio": "9:16"},
        },
    )
    monkeypatch.setattr("src.image_generator.genai.Client", lambda api_key: object())
    monkeypatch.setattr("src.image_generator._brand_reference_paths", lambda: [])
    monkeypatch.setattr("src.image_generator._model_reference_paths", lambda: [])
    monkeypatch.setattr("src.image_generator.db.insert_cost", lambda cost: 1)

    captured: dict[str, list[list[str]]] = {"reference_paths_per_frame": []}

    def fake_build_contents_multi(reference_paths, prompt):
        captured["reference_paths_per_frame"].append([str(p) for p in reference_paths])
        return prompt

    monkeypatch.setattr("src.image_generator._build_contents_multi", fake_build_contents_multi)
    monkeypatch.setattr("src.image_generator._generate_with_retries", lambda *args, **kwargs: b"png")

    content = Content(
        id="content-1",
        product_sku="eye-cream",
        asset_manifest_json=json.dumps({"velura_branding": True}),
    )
    product = Product(sku="eye-cream", name="Eye Cream")
    plan = {
        "frames": [
            {"role": "texture_detail", "image_prompt": "Close-up texture shot."},
        ]
    }

    generate_frame_images_for_plan(content, product, plan, output_dir=output_dir)

    refs = captured["reference_paths_per_frame"][0]
    assert str(hero_labeled) in refs
    assert str(detail_nolabel) not in refs


def test_generate_v5_starting_image_uses_gemini_model_and_horoscope_reference(
    tmp_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """V5 path loads horoscopes/{sign}.png (or .jpeg/.jpg/.webp) and uses gemini.model like image_motion."""
    horo_dir = tmp_path / "horoscopes"
    horo_dir.mkdir()
    ref = horo_dir / "aries.png"
    ref.write_bytes(b"fake-png")

    captured: dict[str, object] = {}

    class FakeModels:
        def generate_content(self, model: str, contents: object, config: object | None = None):
            captured["model"] = model
            captured["contents"] = contents
            return SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b"png-bytes"))],
                        ),
                    ),
                ],
            )

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key
            self.models = FakeModels()

    videos = tmp_path / "velura-data" / "videos"
    monkeypatch.setattr("src.image_generator.genai.Client", FakeClient)
    monkeypatch.setattr("src.image_generator.config.horoscopes_dir", lambda: horo_dir)

    def fake_get(key: str, default=None):
        vals = {
            "gemini.api_key": "gemini-key",
            "gemini.model": "gemini-image-model",
            "gemini.aspect_ratio": "9:16",
        }
        return vals.get(key, default)

    monkeypatch.setattr("src.image_generator.config.get", fake_get)
    monkeypatch.setattr("src.image_generator.config.videos_dir", lambda: videos)

    content = Content(id="v5c1", product_sku="sku-a", theme="aries", hook_type="jessica")
    db.upsert_product(Product(sku="sku-a", name="Test"))
    db.insert_content(content)
    out = generate_v5_starting_image(content, "aries", "jessica")

    assert captured["model"] == "gemini-image-model"
    assert captured["api_key"] == "gemini-key"
    # Reference image is passed as first part (bytes) to Gemini
    assert isinstance(captured["contents"], list)
    prompt_text = captured["contents"][1]
    assert isinstance(prompt_text, str)
    assert "aries horoscope creature" in prompt_text.lower()
    assert "chibi" in prompt_text.lower()
    assert "reproduce this image exactly" in prompt_text.lower()
    assert out == videos / "sku-a" / "v5c1_start.png"
    assert out.read_bytes() == b"png-bytes"


def test_generate_v5_starting_image_missing_reference_raises_clear_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    horo_dir = tmp_path / "horoscopes"
    horo_dir.mkdir()

    monkeypatch.setattr("src.image_generator.config.horoscopes_dir", lambda: horo_dir)
    monkeypatch.setattr(
        "src.image_generator.config.get",
        lambda key, default=None: {"gemini.api_key": "k", "gemini.model": "gemini-2.0-flash"}.get(
            key, default
        ),
    )

    content = Content(id="x", product_sku="s", theme="aries", hook_type="jessica")
    with pytest.raises(FileNotFoundError, match="V5 horoscope reference image not found"):
        generate_v5_starting_image(content, "aries", "jessica")
