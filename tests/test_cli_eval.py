from __future__ import annotations

import sys
import types

from click.testing import CliRunner

sys.modules.setdefault(
    "tweepy",
    types.SimpleNamespace(
        TooManyRequests=Exception,
        TwitterServerError=Exception,
        OAuth1UserHandler=object,
        API=object,
        Client=object,
    ),
)
sys.modules.setdefault(
    "src.shopify",
    types.SimpleNamespace(sync_products=lambda *args, **kwargs: []),
)
sys.modules.setdefault(
    "src.prompt_generator",
    types.SimpleNamespace(
        generate_content=lambda *args, **kwargs: (None, {}),
        generate_paid_variant_captions=lambda *args, **kwargs: [],
    ),
)

import cli as cli_module
from src.models import Content, ContentEval, EVAL_CRITERIA


def test_eval_content_cmd_success(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "_init", lambda: None)
    content = Content(
        id="test-123",
        product_sku="sku",
        theme="benefit_spotlight",
        hook_type="question",
        hook_text="Hook",
    )
    monkeypatch.setattr(cli_module.db, "get_content", lambda cid: content if cid == "test-123" else None)
    monkeypatch.setattr(cli_module.content_eval, "score_content", lambda c: 4)
    evals = [
        ContentEval(content_id="test-123", criterion=name, passed=True)
        for name in EVAL_CRITERIA
    ]
    monkeypatch.setattr(cli_module.db, "get_content_evals", lambda cid: evals)

    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["eval-content", "--content-id", "test-123"])

    assert result.exit_code == 0
    assert "4/6" in result.output


def test_eval_content_cmd_not_found(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "_init", lambda: None)
    monkeypatch.setattr(cli_module.db, "get_content", lambda cid: None)

    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["eval-content", "--content-id", "missing"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_eval_batch_cmd(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "_init", lambda: None)
    monkeypatch.setattr(cli_module.content_eval, "score_batch", lambda lookback_days=7: 3)

    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["eval-batch", "--lookback-days", "14"])

    assert result.exit_code == 0
    assert "3" in result.output


def test_daily_loop_cmd_runs_all_steps(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "_init", lambda: None)
    monkeypatch.setattr(cli_module, "sync_instagram_post_ids_from_sheet", lambda: None)

    def fake_config_get(key: str, default=None):
        if key == "text_review.min_posts":
            return 5
        if key == "text_review.lookback_days":
            return 30
        return default

    monkeypatch.setattr(cli_module.config, "get", fake_config_get)
    monkeypatch.setattr(cli_module.config, "enabled_platforms", lambda _kind: [])

    monkeypatch.setattr(cli_module.bandit, "update_from_metrics", lambda: 0)
    monkeypatch.setattr(cli_module.content_eval, "score_batch", lambda lookback_days=7: 0)
    monkeypatch.setattr(cli_module, "run_text_review", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "generate_briefing", lambda: object())
    monkeypatch.setattr(cli_module, "display_briefing", lambda b: None)
    monkeypatch.setattr(cli_module, "email_briefing", lambda b: None)
    monkeypatch.setattr(cli_module, "_report_bandit_weights", lambda _slug: None)

    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["daily-loop", "--lookback-days", "7"])

    assert result.exit_code == 0
    out = result.output
    assert "Step 1/4" in out
    assert "Step 2/4" in out
    assert "Step 3/4" in out
    assert "Step 4/4" in out


def test_daily_loop_prints_review_text_insight(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "_init", lambda: None)
    monkeypatch.setattr(cli_module, "sync_instagram_post_ids_from_sheet", lambda: None)

    def fake_config_get(key: str, default=None):
        if key == "text_review.min_posts":
            return 5
        if key == "text_review.lookback_days":
            return 30
        return default

    monkeypatch.setattr(cli_module.config, "get", fake_config_get)
    monkeypatch.setattr(cli_module.config, "enabled_platforms", lambda _kind: [])

    monkeypatch.setattr(cli_module.bandit, "update_from_metrics", lambda: 0)
    monkeypatch.setattr(cli_module.content_eval, "score_batch", lambda lookback_days=7: 0)
    fake_insight = types.SimpleNamespace(
        id="insight1",
        insight_text="Test insight paragraph for prompt injection.",
        source_post_count=7,
    )
    monkeypatch.setattr(cli_module, "run_text_review", lambda **kwargs: fake_insight)
    monkeypatch.setattr(cli_module, "generate_briefing", lambda: "brief")
    monkeypatch.setattr(cli_module, "display_briefing", lambda b: None)
    monkeypatch.setattr(cli_module, "email_briefing", lambda b: None)
    monkeypatch.setattr(cli_module, "_report_bandit_weights", lambda _slug: None)

    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["daily-loop", "--lookback-days", "7"])

    assert result.exit_code == 0
    assert "Test insight paragraph for prompt injection." in result.output
    assert "Created text insight insight1" in result.output
