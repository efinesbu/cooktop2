"""SQLite queue helpers for the IG phone poster (shared by CLI enqueue and Flask server)."""

import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path


def db_path() -> Path:
    return Path(os.getenv("IG_POSTER_DB", "ig_poster/queue.db"))


def ensure_db() -> None:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS queue (
            id TEXT PRIMARY KEY,
            file TEXT NOT NULL,
            caption TEXT NOT NULL,
            status TEXT DEFAULT 'ready'
                CHECK(status IN ('ready','processing','posted','failed')),
            posted_url TEXT DEFAULT '',
            error TEXT DEFAULT '',
            step_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
            started_at TEXT,
            completed_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def enqueue(file_relative: str, caption: str, queue_id: str | None = None) -> str:
    ensure_db()
    if queue_id is None:
        queue_id = uuid.uuid4().hex[:8]
    conn = sqlite3.connect(str(db_path()))
    conn.execute(
        "INSERT INTO queue (id, file, caption) VALUES (?, ?, ?)",
        (queue_id, file_relative, caption),
    )
    conn.commit()
    conn.close()
    return queue_id


@dataclass(frozen=True)
class QueueSyncRow:
    posted_url: str
    status: str


def get_queue_row_for_sync(queue_id: str) -> QueueSyncRow | None:
    """Load queue fields needed to reconcile ig_phone posts to Graph media ids."""
    ensure_db()
    conn = sqlite3.connect(str(db_path()))
    row = conn.execute(
        "SELECT posted_url, status FROM queue WHERE id=?",
        (queue_id.strip(),),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return QueueSyncRow(posted_url=str(row[0] or ""), status=str(row[1] or ""))
