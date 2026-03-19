from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from src import db
from src.models import Product, ProductImage, ResearchSnapshot, TextInsight


def _image_motion_frame(
    role: str,
    narrative_role: str,
    frame_intent: str,
    mood: str,
    duration_seconds: float,
    *,
    style_family: str = "realistic_cinematic",
    lighting: str = "golden_window_light",
    camera_distance: str = "macro_closeup",
    image_prompt: str = "Frame",
) -> dict[str, object]:
    return {
        "role": role,
        "narrative_role": narrative_role,
        "frame_intent": frame_intent,
        "mood": mood,
        "duration_seconds": duration_seconds,
        "style_family": style_family,
        "lighting": lighting,
        "camera_distance": camera_distance,
        "image_prompt": image_prompt,
    }


def _standard_image_motion_frames() -> list[dict[str, object]]:
    return [
        _image_motion_frame(
            "hero_macro",
            "hook",
            "Open with a premium product hero that implies value.",
            "soft_curiosity",
            1.5,
            image_prompt="Frame 1",
        ),
        _image_motion_frame(
            "hero_tabletop",
            "problem",
            "Shift to a gentle contrast that acknowledges the viewer's hesitation.",
            "concern",
            1.5,
            lighting="soft_diffused_daylight",
            camera_distance="closeup",
            image_prompt="Frame 2",
        ),
        _image_motion_frame(
            "texture_detail",
            "proof",
            "Show a concrete texture detail that makes the claim feel credible.",
            "calm_confidence",
            1.5,
            lighting="clean_studio_backlight",
            camera_distance="macro_closeup",
            image_prompt="Frame 3",
        ),
        _image_motion_frame(
            "hero_macro",
            "proof",
            "Echo the proof with a second hero angle and a warmer expression.",
            "delight",
            1.5,
            lighting="golden_window_light",
            camera_distance="closeup",
            image_prompt="Frame 4",
        ),
        _image_motion_frame(
            "texture_detail",
            "cta",
            "Close with an inviting product-led CTA.",
            "invitation",
            2.0,
            lighting="clean_studio_backlight",
            camera_distance="medium_shot",
            image_prompt="Frame 5",
        ),
    ]


def test_generate_content_uses_openai_and_persists_outputs(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    captured: dict[str, object] = {}

    response_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want your skin to look fresher by morning?",
        "creative_format": "ai_video_15s",
        "cta_type": "see_product",
        "cta_text": "try me today",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "starting_image_prompt": (
            "A cinematic 3D closeup of an anthropomorphic Serum X standing on a luxury "
            "bathroom counter. The bottle has a high-quality Pixar-style face with large, "
            "expressive eyes and an articulated mouth. Soft focus background of a luxury "
            "bathroom counter. Volumetric lighting, octane render, unreal engine 5, 4k. "
            "Serum X looks radiant and relieved, with a warm golden glow and calm smile. "
            "The product has the brand \"velura\" on it in brown writing using "
            "'Cormorant Garamond', Georgia, 'Times New Roman', serif."
        ),
        "scene_1_desc": "Hook closeup as the bottle smiles softly and leans forward with slow, reassuring movement.",
        "scene_2_desc": "HARD CUT to a cleaner angle as the bottle tilts slightly and soft light reveals texture.",
        "scene_1_script": "I show up worried, hiding every flaw, afraid dull skin makes me look forgotten today.",
        "scene_2_script": "I help skin look fresh and confident every morning, so try Serum X today now.",
        "platform_captions": {
            "youtube": "Glow faster with Serum X",
            "instagram": "Meet your shortcut to brighter skin.",
            "tiktok": "POV: your skin finally looks awake",
            "x": "Serum X makes tired skin look camera-ready fast.",
        },
        "hashtags": ["skincare", "glow", "serumx"],
    }

    class FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_text=json.dumps(response_payload),
                usage=SimpleNamespace(input_tokens=120, output_tokens=80),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key
            self.responses = FakeResponses()

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(
        sku="serum-x",
        name="Serum X",
        product_url="https://example.com/products/serum-x",
    )
    db.upsert_product(product)

    content, extras = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[
            ProductImage(
                product_sku=product.sku,
                file_path="images/serum-x-hero.jpg",
                image_type="hero",
            )
        ],
        cta_type="see_product",
        proof_type="ingredient",
        script_style="conversational",
    )

    assert captured["api_key"] == "test-openai-key"
    assert captured["model"] == "gpt-5.4"
    assert captured["max_output_tokens"] == 1500
    assert captured["text"] == {"format": {"type": "json_object"}}
    assert "expert creative director and AI video prompt engineer" in captured["instructions"]
    assert "Locked creative constraints:" in captured["input"]
    assert "Allowed themes:" in captured["input"]

    assert content.theme == "benefit_spotlight"
    assert content.hook_type == "question"
    assert content.hook_text == "Want your skin to look fresher by morning?"
    assert content.creative_format == "ai_video_15s"
    assert content.cta_type == "see_product"
    assert content.cta_text == "try me today"
    assert content.proof_type == "ingredient"
    assert content.script_style == "conversational"
    assert extras["hashtags"] == response_payload["hashtags"]

    payloads = db.list_platform_payloads(content.id)
    assert len(payloads) == 1
    assert {payload.platform for payload in payloads} == {"youtube"}

    costs = db.costs_for_content(content.id)
    assert len(costs) == 1
    assert costs[0].api_provider == "openai"
    assert costs[0].tokens_or_units == 200
    # 120 input @ $2.50/1M + 80 output @ $15/1M = 0.0003 + 0.0012 = 0.0015
    assert costs[0].cost_usd is not None
    assert abs(costs[0].cost_usd - 0.0015) < 1e-6


def test_generate_content_defaults_to_gpt_5_4(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    captured: dict[str, object] = {}

    response_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want your skin to look fresher by morning?",
        "creative_format": "ai_video_15s",
        "cta_type": "see_product",
        "cta_text": "try me today",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "starting_image_prompt": "A cinematic 3D closeup of an anthropomorphic Serum X.",
        "scene_1_desc": "Hook closeup as the bottle smiles softly.",
        "scene_2_desc": "HARD CUT to a cleaner angle with texture detail.",
        "scene_1_script": "I show up worried, hiding every flaw today.",
        "scene_2_script": "I help skin look fresh and confident by morning.",
        "platform_captions": {
            "youtube": "Glow faster with Serum X",
            "instagram": "Meet your shortcut to brighter skin.",
            "tiktok": "POV: your skin finally looks awake",
            "x": "Serum X helps tired skin look camera-ready fast.",
        },
        "hashtags": ["skincare", "glow", "serumx"],
    }

    class FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_text=json.dumps(response_payload),
                usage=SimpleNamespace(input_tokens=120, output_tokens=80),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.responses = FakeResponses()

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(
        sku="serum-x",
        name="Serum X",
        product_url="https://example.com/products/serum-x",
    )
    db.upsert_product(product)

    prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[
            ProductImage(
                product_sku=product.sku,
                file_path="images/serum-x-hero.jpg",
                image_type="hero",
            )
        ],
        cta_type="see_product",
        proof_type="ingredient",
        script_style="conversational",
    )

    assert captured["model"] == "gpt-5.4"


def test_generate_content_retries_on_empty_openai_response(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    call_count = 0

    response_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want your skin to look fresher by morning?",
        "creative_format": "ai_video_15s",
        "cta_type": "see_product",
        "cta_text": "try me today",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "starting_image_prompt": "A cinematic 3D closeup of an anthropomorphic Serum X.",
        "scene_1_desc": "Hook closeup as the bottle smiles.",
        "scene_2_desc": "HARD CUT to side angle with texture detail.",
        "scene_1_script": "I show up worried and tired.",
        "scene_2_script": "I help skin look fresh and confident by morning.",
        "platform_captions": {
            "youtube": "Glow faster with Serum X",
            "instagram": "Meet your shortcut to brighter skin.",
            "tiktok": "POV: your skin finally looks awake",
            "x": "Serum X helps tired skin look camera-ready fast.",
        },
        "hashtags": ["skincare", "glow", "serumx"],
    }

    class FakeResponses:
        def create(self, **kwargs):
            nonlocal call_count
            call_count += 1
            content = "   " if call_count == 1 else json.dumps(response_payload)
            return SimpleNamespace(
                output_text=content,
                usage=SimpleNamespace(input_tokens=120, output_tokens=80),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.responses = FakeResponses()

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)
    monkeypatch.setattr(prompt_generator.time, "sleep", lambda _: None)

    product = Product(
        sku="serum-x",
        name="Serum X",
        product_url="https://example.com/products/serum-x",
    )
    db.upsert_product(product)

    content, extras = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        cta_type="see_product",
        proof_type="ingredient",
        script_style="conversational",
    )

    assert call_count == 2
    assert content.hook_text == response_payload["hook_text"]
    assert extras["hashtags"] == response_payload["hashtags"]


def test_generate_content_retries_on_invalid_structured_output(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    call_count = 0

    invalid_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want your skin to look fresher by morning?",
        "creative_format": "invalid_format",
        "cta_type": "see_product",
        "cta_text": "try me today",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "starting_image_prompt": "A cinematic 3D closeup of an anthropomorphic Serum X.",
        "scene_1_desc": "Hook closeup as the bottle smiles.",
        "scene_2_desc": "HARD CUT to side angle with texture detail.",
        "scene_1_script": "I show up worried and tired.",
        "scene_2_script": "I help skin look fresh and confident by morning.",
        "platform_captions": {
            "youtube": "Glow faster with Serum X",
            "instagram": "Meet your shortcut to brighter skin.",
            "tiktok": "POV: your skin finally looks awake",
            "x": "Serum X helps tired skin look camera-ready fast.",
        },
        "hashtags": ["skincare", "glow", "serumx"],
    }
    valid_payload = dict(invalid_payload)
    valid_payload["creative_format"] = "ai_video_15s"

    class FakeResponses:
        def create(self, **kwargs):
            nonlocal call_count
            call_count += 1
            payload = invalid_payload if call_count == 1 else valid_payload
            return SimpleNamespace(
                output_text=json.dumps(payload),
                usage=SimpleNamespace(input_tokens=120, output_tokens=80),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.responses = FakeResponses()

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)
    monkeypatch.setattr(prompt_generator.time, "sleep", lambda _: None)

    product = Product(
        sku="serum-x",
        name="Serum X",
        product_url="https://example.com/products/serum-x",
    )
    db.upsert_product(product)

    content, extras = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        cta_type="see_product",
        proof_type="ingredient",
        script_style="conversational",
    )

    assert call_count == 2
    assert content.creative_format == "ai_video_15s"
    assert extras["hashtags"] == valid_payload["hashtags"]


def test_generate_content_allows_prompt_selected_labels_without_overrides(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    captured: dict[str, object] = {}
    response_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "quick_tip",
        "hook_text": "Here is the easiest way to make your routine look more polished.",
        "creative_format": "ai_video_15s",
        "cta_type": "shop_now",
        "cta_text": "add me to your routine",
        "problem_angle": None,
        "proof_type": "none",
        "script_style": "tip_based",
        "starting_image_prompt": (
            "A cinematic 3D closeup of an anthropomorphic Brow Pomade standing on a luxury "
            "bathroom counter with a warm glow and calm, confident expression."
        ),
        "scene_1_desc": "Hook closeup as the pomade points toward the mirror with a tiny, helpful nod.",
        "scene_2_desc": "HARD CUT to a side angle as the pomade demonstrates tidy definition with soft lighting.",
        "scene_1_script": "Here is my easiest trick for cleaner brows when mornings feel rushed and messy lately.",
        "scene_2_script": "I help you shape fast with polished definition, so add me to your routine.",
        "platform_captions": {
            "youtube": "Quick brow routine upgrade",
            "instagram": "A faster brow routine starts here.",
            "tiktok": "Tiny routine shift, bigger brow payoff",
            "x": "A quick brow routine tip that makes mornings easier.",
        },
        "hashtags": ["brows", "beautyroutine", "pomade"],
    }

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(response_payload))
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=90, completion_tokens=70),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(
        sku="brow-pomade",
        name="Brow Pomade",
        product_url="https://example.com/products/brow-pomade",
    )
    db.upsert_product(product)

    content, _ = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="quick_tip",
        product_images=[],
        cta_type="shop_now",
        proof_type="none",
        script_style="tip_based",
    )

    assert "Locked creative constraints:" in captured["messages"][1]["content"]
    assert "Allowed themes:" in captured["messages"][1]["content"]
    assert "  - benefit_spotlight:" in captured["messages"][1]["content"]
    assert "Guidance:" in captured["messages"][1]["content"]
    assert "Allowed hook types:" in captured["messages"][1]["content"]
    assert content.theme == "benefit_spotlight"
    assert content.hook_type == "quick_tip"
    assert content.hook_text == response_payload["hook_text"]

    # YouTube captions must end with "Link in bio"
    payloads = db.list_platform_payloads(content.id)
    youtube_payload = next(p for p in payloads if p.platform == "youtube")
    assert youtube_payload.caption is not None
    assert youtube_payload.caption.rstrip().lower().endswith("link in bio")


def test_generate_content_rejects_invalid_metadata(
    tmp_db: Path, monkeypatch, tmp_path: Path
) -> None:
    """Phase 2: invalid creative_format or cta_type raises ValueError."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    response_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want fresher skin?",
        "creative_format": "invalid_format",
        "cta_type": "see_product",
        "cta_text": "try me",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "starting_image_prompt": "A cinematic 3D closeup of an anthropomorphic Serum X.",
        "scene_1_desc": "Hook closeup.",
        "scene_2_desc": "HARD CUT to a cleaner angle.",
        "scene_1_script": "I show up worried, hiding every flaw.",
        "scene_2_script": "I help skin look fresh and confident every morning.",
        "platform_captions": {
            "youtube": "Glow faster",
            "instagram": "Meet your shortcut.",
            "tiktok": "POV: your skin",
            "x": "Serum X makes tired skin look camera-ready.",
        },
        "hashtags": ["skincare", "glow"],
    }

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(response_payload))
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=80),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(sku="serum-x", name="Serum X", product_url="https://example.com/products/serum-x")
    db.upsert_product(product)

    with pytest.raises(ValueError, match="creative_format.*not in whitelist"):
        prompt_generator.generate_content(
            product=product,
            theme="benefit_spotlight",
            hook_type="question",
            product_images=[],
            proof_type="ingredient",
        )

    response_payload["creative_format"] = "ai_video_15s"
    response_payload["cta_type"] = "invalid_cta"

    with pytest.raises(ValueError, match="cta_type"):
        prompt_generator.generate_content(
            product=product,
            theme="benefit_spotlight",
            hook_type="question",
            product_images=[],
            proof_type="ingredient",
        )


def test_generate_content_injects_research_and_persists_snapshot_id(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Phase 3: research snapshot is injected into prompt and research_snapshot_id persisted."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    captured: dict[str, object] = {}
    response_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want fresher skin?",
        "creative_format": "ai_video_15s",
        "cta_type": "see_product",
        "cta_text": "try me",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "starting_image_prompt": "A cinematic 3D closeup of an anthropomorphic Serum X.",
        "scene_1_desc": "Hook closeup.",
        "scene_2_desc": "HARD CUT to a cleaner angle.",
        "scene_1_script": "I show up worried, hiding every flaw.",
        "scene_2_script": "I help skin look fresh and confident every morning.",
        "platform_captions": {
            "youtube": "Glow faster",
            "instagram": "Meet your shortcut.",
            "tiktok": "POV: your skin",
            "x": "Serum X makes tired skin look camera-ready.",
        },
        "hashtags": ["skincare", "glow"],
    }

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(response_payload))
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=80),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(
        sku="serum-x",
        name="Serum X",
        product_url="https://example.com/products/serum-x",
    )
    db.upsert_product(product)

    research_summary = "Customers love collagen claims; avoid fear-based hooks on this product."
    db.insert_research_snapshot(
        ResearchSnapshot(
            id="rs-inject-test",
            product_sku=product.sku,
            platform=None,
            creative_format="ai_video_15s",
            summary=research_summary,
            source_type="manual",
        )
    )

    content, _ = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        proof_type="ingredient",
    )

    user_msg = captured["messages"][1]["content"]
    assert "RESEARCH INSIGHT" in user_msg
    assert research_summary in user_msg
    assert content.research_snapshot_id == "rs-inject-test"

    fetched = db.get_content(content.id)
    assert fetched is not None
    assert fetched.research_snapshot_id == "rs-inject-test"


def test_build_user_message_includes_and_omits_text_level_insights() -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    product = Product(sku="serum-x", name="Serum X")
    insight = "Hooks that start with a real viewer problem beat generic benefit claims."

    with_text_insight = prompt_generator._build_user_message(
        product=product,
        theme="mechanism_reveal",
        hook_type="question",
        product_images=[],
        text_insights=insight,
        creative_format="ai_video_15s",
        cta_type="see_product",
        proof_type="ingredient",
        script_style="conversational",
    )
    without_text_insight = prompt_generator._build_user_message(
        product=product,
        theme="mechanism_reveal",
        hook_type="question",
        product_images=[],
        creative_format="ai_video_15s",
        cta_type="see_product",
        proof_type="ingredient",
        script_style="conversational",
    )

    assert "Theme must be: mechanism_reveal" in with_text_insight
    assert "TEXT_LEVEL_INSIGHTS" in with_text_insight
    assert insight in with_text_insight
    assert "use these for text direction only, not image or render guidance" in with_text_insight
    assert "TEXT_LEVEL_INSIGHTS" not in without_text_insight


def test_generate_content_injects_text_level_insights_from_db(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    captured_calls: list[dict[str, object]] = []
    text_insight = (
        "Hooks that frame a specific viewer tension and then resolve it with one clean claim "
        "perform better than broad product praise."
    )
    response_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want your skin to look fresher by morning?",
        "creative_format": "ai_video_15s",
        "cta_type": "see_product",
        "cta_text": "try me today",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "starting_image_prompt": (
            "A cinematic 3D closeup of an anthropomorphic Serum X standing on a luxury "
            "bathroom counter. The bottle has a high-quality Pixar-style face with large, "
            "expressive eyes and an articulated mouth. Soft focus background of a luxury "
            "bathroom counter. Volumetric lighting, octane render, unreal engine 5, 4k. "
            "The product has the brand \"velura\" on it in brown writing using "
            "'Cormorant Garamond', Georgia, 'Times New Roman', serif."
        ),
        "scene_1_desc": "Hook closeup as the bottle smiles softly and leans forward with slow, reassuring movement.",
        "scene_2_desc": "HARD CUT to a cleaner angle as the bottle tilts slightly and soft light reveals texture.",
        "scene_1_script": "I show up worried, hiding every flaw, afraid dull skin makes me look forgotten today.",
        "scene_2_script": "I help skin look fresh and confident every morning, so try Serum X today now.",
        "platform_captions": {
            "youtube": "Glow faster with Serum X",
            "instagram": "Meet your shortcut to brighter skin.",
            "tiktok": "POV: your skin finally looks awake",
            "x": "Serum X makes tired skin look camera-ready fast.",
        },
        "hashtags": ["skincare", "glow", "serumx"],
    }

    class FakeResponses:
        def create(self, **kwargs):
            captured_calls.append(kwargs)
            return SimpleNamespace(
                output_text=json.dumps(response_payload),
                usage=SimpleNamespace(input_tokens=120, output_tokens=80),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.responses = FakeResponses()

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(
        sku="serum-x",
        name="Serum X",
        product_url="https://example.com/products/serum-x",
    )
    db.upsert_product(product)
    db.insert_text_insight(
        TextInsight(
            id="text-insight-001",
            product_sku=product.sku,
            platform=None,
            creative_format="ai_video_15s",
            insight_text=text_insight,
            source_post_count=4,
        )
    )

    content, extras = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        cta_type="see_product",
        proof_type="ingredient",
        script_style="conversational",
    )

    assert len(captured_calls) == 1
    assert "TEXT_LEVEL_INSIGHTS" in captured_calls[0]["input"]
    assert text_insight in captured_calls[0]["input"]
    assert "use these for text direction only, not image or render guidance" in captured_calls[0]["input"]
    assert content.theme == "benefit_spotlight"
    assert extras["hashtags"] == response_payload["hashtags"]
    fetched = db.get_latest_text_insight(
        product_sku=product.sku,
        platform=None,
        creative_format="ai_video_15s",
    )
    assert fetched is not None
    assert fetched.id == "text-insight-001"
    assert fetched.insight_text == text_insight
    assert fetched.source_post_count == 4
    assert fetched.product_sku == product.sku
    assert fetched.platform is None
    assert fetched.creative_format == "ai_video_15s"


def test_generate_content_image_motion_15s_persists_image_plan(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """image_motion_15s returns image_plan and persists it in asset_manifest_json."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")
    captured_calls: list[dict[str, object]] = []

    image_plan_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want fresher skin?",
        "creative_format": "image_motion_15s",
        "cta_type": "see_product",
        "cta_text": "try me",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "platform_captions": {
            "youtube": "Glow faster",
            "instagram": "Meet your shortcut.",
            "tiktok": "POV: your skin",
            "x": "Serum X makes tired skin look camera-ready.",
        },
        "hashtags": ["skincare", "glow"],
        "image_plan": {
            "strategy_summary": "Hero-led sequence with texture detail",
            "total_duration_seconds": 8.0,
            "performance_rationale": "default",
            "strategy_metadata": {
                "content_goal": "engagement",
                "primary_engagement_intent": "save",
                "audience_question_cluster": "Which ingredient actually matters?",
                "audience_fear_cluster": None,
            },
            "frames": _standard_image_motion_frames(),
        },
    }
    voiceover_payload = {
        "voiceover_script": "Want fresher-looking skin? Serum X brings polished glow today. Try me.",
        "estimated_word_count": 9,
        "timing_rationale": "The line stays concise enough for a calm premium read over an eight-second sequence.",
    }

    class FakeCompletions:
        def create(self, **kwargs):
            captured_calls.append(kwargs)
            payload = image_plan_payload if len(captured_calls) == 1 else voiceover_payload
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(payload))
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=150),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
            "bandit": {"ranking_objective": "engagement_rate"},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(sku="serum-x", name="Serum X", product_url="https://example.com/products/serum-x")
    db.upsert_product(product)

    content, extras = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        creative_format="image_motion_15s",
        proof_type="ingredient",
    )

    assert content.creative_format == "image_motion_15s"
    assert content.asset_manifest_json is not None
    manifest = json.loads(content.asset_manifest_json)
    assert manifest["format"] == "image_motion_15s"
    assert "image_plan" in manifest
    plan = manifest["image_plan"]
    persisted = db.get_content(content.id)
    assert persisted is not None
    assert len(plan["frames"]) == 5
    assert plan["total_duration_seconds"] == 8.0
    assert plan["frames"][0]["role"] == "hero_macro"
    assert plan["frames"][0]["narrative_role"] == "hook"
    assert plan["frames"][2]["mood"] == "calm_confidence"
    assert plan["strategy_metadata"]["content_goal"] == "engagement"
    assert json.loads(content.strategy_metadata_json or "{}")["primary_engagement_intent"] == "save"
    assert json.loads(persisted.strategy_metadata_json or "{}")["primary_engagement_intent"] == "save"
    assert len(captured_calls) == 2
    assert "Exact clip duration seconds: 8.0" in captured_calls[1]["messages"][1]["content"]
    assert "Frame 1:" in captured_calls[1]["messages"][1]["content"]
    assert "narrative_role: hook" in captured_calls[1]["messages"][1]["content"]
    assert "frame_intent: Open with a premium product hero that implies value." in captured_calls[1]["messages"][1]["content"]
    assert "Content goal: engagement" in captured_calls[1]["messages"][1]["content"]
    assert "scene_description: Frame 1" in captured_calls[1]["messages"][1]["content"]
    assert "voice_prompt_input" in extras
    assert "voice_prompt_output" in extras
    # TTS voiceover plan must be persisted from the second LLM pass
    assert "voiceover_plan" in manifest
    vp = manifest["voiceover_plan"]
    assert vp["script_template_id"] == "llm_scene_timed"
    assert vp["voice"] == "marin"
    assert vp["voiceover_script"] == voiceover_payload["voiceover_script"]
    assert vp["voiceover_script"].lower().endswith("try me.")
    assert "calm, premium, reassuring" in vp["voice_instructions"]
    assert vp["language"] == "english"
    assert "guardrail_checks" in vp
    # Script must leave some room before clip end.
    word_count = len(vp["voiceover_script"].split())
    assert word_count <= 12


def test_generate_content_image_motion_omits_explicit_velura_branding_when_disabled(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")
    captured_calls: list[dict[str, object]] = []

    image_plan_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want fresher skin?",
        "creative_format": "image_motion_15s",
        "cta_type": "see_product",
        "cta_text": "try me",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "platform_captions": {
            "youtube": "Glow faster",
            "instagram": "Meet your shortcut.",
            "tiktok": "POV: your skin",
            "x": "Serum X makes tired skin look camera-ready.",
        },
        "hashtags": ["skincare", "glow"],
        "image_plan": {
            "strategy_summary": "Hero-led sequence with texture detail",
            "total_duration_seconds": 8.0,
            "performance_rationale": "default",
            "strategy_metadata": {
                "content_goal": "engagement",
                "primary_engagement_intent": "save",
                "audience_question_cluster": None,
                "audience_fear_cluster": None,
            },
            "frames": _standard_image_motion_frames(),
        },
    }
    voiceover_payload = {
        "voiceover_script": "Want fresher-looking skin? Serum X brings polished glow today. Try me.",
        "estimated_word_count": 9,
        "timing_rationale": "The line stays concise enough for a calm premium read over an eight-second sequence.",
    }

    class FakeCompletions:
        def create(self, **kwargs):
            captured_calls.append(kwargs)
            payload = image_plan_payload if len(captured_calls) == 1 else voiceover_payload
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=150),
            )

    fake_openai = SimpleNamespace(
        OpenAI=lambda api_key: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
            "bandit": {"ranking_objective": "engagement_rate"},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(sku="serum-x", name="Serum X", product_url="https://example.com/products/serum-x")
    db.upsert_product(product)

    content, _ = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        creative_format="image_motion_15s",
        proof_type="ingredient",
        velura_branding=False,
    )

    manifest = json.loads(content.asset_manifest_json or "{}")
    assert manifest["velura_branding"] is False
    assert "Velura" not in captured_calls[0]["messages"][0]["content"]
    assert "brand 'velura'" not in captured_calls[0]["messages"][0]["content"]
    assert 'brand "velura"' not in captured_calls[0]["messages"][0]["content"]
    assert "Velura" not in captured_calls[0]["messages"][1]["content"]
    assert "explicit brand name or wordmark" in captured_calls[0]["messages"][1]["content"]


def test_generate_content_image_motion_voice_uses_marin(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Voice is always marin."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    response_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want fresher skin?",
        "creative_format": "image_motion_15s",
        "cta_type": "see_product",
        "cta_text": "try me",
        "problem_angle": None,
        "proof_type": "none",
        "script_style": "conversational",
        "platform_captions": {"youtube": "Glow", "instagram": "Glow", "tiktok": "Glow", "x": "Glow"},
        "hashtags": ["skincare"],
        "image_plan": {
            "strategy_summary": "Hero-led sequence",
            "total_duration_seconds": 8.0,
            "performance_rationale": "default",
            "strategy_metadata": {
                "content_goal": "conversion",
                "primary_engagement_intent": "click",
                "audience_question_cluster": None,
                "audience_fear_cluster": None,
            },
            "frames": _standard_image_motion_frames(),
        },
    }
    voiceover_payload = {
        "voiceover_script": "Serum X keeps this quick sequence polished and easy to follow, then closes with a calm try me.",
        "estimated_word_count": 17,
        "timing_rationale": "The script is short enough for a clean premium read over eight seconds.",
    }

    class FakeCompletions:
        def create(self, **kwargs):
            payload = response_payload if getattr(self, "_call_count", 0) % 2 == 0 else voiceover_payload
            self._call_count = getattr(self, "_call_count", 0) + 1
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(prompt_tokens=80, completion_tokens=120),
            )

    fake_openai = SimpleNamespace(
        OpenAI=lambda api_key: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "src.config._config",
        {"openai": {"api_key": "k"}, "site_url": "x", "platforms": {"enabled": ["youtube"]}, "youtube": {"client_secrets_file": str(client_secrets)}},
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(sku="serum-x", name="Serum X", product_url="https://x.com/serum-x")
    db.upsert_product(product)

    content1, _ = prompt_generator.generate_content(product=product, theme="benefit_spotlight", hook_type="question", product_images=[], creative_format="image_motion_15s", proof_type="none")
    manifest1 = json.loads(content1.asset_manifest_json or "{}")
    voice1 = manifest1.get("voiceover_plan", {}).get("voice")

    content2, _ = prompt_generator.generate_content(product=product, theme="benefit_spotlight", hook_type="question", product_images=[], creative_format="image_motion_15s", proof_type="none")
    manifest2 = json.loads(content2.asset_manifest_json or "{}")
    voice2 = manifest2.get("voiceover_plan", {}).get("voice")

    assert voice1 == "marin"
    assert voice2 == "marin"
    assert voice1 == manifest1["voiceover_plan"]["voice"]
    assert voice2 == manifest2["voiceover_plan"]["voice"]
    assert manifest1["voiceover_plan"]["script_template_id"] == "llm_scene_timed"
    assert manifest2["voiceover_plan"]["script_template_id"] == "llm_scene_timed"


def test_build_v3_voiceover_plan_sanitizes_unicode() -> None:
    """V3 stitched voiceover_script normalizes em dash to ASCII hyphen."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    timeline = [
        {"start_seconds": 0.0, "end_seconds": 2.0, "script": "First\u2014line."},
        {"start_seconds": 2.0, "end_seconds": 4.0, "script": "Second line."},
    ]
    plan = prompt_generator._build_v3_voiceover_plan(timeline, "content-1", 4.0)
    assert plan is not None
    assert plan["voiceover_script"] == "First-line. Second line."
    assert "\u2014" not in plan["voiceover_script"]


def test_image_motion_voiceover_budget_leaves_end_buffer() -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    parsed = {
        "hook_text": "What makes this lipstick look this smooth?",
        "cta_text": "Shop now",
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "proof_type": "none",
        "script_style": "conversational",
        "image_plan": {
            "strategy_summary": "Macro texture reveal followed by hero payoff.",
            "frames": [
                {
                    "duration_seconds": 2.0,
                    "role": "hero_macro",
                    "narrative_role": "hook",
                    "frame_intent": "Make the texture feel instantly premium and attention-grabbing.",
                    "mood": "soft_curiosity",
                    "style_family": "realistic_cinematic",
                    "lighting": "soft_diffused_daylight",
                    "camera_distance": "macro_closeup",
                    "image_prompt": "Macro texture reveal.",
                },
                {
                    "duration_seconds": 2.0,
                    "role": "texture_detail",
                    "narrative_role": "cta",
                    "frame_intent": "Turn the payoff shot into a calm invitation.",
                    "mood": "invitation",
                    "style_family": "realistic_cinematic",
                    "lighting": "soft_diffused_daylight",
                    "camera_distance": "closeup",
                    "image_prompt": "Creamy satin swipe.",
                },
            ],
        },
    }

    user_message = prompt_generator._build_image_motion_voiceover_user_message(
        parsed,
        "Lux Lipstick",
        8.0,
    )
    assert "Voiceover should finish 1.0 to 1.5 seconds before clip end." in user_message
    assert "Preferred spoken duration: 6.5-7.0 seconds" in user_message
    assert "Target word count: 16" in user_message
    assert "Brand guardrails:" in user_message
    assert "Forbidden terms:" in user_message
    assert "instant" in user_message
    assert "Approved softeners when needed:" in user_message

    script = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen"
    trimmed = prompt_generator._trim_script_to_duration(script, 8.0)
    assert len(trimmed.split()) == 16


def test_generate_content_image_motion_retries_voiceover_guardrail_failures(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")
    captured_calls: list[dict[str, object]] = []

    image_plan_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want fresher skin?",
        "creative_format": "image_motion_15s",
        "cta_type": "see_product",
        "cta_text": "try me",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "platform_captions": {
            "youtube": "Glow faster",
            "instagram": "Meet your shortcut.",
            "tiktok": "POV: your skin",
            "x": "Serum X makes tired skin look camera-ready.",
        },
        "hashtags": ["skincare", "glow"],
        "image_plan": {
            "strategy_summary": "Hero-led sequence with texture detail",
            "total_duration_seconds": 8.0,
            "performance_rationale": "default",
            "strategy_metadata": {
                "content_goal": "engagement",
                "primary_engagement_intent": "follow",
                "audience_question_cluster": "How does it fit into my routine?",
                "audience_fear_cluster": None,
            },
            "frames": _standard_image_motion_frames(),
        },
    }
    voiceover_payloads = [
        {
            "voiceover_script": "Serum X brings instant glow, then closes with a calm try me.",
            "estimated_word_count": 11,
            "timing_rationale": "The line is concise enough for a calm premium read.",
        },
        {
            "voiceover_script": "Serum X keeps skin polished overnight, then ends with try me.",
            "estimated_word_count": 11,
            "timing_rationale": "The line remains short for a premium read.",
        },
        {
            "voiceover_script": "Serum X brings polished glow, then closes with a calm try me.",
            "estimated_word_count": 11,
            "timing_rationale": "The line stays concise enough for a calm premium read over an eight-second sequence.",
        },
    ]

    class FakeCompletions:
        def create(self, **kwargs):
            captured_calls.append(kwargs)
            payload = image_plan_payload if len(captured_calls) == 1 else voiceover_payloads[len(captured_calls) - 2]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=150),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
            "bandit": {"ranking_objective": "engagement_rate"},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(sku="serum-x", name="Serum X", product_url="https://example.com/products/serum-x")
    db.upsert_product(product)

    content, _ = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        creative_format="image_motion_15s",
        proof_type="ingredient",
    )

    manifest = json.loads(content.asset_manifest_json or "{}")
    voiceover_plan = manifest["voiceover_plan"]
    assert len(captured_calls) == 4
    assert "Forbidden terms:" in captured_calls[1]["messages"][1]["content"]
    assert "Retry instruction:" in captured_calls[2]["messages"][1]["content"]
    assert "instant" in captured_calls[2]["messages"][1]["content"]
    assert "overnight" in captured_calls[3]["messages"][1]["content"]
    assert "Serum X brings polished glow" in voiceover_plan["voiceover_script"]
    assert "instant" not in voiceover_plan["voiceover_script"]
    assert "overnight" not in voiceover_plan["voiceover_script"]


def test_image_motion_15s_voice_prompt_output_sanitizes_unicode(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """voice_prompt_output and persisted voiceover_script normalize em dash to ASCII hyphen."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    image_plan_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want fresher skin?",
        "creative_format": "image_motion_15s",
        "cta_type": "see_product",
        "cta_text": "try me",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "platform_captions": {"youtube": "Glow", "instagram": "Glow", "tiktok": "Glow", "x": "Glow"},
        "hashtags": ["skincare"],
        "image_plan": {
            "strategy_summary": "Hero-led sequence",
            "total_duration_seconds": 8.0,
            "performance_rationale": "default",
            "strategy_metadata": {
                "content_goal": "conversion",
                "primary_engagement_intent": "click",
                "audience_question_cluster": None,
                "audience_fear_cluster": None,
            },
            "frames": _standard_image_motion_frames(),
        },
    }
    # Raw LLM response with em dash (U+2014) in voiceover_script
    voiceover_payload = {
        "voiceover_script": "Glow\u2014polish today. Try me.",
        "estimated_word_count": 5,
        "timing_rationale": "Short premium read.",
    }

    class FakeCompletions:
        def create(self, **kwargs):
            call = getattr(self, "_call", 0)
            self._call = call + 1
            payload = image_plan_payload if call == 0 else voiceover_payload
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=150),
            )

    fake_openai = SimpleNamespace(
        OpenAI=lambda api_key: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(sku="serum-x", name="Serum X", product_url="https://example.com/serum-x")
    db.upsert_product(product)

    content, extras = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        creative_format="image_motion_15s",
        proof_type="ingredient",
    )

    assert "voice_prompt_output" in extras
    output = json.loads(extras["voice_prompt_output"])
    assert output["voiceover_script"] == "Glow-polish today. Try me."
    assert "\u2014" not in extras["voice_prompt_output"]

    manifest = json.loads(content.asset_manifest_json or "{}")
    vp = manifest["voiceover_plan"]
    assert vp["voiceover_script"] == "Glow-polish today. Try me."
    assert "\u2014" not in vp["voiceover_script"]


def test_generate_content_image_motion_raises_after_third_voiceover_guardrail_failure(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")
    captured_calls: list[dict[str, object]] = []

    image_plan_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want fresher skin?",
        "creative_format": "image_motion_15s",
        "cta_type": "see_product",
        "cta_text": "try me",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "platform_captions": {
            "youtube": "Glow faster",
            "instagram": "Meet your shortcut.",
            "tiktok": "POV: your skin",
            "x": "Serum X makes tired skin look camera-ready.",
        },
        "hashtags": ["skincare", "glow"],
        "image_plan": {
            "strategy_summary": "Hero-led sequence with texture detail",
            "total_duration_seconds": 8.0,
            "performance_rationale": "default",
            "strategy_metadata": {
                "content_goal": "conversion",
                "primary_engagement_intent": "click",
                "audience_question_cluster": None,
                "audience_fear_cluster": "Wasting money on hype",
            },
            "frames": _standard_image_motion_frames(),
        },
    }
    invalid_voiceover_payload = {
        "voiceover_script": "Serum X brings instant glow, then closes with a calm try me.",
        "estimated_word_count": 11,
        "timing_rationale": "The line is concise enough for a calm premium read.",
    }

    class FakeCompletions:
        def create(self, **kwargs):
            captured_calls.append(kwargs)
            payload = image_plan_payload if len(captured_calls) == 1 else invalid_voiceover_payload
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=150),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
            "bandit": {"ranking_objective": "engagement_rate"},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(sku="serum-x", name="Serum X", product_url="https://example.com/products/serum-x")
    db.upsert_product(product)

    with pytest.raises(ValueError, match="Voiceover script violated brand guardrails: instant"):
        prompt_generator.generate_content(
            product=product,
            theme="benefit_spotlight",
            hook_type="question",
            product_images=[],
            creative_format="image_motion_15s",
            proof_type="ingredient",
        )

    assert len(captured_calls) == 4
    assert "Retry instruction:" in captured_calls[2]["messages"][1]["content"]
    assert "Retry instruction:" in captured_calls[3]["messages"][1]["content"]


def test_validate_and_normalize_image_motion_plan_requires_new_narrative_fields(monkeypatch) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")
    monkeypatch.setattr(prompt_generator, "_has_model_reference_assets", lambda: True)

    data = {
        "image_plan": {
            "strategy_summary": "Five-frame story",
            "total_duration_seconds": 8.0,
            "performance_rationale": "default",
            "strategy_metadata": {
                "content_goal": "engagement",
                "primary_engagement_intent": "save",
                "audience_question_cluster": "Which ingredient actually matters?",
                "audience_fear_cluster": None,
            },
            "frames": _standard_image_motion_frames(),
        }
    }
    data["image_plan"]["frames"][0].pop("frame_intent")

    with pytest.raises(ValueError, match="image_plan.frames\\[0\\]\\.frame_intent is required"):
        prompt_generator._validate_and_normalize_image_motion_plan(data)


def test_validate_and_normalize_image_motion_plan_rejects_lifestyle_without_model_assets(monkeypatch) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")
    monkeypatch.setattr(prompt_generator, "_has_model_reference_assets", lambda: False)

    data = {
        "image_plan": {
            "strategy_summary": "Lifestyle-driven sequence",
            "total_duration_seconds": 8.0,
            "performance_rationale": "default",
            "strategy_metadata": {
                "content_goal": "engagement",
                "primary_engagement_intent": "follow",
                "audience_question_cluster": "How does it fit into my routine?",
                "audience_fear_cluster": None,
            },
            "frames": _standard_image_motion_frames(),
        }
    }
    data["image_plan"]["frames"][1]["role"] = "lifestyle_in_use"

    with pytest.raises(ValueError, match="requires model reference assets"):
        prompt_generator._validate_and_normalize_image_motion_plan(data)


def test_validate_and_normalize_image_motion_plan_rejects_repeated_visual_signature(monkeypatch) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")
    monkeypatch.setattr(prompt_generator, "_has_model_reference_assets", lambda: True)

    data = {
        "image_plan": {
            "strategy_summary": "Repeated-look sequence",
            "total_duration_seconds": 8.0,
            "performance_rationale": "default",
            "strategy_metadata": {
                "content_goal": "conversion",
                "primary_engagement_intent": "click",
                "audience_question_cluster": None,
                "audience_fear_cluster": "Wasting money on hype",
            },
            "frames": _standard_image_motion_frames(),
        }
    }

    data["image_plan"]["frames"][1]["lighting"] = data["image_plan"]["frames"][0]["lighting"]
    data["image_plan"]["frames"][1]["camera_distance"] = data["image_plan"]["frames"][0]["camera_distance"]

    with pytest.raises(ValueError, match="Consecutive image_motion_15s frames must vary"):
        prompt_generator._validate_and_normalize_image_motion_plan(data)


def test_generate_content_ai_video_flex_15s_persists_video_plan(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """ai_video_flex_15s returns video_plan and persists it in asset_manifest_json."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    response_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want fresher skin?",
        "creative_format": "ai_video_flex_15s",
        "cta_type": "see_product",
        "cta_text": "try me",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "starting_image_prompt": "Cinematic 3D closeup of Serum X on luxury bathroom counter.",
        "platform_captions": {
            "youtube": "Glow faster",
            "instagram": "Meet your shortcut.",
            "tiktok": "POV: your skin",
            "x": "Serum X makes tired skin look camera-ready.",
        },
        "hashtags": ["skincare", "glow"],
        "video_plan": {
            "strategy_summary": "Quick-cut hero sequence",
            "total_duration_seconds": 8.0,
            "style_family": "realistic_cinematic",
            "style_rationale": "Premium skincare positioning",
            "script_total_words": 18,
            "scenes": [
                {"duration_seconds": 2.0, "scene_description": "Hook closeup.", "script": "First line."},
                {"duration_seconds": 2.0, "scene_description": "HARD CUT to side.", "script": "Second line."},
                {"duration_seconds": 2.0, "scene_description": "HARD CUT to texture.", "script": "Third line."},
                {"duration_seconds": 2.0, "scene_description": "HARD CUT to CTA.", "script": "Fourth line."},
            ],
        },
    }

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(response_payload))
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=150),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(sku="serum-x", name="Serum X", product_url="https://example.com/products/serum-x")
    db.upsert_product(product)

    content, _ = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        creative_format="ai_video_flex_15s",
        proof_type="ingredient",
    )

    assert content.creative_format == "ai_video_flex_15s"
    assert content.asset_manifest_json is not None
    manifest = json.loads(content.asset_manifest_json)
    assert manifest["format"] == "ai_video_flex_15s"
    assert "video_plan" in manifest
    plan = manifest["video_plan"]
    assert len(plan["scenes"]) == 4
    assert plan["total_duration_seconds"] == 8.0
    assert plan["style_family"] == "realistic_cinematic"
    assert "generation_metadata" in manifest
    assert manifest["generation_metadata"]["scene_count"] == 4


def test_generate_content_ai_video_flex_15s_persists_v3_voiceover_plan(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """ai_video_flex_15s V3 persists schema_version 3 and stitched voiceover metadata."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    response_payload = {
        "theme": "benefit_spotlight",
        "hook_text": "Want fresher skin?",
        "creative_format": "ai_video_flex_15s",
        "cta_text": "try me",
        "starting_image_prompt": "Cinematic 3D closeup of Serum X on luxury bathroom counter.",
        "strategy_metadata": {
            "style_family": "anamorphic",
            "style_angle": "Premium skincare positioning",
        },
        "background_music": {
            "description": "Warm premium synth bed with soft percussion.",
            "energy_level": "medium",
        },
        "timeline": [
            {
                "start_seconds": 0.0,
                "end_seconds": 2.3,
                "scene_description": "Hook closeup with a confident product reveal.",
                "script": "First line.",
            },
            {
                "start_seconds": 2.3,
                "end_seconds": 4.6,
                "scene_description": "HARD CUT: side angle with texture detail.",
                "script": "Second line.",
            },
            {
                "start_seconds": 4.6,
                "end_seconds": 6.9,
                "scene_description": "HARD CUT: slower macro shift for proof.",
                "script": "Third line.",
            },
            {
                "start_seconds": 6.9,
                "end_seconds": 9.2,
                "scene_description": "HARD CUT: polished glow reveal.",
                "script": "Fourth line.",
            },
            {
                "start_seconds": 9.2,
                "end_seconds": 11.5,
                "scene_description": "HARD CUT: final product hold.",
                "script": "Fifth line.",
            },
            {
                "start_seconds": 11.5,
                "end_seconds": 13.8,
                "scene_description": "HARD CUT: end frame CTA.",
                "script": "Sixth line.",
            },
        ],
        "platform_captions": {
            "youtube": "Glow faster",
            "instagram": "Meet your shortcut.",
            "tiktok": "POV: your skin",
            "x": "Serum X makes tired skin look camera-ready.",
        },
        "hashtags": ["skincare", "glow"],
    }

    class FakeCompletions:
        def create(self, **kwargs):
            model = kwargs["model"]
            if model == "gpt-5.4":
                payload = response_payload
            else:
                payload = {
                    "hook_type": "bold_claim",
                    "script_style": "conversational",
                    "proof_type": "none",
                }
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=120),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(sku="serum-x", name="Serum X", product_url="https://example.com/products/serum-x")
    db.upsert_product(product)

    content, _ = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        creative_format="ai_video_flex_15s",
        proof_type="ingredient",
        video_v3=True,
    )

    assert content.creative_format == "ai_video_flex_15s"
    assert content.asset_manifest_json is not None
    manifest = json.loads(content.asset_manifest_json)
    persisted = db.get_content(content.id)
    assert persisted is not None
    persisted_manifest = json.loads(persisted.asset_manifest_json or "{}")
    assert manifest == persisted_manifest
    assert manifest["schema_version"] == 3
    assert manifest["format"] == "ai_video_flex_15s"
    assert "voiceover_plan" in manifest
    voiceover_plan = manifest["voiceover_plan"]
    assert voiceover_plan["script_template_id"] == "timeline_stitch_v3"
    assert voiceover_plan["voiceover_script"] == (
        "First line. Second line. Third line. Fourth line. Fifth line. Sixth line."
    )
    assert voiceover_plan["voice"] == "marin"
    assert "calm, premium, reassuring" in voiceover_plan["voice_instructions"]
    assert voiceover_plan["estimated_word_count"] == 12
    assert voiceover_plan["language"] == "english"
    assert voiceover_plan["speech_rate_words_per_second"] == 0.9
    assert manifest["background_music"]["energy_level"] == "medium"
    assert manifest["video_plan"]["scenes"][0]["script"] == "First line."
    assert manifest["video_plan"]["scenes"][1]["script"] == "Second line."


def test_generate_content_ai_video_flex_15s_v3_omits_voiceover_plan_when_scripts_are_empty(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """V3 flex manifest should skip voiceover_plan when all timeline scripts are blank."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    response_payload = {
        "theme": "benefit_spotlight",
        "hook_text": "Want fresher skin?",
        "creative_format": "ai_video_flex_15s",
        "cta_text": "try me",
        "starting_image_prompt": "Cinematic 3D closeup of Serum X on luxury bathroom counter.",
        "strategy_metadata": {
            "style_family": "anamorphic",
            "style_angle": "Premium skincare positioning",
        },
        "background_music": {
            "description": "Warm premium synth bed with soft percussion.",
            "energy_level": "medium",
        },
        "timeline": [
            {
                "start_seconds": 0.0,
                "end_seconds": 2.3,
                "scene_description": "Hook closeup with a confident product reveal.",
                "script": None,
            },
            {
                "start_seconds": 2.3,
                "end_seconds": 4.6,
                "scene_description": "HARD CUT: side angle with texture detail.",
                "script": "",
            },
            {
                "start_seconds": 4.6,
                "end_seconds": 6.9,
                "scene_description": "HARD CUT: slower macro shift for proof.",
                "script": "   ",
            },
            {
                "start_seconds": 6.9,
                "end_seconds": 9.2,
                "scene_description": "HARD CUT: polished glow reveal.",
                "script": None,
            },
            {
                "start_seconds": 9.2,
                "end_seconds": 11.5,
                "scene_description": "HARD CUT: final product hold.",
                "script": "",
            },
            {
                "start_seconds": 11.5,
                "end_seconds": 13.8,
                "scene_description": "HARD CUT: end frame CTA.",
                "script": None,
            },
        ],
        "platform_captions": {
            "youtube": "Glow faster",
            "instagram": "Meet your shortcut.",
            "tiktok": "POV: your skin",
            "x": "Serum X makes tired skin look camera-ready.",
        },
        "hashtags": ["skincare", "glow"],
    }

    class FakeCompletions:
        def create(self, **kwargs):
            model = kwargs["model"]
            if model == "gpt-5.4":
                payload = response_payload
            else:
                payload = {
                    "hook_type": "bold_claim",
                    "script_style": "conversational",
                    "proof_type": "none",
                }
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=120),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    fake_openai = SimpleNamespace(
        OpenAI=FakeOpenAIClient,
        APIConnectionError=Exception,
        RateLimitError=Exception,
        APIStatusError=Exception,
    )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        "src.config._config",
        {
            "openai": {"api_key": "test-openai-key", "model": "gpt-5.4"},
            "site_url": "https://example.com",
            "platforms": {"enabled": ["youtube"]},
            "youtube": {"client_secrets_file": str(client_secrets)},
        },
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: fake_openai)

    product = Product(sku="serum-x", name="Serum X", product_url="https://example.com/products/serum-x")
    db.upsert_product(product)

    content, _ = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        creative_format="ai_video_flex_15s",
        proof_type="ingredient",
        video_v3=True,
    )

    manifest = json.loads(content.asset_manifest_json or "{}")
    assert manifest["schema_version"] == 3
    assert "voiceover_plan" not in manifest
    assert manifest["video_plan"]["scenes"][0]["script"] is None
    assert manifest["video_plan"]["scenes"][1]["script"] is None
    assert manifest["video_plan"]["scenes"][2]["script"] is None


def test_generate_content_ai_video_flex_rejects_invalid_video_plan(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """ai_video_flex_15s rejects video_plan with invalid scene count or duration."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    base_payload = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want fresher skin?",
        "creative_format": "ai_video_flex_15s",
        "cta_type": "see_product",
        "cta_text": "try me",
        "problem_angle": None,
        "proof_type": "ingredient",
        "script_style": "conversational",
        "starting_image_prompt": "Product on counter.",
        "platform_captions": {"youtube": "Glow", "instagram": "Glow", "tiktok": "Glow", "x": "Glow"},
        "hashtags": ["skincare"],
    }

    def make_fake(payload):
        class FakeCompletions:
            def create(self, **kwargs):
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                    usage=SimpleNamespace(prompt_tokens=100, completion_tokens=100),
                )
        return SimpleNamespace(
            OpenAI=lambda api_key: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
            APIConnectionError=Exception,
            RateLimitError=Exception,
            APIStatusError=Exception,
        )

    client_secrets = tmp_path / "youtube_client_secrets.json"
    client_secrets.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "src.config._config",
        {"openai": {"api_key": "k"}, "site_url": "x", "platforms": {"enabled": ["youtube"]}, "youtube": {"client_secrets_file": str(client_secrets)}},
    )
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)

    product = Product(sku="serum-x", name="Serum X", product_url="https://x.com/serum-x")
    db.upsert_product(product)

    # Too few scenes (2 < 3)
    payload = {**base_payload, "video_plan": {
        "total_duration_seconds": 4,
        "style_family": "realistic",
        "style_rationale": "Test",
        "script_total_words": 8,
        "scenes": [
            {"duration_seconds": 2.0, "scene_description": "A", "script": "A"},
            {"duration_seconds": 2.0, "scene_description": "B", "script": "B"},
        ],
    }}
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: make_fake(payload))
    with pytest.raises(ValueError, match="3–7 entries"):
        prompt_generator.generate_content(product=product, theme="benefit_spotlight", hook_type="question", product_images=[], creative_format="ai_video_flex_15s", proof_type="ingredient")

    # Scene duration 4.0 out of range (1.5–3.0): clamped and total normalized
    payload = {**base_payload, "video_plan": {
        "total_duration_seconds": 10,
        "style_family": "realistic",
        "style_rationale": "Test",
        "script_total_words": 12,
        "scenes": [
            {"duration_seconds": 4.0, "scene_description": "A", "script": "A"},
            {"duration_seconds": 3.0, "scene_description": "B", "script": "B"},
            {"duration_seconds": 3.0, "scene_description": "C", "script": "C"},
        ],
    }}
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: make_fake(payload))
    content, _ = prompt_generator.generate_content(
        product=product, theme="benefit_spotlight", hook_type="question", product_images=[], creative_format="ai_video_flex_15s", proof_type="ingredient"
    )
    manifest = json.loads(content.asset_manifest_json)
    plan = manifest["video_plan"]
    assert plan["scenes"][0]["duration_seconds"] == 3.0  # 4.0 clamped to 3.0
    assert 6 <= plan["total_duration_seconds"] <= 15


def test_build_user_message_video_v2_preserves_branding_from_hero_references() -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    product = Product(sku="eye-cream", name="Eye Cream")
    product_images = [
        ProductImage(
            product_sku=product.sku,
            file_path="C:/tmp/hero-eyecream.png",
            image_type="hero",
        ),
        ProductImage(
            product_sku=product.sku,
            file_path="C:/tmp/detail-eyecream.jpeg",
            image_type="detail",
        ),
    ]

    message = prompt_generator._build_user_message(
        product=product,
        theme="benefit_spotlight",
        hook_type="visual_surprise",
        product_images=product_images,
        creative_format="ai_video_flex_15s",
        video_v2=True,
    )

    assert "preserve the real package silhouette, label layout, and visible brand wordmark" in message
    assert "Do not genericize or omit the on-pack Velura branding" in message


def test_build_user_message_includes_all_locked_constraints() -> None:
    """_build_user_message always includes locked theme, hook_type, cta_type, proof_type, script_style."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    product = Product(sku="test", name="Test Product")
    message = prompt_generator._build_user_message(
        product=product,
        theme="mechanism_reveal",
        hook_type="question",
        product_images=[],
        cta_type="shop_now",
        proof_type="ingredient",
        script_style="tip_based",
    )

    assert "Locked creative constraints:" in message
    assert "Theme must be: mechanism_reveal" in message
    assert "Hook type must be: question" in message
    assert "CTA type must be: shop_now" in message
    assert "Proof type must be: ingredient" in message
    assert "Script style must be: tip_based" in message


def test_build_user_message_omits_velura_name_when_branding_disabled() -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    product = Product(sku="test", name="Test Product")
    product_images = [
        ProductImage(
            product_sku=product.sku,
            file_path="C:/tmp/hero-test.png",
            image_type="hero",
        ),
    ]
    message = prompt_generator._build_user_message(
        product=product,
        theme="mechanism_reveal",
        hook_type="question",
        product_images=product_images,
        creative_format="image_motion_15s",
        velura_branding=False,
    )

    assert "Velura" not in message
    assert "brand 'velura'" not in message
    assert "warm-neutral palette" in message
    assert "without forcing an added wordmark" in message


def test_build_user_message_video_v3_includes_theme_guidance() -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    product = Product(sku="test", name="Test Product")
    message = prompt_generator._build_user_message(
        product=product,
        theme="mythbust",
        hook_type="question",
        product_images=[],
        video_v3=True,
    )

    assert "Theme must be: mythbust" in message
    assert "THEME GUIDANCE: Challenge a common assumption and replace it with a sharper truth." in message
    assert "What you've been told is wrong. Here's the truth." in message


def test_build_user_message_v3_cta_enabled_vs_disabled() -> None:
    """V3 prompt instructs soft CTA when enabled, omit CTA when disabled."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    product = Product(sku="test", name="Test Product")
    msg_enabled = prompt_generator._build_user_message(
        product=product, theme="mythbust", hook_type="question", product_images=[],
        video_v3=True, v3_cta_enabled=True,
    )
    msg_disabled = prompt_generator._build_user_message(
        product=product, theme="mythbust", hook_type="question", product_images=[],
        video_v3=True, v3_cta_enabled=False,
    )

    assert "Soft CTA only" in msg_enabled
    assert "Omit CTA entirely" in msg_disabled
    assert "Set cta_text to empty string" in msg_disabled


def test_validate_v3_response_shape_allows_empty_cta_when_disabled() -> None:
    """V3 validator allows cta_text absent/empty when v3_cta_enabled=False."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    data = {
        "theme": "benefit_spotlight",
        "hook_text": "Want fresher skin?",
        "creative_format": "ai_video_flex_15s",
        "cta_text": "",
        "starting_image_prompt": "Test prompt",
        "timeline": [],
        "strategy_metadata": {},
        "background_music": {"description": "calm", "energy_level": "low"},
        "platform_captions": {"youtube": "X", "instagram": "X", "tiktok": "X", "x": "X"},
        "hashtags": ["skincare"],
    }
    # Should not raise when v3_cta_enabled=False
    prompt_generator._validate_v3_response_shape(data, theme="benefit_spotlight", v3_cta_enabled=False)


def test_generate_content_v3_cta_disabled_persists_see_product_and_empty_cta(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """When _should_include_v3_cta returns False, Content has cta_type=see_product and cta_text=None."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    v3_payload_no_cta = {
        "theme": "benefit_spotlight",
        "hook_text": "Want fresher skin?",
        "creative_format": "ai_video_flex_15s",
        "cta_text": "",
        "starting_image_prompt": "Cinematic 3D closeup.",
        "strategy_metadata": {"style_family": "anamorphic", "style_angle": "Test"},
        "background_music": {"description": "calm", "energy_level": "low"},
        "timeline": [
            {"start_seconds": 0, "end_seconds": 2.2, "scene_description": "Hook.", "script": "First.", "tone": "curious"},
            {"start_seconds": 2.2, "end_seconds": 4.4, "scene_description": "HARD CUT: side.", "script": "Second.", "tone": "warm"},
            {"start_seconds": 4.4, "end_seconds": 6.6, "scene_description": "HARD CUT: proof.", "script": "Third.", "tone": "confident"},
            {"start_seconds": 6.6, "end_seconds": 8.8, "scene_description": "HARD CUT: end.", "script": "Fourth.", "tone": "inviting"},
            {"start_seconds": 8.8, "end_seconds": 11, "scene_description": "HARD CUT: final.", "script": "Fifth.", "tone": "warm"},
            {"start_seconds": 11, "end_seconds": 13.2, "scene_description": "HARD CUT: close.", "script": "Sixth.", "tone": "calm"},
        ],
        "platform_captions": {"youtube": "X", "instagram": "X", "tiktok": "X", "x": "X"},
        "hashtags": ["skincare"],
    }
    classify_payload = {"hook_type": "bold_claim", "script_style": "conversational", "proof_type": "none"}

    class FakeCompletions:
        def create(self, **kwargs):
            model = kwargs.get("model", "")
            payload = v3_payload_no_cta if "gpt-5" in model else classify_payload
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=80),
            )

    class FakeOpenAI:
        def __init__(self, api_key: str):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(
        "src.config._config",
        {"openai": {"api_key": "k", "model": "gpt-5.4"}, "site_url": "https://x.com", "platforms": {"enabled": ["youtube"]}, "youtube": {"client_secrets_file": str(tmp_path / "secrets.json")}},
    )
    (tmp_path / "secrets.json").write_text("{}")
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.setattr(prompt_generator, "_load_openai_module", lambda: SimpleNamespace(OpenAI=FakeOpenAI, APIConnectionError=Exception, RateLimitError=Exception, APIStatusError=Exception))
    monkeypatch.setattr(prompt_generator, "_should_include_v3_cta", lambda: False)

    product = Product(sku="serum-x", name="Serum X", product_url="https://x.com/serum-x")
    db.upsert_product(product)

    content, _ = prompt_generator.generate_content(
        product=product,
        theme="benefit_spotlight",
        hook_type="question",
        product_images=[],
        creative_format="ai_video_flex_15s",
        video_v3=True,
    )

    assert content.cta_type == "see_product"
    assert content.cta_text is None


def test_validate_response_shape_rejects_mismatched_locked_cta_proof_script() -> None:
    """_validate_response_shape raises when LLM returns cta_type, proof_type, or script_style that does not match locked value."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    data = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Want fresher skin?",
        "platform_captions": {"youtube": "X", "instagram": "X", "tiktok": "X", "x": "X"},
        "hashtags": ["skincare"],
    }

    with pytest.raises(ValueError, match="cta_type.*did not match locked"):
        prompt_generator._validate_response_shape(
            {**data, "cta_type": "shop_now"},
            theme="benefit_spotlight",
            hook_type="question",
            cta_type="see_product",
        )

    with pytest.raises(ValueError, match="proof_type.*did not match locked"):
        prompt_generator._validate_response_shape(
            {**data, "cta_type": "see_product", "proof_type": "testimonial"},
            theme="benefit_spotlight",
            hook_type="question",
            proof_type="ingredient",
        )

    with pytest.raises(ValueError, match="script_style.*did not match locked"):
        prompt_generator._validate_response_shape(
            {**data, "cta_type": "see_product", "proof_type": "ingredient", "script_style": "storytelling"},
            theme="benefit_spotlight",
            hook_type="question",
            cta_type="see_product",
            proof_type="ingredient",
            script_style="conversational",
        )


# ---------------------------------------------------------------------------
# Video V2: _validate_and_normalize_v2_timeline (Batch 5)
# ---------------------------------------------------------------------------


def test_validate_and_normalize_v2_timeline_valid_four_scenes() -> None:
    """Valid 4-scene timeline with timestamps [0-3], [3-7], [7-11], [11-15] returns video_plan with duration_seconds."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    data = {
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "timeline": [
            {"start_seconds": 0, "end_seconds": 3, "scene_description": "Hook closeup.", "script": "First line."},
            {"start_seconds": 3, "end_seconds": 7, "scene_description": "HARD CUT to side.", "script": "Second line."},
            {"start_seconds": 7, "end_seconds": 11, "scene_description": "HARD CUT to texture.", "script": "Third line."},
            {"start_seconds": 11, "end_seconds": 15, "scene_description": "HARD CUT to CTA.", "script": "Fourth line."},
        ],
        "strategy_metadata": {"style_family": "anamorphic", "style_angle": "Test rationale"},
    }
    prompt_generator._validate_and_normalize_v2_timeline(data)

    assert "video_plan" in data
    plan = data["video_plan"]
    assert plan["total_duration_seconds"] == 15
    assert len(plan["scenes"]) == 4
    assert plan["scenes"][0]["duration_seconds"] == 3.0
    assert plan["scenes"][1]["duration_seconds"] == 4.0
    assert plan["scenes"][2]["duration_seconds"] == 4.0
    assert plan["scenes"][3]["duration_seconds"] == 4.0
    assert plan["style_family"] == "anamorphic"
    assert plan["style_rationale"] == "Test rationale"


def test_validate_and_normalize_v2_timeline_wrong_scene_count_raises() -> None:
    """Invalid: wrong scene count (3 or 5) raises ValueError."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    base_scene = {"start_seconds": 0, "end_seconds": 3, "scene_description": "X", "script": "X"}

    # 3 scenes
    data = {"timeline": [base_scene.copy(), base_scene.copy(), base_scene.copy()]}
    with pytest.raises(ValueError, match="exactly 4 scenes"):
        prompt_generator._validate_and_normalize_v2_timeline(data)

    # 5 scenes
    data = {"timeline": [base_scene.copy() for _ in range(5)]}
    with pytest.raises(ValueError, match="exactly 4 scenes"):
        prompt_generator._validate_and_normalize_v2_timeline(data)


def test_validate_and_normalize_v2_timeline_missing_hard_cut_raises() -> None:
    """Invalid: missing HARD CUT prefix on scenes 2-4 raises ValueError."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    valid_timeline = [
        {"start_seconds": 0, "end_seconds": 3, "scene_description": "Hook closeup.", "script": "First."},
        {"start_seconds": 3, "end_seconds": 7, "scene_description": "HARD CUT to side.", "script": "Second."},
        {"start_seconds": 7, "end_seconds": 11, "scene_description": "HARD CUT to texture.", "script": "Third."},
        {"start_seconds": 11, "end_seconds": 15, "scene_description": "HARD CUT to CTA.", "script": "Fourth."},
    ]

    # Scene 2 missing HARD CUT
    data = {"timeline": valid_timeline.copy()}
    data["timeline"][1]["scene_description"] = "Just a cut to side."
    with pytest.raises(ValueError, match="HARD CUT"):
        prompt_generator._validate_and_normalize_v2_timeline(data)

    # Scene 3 missing HARD CUT
    data = {"timeline": [s.copy() for s in valid_timeline]}
    data["timeline"][2]["scene_description"] = "Another angle."
    with pytest.raises(ValueError, match="HARD CUT"):
        prompt_generator._validate_and_normalize_v2_timeline(data)

    # Scene 4 missing HARD CUT
    data = {"timeline": [s.copy() for s in valid_timeline]}
    data["timeline"][3]["scene_description"] = "Final CTA shot."
    with pytest.raises(ValueError, match="HARD CUT"):
        prompt_generator._validate_and_normalize_v2_timeline(data)


def test_validate_and_normalize_v2_timeline_malformed_timestamps_raises() -> None:
    """Invalid: malformed timestamps raises ValueError."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    valid_timeline = [
        {"start_seconds": 0, "end_seconds": 3, "scene_description": "Hook.", "script": "A"},
        {"start_seconds": 3, "end_seconds": 7, "scene_description": "HARD CUT.", "script": "B"},
        {"start_seconds": 7, "end_seconds": 11, "scene_description": "HARD CUT.", "script": "C"},
        {"start_seconds": 11, "end_seconds": 15, "scene_description": "HARD CUT.", "script": "D"},
    ]

    # Wrong start/end for scene 0
    data = {"timeline": [s.copy() for s in valid_timeline]}
    data["timeline"][0]["start_seconds"] = 1
    data["timeline"][0]["end_seconds"] = 4
    with pytest.raises(ValueError, match="start_seconds=0, end_seconds=3"):
        prompt_generator._validate_and_normalize_v2_timeline(data)

    # Wrong start/end for scene 2
    data = {"timeline": [s.copy() for s in valid_timeline]}
    data["timeline"][2]["start_seconds"] = 8
    data["timeline"][2]["end_seconds"] = 12
    with pytest.raises(ValueError, match="start_seconds=7, end_seconds=11"):
        prompt_generator._validate_and_normalize_v2_timeline(data)

    # Missing scene_description
    data = {"timeline": [s.copy() for s in valid_timeline]}
    data["timeline"][1]["scene_description"] = ""
    with pytest.raises(ValueError, match="scene_description is required"):
        prompt_generator._validate_and_normalize_v2_timeline(data)

    # Missing script
    data = {"timeline": [s.copy() for s in valid_timeline]}
    data["timeline"][3]["script"] = ""
    with pytest.raises(ValueError, match="script is required"):
        prompt_generator._validate_and_normalize_v2_timeline(data)

    # Non-dict scene
    data = {"timeline": [valid_timeline[0], "not a dict", valid_timeline[2], valid_timeline[3]]}
    with pytest.raises(ValueError, match="must be an object"):
        prompt_generator._validate_and_normalize_v2_timeline(data)
