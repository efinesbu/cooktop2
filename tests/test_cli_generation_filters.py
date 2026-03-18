from __future__ import annotations

import sys
import types
from pathlib import Path

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
from src import db, product_images
from src.models import BanditRecommendation, Content, Product, ThemeHookAllocation


def test_run_cli_rejects_rotation_with_auto(tmp_db) -> None:
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
                ThemeHookAllocation(theme="hidden_knowledge", hook_type="question", count=1, score=0.7),
                ThemeHookAllocation(theme="benefit_spotlight", hook_type="visual_surprise", count=1, score=0.6),
            ]
        ),
    )

    calls: list[tuple[str | None, str | None]] = []

    def fake_generate_single(product, theme, hook_type, generation_index, should_post, creative_format=None, video_v2=False, cta_type=None, proof_type=None, script_style=None):
        calls.append((theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(("sku-1",), (), (), count=2, should_post=False)

    assert calls == [("hidden_knowledge", "question"), ("benefit_spotlight", "visual_surprise")]


def test_run_manual_supports_partial_overrides(monkeypatch) -> None:
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
                ThemeHookAllocation(theme="benefit_spotlight", hook_type="bold_claim", count=1, score=0.7),
            ]
        ),
    )

    calls: list[tuple[str | None, str | None]] = []

    def fake_generate_single(product, theme, hook_type, generation_index, should_post, creative_format=None, video_v2=False, cta_type=None, proof_type=None, script_style=None):
        calls.append((theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(("sku-1",), ("benefit_spotlight",), (), count=1, should_post=False)
    cli_module._run_manual(("sku-1",), (), ("question",), count=1, should_post=False)

    assert calls == [("benefit_spotlight", "bold_claim"), ("benefit_spotlight", "question")]


def test_run_manual_repeats_same_locked_pair_without_rotation(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.db,
        "get_product",
        lambda sku: Product(sku=sku, name=f"Product {sku}"),
    )

    calls: list[tuple[str | None, str | None]] = []

    def fake_generate_single(product, theme, hook_type, generation_index, should_post, creative_format=None, video_v2=False, cta_type=None, proof_type=None, script_style=None):
        calls.append((theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(
        ("sku-1",),
        ("benefit_spotlight",),
        ("question",),
        count=3,
        should_post=False,
    )

    assert calls == [("benefit_spotlight", "question")] * 3


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

    def fake_generate_single(product, theme, hook_type, generation_index, should_post, creative_format=None, video_v2=False, cta_type=None, proof_type=None, script_style=None):
        calls.append((theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(
        ("sku-1",),
        ("benefit_spotlight",),
        ("question",),
        count=3,
        should_post=False,
    )

    assert executor_usage == {"used": True, "max_workers": 3}
    assert calls == [("benefit_spotlight", "question")] * 3


def test_run_manual_stays_serial_at_parallel_threshold(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.db,
        "get_product",
        lambda sku: Product(sku=sku, name=f"Product {sku}"),
    )

    calls: list[tuple[str | None, str | None]] = []

    def fake_generate_single(product, theme, hook_type, generation_index, should_post, creative_format=None, video_v2=False, cta_type=None, proof_type=None, script_style=None):
        calls.append((theme, hook_type))
        return object()

    class UnexpectedExecutor:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("ThreadPoolExecutor should not be used at count >= 10")

    monkeypatch.setattr(cli_module, "ThreadPoolExecutor", UnexpectedExecutor)
    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(
        ("sku-1",),
        ("benefit_spotlight",),
        ("question",),
        count=10,
        should_post=False,
    )

    assert calls == [("benefit_spotlight", "question")] * 10


def test_run_manual_rotates_theme_and_hook_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module.db,
        "get_product",
        lambda sku: Product(sku=sku, name=f"Product {sku}"),
    )

    calls: list[tuple[str | None, str | None]] = []

    def fake_generate_single(product, theme, hook_type, generation_index, should_post, creative_format=None, video_v2=False, cta_type=None, proof_type=None, script_style=None):
        calls.append((theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_manual(
        ("sku-1",),
        ("benefit_spotlight", "identity_tribe"),
        ("question", "quick_tip", "bold_claim"),
        count=5,
        should_post=False,
        rotate_theme_hook=True,
    )

    assert calls == [
        ("benefit_spotlight", "question"),
        ("identity_tribe", "quick_tip"),
        ("benefit_spotlight", "bold_claim"),
        ("identity_tribe", "question"),
        ("benefit_spotlight", "quick_tip"),
    ]


def test_run_auto_parallelizes_across_products_below_threshold(monkeypatch) -> None:
    products = [
        Product(sku="sku-1", name="Product 1", generation_ready=True),
        Product(sku="sku-2", name="Product 2", generation_ready=True),
    ]
    monkeypatch.setattr(cli_module.db, "list_products", lambda **kwargs: products)
    monkeypatch.setattr(cli_module.random, "randrange", lambda _: 0)
    monkeypatch.setattr(
        cli_module.bandit,
        "recommend",
        lambda total_slots: BanditRecommendation(
            allocations=[
                ThemeHookAllocation(theme="benefit_spotlight", hook_type="bold_claim", count=2, score=0.8),
                ThemeHookAllocation(theme="hidden_knowledge", hook_type="question", count=1, score=0.6),
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

    def fake_generate_single(product, theme, hook_type, generation_index, should_post, creative_format=None, video_v2=False, cta_type=None, proof_type=None, script_style=None):
        calls.append((product.sku, theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_auto(count=3, should_post=False)

    assert executor_usage == {"used": True, "max_workers": 3}
    assert len(calls) == 3
    assert calls.count(("sku-1", "benefit_spotlight", "bold_claim")) == 1
    assert calls.count(("sku-2", "benefit_spotlight", "bold_claim")) == 1
    assert calls.count(("sku-1", "hidden_knowledge", "question")) == 1


def test_run_auto_uses_global_allocation_and_round_robin_product_split(monkeypatch) -> None:
    products = [
        Product(sku="sku-1", name="Product 1", generation_ready=True),
        Product(sku="sku-2", name="Product 2", generation_ready=True),
    ]
    monkeypatch.setattr(cli_module.db, "list_products", lambda **kwargs: products)
    monkeypatch.setattr(cli_module.random, "randrange", lambda _: 0)
    monkeypatch.setattr(
        cli_module.bandit,
        "recommend",
        lambda total_slots: BanditRecommendation(
            allocations=[
                ThemeHookAllocation(theme="benefit_spotlight", hook_type="bold_claim", count=2, score=0.8),
                ThemeHookAllocation(theme="hidden_knowledge", hook_type="question", count=1, score=0.6),
            ]
        ),
    )

    calls: list[tuple[str, str, str]] = []

    def fake_generate_single(product, theme, hook_type, generation_index, should_post, creative_format=None, video_v2=False, cta_type=None, proof_type=None, script_style=None):
        calls.append((product.sku, theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_auto(count=3, should_post=False)

    assert len(calls) == 3
    assert calls.count(("sku-1", "benefit_spotlight", "bold_claim")) == 1
    assert calls.count(("sku-2", "benefit_spotlight", "bold_claim")) == 1
    assert calls.count(("sku-1", "hidden_knowledge", "question")) == 1


def test_run_auto_randomizes_round_robin_starting_product(monkeypatch) -> None:
    products = [
        Product(sku="sku-1", name="Product 1", generation_ready=True),
        Product(sku="sku-2", name="Product 2", generation_ready=True),
        Product(sku="sku-3", name="Product 3", generation_ready=True),
    ]
    monkeypatch.setattr(cli_module.db, "list_products", lambda **kwargs: products)
    monkeypatch.setattr(
        cli_module.bandit,
        "recommend",
        lambda total_slots: BanditRecommendation(
            allocations=[
                ThemeHookAllocation(theme="benefit_spotlight", hook_type="bold_claim", count=3, score=0.8),
            ]
        ),
    )

    calls: list[tuple[str, str, str]] = []

    def fake_generate_single(product, theme, hook_type, generation_index, should_post, creative_format=None, video_v2=False, cta_type=None, proof_type=None, script_style=None):
        calls.append((product.sku, theme, hook_type))
        return object()

    monkeypatch.setattr(cli_module.random, "randrange", lambda _: 1)
    monkeypatch.setattr(cli_module, "_generate_single", fake_generate_single)

    cli_module._run_auto(count=3, should_post=False)

    assert calls == [
        ("sku-2", "benefit_spotlight", "bold_claim"),
        ("sku-3", "benefit_spotlight", "bold_claim"),
        ("sku-1", "benefit_spotlight", "bold_claim"),
    ]


def test_generate_single_refreshes_registered_images_when_disk_changes(
    tmp_db,
    mock_config,
    monkeypatch,
) -> None:
    product = Product(sku="eye-cream", name="Eye Cream")
    db.upsert_product(product)

    image_root = Path(mock_config["data_root"]) / "product-images" / product.sku
    image_root.mkdir(parents=True)

    stale_hero = image_root / "hero-alt-eyecream.jpeg"
    stale_hero.write_bytes(b"old-hero")
    product_images.register_images(product.sku)

    stale_hero.unlink()
    current_hero = image_root / "hero-eyecream.png"
    current_hero.write_bytes(b"new-hero")
    lifestyle = image_root / "lifestyle-eyecream.jpeg"
    lifestyle.write_bytes(b"lifestyle")

    captured: dict[str, list[str]] = {}

    monkeypatch.setattr(cli_module, "check_budget", lambda: (0.0, 100.0, True))

    def fake_generate_content(
        product_arg,
        theme,
        hook_type,
        images,
        creative_format=None,
        video_v2=False,
        **kwargs,
    ):
        captured["paths"] = [img.file_path for img in images]
        return (
            Content(
                id="content-1",
                product_sku=product_arg.sku,
                creative_format=creative_format or "ai_video_15s",
            ),
            {"platform_captions": {}, "hashtags": []},
        )

    monkeypatch.setattr(cli_module, "generate_content", fake_generate_content)
    monkeypatch.setattr(cli_module, "render_media", lambda *args, **kwargs: None)

    result = cli_module._generate_single(
        product,
        theme="benefit_spotlight",
        hook_type="question",
        generation_index=0,
        should_post=False,
    )

    assert result is not None
    assert set(captured["paths"]) == {str(current_hero), str(lifestyle)}
    assert str(stale_hero) not in captured["paths"]

    stored_paths = {
        img.file_path
        for img in db.list_product_images(product.sku)
    }
    assert stored_paths == {str(current_hero), str(lifestyle)}


def test_generate_single_refreshes_registered_images_from_custom_image_dir(
    tmp_db,
    mock_config,
    monkeypatch,
) -> None:
    image_root = Path(mock_config["data_root"]) / "product-images" / "eye-cream"
    image_root.mkdir(parents=True)

    stale_hero = image_root / "hero-alt-eyecream.jpeg"
    stale_hero.write_bytes(b"old-hero")

    product = Product(
        sku="92852-BLNK-PC-03-04-CR-AEC",
        name="Eye Cream",
        image_dir=str(image_root),
    )
    db.upsert_product(product)
    product_images.register_images(product.sku)

    stale_hero.unlink()
    current_hero = image_root / "hero-eyecream.png"
    current_hero.write_bytes(b"new-hero")
    lifestyle = image_root / "lifestyle-eyecream.jpeg"
    lifestyle.write_bytes(b"lifestyle")

    captured: dict[str, list[str]] = {}

    monkeypatch.setattr(cli_module, "check_budget", lambda: (0.0, 100.0, True))

    def fake_generate_content(
        product_arg,
        theme,
        hook_type,
        images,
        creative_format=None,
        video_v2=False,
        **kwargs,
    ):
        captured["paths"] = [img.file_path for img in images]
        return (
            Content(
                id="content-1",
                product_sku=product_arg.sku,
                creative_format=creative_format or "ai_video_15s",
            ),
            {"platform_captions": {}, "hashtags": []},
        )

    monkeypatch.setattr(cli_module, "generate_content", fake_generate_content)
    monkeypatch.setattr(cli_module, "render_media", lambda *args, **kwargs: None)

    result = cli_module._generate_single(
        product,
        theme="benefit_spotlight",
        hook_type="question",
        generation_index=0,
        should_post=False,
    )

    assert result is not None
    assert set(captured["paths"]) == {str(current_hero), str(lifestyle)}
    assert str(stale_hero) not in captured["paths"]

    refreshed_product = db.get_product(product.sku)
    assert refreshed_product is not None
    assert refreshed_product.image_dir == str(image_root)
    assert refreshed_product.generation_ready is True
