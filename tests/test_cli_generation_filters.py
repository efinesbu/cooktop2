from __future__ import annotations

import sys
import types

from click.testing import CliRunner


class _UnusedPoster:
    def __init__(self, *args, **kwargs) -> None:
        pass


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
    types.SimpleNamespace(YouTubePoster=_UnusedPoster),
)
sys.modules.setdefault(
    "src.posters.instagram",
    types.SimpleNamespace(InstagramPoster=_UnusedPoster),
)
sys.modules.setdefault(
    "src.posters.tiktok",
    types.SimpleNamespace(TikTokPoster=_UnusedPoster),
)
sys.modules.setdefault(
    "src.posters.x",
    types.SimpleNamespace(XPoster=_UnusedPoster),
)

import cli as cli_module
from src.models import Product


def test_run_cli_rejects_rotation_with_auto() -> None:
    runner = CliRunner()

    result = runner.invoke(cli_module.cli, ["run", "--auto", "--rotate-theme-hook"])

    assert result.exit_code == 1
    assert "--auto cannot be combined" in result.output


def test_run_auto_requires_generation_ready(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_list_products(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(cli_module.db, "list_products", fake_list_products)
    cli_module._run_auto(count=3, should_post=False)

    assert captured["active_only"] is True
    assert captured["exclude_excluded"] is True
    assert captured["generation_ready_only"] is True


def test_run_manual_allows_prompt_selected_theme_and_hook(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.db,
        "get_product",
        lambda sku: Product(sku=sku, name=f"Product {sku}"),
    )

    calls: list[tuple[str | None, str | None]] = []

    def fake_generate_single(product, theme, hook_type, should_post):
        calls.append((theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(("sku-1",), (), (), count=2, should_post=False)

    assert calls == [(None, None), (None, None)]


def test_run_manual_supports_partial_overrides(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.db,
        "get_product",
        lambda sku: Product(sku=sku, name=f"Product {sku}"),
    )

    calls: list[tuple[str | None, str | None]] = []

    def fake_generate_single(product, theme, hook_type, should_post):
        calls.append((theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(("sku-1",), ("benefit",), (), count=1, should_post=False)
    cli_module._run_manual(("sku-1",), (), ("question",), count=1, should_post=False)

    assert calls == [("benefit", None), (None, "question")]


def test_run_manual_repeats_same_locked_pair_without_rotation(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.db,
        "get_product",
        lambda sku: Product(sku=sku, name=f"Product {sku}"),
    )

    calls: list[tuple[str | None, str | None]] = []

    def fake_generate_single(product, theme, hook_type, should_post):
        calls.append((theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(
        ("sku-1",),
        ("benefit",),
        ("question",),
        count=3,
        should_post=False,
    )

    assert calls == [("benefit", "question")] * 3


def test_run_manual_rotates_theme_and_hook_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.db,
        "get_product",
        lambda sku: Product(sku=sku, name=f"Product {sku}"),
    )

    calls: list[tuple[str | None, str | None]] = []

    def fake_generate_single(product, theme, hook_type, should_post):
        calls.append((theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(
        ("sku-1",),
        ("benefit", "social_proof"),
        ("question", "quick_tip", "bold_claim"),
        count=5,
        should_post=False,
        rotate_theme_hook=True,
    )

    assert calls == [
        ("benefit", "question"),
        ("social_proof", "quick_tip"),
        ("benefit", "bold_claim"),
        ("social_proof", "question"),
        ("benefit", "quick_tip"),
    ]
