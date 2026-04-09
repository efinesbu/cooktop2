from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from io import StringIO
from typing import Any
from urllib.parse import urlparse, urlunparse

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import requests

from ig_poster import get_queue_row_for_sync
from src import config, db

logger = logging.getLogger(__name__)

_GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

_SHEETS_SCOPE = ("https://www.googleapis.com/auth/spreadsheets.readonly",)


@dataclass(frozen=True)
class InstagramSheetSyncResult:
    rows_read: int = 0
    rows_considered: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0


@dataclass(frozen=True)
class InstagramSheetSyncRowResult:
    row_number: int
    status: str
    matched_by: str = ""
    detail: str = ""
    handoff_id: str = ""
    content_id: str = ""
    instagram_post_id: str = ""
    local_post_row_id: int | None = None
    local_post_id_before: str = ""


@dataclass(frozen=True)
class InstagramSheetSyncDiagnostic:
    rows_read: int
    rows_considered: int
    rows_updated: int
    rows_skipped: int
    row_results: list[InstagramSheetSyncRowResult]


@dataclass(frozen=True)
class InstagramPhoneQueueSyncResult:
    posts_considered: int = 0
    posts_updated: int = 0
    posts_skipped: int = 0


def sync_instagram_post_ids_from_sheet() -> InstagramSheetSyncResult:
    diagnostic = inspect_instagram_post_ids_from_sheet(apply_updates=True)
    return InstagramSheetSyncResult(
        rows_read=diagnostic.rows_read,
        rows_considered=diagnostic.rows_considered,
        rows_updated=diagnostic.rows_updated,
        rows_skipped=diagnostic.rows_skipped,
    )


def inspect_instagram_post_ids_from_sheet(
    *,
    apply_updates: bool = False,
) -> InstagramSheetSyncDiagnostic:
    spreadsheet_id = str(config.get("instagram_sync.spreadsheet_id", "")).strip()
    if not spreadsheet_id:
        return InstagramSheetSyncDiagnostic(
            rows_read=0,
            rows_considered=0,
            rows_updated=0,
            rows_skipped=0,
            row_results=[],
        )

    rows = _read_sheet_rows(
        spreadsheet_id=spreadsheet_id,
        worksheet_name=str(config.get("instagram_sync.worksheet_name", "")).strip() or None,
        worksheet_gid=str(config.get("instagram_sync.worksheet_gid", "0")).strip() or "0",
        credentials_file=str(config.get("instagram_sync.credentials_file", "")).strip() or None,
        public_csv_url=str(config.get("instagram_sync.public_csv_url", "")).strip() or None,
    )

    row_results = [
        _inspect_row(row_number=index, row=row, apply_updates=apply_updates)
        for index, row in enumerate(rows, start=2)
    ]
    considered = sum(1 for row in row_results if row.status != "skipped")
    updated = sum(1 for row in row_results if row.status == "updated")
    skipped = sum(1 for row in row_results if row.status == "skipped")
    return InstagramSheetSyncDiagnostic(
        rows_read=len(rows),
        rows_considered=considered,
        rows_updated=updated,
        rows_skipped=skipped,
        row_results=row_results,
    )


def _inspect_row(
    *,
    row_number: int,
    row: dict[str, str],
    apply_updates: bool,
) -> InstagramSheetSyncRowResult:
    platform = _norm(row.get("platform")).lower()
    instagram_post_id = _norm(row.get("instagram_post_id"))
    handoff_id = _norm(row.get("handoff_id"))
    handoff_object_key = _norm(row.get("handoff_object_key"))
    content_id = _norm(row.get("content_id"))

    if not handoff_id and handoff_object_key:
        handoff_id = f"make:{handoff_object_key}"

    if platform != "instagram":
        return InstagramSheetSyncRowResult(
            row_number=row_number,
            status="skipped",
            detail="platform is not instagram",
            handoff_id=handoff_id,
            content_id=content_id,
            instagram_post_id=instagram_post_id,
        )

    if not instagram_post_id:
        return InstagramSheetSyncRowResult(
            row_number=row_number,
            status="skipped",
            detail="instagram_post_id is blank",
            handoff_id=handoff_id,
            content_id=content_id,
            instagram_post_id=instagram_post_id,
        )

    handoff_match = (
        db.find_post_by_platform_remote_id("instagram", handoff_id)
        if handoff_id else None
    )
    if handoff_match is not None:
        if apply_updates:
            db.sync_instagram_post_id(
                instagram_post_id,
                handoff_id=handoff_id or None,
                content_id=content_id or None,
            )
        return InstagramSheetSyncRowResult(
            row_number=row_number,
            status="updated" if apply_updates else "matched",
            matched_by="handoff_id",
            detail="matched local instagram post by handoff_id",
            handoff_id=handoff_id,
            content_id=content_id,
            instagram_post_id=instagram_post_id,
            local_post_row_id=handoff_match.id,
            local_post_id_before=handoff_match.post_id or "",
        )

    instagram_posts = [
        post for post in db.list_posts_for_content(content_id)
        if post.platform == "instagram"
    ] if content_id else []

    exact_match = next(
        (post for post in instagram_posts if (post.post_id or "").strip() == instagram_post_id),
        None,
    )
    if exact_match is not None:
        return InstagramSheetSyncRowResult(
            row_number=row_number,
            status="already_synced",
            matched_by="content_id",
            detail="content already points at this instagram_post_id",
            handoff_id=handoff_id,
            content_id=content_id,
            instagram_post_id=instagram_post_id,
            local_post_row_id=exact_match.id,
            local_post_id_before=exact_match.post_id or "",
        )

    handoff_rows = [
        post for post in instagram_posts
        if _is_instagram_handoff_post_id(post.post_id)
    ]
    if len(handoff_rows) == 1:
        resolved_handoff = (handoff_id or None) or (handoff_rows[0].post_id or None)
        if apply_updates:
            db.sync_instagram_post_id(
                instagram_post_id,
                handoff_id=resolved_handoff,
                content_id=content_id or None,
            )
        return InstagramSheetSyncRowResult(
            row_number=row_number,
            status="updated" if apply_updates else "matched",
            matched_by="content_id",
            detail="matched single instagram handoff row (make or ig_phone) for content_id",
            handoff_id=handoff_id,
            content_id=content_id,
            instagram_post_id=instagram_post_id,
            local_post_row_id=handoff_rows[0].id,
            local_post_id_before=handoff_rows[0].post_id or "",
        )

    if len(handoff_rows) > 1:
        return InstagramSheetSyncRowResult(
            row_number=row_number,
            status="ambiguous",
            matched_by="content_id",
            detail="multiple instagram handoff rows (make or ig_phone) found for content_id",
            handoff_id=handoff_id,
            content_id=content_id,
            instagram_post_id=instagram_post_id,
        )

    return InstagramSheetSyncRowResult(
        row_number=row_number,
        status="no_match",
        detail="no matching local instagram post found",
        handoff_id=handoff_id,
        content_id=content_id,
        instagram_post_id=instagram_post_id,
    )


def _read_sheet_rows(
    *,
    spreadsheet_id: str,
    worksheet_name: str | None,
    worksheet_gid: str,
    credentials_file: str | None,
    public_csv_url: str | None,
) -> list[dict[str, str]]:
    if credentials_file:
        credentials = Credentials.from_service_account_file(
            str(Path(credentials_file).expanduser()),
            scopes=_SHEETS_SCOPE,
        )
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        sheet_name = worksheet_name or _first_sheet_name(service, spreadsheet_id)
        values = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=sheet_name,
        ).execute().get("values", [])
        return _rows_from_values(values)

    csv_url = public_csv_url or _public_csv_url(spreadsheet_id, worksheet_gid)
    return _read_public_csv_rows(csv_url)


def _first_sheet_name(service: Any, spreadsheet_id: str) -> str:
    data = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(title))",
    ).execute()
    sheets = data.get("sheets", [])
    if not sheets:
        raise ValueError("Google Sheet has no worksheets.")
    return str(sheets[0]["properties"]["title"])


def _rows_from_values(values: list[list[str]]) -> list[dict[str, str]]:
    if not values:
        return []
    headers = [_norm(value) for value in values[0]]
    rows: list[dict[str, str]] = []
    for raw_row in values[1:]:
        row = {header: _norm(raw_row[idx]) if idx < len(raw_row) else "" for idx, header in enumerate(headers) if header}
        if any(row.values()):
            rows.append(row)
    return rows


def _read_public_csv_rows(csv_url: str) -> list[dict[str, str]]:
    response = requests.get(csv_url, timeout=30)
    response.raise_for_status()
    parsed = csv.reader(StringIO(response.text))
    return _rows_from_values([list(row) for row in parsed])


def _public_csv_url(spreadsheet_id: str, worksheet_gid: str) -> str:
    return (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export"
        f"?format=csv&gid={worksheet_gid}"
    )


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_instagram_handoff_post_id(post_id: str | None) -> bool:
    pid = (post_id or "").strip()
    return pid.startswith("make:") or pid.startswith("ig_phone:")


def sync_instagram_post_ids_from_phone_queue() -> InstagramPhoneQueueSyncResult:
    """Resolve ig_phone:<queue_id> post_id values to final Graph media ids via permalink match."""
    token = str(config.get("instagram.access_token", "")).strip()
    ig_account_id = str(config.get("instagram.instagram_account_id", "")).strip()
    if not token or not ig_account_id:
        return InstagramPhoneQueueSyncResult()

    try:
        media_pairs = _fetch_instagram_media_permalinks(token, ig_account_id)
    except Exception as exc:
        logger.warning("Instagram phone queue sync: failed to list media: %s", exc)
        return InstagramPhoneQueueSyncResult()

    norm_to_id: dict[str, str] = {}
    for media_id, permalink in media_pairs:
        norm = _normalize_instagram_permalink(permalink)
        if norm and norm not in norm_to_id:
            norm_to_id[norm] = media_id

    considered = 0
    updated = 0
    skipped = 0

    for post in db.list_recent_posts():
        if post.platform != "instagram":
            continue
        pid = (post.post_id or "").strip()
        if not pid.startswith("ig_phone:"):
            continue

        queue_id = pid.split(":", 1)[1].strip()
        if not queue_id:
            skipped += 1
            continue

        qrow = get_queue_row_for_sync(queue_id)
        if qrow is None:
            skipped += 1
            continue

        posted_url = (qrow.posted_url or "").strip()
        if not posted_url:
            skipped += 1
            continue

        considered += 1
        norm_queue = _normalize_instagram_permalink(posted_url)
        if not norm_queue:
            skipped += 1
            continue

        final_id = norm_to_id.get(norm_queue)
        if not final_id:
            skipped += 1
            continue

        n = db.sync_instagram_post_id(
            final_id,
            handoff_id=post.post_id,
            content_id=post.content_id,
        )
        if n:
            updated += 1
        else:
            skipped += 1

    return InstagramPhoneQueueSyncResult(
        posts_considered=considered,
        posts_updated=updated,
        posts_skipped=skipped,
    )


def _normalize_instagram_permalink(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    netloc = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    if path and not path.startswith("/"):
        path = "/" + path
    scheme = "https"
    return urlunparse((scheme, netloc, path, "", "", ""))


def _fetch_instagram_media_permalinks(
    access_token: str,
    ig_account_id: str,
    *,
    page_limit: int = 50,
    max_pages: int = 20,
) -> list[tuple[str, str]]:
    """Return (media_id, permalink) from the IG user media edge."""
    out: list[tuple[str, str]] = []
    url = f"{_GRAPH_API_BASE}/{ig_account_id}/media"
    params = {
        "fields": "id,permalink",
        "limit": str(page_limit),
        "access_token": access_token,
    }
    pages = 0
    first = True
    while url and pages < max_pages:
        if first:
            resp = requests.get(url, params=params, timeout=30)
            first = False
        else:
            resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        for item in payload.get("data", []):
            mid = item.get("id")
            pl = item.get("permalink")
            if mid and pl:
                out.append((str(mid), str(pl)))
        url = (payload.get("paging") or {}).get("next") or ""
        if not url:
            break
        pages += 1
    return out
