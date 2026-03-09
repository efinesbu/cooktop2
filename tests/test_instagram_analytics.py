from __future__ import annotations

import sys
import types

from click.testing import CliRunner

from src.analytics.instagram import InstagramAnalyticsPuller
from src.instagram_sheet_sync import (
    InstagramSheetSyncDiagnostic,
    InstagramSheetSyncResult,
    InstagramSheetSyncRowResult,
)
from src.models import Post

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
sys.modules.setdefault("src.analytics", types.SimpleNamespace(PULLERS={}))
sys.modules.setdefault(
    "src.image_generator",
    types.SimpleNamespace(generate_starting_image=lambda *args, **kwargs: None),
)
sys.modules.setdefault(
    "src.shopify",
    types.SimpleNamespace(sync_products=lambda *args, **kwargs: []),
)
sys.modules.setdefault(
    "src.prompt_generator",
    types.SimpleNamespace(generate_content=lambda *args, **kwargs: (None, {})),
)
sys.modules.setdefault(
    "src.posters.youtube",
    types.SimpleNamespace(YouTubePoster=object),
)
sys.modules.setdefault(
    "src.posters.instagram",
    types.SimpleNamespace(InstagramPoster=object),
)
sys.modules.setdefault(
    "src.posters.tiktok",
    types.SimpleNamespace(TikTokPoster=object),
)
sys.modules.setdefault(
    "src.posters.x",
    types.SimpleNamespace(XPoster=object),
)

import cli as cli_module


def test_instagram_analytics_skips_make_handoff_ids(monkeypatch) -> None:
    monkeypatch.setattr("src.config.get", lambda key, default=None: "token" if key == "instagram.access_token" else default)

    def fail_get(*args, **kwargs):
        raise AssertionError("Instagram API should not be called for Make handoff ids")

    monkeypatch.setattr("src.analytics.instagram.httpx.get", fail_get)

    puller = InstagramAnalyticsPuller()
    metric = puller.fetch_metrics(
        Post(id=1, platform="instagram", post_id="make:videos/clip-123.mp4")
    )

    assert metric is None


def test_instagram_analytics_returns_metric_on_success(monkeypatch) -> None:
    monkeypatch.setattr("src.config.get", lambda key, default=None: "token" if key == "instagram.access_token" else default)
    insight_requests: list[str] = []

    def mock_get(url, **kwargs):
        from unittest.mock import Mock
        resp = Mock()
        resp.status_code = 200
        if "/insights" in url:
            insight_requests.append(kwargs["params"]["metric"])
            resp.json.return_value = {
                "data": [
                    {"name": "views", "values": [{"value": 150}]},
                    {"name": "reach", "values": [{"value": 120}]},
                    {"name": "saved", "values": [{"value": 5}]},
                    {"name": "shares", "values": [{"value": 3}]},
                ]
            }
        else:
            resp.json.return_value = {"like_count": 12, "comments_count": 2}
        resp.raise_for_status = lambda: None
        return resp

    monkeypatch.setattr("src.analytics.instagram.httpx.get", mock_get)

    puller = InstagramAnalyticsPuller()
    metric = puller.fetch_metrics(
        Post(id=1, platform="instagram", post_id="18211139935321169")
    )

    assert metric is not None
    assert metric.platform == "instagram"
    assert metric.views == 150
    assert metric.likes == 12
    assert metric.comments == 2
    assert metric.shares == 3
    assert metric.saves == 5
    assert insight_requests == ["views,reach,saved,shares"]


def test_pull_analytics_reports_saved_metric_rows(monkeypatch) -> None:
    class ZeroPuller:
        def pull(self) -> int:
            return 0

    class TwoRowPuller:
        def pull(self) -> int:
            return 2

    monkeypatch.setattr(cli_module, "_init", lambda: None)
    monkeypatch.setattr(cli_module.config, "enabled_platforms", lambda purpose="posting": ["instagram", "youtube"])
    monkeypatch.setattr(cli_module, "PULLERS", {"instagram": ZeroPuller, "youtube": TwoRowPuller})
    monkeypatch.setattr(cli_module.bandit, "update_from_metrics", lambda: 3)

    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["pull-analytics"])

    assert result.exit_code == 0
    assert "instagram: no metric rows saved" in result.output
    assert "youtube (2 metrics)" in result.output
    assert "1 platforms returned metrics" in result.output
    assert "2 metric rows saved" in result.output
    assert "3 bandit arms updated" in result.output
    assert "✓" not in result.output
    assert "✗" not in result.output


def test_pull_analytics_syncs_sheet_before_platform_pulls(monkeypatch) -> None:
    class OneRowPuller:
        def pull(self) -> int:
            return 1

    monkeypatch.setattr(cli_module, "_init", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "sync_instagram_post_ids_from_sheet",
        lambda: InstagramSheetSyncResult(
            rows_read=1,
            rows_considered=1,
            rows_updated=1,
            rows_skipped=0,
        ),
    )
    monkeypatch.setattr(cli_module.config, "enabled_platforms", lambda purpose="posting": ["instagram"])
    monkeypatch.setattr(cli_module, "PULLERS", {"instagram": OneRowPuller})
    monkeypatch.setattr(
        cli_module.bandit,
        "update_from_metrics",
        lambda: 0,
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["pull-analytics"])

    assert result.exit_code == 0
    assert "Instagram ID sync: 1/1 eligible rows updated from 1 sheet rows." in result.output
    assert "instagram (1 metrics)" in result.output


def test_diagnose_instagram_sync_prints_row_statuses(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "_init", lambda: None)
    monkeypatch.setattr(
        cli_module,
        "inspect_instagram_post_ids_from_sheet",
        lambda: InstagramSheetSyncDiagnostic(
            rows_read=2,
            rows_considered=2,
            rows_updated=0,
            rows_skipped=0,
            row_results=[
                InstagramSheetSyncRowResult(
                    row_number=2,
                    status="matched",
                    matched_by="handoff_id",
                    detail="matched local instagram post by handoff_id",
                    handoff_id="make:videos/clip-1.mp4",
                    content_id="content-1",
                    instagram_post_id="1801",
                    local_post_row_id=7,
                    local_post_id_before="make:videos/clip-1.mp4",
                ),
                InstagramSheetSyncRowResult(
                    row_number=3,
                    status="no_match",
                    detail="no matching local instagram post found",
                    content_id="content-2",
                    instagram_post_id="1802",
                ),
            ],
        ),
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.cli, ["diagnose-instagram-sync"])

    assert result.exit_code == 0
    assert "Instagram ID sync diagnostic: 2 eligible rows from 2 sheet rows" in result.output
    assert "1 matched" in result.output
    assert "0 " in result.output and "already synced" in result.output
    assert "1 unmatched" in result.output
    assert "matched" in result.output
    assert "1801" in result.output
    assert "1802" in result.output
