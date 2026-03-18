"""Phase 6: Commerce facts storage, ingestion, and bandit integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from src import db
from src.commerce_ingest import ingest_commerce_csv
from src.models import CommerceFact, Content, Metric, Post, Product


def test_upsert_commerce_fact_idempotent(tmp_db: Path) -> None:
    """Re-importing same row replaces, does not duplicate."""
    db.upsert_product(Product(sku="serum-x", name="Serum X"))
    db.insert_content(
        Content(id="abc123", product_sku="serum-x", theme="benefit_spotlight", hook_type="bold_claim")
    )

    fact = CommerceFact(
        content_id="abc123",
        platform="instagram",
        event_date="2026-03-08",
        sessions=10,
        purchases=1,
        revenue=29.99,
    )
    id1 = db.upsert_commerce_fact(fact)
    id2 = db.upsert_commerce_fact(fact)

    agg = db.aggregate_commerce_for_content("abc123", days=30, platform="instagram")
    assert agg["sessions"] == 10
    assert agg["purchases"] == 1
    assert agg["revenue"] == pytest.approx(29.99, rel=0.01)
    assert id1 == id2


def test_aggregate_commerce_empty(tmp_db: Path) -> None:
    """No commerce facts returns zeros."""
    agg = db.aggregate_commerce_for_content("nonexistent", days=30)
    assert agg["sessions"] == 0
    assert agg["purchases"] == 0
    assert agg["revenue"] == 0.0


def test_aggregate_commerce_filters_by_platform(tmp_db: Path) -> None:
    """Commerce is filtered by platform when specified."""
    db.upsert_product(Product(sku="p1", name="P1"))
    db.insert_content(Content(id="c1", product_sku="p1", theme="benefit_spotlight", hook_type="bold_claim"))

    db.upsert_commerce_fact(
        CommerceFact(content_id="c1", platform="instagram", event_date="2026-03-08", revenue=50.0)
    )
    db.upsert_commerce_fact(
        CommerceFact(content_id="c1", platform="youtube", event_date="2026-03-08", revenue=30.0)
    )

    agg_all = db.aggregate_commerce_for_content("c1", days=30)
    assert agg_all["revenue"] == pytest.approx(80.0, rel=0.01)

    agg_ig = db.aggregate_commerce_for_content("c1", days=30, platform="instagram")
    assert agg_ig["revenue"] == pytest.approx(50.0, rel=0.01)


def test_ingest_commerce_csv(tmp_db: Path, tmp_path: Path) -> None:
    """CSV ingest upserts rows and returns counts."""
    db.upsert_product(Product(sku="serum", name="Serum"))
    db.insert_content(Content(id="xyz789", product_sku="serum", theme="benefit_spotlight", hook_type="bold_claim"))

    csv_path = tmp_path / "commerce.csv"
    csv_path.write_text(
        "content_id,platform,event_date,sessions,purchases,revenue\n"
        "xyz789,instagram,2026-03-08,25,2,59.98\n"
        "xyz789,youtube,2026-03-08,10,0,0\n",
        encoding="utf-8",
    )

    inserted, skipped = ingest_commerce_csv(csv_path)
    assert inserted == 2
    assert skipped == 0

    agg = db.aggregate_commerce_for_content("xyz789", days=30)
    assert agg["sessions"] == 35
    assert agg["purchases"] == 2
    assert agg["revenue"] == pytest.approx(59.98, rel=0.01)


def test_ingest_commerce_csv_idempotent(tmp_db: Path, tmp_path: Path) -> None:
    """Re-importing same CSV does not create duplicates."""
    db.upsert_product(Product(sku="p", name="P"))
    db.insert_content(Content(id="c1", product_sku="p", theme="benefit_spotlight", hook_type="bold_claim"))

    csv_path = tmp_path / "commerce.csv"
    csv_path.write_text(
        "content_id,platform,event_date,sessions,purchases,revenue\n"
        "c1,instagram,2026-03-08,5,1,29.99\n",
        encoding="utf-8",
    )

    ingest_commerce_csv(csv_path)
    ingest_commerce_csv(csv_path)

    agg = db.aggregate_commerce_for_content("c1", days=30, platform="instagram")
    assert agg["sessions"] == 5
    assert agg["purchases"] == 1


def test_ingest_commerce_csv_skips_invalid_rows(tmp_db: Path, tmp_path: Path) -> None:
    """Invalid rows are skipped when skip_invalid=True."""
    db.upsert_product(Product(sku="p", name="P"))
    db.insert_content(Content(id="c1", product_sku="p", theme="benefit_spotlight", hook_type="bold_claim"))
    db.insert_content(Content(id="c3", product_sku="p", theme="benefit_spotlight", hook_type="bold_claim"))

    csv_path = tmp_path / "commerce.csv"
    csv_path.write_text(
        "content_id,platform,event_date,sessions,purchases,revenue\n"
        "c1,instagram,2026-03-08,5,1,29.99\n"
        ",instagram,2026-03-08,0,0,0\n"
        "c2,instagram,bad-date,0,0,0\n"
        "c3,ig,2026-03-09,1,0,0\n",
        encoding="utf-8",
    )

    inserted, skipped = ingest_commerce_csv(csv_path)
    assert inserted == 2
    assert skipped == 2
