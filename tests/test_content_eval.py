from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src import db
from src.content_eval import (
    _build_eval_prompt,
    _parse_eval_response,
    score_batch,
    score_content,
)
from src.models import Content, ContentEval, Cost, EVAL_CRITERIA


def test_build_eval_prompt_v3() -> None:
    manifest = {
        "timeline": [
            {
                "scene_description": "Cold open on dry skin texture",
                "script": "If your face feels tight by 2pm, this is why.",
                "tone": "empathetic",
            },
            {
                "scene_description": "Product demo with water splash",
                "script": "One layer locks moisture for 12 hours.",
                "tone": "confident",
            },
        ],
        "strategy_metadata": {"expression_arc": "problem → proof → CTA"},
    }
    content = Content(
        id="eval-v3-1",
        product_sku="SKU-1",
        theme="benefit_spotlight",
        hook_type="question",
        hook_text="Why does your moisturizer vanish by lunch?",
        creative_format="ai_video_flex_15s",
        problem_angle="Midday tightness for desk workers",
        asset_manifest_json=json.dumps(manifest),
        starting_image_prompt="Cinematic macro skin texture, soft window light",
    )
    prompt = _build_eval_prompt(content)

    assert "Format: V3 (ai_video_flex_15s)" in prompt
    assert "V3" in prompt
    assert "Why does your moisturizer vanish by lunch?" in prompt
    assert "Midday tightness for desk workers" in prompt
    assert "Cold open on dry skin texture" in prompt
    assert "If your face feels tight by 2pm, this is why." in prompt
    assert "Product demo with water splash" in prompt
    assert "One layer locks moisture for 12 hours." in prompt


def test_build_eval_prompt_image_motion() -> None:
    manifest = {
        "image_plan": {
            "frames": [
                {"frame_intent": "Pattern interrupt on routine", "image_prompt": "bathroom mirror morning"},
                {"frame_intent": "Ingredient hero shot", "image_prompt": "serum drop macro"},
            ]
        },
        "voiceover_plan": {"voiceover_script": "Stop guessing—here is the one step derms agree on."},
    }
    content = Content(
        id="eval-im-1",
        product_sku="SKU-2",
        theme="mechanism_reveal",
        hook_type="bold_claim",
        hook_text="This is not another hyaluronic post.",
        creative_format="image_motion_15s",
        problem_angle="Confusing ingredient stacks",
        asset_manifest_json=json.dumps(manifest),
    )
    prompt = _build_eval_prompt(content)

    assert "Format: Image motion (image_motion_15s)" in prompt
    assert "Pattern interrupt on routine" in prompt
    assert "Ingredient hero shot" in prompt
    assert "Stop guessing—here is the one step derms agree on." in prompt


def test_parse_eval_response_valid_json() -> None:
    raw = (
        '{"hook": true, "first_frame": false, "narrative_arc": true, '
        '"specificity": false, "caption": true, "scene_progression": true, '
        '"standalone_value": false}'
    )
    parsed = _parse_eval_response(raw)
    assert parsed == {
        "hook": True,
        "first_frame": False,
        "narrative_arc": True,
        "specificity": False,
        "caption": True,
        "scene_progression": True,
        "standalone_value": False,
    }
    assert set(parsed.keys()) == set(EVAL_CRITERIA)


def test_parse_eval_response_with_markdown_fences() -> None:
    inner = (
        '{"hook": true, "first_frame": true, "narrative_arc": false, '
        '"specificity": true, "caption": false, "scene_progression": true, '
        '"standalone_value": true}'
    )
    raw = f"```json\n{inner}\n```\n"
    parsed = _parse_eval_response(raw)
    assert parsed["hook"] is True
    assert parsed["narrative_arc"] is False
    assert parsed["caption"] is False
    assert parsed["standalone_value"] is True
    assert len(parsed) == 7


def test_parse_eval_response_missing_criteria() -> None:
    raw = '{"hook": true, "caption": false}'
    parsed = _parse_eval_response(raw)
    assert parsed["hook"] is True
    assert parsed["caption"] is False
    assert parsed["first_frame"] is False
    assert parsed["narrative_arc"] is False
    assert parsed["specificity"] is False
    assert parsed["scene_progression"] is False
    assert parsed["standalone_value"] is False


def test_score_content_mocked(
    mock_config: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_config["eval"] = {"model": "gpt-eval-test"}

    eval_json = (
        '{"hook": true, "first_frame": true, "narrative_arc": false, '
        '"specificity": false, "caption": true, "scene_progression": true, '
        '"standalone_value": true}'
    )

    class FakeCompletions:
        def create(self, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=eval_json)),
                ],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
            )

    class FakeChat:
        def __init__(self) -> None:
            self.completions = FakeCompletions()

    class FakeOpenAIClient:
        def __init__(self, api_key: str) -> None:
            self.chat = FakeChat()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAIClient)

    insert_evals_calls: list[tuple[str, list[ContentEval]]] = []
    update_score_calls: list[tuple[str, int]] = []
    insert_cost_calls: list[Cost] = []

    def fake_insert_content_evals(content_id: str, evals: list[ContentEval]) -> None:
        insert_evals_calls.append((content_id, evals))

    def fake_update_content_eval_score(content_id: str, score: int) -> None:
        update_score_calls.append((content_id, score))

    def fake_insert_cost(c: Cost) -> int:
        insert_cost_calls.append(c)
        return 1

    monkeypatch.setattr(db, "insert_content_evals", fake_insert_content_evals)
    monkeypatch.setattr(db, "update_content_eval_score", fake_update_content_eval_score)
    monkeypatch.setattr(db, "insert_cost", fake_insert_cost)

    content = Content(
        id="score-mock-1",
        product_sku="SKU",
        theme="benefit_spotlight",
        hook_type="question",
        hook_text="Test hook",
        creative_format="ai_video_15s",
    )

    score = score_content(content)

    assert score == 5
    assert len(insert_evals_calls) == 1
    cid, evals = insert_evals_calls[0]
    assert cid == "score-mock-1"
    assert len(evals) == 7
    assert [e.criterion for e in evals] == EVAL_CRITERIA
    assert [e.passed for e in evals] == [True, True, False, False, True, True, True]

    assert update_score_calls == [("score-mock-1", 5)]

    assert len(insert_cost_calls) == 1
    cost = insert_cost_calls[0]
    assert cost.content_id == "score-mock-1"
    assert cost.step == "eval"
    assert cost.api_provider == "gpt-eval-test"
    assert cost.tokens_or_units == 30


def test_score_batch_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        Content(id="a", product_sku="s", theme="t", hook_type="h", hook_text="x"),
        Content(id="b", product_sku="s", theme="t", hook_type="h", hook_text="y"),
    ]

    monkeypatch.setattr(
        "src.content_eval.db.list_unscored_content",
        lambda lookback_days=7: items,
    )
    monkeypatch.setattr("src.content_eval.score_content", lambda c: 3)

    assert score_batch(lookback_days=7) == 2


def test_score_batch_handles_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        Content(id="c1", product_sku="s", theme="t", hook_type="h", hook_text="1"),
        Content(id="c2", product_sku="s", theme="t", hook_type="h", hook_text="2"),
        Content(id="c3", product_sku="s", theme="t", hook_type="h", hook_text="3"),
    ]

    def fake_score(content: Content) -> int:
        if content.id == "c2":
            raise RuntimeError("boom")
        return 1

    monkeypatch.setattr(
        "src.content_eval.db.list_unscored_content",
        lambda lookback_days=7: items,
    )
    monkeypatch.setattr("src.content_eval.score_content", fake_score)

    assert score_batch(lookback_days=14) == 2
