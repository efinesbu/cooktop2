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
import cli as cli_module
from src.models import BanditRecommendation, Product, ThemeHookAllocation


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


def test_run_manual_uses_bandit_when_no_theme_or_hook(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.db,
        "get_product",
        lambda sku: Product(sku=sku, name=f"Product {sku}"),
    )
    monkeypatch.setattr(
        cli_module.bandit,
        "recommend",
        lambda total_slots: BanditRecommendation(
            allocations=[
                ThemeHookAllocation(theme="curiosity", hook_type="question", count=1, score=0.7),
                ThemeHookAllocation(theme="benefit", hook_type="visual_surprise", count=1, score=0.6),
            ]
        ),
    )

    calls: list[tuple[str | None, str | None]] = []

    def fake_generate_single(product, theme, hook_type, should_post):
        calls.append((theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(("sku-1",), (), (), count=2, should_post=False)

    assert calls == [("curiosity", "question"), ("benefit", "visual_surprise")]


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


def test_run_manual_parallelizes_generation_when_count_below_threshold(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.db,
        "get_product",
        lambda sku: Product(sku=sku, name=f"Product {sku}"),
    )

    calls: list[tuple[str | None, str | None]] = []
    executor_usage: dict[str, int | bool] = {"used": False}

    class FakeExecutor:
        def __init__(self, max_workers: int) -> None:
            executor_usage["used"] = True
            executor_usage["max_workers"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def map(self, fn, iterable):
            for item in iterable:
                yield fn(item)

    def fake_generate_single(product, theme, hook_type, should_post):
        calls.append((theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(
        ("sku-1",),
        ("benefit",),
        ("question",),
        count=3,
        should_post=False,
    )

    assert executor_usage == {"used": True, "max_workers": 3}
    assert calls == [("benefit", "question")] * 3


def test_run_manual_stays_serial_at_parallel_threshold(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.db,
        "get_product",
        lambda sku: Product(sku=sku, name=f"Product {sku}"),
    )

    calls: list[tuple[str | None, str | None]] = []

    def fake_generate_single(product, theme, hook_type, should_post):
        calls.append((theme, hook_type))
        return object()

    class UnexpectedExecutor:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("ThreadPoolExecutor should not be used at count >= 10")

    monkeypatch.setattr(cli_module, "ThreadPoolExecutor", UnexpectedExecutor)
    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(
        ("sku-1",),
        ("benefit",),
        ("question",),
        count=10,
        should_post=False,
    )

    assert calls == [("benefit", "question")] * 10


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


def test_run_auto_parallelizes_across_products_below_threshold(monkeypatch) -> None:
    products = [
        Product(sku="sku-1", name="Product 1", generation_ready=True),
        Product(sku="sku-2", name="Product 2", generation_ready=True),
    ]
    monkeypatch.setattr(cli_module.db, "list_products", lambda **kwargs: products)
    monkeypatch.setattr(
        cli_module.bandit,
        "recommend",
        lambda total_slots: BanditRecommendation(
            allocations=[
                ThemeHookAllocation(theme="benefit", hook_type="bold_claim", count=2, score=0.8),
                ThemeHookAllocation(theme="curiosity", hook_type="question", count=1, score=0.6),
            ]
        ),
    )

    calls: list[tuple[str, str, str]] = []
    executor_usage: dict[str, int | bool] = {"used": False}

    class FakeExecutor:
        def __init__(self, max_workers: int) -> None:
            executor_usage["used"] = True
            executor_usage["max_workers"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def map(self, fn, iterable):
            for item in iterable:
                yield fn(item)

    def fake_generate_single(product, theme, hook_type, should_post):
        calls.append((product.sku, theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_auto(count=3, should_post=False)

    assert executor_usage == {"used": True, "max_workers": 3}
    assert len(calls) == 3
    assert calls.count(("sku-1", "benefit", "bold_claim")) == 1
    assert calls.count(("sku-2", "benefit", "bold_claim")) == 1
    assert calls.count(("sku-1", "curiosity", "question")) == 1


def test_run_auto_uses_global_allocation_and_round_robin_product_split(monkeypatch) -> None:
    products = [
        Product(sku="sku-1", name="Product 1", generation_ready=True),
        Product(sku="sku-2", name="Product 2", generation_ready=True),
    ]
    monkeypatch.setattr(cli_module.db, "list_products", lambda **kwargs: products)
    monkeypatch.setattr(
        cli_module.bandit,
        "recommend",
        lambda total_slots: BanditRecommendation(
            allocations=[
                ThemeHookAllocation(theme="benefit", hook_type="bold_claim", count=2, score=0.8),
                ThemeHookAllocation(theme="curiosity", hook_type="question", count=1, score=0.6),
            ]
        ),
    )

    calls: list[tuple[str, str, str]] = []

    def fake_generate_single(product, theme, hook_type, should_post):
        calls.append((product.sku, theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_auto(count=3, should_post=False)

    assert len(calls) == 3
    assert calls.count(("sku-1", "benefit", "bold_claim")) == 1
    assert calls.count(("sku-2", "benefit", "bold_claim")) == 1
    assert calls.count(("sku-1", "curiosity", "question")) == 1
