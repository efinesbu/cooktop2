from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from src import db
from src.models import Product, ProductImage


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
    assert extras["hashtags"] == response_payload["hashtags"]

    payloads = db.list_platform_payloads(content.id)
    assert len(payloads) == 1
    assert {payload.platform for payload in payloads} == {"youtube"}

    costs = db.costs_for_content(content.id)
    assert len(costs) == 1
    assert costs[0].api_provider == "openai"
    assert costs[0].tokens_or_units == 200


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

    assert "Creative selection task:" in captured["messages"][1]["content"]
    assert "Allowed themes:" in captured["messages"][1]["content"]
    assert "Allowed hook types:" in captured["messages"][1]["content"]
    assert content.theme == "routine"
    assert content.hook_type == "quick_tip"
    assert content.hook_text == response_payload["hook_text"]
