from __future__ import annotations

from pathlib import Path

from src import db, instagram_sheet_sync
from src.models import Content, Post, Product


def test_sync_instagram_post_ids_from_sheet_uses_handoff_id(
    tmp_db: Path,
    monkeypatch,
) -> None:
    product = Product(sku="sku-1", name="Product 1")
    content = Content(id="content-1", product_sku=product.sku, theme="benefit", hook_type="question")
    db.upsert_product(product)
    db.insert_content(content)
    post_id = db.insert_post(
        Post(content_id=content.id, platform="instagram", post_id="make:videos/clip-123.mp4")
    )

    monkeypatch.setattr(
        instagram_sheet_sync.config,
        "get",
        lambda key, default=None: {
            "instagram_sync.spreadsheet_id": "sheet-123",
            "instagram_sync.worksheet_name": "Sheet1",
            "instagram_sync.credentials_file": "creds.json",
        }.get(key, default),
    )
    monkeypatch.setattr(
        instagram_sheet_sync,
        "_read_sheet_rows",
        lambda **kwargs: [{
            "handoff_id": "make:videos/clip-123.mp4",
            "handoff_object_key": "videos/clip-123.mp4",
            "platform": "instagram",
            "instagram_post_id": "179000111222333",
            "content_id": "content-1",
            "posted_at": "2026-03-09T10:00:00Z",
        }],
    )

    result = instagram_sheet_sync.sync_instagram_post_ids_from_sheet()

    updated_post = db.get_post(post_id)
    assert updated_post is not None
    assert updated_post.post_id == "179000111222333"
    assert result.rows_read == 1
    assert result.rows_considered == 1
    assert result.rows_updated == 1
    assert result.rows_skipped == 0


def test_sync_instagram_post_ids_from_sheet_falls_back_to_content_id(
    tmp_db: Path,
    monkeypatch,
) -> None:
    product = Product(sku="sku-2", name="Product 2")
    content = Content(id="content-2", product_sku=product.sku, theme="benefit", hook_type="question")
    db.upsert_product(product)
    db.insert_content(content)
    post_id = db.insert_post(
        Post(content_id=content.id, platform="instagram", post_id="make:videos/clip-456.mp4")
    )

    monkeypatch.setattr(
        instagram_sheet_sync.config,
        "get",
        lambda key, default=None: {
            "instagram_sync.spreadsheet_id": "sheet-123",
            "instagram_sync.worksheet_name": "Sheet1",
            "instagram_sync.credentials_file": "creds.json",
        }.get(key, default),
    )
    monkeypatch.setattr(
        instagram_sheet_sync,
        "_read_sheet_rows",
        lambda **kwargs: [{
            "handoff_id": "",
            "handoff_object_key": "",
            "platform": "instagram",
            "instagram_post_id": "179999888777666",
            "content_id": "content-2",
            "posted_at": "2026-03-09T10:05:00Z",
        }],
    )

    result = instagram_sheet_sync.sync_instagram_post_ids_from_sheet()

    updated_post = db.get_post(post_id)
    assert updated_post is not None
    assert updated_post.post_id == "179999888777666"
    assert result.rows_updated == 1


def test_rows_from_values_uses_sheet_headers() -> None:
    rows = instagram_sheet_sync._rows_from_values([
        [
            "handoff_id",
            "handoff_object_key",
            "platform",
            "instagram_post_id",
            "content_id",
            "posted_at",
        ],
        [
            "make:videos/clip-123.mp4",
            "videos/clip-123.mp4",
            "instagram",
            "179000111222333",
            "content-1",
            "2026-03-09T10:00:00Z",
        ],
    ])

    assert rows == [{
        "handoff_id": "make:videos/clip-123.mp4",
        "handoff_object_key": "videos/clip-123.mp4",
        "platform": "instagram",
        "instagram_post_id": "179000111222333",
        "content_id": "content-1",
        "posted_at": "2026-03-09T10:00:00Z",
    }]


def test_read_sheet_rows_supports_public_csv_without_credentials(monkeypatch) -> None:
    class FakeResponse:
        text = (
            "handoff_id,handoff_object_key,platform,instagram_post_id,content_id,posted_at\n"
            "make:videos/make-mapping-test.mp4,videos/make-mapping-test.mp4,instagram,"
            "18084635933590525,test-content-001,1773069354\n"
        )

        def raise_for_status(self) -> None:
            return None

    captured: dict[str, str] = {}

    def fake_get(url: str, timeout: int):
        captured["url"] = url
        assert timeout == 30
        return FakeResponse()

    monkeypatch.setattr(instagram_sheet_sync.requests, "get", fake_get)

    rows = instagram_sheet_sync._read_sheet_rows(
        spreadsheet_id="1xqShI6fSYiIlYIlJI-nE9GvuNPH6qM-1F4h_vEfjrf4",
        worksheet_name=None,
        worksheet_gid="0",
        credentials_file=None,
        public_csv_url=None,
    )

    assert captured["url"] == (
        "https://docs.google.com/spreadsheets/d/"
        "1xqShI6fSYiIlYIlJI-nE9GvuNPH6qM-1F4h_vEfjrf4/export?format=csv&gid=0"
    )
    assert rows == [{
        "handoff_id": "make:videos/make-mapping-test.mp4",
        "handoff_object_key": "videos/make-mapping-test.mp4",
        "platform": "instagram",
        "instagram_post_id": "18084635933590525",
        "content_id": "test-content-001",
        "posted_at": "1773069354",
    }]


def test_inspect_instagram_post_ids_reports_statuses(
    tmp_db: Path,
    monkeypatch,
) -> None:
    product = Product(sku="sku-3", name="Product 3")
    content_match = Content(id="content-match", product_sku=product.sku, theme="benefit", hook_type="question")
    content_synced = Content(id="content-synced", product_sku=product.sku, theme="benefit", hook_type="question")
    db.upsert_product(product)
    db.insert_content(content_match)
    db.insert_content(content_synced)
    db.insert_post(
        Post(content_id=content_match.id, platform="instagram", post_id="make:videos/clip-789.mp4")
    )
    db.insert_post(
        Post(content_id=content_synced.id, platform="instagram", post_id="18000000000000001")
    )

    monkeypatch.setattr(
        instagram_sheet_sync.config,
        "get",
        lambda key, default=None: {
            "instagram_sync.spreadsheet_id": "sheet-123",
            "instagram_sync.worksheet_gid": "0",
        }.get(key, default),
    )
    monkeypatch.setattr(
        instagram_sheet_sync,
        "_read_sheet_rows",
        lambda **kwargs: [
            {
                "handoff_id": "make:videos/clip-789.mp4",
                "handoff_object_key": "videos/clip-789.mp4",
                "platform": "instagram",
                "instagram_post_id": "18099999999999999",
                "content_id": "content-match",
                "posted_at": "1773069354",
            },
            {
                "handoff_id": "",
                "handoff_object_key": "",
                "platform": "instagram",
                "instagram_post_id": "18000000000000001",
                "content_id": "content-synced",
                "posted_at": "1773069355",
            },
            {
                "handoff_id": "",
                "handoff_object_key": "",
                "platform": "instagram",
                "instagram_post_id": "18011111111111111",
                "content_id": "missing-content",
                "posted_at": "1773069356",
            },
        ],
    )

    diagnostic = instagram_sheet_sync.inspect_instagram_post_ids_from_sheet()

    assert diagnostic.rows_read == 3
    assert diagnostic.rows_considered == 3
    assert diagnostic.rows_updated == 0
    assert diagnostic.rows_skipped == 0
    assert [row.status for row in diagnostic.row_results] == [
        "matched",
        "already_synced",
        "no_match",
    ]
    assert diagnostic.row_results[0].matched_by == "handoff_id"
    assert diagnostic.row_results[1].matched_by == "content_id"
