"""Phase 6: Commerce fact ingestion from CSV.

Expects a CSV with columns:
  content_id, platform, event_date, sessions, add_to_cart, checkout_started, purchases, revenue

- content_id: matches content.id (our utm_content)
- platform: utm_source (youtube, instagram, tiktok, x)
- event_date: YYYY-MM-DD
- sessions, add_to_cart, checkout_started, purchases: integers (default 0)
- revenue: float (default 0)

Produce this CSV from Shopify order export by:
  1. Export orders with landing_site or use an app that extracts UTM to columns
  2. Parse utm_content -> content_id, utm_source -> platform
  3. Aggregate by (content_id, platform, event_date)
  4. Sum sessions, purchases, revenue per row
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from src import db
from src.models import CommerceFact

logger = logging.getLogger(__name__)

EXPECTED_COLUMNS = {
    "content_id",
    "platform",
    "event_date",
    "sessions",
    "add_to_cart",
    "checkout_started",
    "purchases",
    "revenue",
}


def _parse_int(val: str) -> int:
    if not val or not str(val).strip():
        return 0
    try:
        return int(float(str(val).strip()))
    except ValueError:
        return 0


def _parse_float(val: str) -> float:
    if not val or not str(val).strip():
        return 0.0
    try:
        return float(str(val).strip())
    except ValueError:
        return 0.0


def _normalize_platform(platform: str) -> str:
    """Map common variants to canonical platform names."""
    p = (platform or "").strip().lower()
    mapping = {
        "ig": "instagram",
        "insta": "instagram",
        "tt": "tiktok",
        "yt": "youtube",
        "twitter": "x",
    }
    return mapping.get(p, p) if p else ""


def ingest_commerce_csv(
    path: Path | str,
    source: str = "shopify_import",
    skip_invalid: bool = True,
) -> tuple[int, int]:
    """Ingest commerce facts from CSV. Returns (inserted_count, skipped_count).

    Idempotent: re-importing the same file replaces existing rows for same
    (content_id, platform, event_date).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Commerce CSV not found: {path}")

    inserted = 0
    skipped = 0

    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")

        # Allow flexible column names (case-insensitive, strip)
        field_map = {c.strip().lower(): c for c in reader.fieldnames}
        required = {"content_id", "platform", "event_date"}
        for r in required:
            if r not in field_map:
                raise ValueError(
                    f"CSV must have columns: content_id, platform, event_date. "
                    f"Found: {list(reader.fieldnames)}"
                )

        for row_num, row in enumerate(reader, start=2):
            content_id = (row.get(field_map.get("content_id", "content_id")) or "").strip()
            platform = _normalize_platform(
                row.get(field_map.get("platform", "platform")) or ""
            )
            event_date = (row.get(field_map.get("event_date", "event_date")) or "").strip()[:10]

            if not content_id or not platform or not event_date:
                if skip_invalid:
                    skipped += 1
                    continue
                raise ValueError(
                    f"Row {row_num}: content_id, platform, event_date required"
                )

            # Validate event_date format YYYY-MM-DD
            if len(event_date) != 10 or event_date[4] != "-" or event_date[7] != "-":
                if skip_invalid:
                    skipped += 1
                    continue
                raise ValueError(f"Row {row_num}: event_date must be YYYY-MM-DD, got {event_date}")

            fact = CommerceFact(
                content_id=content_id,
                platform=platform,
                event_date=event_date,
                sessions=_parse_int(row.get(field_map.get("sessions", "sessions"), 0)),
                add_to_cart=_parse_int(row.get(field_map.get("add_to_cart", "add_to_cart"), 0)),
                checkout_started=_parse_int(
                    row.get(field_map.get("checkout_started", "checkout_started"), 0)
                ),
                purchases=_parse_int(row.get(field_map.get("purchases", "purchases"), 0)),
                revenue=_parse_float(row.get(field_map.get("revenue", "revenue"), 0)),
                source=source,
            )
            db.upsert_commerce_fact(fact)
            inserted += 1

    logger.info("Commerce ingest: %d rows upserted, %d skipped", inserted, skipped)
    return inserted, skipped
