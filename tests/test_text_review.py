from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

from src import db
from src.models import Content, Metric, Post, Product
from src.text_review import (
    _SYSTEM_PROMPT,
    _build_analysis_prompt,
    _format_row_lines,
)


def _seed_text_review_row(
    *,
    content_id: str,
    product_sku: str,
    theme: str,
    hook_type: str,
    hook_text: str,
    platform: str,
    creative_format: str,
    published_at: str,
    pulled_at: str,
    views: int,
    likes: int,
    shares: int,
    comments: int,
    saves: int,
) -> None:
    db.insert_content(
        Content(
            id=content_id,
            product_sku=product_sku,
            theme=theme,
            hook_type=hook_type,
            hook_text=hook_text,
            creative_format=creative_format,
        )
    )
    post_id = db.insert_post(
        Post(
            content_id=content_id,
            platform=platform,
            post_id=f"{platform}-{content_id}",
            published_at=published_at,
        )
    )
    metric_id = db.insert_metric(
        Metric(
            post_id=post_id,
            platform=platform,
            views=views,
            likes=likes,
            shares=shares,
            comments=comments,
            saves=saves,
        )
    )
    with db._connect() as conn:
        conn.execute(
            "UPDATE metrics SET pulled_at=? WHERE id=?",
            (pulled_at, metric_id),
        )


def test_run_text_review_returns_none_below_min_volume(
    tmp_db: Path,
    sample_product: Product,
) -> None:
    sys.modules.pop("src.text_review", None)
    text_review = importlib.import_module("src.text_review")

    db.upsert_product(sample_product)
    _seed_text_review_row(
        content_id="text-review-low-volume",
        product_sku=sample_product.sku,
        theme="benefit_spotlight",
        hook_type="question",
        hook_text="Want fresher skin by morning?",
        platform="instagram",
        creative_format="ai_video_15s",
        published_at="2099-01-01 09:00:00",
        pulled_at="2099-01-01 10:00:00",
        views=100,
        likes=10,
        shares=2,
        comments=1,
        saves=1,
    )

    result = text_review.run_text_review(
        min_posts=2,
        product_sku=sample_product.sku,
        platform="instagram",
        creative_format="ai_video_15s",
        lookback_days=0,
    )

    assert result is None
    assert (
        db.get_latest_text_insight(
            product_sku=sample_product.sku,
            platform="instagram",
            creative_format="ai_video_15s",
        )
        is None
    )


def test_run_text_review_persists_text_insight_on_openai_success(
    tmp_db: Path,
    mock_config,
    monkeypatch,
    sample_product: Product,
) -> None:
    sys.modules.pop("src.text_review", None)
    text_review = importlib.import_module("src.text_review")

    captured_calls: list[dict[str, object]] = []
    insight_text = (
        "Hooks that name a specific viewer tension and resolve it with one clean claim "
        "outperform generic product praise."
    )

    class FakeResponses:
        def create(self, **kwargs):
            captured_calls.append(kwargs)
            return SimpleNamespace(
                output_text=insight_text,
                usage=SimpleNamespace(input_tokens=42, output_tokens=18),
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

    monkeypatch.setattr(text_review, "_load_openai_module", lambda: fake_openai)

    db.upsert_product(sample_product)
    _seed_text_review_row(
        content_id="text-review-success-1",
        product_sku=sample_product.sku,
        theme="benefit_spotlight",
        hook_type="question",
        hook_text="Want fresher skin by morning?",
        platform="instagram",
        creative_format="ai_video_15s",
        published_at="2099-01-02 09:00:00",
        pulled_at="2099-01-02 10:00:00",
        views=120,
        likes=18,
        shares=6,
        comments=4,
        saves=5,
    )
    _seed_text_review_row(
        content_id="text-review-success-2",
        product_sku=sample_product.sku,
        theme="mechanism_reveal",
        hook_type="quick_tip",
        hook_text="Here is the one texture cue people keep missing.",
        platform="instagram",
        creative_format="ai_video_15s",
        published_at="2099-01-01 09:00:00",
        pulled_at="2099-01-01 10:00:00",
        views=80,
        likes=8,
        shares=2,
        comments=1,
        saves=1,
    )

    insight = text_review.run_text_review(
        min_posts=2,
        product_sku=sample_product.sku,
        platform=None,
        creative_format="ai_video_15s",
        lookback_days=0,
    )

    assert insight is not None
    assert len(captured_calls) == 1
    assert "Scope:" in captured_calls[0]["input"]
    assert f"- product_sku: {sample_product.sku}" in captured_calls[0]["input"]
    assert "- platform: all" in captured_calls[0]["input"]
    assert "- creative_format: ai_video_15s" in captured_calls[0]["input"]
    assert "- lookback_days: 0" in captured_calls[0]["input"]
    assert "analyzed_content_rows: 2" in captured_calls[0]["input"]
    assert "Want fresher skin by morning?" in captured_calls[0]["input"]
    assert "Here is the one texture cue people keep missing." in captured_calls[0]["input"]
    assert insight.insight_text == insight_text
    assert insight.product_sku == sample_product.sku
    assert insight.platform is None
    assert insight.creative_format == "ai_video_15s"
    assert insight.source_post_count == 2
    assert insight.created_at is not None

    fetched = db.get_latest_text_insight(
        product_sku=sample_product.sku,
        platform=None,
        creative_format="ai_video_15s",
    )
    assert fetched is not None
    assert fetched.id == insight.id
    assert fetched.product_sku == sample_product.sku
    assert fetched.platform is None
    assert fetched.creative_format == "ai_video_15s"
    assert fetched.insight_text == insight_text
    assert fetched.source_post_count == 2


def test_format_row_lines_includes_eval_score() -> None:
    base = {
        "engagement_rate": 0.05,
        "total_views": 100,
        "total_engagements": 5,
        "theme": "benefit_spotlight",
        "hook_type": "question",
        "hook_text": "Test hook line",
    }
    lines = _format_row_lines([{**base, "eval_score": 4}])
    assert any("eval 4/6" in line for line in lines)

    lines_none = _format_row_lines([{**base, "eval_score": None}])
    assert any("eval 0/6" in line for line in lines_none)


def test_build_analysis_prompt_includes_eval_avg() -> None:
    rows = [
        {
            "engagement_rate": 0.1,
            "total_views": 100,
            "total_engagements": 10,
            "theme": "a",
            "hook_type": "q",
            "hook_text": "x",
            "content_id": "c1",
            "eval_score": 4,
        },
        {
            "engagement_rate": 0.2,
            "total_views": 200,
            "total_engagements": 40,
            "theme": "b",
            "hook_type": "q",
            "hook_text": "y",
            "content_id": "c2",
            "eval_score": 2,
        },
    ]
    prompt = _build_analysis_prompt(
        rows,
        product_sku=None,
        platform=None,
        creative_format=None,
        lookback_days=30,
    )
    assert "eval_score_avg: 3.0/6" in prompt


def test_build_analysis_prompt_omits_eval_when_missing() -> None:
    rows = [
        {
            "engagement_rate": 0.1,
            "total_views": 100,
            "total_engagements": 10,
            "theme": "a",
            "hook_type": "q",
            "hook_text": "x",
            "content_id": "c1",
        },
    ]
    prompt = _build_analysis_prompt(
        rows,
        product_sku=None,
        platform=None,
        creative_format=None,
        lookback_days=30,
    )
    assert "eval_score_avg" not in prompt


def test_system_prompt_mentions_eval_score() -> None:
    assert "eval_score" in _SYSTEM_PROMPT
