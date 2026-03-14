from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from src import db
from src.models import Product, ProductImage, ResearchSnapshot


def test_generate_content_uses_openai_and_persists_outputs(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    captured: dict[str, object] = {}

    response_payload = {
        "theme": "benefit",
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

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(response_payload))
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=120, completion_tokens=80),
            )

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key
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
            "openai": {"api_key": "test-openai-key", "model": "gpt-4.1-mini"},
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
        theme="benefit",
        hook_type="question",
        product_images=[
            ProductImage(
                product_sku=product.sku,
                file_path="images/serum-x-hero.jpg",
                image_type="hero",
            )
        ],
    )

    assert captured["api_key"] == "test-openai-key"
    assert captured["model"] == "gpt-4.1-mini"
    assert captured["max_completion_tokens"] == 1500
    assert "max_tokens" not in captured
    assert captured["response_format"] == {"type": "json_object"}
    assert "expert creative director and AI video prompt engineer" in captured["messages"][0]["content"]
    assert "Locked creative constraints:" in captured["messages"][1]["content"]
    assert "Allowed themes:" in captured["messages"][1]["content"]

    assert content.theme == "benefit"
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


def test_generate_content_allows_prompt_selected_labels_without_overrides(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    captured: dict[str, object] = {}
    response_payload = {
        "theme": "routine",
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
            "openai": {"api_key": "test-openai-key", "model": "gpt-4.1-mini"},
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
        theme=None,
        hook_type=None,
        product_images=[],
    )

    assert "Locked creative constraints:" in captured["messages"][1]["content"]
    assert "Allowed themes:" in captured["messages"][1]["content"]
    assert "Allowed hook types:" in captured["messages"][1]["content"]
    assert content.theme == "routine"
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
        "theme": "benefit",
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
            "openai": {"api_key": "test-openai-key", "model": "gpt-4.1-mini"},
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
            theme="benefit",
            hook_type="question",
            product_images=[],
        )

    response_payload["creative_format"] = "ai_video_15s"
    response_payload["cta_type"] = "invalid_cta"

    with pytest.raises(ValueError, match="cta_type.*not in whitelist"):
        prompt_generator.generate_content(
            product=product,
            theme="benefit",
            hook_type="question",
            product_images=[],
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
        "theme": "benefit",
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
            "openai": {"api_key": "test-openai-key", "model": "gpt-4.1-mini"},
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
        theme="benefit",
        hook_type="question",
        product_images=[],
    )

    user_msg = captured["messages"][1]["content"]
    assert "RESEARCH INSIGHT" in user_msg
    assert research_summary in user_msg
    assert content.research_snapshot_id == "rs-inject-test"

    fetched = db.get_content(content.id)
    assert fetched is not None
    assert fetched.research_snapshot_id == "rs-inject-test"


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
        "theme": "benefit",
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
            "total_duration_seconds": 9.0,
            "performance_rationale": "default",
            "frames": [
                {
                    "role": "hero_macro",
                    "duration_seconds": 1.5,
                    "style_family": "realistic_cinematic",
                    "lighting": "golden_window_light",
                    "camera_distance": "macro_closeup",
                    "image_prompt": "Close-up of Serum X bottle with golden light.",
                },
                {
                    "role": "hero_tabletop",
                    "duration_seconds": 2.0,
                    "style_family": "realistic_cinematic",
                    "lighting": "soft_diffused_daylight",
                    "camera_distance": "closeup",
                    "image_prompt": "Serum X on bathroom counter.",
                },
                {
                    "role": "texture_detail",
                    "duration_seconds": 1.5,
                    "style_family": "realistic_cinematic",
                    "lighting": "clean_studio_backlight",
                    "camera_distance": "macro_closeup",
                    "image_prompt": "Texture detail of Serum X.",
                },
            ],
        },
    }
    voiceover_payload = {
        "voiceover_script": "Want fresher-looking skin? Serum X brings a polished glow across every frame, ending with a soft confident try me today.",
        "estimated_word_count": 18,
        "timing_rationale": "The line stays concise enough for a calm premium read over a 9-second sequence.",
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
            "openai": {"api_key": "test-openai-key", "model": "gpt-4.1-mini"},
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
        theme="benefit",
        hook_type="question",
        product_images=[],
        creative_format="image_motion_15s",
    )

    assert content.creative_format == "image_motion_15s"
    assert content.asset_manifest_json is not None
    manifest = json.loads(content.asset_manifest_json)
    assert manifest["format"] == "image_motion_15s"
    assert "image_plan" in manifest
    plan = manifest["image_plan"]
    assert len(plan["frames"]) == 3
    assert plan["total_duration_seconds"] == 9.0
    assert plan["frames"][0]["role"] == "hero_macro"
    assert len(captured_calls) == 2
    assert "Exact clip duration seconds: 9.0" in captured_calls[1]["messages"][1]["content"]
    assert "Frame 1:" in captured_calls[1]["messages"][1]["content"]
    assert "scene_description: Close-up of Serum X bottle with golden light." in captured_calls[1]["messages"][1]["content"]
    assert "voice_prompt_input" in extras
    assert "voice_prompt_output" in extras
    # TTS voiceover plan must be persisted from the second LLM pass
    assert "voiceover_plan" in manifest
    vp = manifest["voiceover_plan"]
    assert vp["script_template_id"] == "llm_scene_timed"
    assert vp["voice"] == "marin"
    assert vp["voiceover_script"] != voiceover_payload["voiceover_script"]
    assert vp["voiceover_script"].endswith("try me")
    assert "calm, premium, reassuring" in vp["voice_instructions"]
    assert vp["language"] == "english"
    assert "guardrail_checks" in vp
    # Script must leave some room before clip end.
    word_count = len(vp["voiceover_script"].split())
    assert word_count <= 19


def test_generate_content_image_motion_voice_uses_marin(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Voice is always marin."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    response_payload = {
        "theme": "benefit",
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
            "total_duration_seconds": 6.0,
            "performance_rationale": "default",
            "frames": [
                {"role": "hero_macro", "duration_seconds": 2.0, "image_prompt": "F1"},
                {"role": "hero_tabletop", "duration_seconds": 2.0, "image_prompt": "F2"},
                {"role": "texture_detail", "duration_seconds": 2.0, "image_prompt": "F3"},
            ],
        },
    }
    voiceover_payload = {
        "voiceover_script": "Serum X keeps this quick sequence polished and easy to follow, then closes with a calm try me.",
        "estimated_word_count": 17,
        "timing_rationale": "The script is short enough for a clean premium read over six seconds.",
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

    content1, _ = prompt_generator.generate_content(product=product, theme="benefit", hook_type="question", product_images=[], creative_format="image_motion_15s")
    manifest1 = json.loads(content1.asset_manifest_json or "{}")
    voice1 = manifest1.get("voiceover_plan", {}).get("voice")

    content2, _ = prompt_generator.generate_content(product=product, theme="benefit", hook_type="question", product_images=[], creative_format="image_motion_15s")
    manifest2 = json.loads(content2.asset_manifest_json or "{}")
    voice2 = manifest2.get("voiceover_plan", {}).get("voice")

    assert voice1 == "marin"
    assert voice2 == "marin"
    assert voice1 == manifest1["voiceover_plan"]["voice"]
    assert voice2 == manifest2["voiceover_plan"]["voice"]
    assert manifest1["voiceover_plan"]["script_template_id"] == "llm_scene_timed"
    assert manifest2["voiceover_plan"]["script_template_id"] == "llm_scene_timed"


def test_image_motion_voiceover_budget_leaves_end_buffer() -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    parsed = {
        "hook_text": "What makes this lipstick look this smooth?",
        "cta_text": "Shop now",
        "theme": "benefit",
        "hook_type": "question",
        "proof_type": "none",
        "script_style": "conversational",
        "image_plan": {
            "strategy_summary": "Macro texture reveal followed by hero payoff.",
            "frames": [
                {
                    "duration_seconds": 2.0,
                    "role": "hero_macro",
                    "style_family": "realistic_cinematic",
                    "lighting": "soft_diffused_daylight",
                    "camera_distance": "macro_closeup",
                    "image_prompt": "Macro texture reveal.",
                },
                {
                    "duration_seconds": 2.0,
                    "role": "texture_detail",
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
    assert "Voiceover should finish 0.5 to 1.0 seconds before clip end." in user_message
    assert "Preferred spoken duration: 7.0-7.5 seconds" in user_message
    assert "Target word count: 17" in user_message
    assert "Brand guardrails:" in user_message
    assert "Forbidden terms:" in user_message
    assert "instant" in user_message
    assert "Approved softeners when needed:" in user_message

    script = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen"
    trimmed = prompt_generator._trim_script_to_duration(script, 8.0)
    assert len(trimmed.split()) == 17


def test_generate_content_image_motion_retries_voiceover_guardrail_failures(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")
    captured_calls: list[dict[str, object]] = []

    image_plan_payload = {
        "theme": "benefit",
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
            "total_duration_seconds": 9.0,
            "performance_rationale": "default",
            "frames": [
                {
                    "role": "hero_macro",
                    "duration_seconds": 1.5,
                    "style_family": "realistic_cinematic",
                    "lighting": "golden_window_light",
                    "camera_distance": "macro_closeup",
                    "image_prompt": "Close-up of Serum X bottle with golden light.",
                },
                {
                    "role": "hero_tabletop",
                    "duration_seconds": 2.0,
                    "style_family": "realistic_cinematic",
                    "lighting": "soft_diffused_daylight",
                    "camera_distance": "closeup",
                    "image_prompt": "Serum X on bathroom counter.",
                },
                {
                    "role": "texture_detail",
                    "duration_seconds": 1.5,
                    "style_family": "realistic_cinematic",
                    "lighting": "clean_studio_backlight",
                    "camera_distance": "macro_closeup",
                    "image_prompt": "Texture detail of Serum X.",
                },
            ],
        },
    }
    voiceover_payloads = [
        {
            "voiceover_script": "Serum X delivers instant glow across every frame, then closes with a soft confident try me.",
            "estimated_word_count": 16,
            "timing_rationale": "The line is concise enough for a calm premium read.",
        },
        {
            "voiceover_script": "Serum X keeps skin polished overnight, then ends with a calm confident try me.",
            "estimated_word_count": 14,
            "timing_rationale": "The line remains short for a premium read.",
        },
        {
            "voiceover_script": "Serum X brings a polished glow across every frame, then closes with a soft confident try me.",
            "estimated_word_count": 17,
            "timing_rationale": "The line stays concise enough for a calm premium read over a 9-second sequence.",
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
            "openai": {"api_key": "test-openai-key", "model": "gpt-4.1-mini"},
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
        theme="benefit",
        hook_type="question",
        product_images=[],
        creative_format="image_motion_15s",
    )

    manifest = json.loads(content.asset_manifest_json or "{}")
    voiceover_plan = manifest["voiceover_plan"]
    assert len(captured_calls) == 4
    assert "Forbidden terms:" in captured_calls[1]["messages"][1]["content"]
    assert "Retry instruction:" in captured_calls[2]["messages"][1]["content"]
    assert "instant" in captured_calls[2]["messages"][1]["content"]
    assert "overnight" in captured_calls[3]["messages"][1]["content"]
    assert voiceover_plan["voiceover_script"] == voiceover_payloads[2]["voiceover_script"]


def test_generate_content_image_motion_raises_after_third_voiceover_guardrail_failure(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")
    captured_calls: list[dict[str, object]] = []

    image_plan_payload = {
        "theme": "benefit",
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
            "total_duration_seconds": 9.0,
            "performance_rationale": "default",
            "frames": [
                {
                    "role": "hero_macro",
                    "duration_seconds": 1.5,
                    "style_family": "realistic_cinematic",
                    "lighting": "golden_window_light",
                    "camera_distance": "macro_closeup",
                    "image_prompt": "Close-up of Serum X bottle with golden light.",
                },
                {
                    "role": "hero_tabletop",
                    "duration_seconds": 2.0,
                    "style_family": "realistic_cinematic",
                    "lighting": "soft_diffused_daylight",
                    "camera_distance": "closeup",
                    "image_prompt": "Serum X on bathroom counter.",
                },
                {
                    "role": "texture_detail",
                    "duration_seconds": 1.5,
                    "style_family": "realistic_cinematic",
                    "lighting": "clean_studio_backlight",
                    "camera_distance": "macro_closeup",
                    "image_prompt": "Texture detail of Serum X.",
                },
            ],
        },
    }
    invalid_voiceover_payload = {
        "voiceover_script": "Serum X delivers instant glow across every frame, then closes with a soft confident try me.",
        "estimated_word_count": 16,
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
            "openai": {"api_key": "test-openai-key", "model": "gpt-4.1-mini"},
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
            theme="benefit",
            hook_type="question",
            product_images=[],
            creative_format="image_motion_15s",
        )

    assert len(captured_calls) == 4
    assert "Retry instruction:" in captured_calls[2]["messages"][1]["content"]
    assert "Retry instruction:" in captured_calls[3]["messages"][1]["content"]


def test_generate_content_ai_video_flex_15s_persists_video_plan(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """ai_video_flex_15s returns video_plan and persists it in asset_manifest_json."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    response_payload = {
        "theme": "benefit",
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
            "openai": {"api_key": "test-openai-key", "model": "gpt-4.1-mini"},
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
        theme="benefit",
        hook_type="question",
        product_images=[],
        creative_format="ai_video_flex_15s",
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


def test_generate_content_ai_video_flex_rejects_invalid_video_plan(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """ai_video_flex_15s rejects video_plan with invalid scene count or duration."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    base_payload = {
        "theme": "benefit",
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
        prompt_generator.generate_content(product=product, theme="benefit", hook_type="question", product_images=[], creative_format="ai_video_flex_15s")

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
        product=product, theme="benefit", hook_type="question", product_images=[], creative_format="ai_video_flex_15s"
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
        theme="benefit",
        hook_type="visual_surprise",
        product_images=product_images,
        creative_format="ai_video_flex_15s",
        video_v2=True,
    )

    assert "preserve the real package silhouette, label layout, and visible brand wordmark" in message
    assert "Do not genericize or omit the on-pack Velura branding" in message


# ---------------------------------------------------------------------------
# Video V2: _validate_and_normalize_v2_timeline (Batch 5)
# ---------------------------------------------------------------------------


def test_validate_and_normalize_v2_timeline_valid_four_scenes() -> None:
    """Valid 4-scene timeline with timestamps [0-3], [3-7], [7-11], [11-15] returns video_plan with duration_seconds."""
    sys.modules.pop("src.prompt_generator", None)
    prompt_generator = importlib.import_module("src.prompt_generator")

    data = {
        "theme": "benefit",
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
