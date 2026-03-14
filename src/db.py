from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from src import config
from src.models import (
    Product, ProductImage, Content, PlatformPayload, Post, Metric, BanditArm,
    BanditObservation, Cost, CommerceFact, ResearchSnapshot,
)

_DB_PATH: Path | None = None


def _db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = config.db_path()
    return _DB_PATH


def set_db_path(path: Path | str) -> None:
    global _DB_PATH
    _DB_PATH = Path(path)


def init_db() -> None:
    schema = Path("db/schema.sql").read_text(encoding="utf-8")
    with _connect() as conn:
        _migrate_bandit_tables(conn)
        conn.executescript(schema)
        _run_migrations(conn)


def _run_migrations(conn: sqlite3.Connection) -> None:
    product_columns = set(_table_columns(conn, "products"))
    if "product_url" not in product_columns:
        conn.execute("ALTER TABLE products ADD COLUMN product_url TEXT")

    content_columns = set(_table_columns(conn, "content"))
    for name, ddl in (
        ("review_status", "ALTER TABLE content ADD COLUMN review_status TEXT DEFAULT 'pending'"),
        ("review_notes", "ALTER TABLE content ADD COLUMN review_notes TEXT"),
        ("approved_at", "ALTER TABLE content ADD COLUMN approved_at TEXT"),
        ("rejected_at", "ALTER TABLE content ADD COLUMN rejected_at TEXT"),
        ("creative_format", "ALTER TABLE content ADD COLUMN creative_format TEXT DEFAULT 'ai_video_15s'"),
        ("cta_type", "ALTER TABLE content ADD COLUMN cta_type TEXT DEFAULT 'see_product'"),
        ("cta_text", "ALTER TABLE content ADD COLUMN cta_text TEXT"),
        ("problem_angle", "ALTER TABLE content ADD COLUMN problem_angle TEXT"),
        ("proof_type", "ALTER TABLE content ADD COLUMN proof_type TEXT"),
        ("script_style", "ALTER TABLE content ADD COLUMN script_style TEXT"),
        ("research_snapshot_id", "ALTER TABLE content ADD COLUMN research_snapshot_id TEXT"),
        ("asset_manifest_json", "ALTER TABLE content ADD COLUMN asset_manifest_json TEXT"),
        ("source_content_id", "ALTER TABLE content ADD COLUMN source_content_id TEXT"),
        ("strategy_metadata_json", "ALTER TABLE content ADD COLUMN strategy_metadata_json TEXT"),
    ):
        if name not in content_columns:
            conn.execute(ddl)

    # Phase 7: index for paid-variant lineage (after source_content_id exists)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_content_source ON content(source_content_id)")

    payload_columns = set(_table_columns(conn, "platform_payloads")) if _table_exists(conn, "platform_payloads") else set()
    for name, ddl in (
        ("destination_url", "ALTER TABLE platform_payloads ADD COLUMN destination_url TEXT"),
        ("utm_source", "ALTER TABLE platform_payloads ADD COLUMN utm_source TEXT"),
        ("utm_medium", "ALTER TABLE platform_payloads ADD COLUMN utm_medium TEXT"),
        ("utm_campaign", "ALTER TABLE platform_payloads ADD COLUMN utm_campaign TEXT"),
        ("utm_content", "ALTER TABLE platform_payloads ADD COLUMN utm_content TEXT"),
        ("link_mode", "ALTER TABLE platform_payloads ADD COLUMN link_mode TEXT DEFAULT 'direct'"),
    ):
        if name not in payload_columns and payload_columns:
            conn.execute(ddl)

    posts_columns = set(_table_columns(conn, "posts")) if _table_exists(conn, "posts") else set()
    for name, ddl in (
        ("destination_url", "ALTER TABLE posts ADD COLUMN destination_url TEXT"),
        ("utm_source", "ALTER TABLE posts ADD COLUMN utm_source TEXT"),
        ("utm_medium", "ALTER TABLE posts ADD COLUMN utm_medium TEXT"),
        ("utm_campaign", "ALTER TABLE posts ADD COLUMN utm_campaign TEXT"),
        ("utm_content", "ALTER TABLE posts ADD COLUMN utm_content TEXT"),
        ("link_mode", "ALTER TABLE posts ADD COLUMN link_mode TEXT DEFAULT 'direct'"),
    ):
        if name not in posts_columns and posts_columns:
            conn.execute(ddl)

    if not _table_exists(conn, "research_snapshots"):
        conn.executescript(
            """
            CREATE TABLE research_snapshots (
                id              TEXT PRIMARY KEY,
                product_sku     TEXT,
                platform        TEXT,
                creative_format TEXT,
                summary         TEXT NOT NULL,
                source_type     TEXT DEFAULT 'manual',
                created_at      TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_research_product   ON research_snapshots(product_sku);
            CREATE INDEX IF NOT EXISTS idx_research_platform  ON research_snapshots(platform);
            CREATE INDEX IF NOT EXISTS idx_research_format    ON research_snapshots(creative_format);
            CREATE INDEX IF NOT EXISTS idx_research_created   ON research_snapshots(created_at);
            """
        )

    # Phase 4: expand costs.step for slideshow and image_motion renderers
    _migrate_costs_step(conn)

    if not _table_exists(conn, "commerce_facts"):
        conn.executescript(
            """
            CREATE TABLE commerce_facts (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id           TEXT NOT NULL REFERENCES content(id),
                platform             TEXT NOT NULL,
                event_date           TEXT NOT NULL,
                sessions             INTEGER DEFAULT 0,
                add_to_cart          INTEGER DEFAULT 0,
                checkout_started     INTEGER DEFAULT 0,
                purchases            INTEGER DEFAULT 0,
                revenue              REAL DEFAULT 0,
                source               TEXT DEFAULT 'shopify_import',
                ingested_at          TEXT DEFAULT (datetime('now')),
                UNIQUE (content_id, platform, event_date)
            );
            CREATE INDEX IF NOT EXISTS idx_commerce_content   ON commerce_facts(content_id);
            CREATE INDEX IF NOT EXISTS idx_commerce_date      ON commerce_facts(event_date);
            CREATE INDEX IF NOT EXISTS idx_commerce_platform  ON commerce_facts(platform);
            """
        )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS platform_payloads (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id      TEXT NOT NULL REFERENCES content(id),
            platform        TEXT NOT NULL,
            caption         TEXT,
            hashtags        TEXT,
            utm_url         TEXT,
            publish_at      TEXT,
            status          TEXT NOT NULL DEFAULT 'pending',
            last_error      TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now')),
            UNIQUE (content_id, platform)
        );
        CREATE INDEX IF NOT EXISTS idx_content_review   ON content(review_status);
        CREATE INDEX IF NOT EXISTS idx_payloads_content ON platform_payloads(content_id);
        CREATE INDEX IF NOT EXISTS idx_payloads_publish ON platform_payloads(publish_at);
        CREATE INDEX IF NOT EXISTS idx_payloads_status  ON platform_payloads(status);
        """
    )
    _migrate_bandit_tables(conn)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [row["name"] for row in rows]


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _costs_step_has_legacy_check(conn: sqlite3.Connection) -> bool:
    """Return True when the costs table still has a restrictive step CHECK."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='costs'"
    ).fetchone()
    if not row or not row["sql"]:
        return False
    sql = row["sql"].lower()
    return "check" in sql and "step" in sql


def _migrate_costs_step(conn: sqlite3.Connection) -> None:
    """Remove legacy costs.step CHECK constraints so new cost steps keep working."""
    if not _table_exists(conn, "costs") or not _costs_step_has_legacy_check(conn):
        return

    # Recreate table without the legacy step CHECK so new cost events like
    # renderer-specific work and TTS can be stored on existing databases.
    conn.execute(
        "CREATE TABLE costs_new (id INTEGER PRIMARY KEY AUTOINCREMENT, content_id TEXT NOT NULL REFERENCES content(id), "
        "step TEXT NOT NULL, api_provider TEXT NOT NULL, tokens_or_units INTEGER, cost_usd REAL, "
        "created_at TEXT DEFAULT (datetime('now')))"
    )
    conn.execute(
        "INSERT INTO costs_new (id, content_id, step, api_provider, tokens_or_units, cost_usd, created_at) "
        "SELECT id, content_id, step, api_provider, tokens_or_units, cost_usd, created_at FROM costs"
    )
    conn.execute("DROP TABLE costs")
    conn.execute("ALTER TABLE costs_new RENAME TO costs")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_costs_content ON costs(content_id)")


def _migrate_bandit_tables(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "bandit_state"):
        bandit_state_columns = set(_table_columns(conn, "bandit_state"))
        if "arm_key" not in bandit_state_columns:
            conn.execute("ALTER TABLE bandit_state RENAME TO bandit_state_legacy")

    if _table_exists(conn, "bandit_observations"):
        observation_columns = set(_table_columns(conn, "bandit_observations"))
        if "content_id" not in observation_columns:
            conn.execute("ALTER TABLE bandit_observations RENAME TO bandit_observations_legacy")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bandit_state (
            arm_key         TEXT PRIMARY KEY,
            theme           TEXT NOT NULL,
            hook_type       TEXT NOT NULL,
            alpha           REAL DEFAULT 1.0,
            beta            REAL DEFAULT 1.0,
            last_updated    TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS bandit_observations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id      TEXT NOT NULL UNIQUE REFERENCES content(id),
            product_sku     TEXT NOT NULL REFERENCES products(sku),
            arm_key         TEXT NOT NULL,
            theme           TEXT NOT NULL,
            hook_type       TEXT NOT NULL,
            aggregated_engagement_rate REAL NOT NULL,
            success         INTEGER NOT NULL,
            observed_at     TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_bandit_state_theme ON bandit_state(theme, hook_type);
        CREATE INDEX IF NOT EXISTS idx_bandit_obs_arm     ON bandit_observations(arm_key);
        CREATE INDEX IF NOT EXISTS idx_bandit_obs_product ON bandit_observations(product_sku);
        """
    )


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

def upsert_product(p: Product) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO products
               (sku, name, category, price, product_url, shopify_image_url, image_dir,
                generation_ready, active, excluded, exclude_reason,
                last_content_date, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(sku) DO UPDATE SET
                 name=excluded.name,
                 category=excluded.category,
                 price=excluded.price,
                 product_url=excluded.product_url,
                 shopify_image_url=excluded.shopify_image_url,
                 image_dir=excluded.image_dir,
                 generation_ready=excluded.generation_ready,
                 active=excluded.active,
                 updated_at=datetime('now')""",
            (p.sku, p.name, p.category, p.price, p.product_url, p.shopify_image_url,
             p.image_dir, int(p.generation_ready), int(p.active),
             int(p.excluded), p.exclude_reason, p.last_content_date),
        )


def get_product(sku: str) -> Product | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
    return _row_to_product(row) if row else None


def list_products(
    active_only: bool = True,
    exclude_excluded: bool = True,
    generation_ready_only: bool = False,
) -> list[Product]:
    clauses = []
    if active_only:
        clauses.append("active=1")
    if exclude_excluded:
        clauses.append("excluded=0")
    if generation_ready_only:
        clauses.append("generation_ready=1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(f"SELECT * FROM products {where}").fetchall()
    return [_row_to_product(r) for r in rows]


def exclude_product(sku: str, reason: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE products SET excluded=1, exclude_reason=?, updated_at=datetime('now') WHERE sku=?",
            (reason, sku),
        )


def include_product(sku: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE products SET excluded=0, exclude_reason=NULL, updated_at=datetime('now') WHERE sku=?",
            (sku,),
        )


def update_last_content_date(sku: str, dt: str | None = None) -> None:
    dt = dt or datetime.utcnow().strftime("%Y-%m-%d")
    with _connect() as conn:
        conn.execute(
            "UPDATE products SET last_content_date=?, updated_at=datetime('now') WHERE sku=?",
            (dt, sku),
        )


def _row_to_product(row: sqlite3.Row) -> Product:
    return Product(
        sku=row["sku"], name=row["name"], category=row["category"],
        price=row["price"], product_url=row["product_url"],
        shopify_image_url=row["shopify_image_url"],
        image_dir=row["image_dir"],
        generation_ready=bool(row["generation_ready"]),
        active=bool(row["active"]), excluded=bool(row["excluded"]),
        exclude_reason=row["exclude_reason"],
        last_content_date=row["last_content_date"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Product Images
# ---------------------------------------------------------------------------

def insert_product_image(img: ProductImage) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO product_images (product_sku, file_path, image_type) VALUES (?,?,?)",
            (img.product_sku, img.file_path, img.image_type),
        )
        return cur.lastrowid  # type: ignore[return-value]


def list_product_images(sku: str) -> list[ProductImage]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM product_images WHERE product_sku=?", (sku,)
        ).fetchall()
    return [
        ProductImage(id=r["id"], product_sku=r["product_sku"],
                     file_path=r["file_path"], image_type=r["image_type"],
                     registered_at=r["registered_at"])
        for r in rows
    ]


def clear_product_images(sku: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM product_images WHERE product_sku=?", (sku,))


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------

def insert_content(c: Content) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO content
               (id, product_sku, theme, hook_type, hook_text,
                starting_image_prompt, scene_1_desc, scene_2_desc,
                scene_1_script, scene_2_script, video_local_path, approved,
                review_status, review_notes, approved_at, rejected_at,
                creative_format, cta_type, cta_text, problem_angle,
                proof_type, script_style, research_snapshot_id, asset_manifest_json,
                source_content_id, strategy_metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (c.id, c.product_sku, c.theme, c.hook_type, c.hook_text,
             c.starting_image_prompt, c.scene_1_desc, c.scene_2_desc,
             c.scene_1_script, c.scene_2_script, c.video_local_path,
             int(c.approved), c.review_status, c.review_notes,
             c.approved_at, c.rejected_at,
             c.creative_format, c.cta_type, c.cta_text, c.problem_angle,
             c.proof_type, c.script_style, c.research_snapshot_id, c.asset_manifest_json,
             getattr(c, "source_content_id", None),
             getattr(c, "strategy_metadata_json", None)),
        )


def get_content(content_id: str) -> Content | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM content WHERE id=?", (content_id,)).fetchone()
        if row:
            return _row_to_content(row)
        # Preview table shows id[:12]; allow prefix match when unique
        if content_id and len(content_id) <= 16 and content_id.isalnum():
            rows = conn.execute(
                "SELECT * FROM content WHERE id LIKE ?", (content_id + "%",)
            ).fetchall()
            if len(rows) == 1:
                return _row_to_content(rows[0])
    return None


def list_content_today() -> list[Content]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM content
               WHERE date(created_at, 'localtime') = date('now', 'localtime')
               ORDER BY created_at ASC"""
        ).fetchall()
    return [_row_to_content(r) for r in rows]


def list_content_last_24h() -> list[Content]:
    """Content created in the last 24 hours (rolling window, local time)."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM content
               WHERE datetime(created_at, 'localtime') >= datetime('now', 'localtime', '-24 hours')
               ORDER BY created_at DESC"""
        ).fetchall()
    return [_row_to_content(r) for r in rows]


def list_all_content() -> list[Content]:
    """Return all content, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM content
               ORDER BY created_at DESC"""
        ).fetchall()
    return [_row_to_content(r) for r in rows]


def approve_content(content_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE content
               SET approved=1,
                   review_status='approved',
                   approved_at=datetime('now'),
                   rejected_at=NULL
               WHERE id=?""",
            (content_id,),
        )


def approve_all_pending_content() -> int:
    """Set all content with review_status='pending' to 'approved'. Returns count updated."""
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE content
               SET approved=1,
                   review_status='approved',
                   approved_at=datetime('now'),
                   rejected_at=NULL
               WHERE review_status='pending'"""
        )
        return cur.rowcount


def reject_content(content_id: str, notes: str | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE content
               SET approved=0,
                   review_status='rejected',
                   review_notes=?,
                   approved_at=NULL,
                   rejected_at=datetime('now')
               WHERE id=?""",
            (notes, content_id),
        )


def reject_all_approved_content(notes: str | None = None) -> int:
    """Set all content with review_status='approved' to 'rejected'. Returns count updated."""
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE content
               SET approved=0,
                   review_status='rejected',
                   review_notes=?,
                   approved_at=NULL,
                   rejected_at=datetime('now')
               WHERE review_status='approved'""",
            (notes,),
        )
        return cur.rowcount


def update_content_video_path(content_id: str, path: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE content SET video_local_path=? WHERE id=?", (path, content_id)
        )


def update_content_asset_manifest(content_id: str, manifest_json: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE content SET asset_manifest_json=? WHERE id=?",
            (manifest_json, content_id),
        )


def list_content_for_product(sku: str, limit: int = 50) -> list[Content]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM content WHERE product_sku=? ORDER BY created_at DESC LIMIT ?",
            (sku, limit),
        ).fetchall()
    return [_row_to_content(r) for r in rows]


def _row_to_content(row: sqlite3.Row) -> Content:
    keys = row.keys()
    return Content(
        id=row["id"], product_sku=row["product_sku"], theme=row["theme"],
        hook_type=row["hook_type"], hook_text=row["hook_text"],
        starting_image_prompt=row["starting_image_prompt"],
        scene_1_desc=row["scene_1_desc"], scene_2_desc=row["scene_2_desc"],
        scene_1_script=row["scene_1_script"], scene_2_script=row["scene_2_script"],
        video_local_path=row["video_local_path"],
        approved=bool(row["approved"]),
        review_status=row["review_status"] or "pending",
        review_notes=row["review_notes"],
        approved_at=row["approved_at"],
        rejected_at=row["rejected_at"],
        created_at=row["created_at"],
        creative_format=row["creative_format"] or "ai_video_15s" if "creative_format" in keys else "ai_video_15s",
        cta_type=row["cta_type"] or "see_product" if "cta_type" in keys else "see_product",
        cta_text=row["cta_text"] if "cta_text" in keys else None,
        problem_angle=row["problem_angle"] if "problem_angle" in keys else None,
        proof_type=row["proof_type"] if "proof_type" in keys else None,
        script_style=row["script_style"] if "script_style" in keys else None,
        research_snapshot_id=row["research_snapshot_id"] if "research_snapshot_id" in keys else None,
        asset_manifest_json=row["asset_manifest_json"] if "asset_manifest_json" in keys else None,
        source_content_id=row["source_content_id"] if "source_content_id" in keys else None,
        strategy_metadata_json=row["strategy_metadata_json"] if "strategy_metadata_json" in keys else None,
    )


# ---------------------------------------------------------------------------
# Platform Payloads
# ---------------------------------------------------------------------------

def upsert_platform_payload(payload: PlatformPayload) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO platform_payloads
               (content_id, platform, caption, hashtags, utm_url, destination_url, utm_source, utm_medium, utm_campaign, utm_content, link_mode, publish_at, status, last_error, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(content_id, platform) DO UPDATE SET
                 caption=excluded.caption,
                 hashtags=excluded.hashtags,
                 utm_url=excluded.utm_url,
                 destination_url=excluded.destination_url,
                 utm_source=excluded.utm_source,
                 utm_medium=excluded.utm_medium,
                 utm_campaign=excluded.utm_campaign,
                 utm_content=excluded.utm_content,
                 link_mode=excluded.link_mode,
                 publish_at=excluded.publish_at,
                 status=excluded.status,
                 last_error=excluded.last_error,
                 updated_at=datetime('now')
               RETURNING id""",
            (
                payload.content_id,
                payload.platform,
                payload.caption,
                payload.hashtags,
                payload.utm_url,
                payload.destination_url,
                payload.utm_source,
                payload.utm_medium,
                payload.utm_campaign,
                payload.utm_content,
                payload.link_mode,
                payload.publish_at,
                payload.status,
                payload.last_error,
            ),
        )
        row = cur.fetchone()
        return int(row["id"])


def get_platform_payload(content_id: str, platform: str) -> PlatformPayload | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM platform_payloads WHERE content_id=? AND platform=?",
            (content_id, platform),
        ).fetchone()
    return _row_to_platform_payload(row) if row else None


def list_platform_payloads(content_id: str) -> list[PlatformPayload]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM platform_payloads WHERE content_id=? ORDER BY platform",
            (content_id,),
        ).fetchall()
    return [_row_to_platform_payload(row) for row in rows]


def list_due_platform_payloads(now_iso: str | None = None) -> list[PlatformPayload]:
    with _connect() as conn:
        if now_iso:
            rows = conn.execute(
                """SELECT * FROM platform_payloads
                   WHERE publish_at IS NOT NULL
                     AND publish_at <= ?
                     AND status IN ('pending', 'scheduled')
                   ORDER BY publish_at ASC, id ASC""",
                (now_iso,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM platform_payloads
                   WHERE publish_at IS NOT NULL
                     AND publish_at <= datetime('now')
                     AND status IN ('pending', 'scheduled')
                   ORDER BY publish_at ASC, id ASC"""
            ).fetchall()
    return [_row_to_platform_payload(row) for row in rows]


def update_platform_payload_status(
    payload_id: int,
    status: str,
    last_error: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """UPDATE platform_payloads
               SET status=?,
                   last_error=?,
                   updated_at=datetime('now')
               WHERE id=?""",
            (status, last_error, payload_id),
        )


def mark_platform_payload_delivery(payload_id: int, remote_post_id: str) -> str:
    remote_post_id = remote_post_id.strip()
    if not remote_post_id:
        raise ValueError("remote_post_id must not be empty")

    status = "submitted" if remote_post_id.startswith("make:") else "posted"
    update_platform_payload_status(payload_id, status, None)
    return status


def _row_to_platform_payload(row: sqlite3.Row) -> PlatformPayload:
    return PlatformPayload(
        id=row["id"],
        content_id=row["content_id"],
        platform=row["platform"],
        caption=row["caption"],
        hashtags=row["hashtags"],
        utm_url=row["utm_url"],
        destination_url=row["destination_url"],
        utm_source=row["utm_source"],
        utm_medium=row["utm_medium"],
        utm_campaign=row["utm_campaign"],
        utm_content=row["utm_content"],
        link_mode=row["link_mode"] or "direct",
        publish_at=row["publish_at"],
        status=row["status"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

def insert_post(p: Post) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO posts (content_id, platform, post_id, caption, hashtags, utm_url, destination_url, utm_source, utm_medium, utm_campaign, utm_content, link_mode)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p.content_id, p.platform, p.post_id, p.caption, p.hashtags, p.utm_url, p.destination_url, p.utm_source, p.utm_medium, p.utm_campaign, p.utm_content, p.link_mode),
        )
        return cur.lastrowid  # type: ignore[return-value]


def get_post(post_id: int) -> Post | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    return _row_to_post(row) if row else None


def find_post_by_platform_remote_id(platform: str, remote_post_id: str) -> Post | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM posts WHERE platform=? AND post_id=? ORDER BY id DESC LIMIT 1",
            (platform, remote_post_id),
        ).fetchone()
    return _row_to_post(row) if row else None


def list_posts_for_content(content_id: str) -> list[Post]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE content_id=?", (content_id,)
        ).fetchall()
    return [_row_to_post(r) for r in rows]


def list_recent_posts(days: int = 30) -> list[Post]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM posts WHERE published_at >= datetime('now', ?)",
            (f"-{days} days",),
        ).fetchall()
    return [_row_to_post(r) for r in rows]


def sync_instagram_post_id(
    instagram_post_id: str,
    *,
    handoff_id: str | None = None,
    content_id: str | None = None,
) -> int:
    instagram_post_id = instagram_post_id.strip()
    if not instagram_post_id:
        return 0

    with _connect() as conn:
        target_row: sqlite3.Row | None = None

        if handoff_id:
            target_row = conn.execute(
                """SELECT id, post_id, content_id
                   FROM posts
                   WHERE platform='instagram' AND post_id=?""",
                (handoff_id,),
            ).fetchone()

        if target_row is None and content_id:
            rows = conn.execute(
                """SELECT id, post_id, content_id
                   FROM posts
                   WHERE platform='instagram' AND content_id=?
                   ORDER BY id DESC""",
                (content_id,),
            ).fetchall()
            exact_match = next(
                (row for row in rows if (row["post_id"] or "").strip() == instagram_post_id),
                None,
            )
            if exact_match is not None:
                return 0

            make_rows = [row for row in rows if (row["post_id"] or "").startswith("make:")]
            if len(make_rows) == 1:
                target_row = make_rows[0]

        if target_row is None:
            return 0

        if (target_row["post_id"] or "").strip() == instagram_post_id:
            return 0

        conn.execute(
            "UPDATE posts SET post_id=? WHERE id=?",
            (instagram_post_id, target_row["id"]),
        )
        conn.execute(
            """UPDATE platform_payloads
               SET status='posted',
                   last_error=NULL,
                   updated_at=datetime('now')
               WHERE content_id=?
                 AND platform='instagram'""",
            (target_row["content_id"],),
        )
        return 1


def _row_to_post(row: sqlite3.Row) -> Post:
    return Post(
        id=row["id"], content_id=row["content_id"], platform=row["platform"],
        post_id=row["post_id"], caption=row["caption"],
        hashtags=row["hashtags"], utm_url=row["utm_url"],
        destination_url=row["destination_url"], utm_source=row["utm_source"],
        utm_medium=row["utm_medium"], utm_campaign=row["utm_campaign"],
        utm_content=row["utm_content"], link_mode=row["link_mode"] or "direct",
        published_at=row["published_at"],
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def insert_metric(m: Metric) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO metrics
               (post_id, platform, views, likes, shares, comments, saves,
                watch_through_rate, avg_watch_time)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (m.post_id, m.platform, m.views, m.likes, m.shares,
             m.comments, m.saves, m.watch_through_rate, m.avg_watch_time),
        )
        return cur.lastrowid  # type: ignore[return-value]


def latest_metrics_for_post(post_id: int) -> Metric | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM metrics WHERE post_id=? ORDER BY pulled_at DESC, id DESC LIMIT 1",
            (post_id,),
        ).fetchone()
    if not row:
        return None
    return Metric(
        id=row["id"], post_id=row["post_id"], platform=row["platform"],
        views=row["views"], likes=row["likes"], shares=row["shares"],
        comments=row["comments"], saves=row["saves"],
        watch_through_rate=row["watch_through_rate"],
        avg_watch_time=row["avg_watch_time"], pulled_at=row["pulled_at"],
    )


def list_metrics_since(days: int = 30) -> list[Metric]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM metrics WHERE pulled_at >= datetime('now', ?)",
            (f"-{days} days",),
        ).fetchall()
    return [
        Metric(
            id=r["id"], post_id=r["post_id"], platform=r["platform"],
            views=r["views"], likes=r["likes"], shares=r["shares"],
            comments=r["comments"], saves=r["saves"],
            watch_through_rate=r["watch_through_rate"],
            avg_watch_time=r["avg_watch_time"], pulled_at=r["pulled_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Bandit State
# ---------------------------------------------------------------------------

def upsert_bandit_arm(arm: BanditArm) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO bandit_state (arm_key, theme, hook_type, alpha, beta)
               VALUES (?,?,?,?,?)
               ON CONFLICT(arm_key) DO UPDATE SET
                 theme=excluded.theme,
                 hook_type=excluded.hook_type,
                 alpha=excluded.alpha,
                 beta=excluded.beta,
                 last_updated=datetime('now')""",
            (arm.arm_key, arm.theme, arm.hook_type, arm.alpha, arm.beta),
        )


def seed_bandit_arms(arms: list[BanditArm]) -> None:
    for arm in arms:
        upsert_bandit_arm(arm)


def get_bandit_arm(arm_key: str) -> BanditArm | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM bandit_state WHERE arm_key=?",
            (arm_key,),
        ).fetchone()
    if not row:
        return None
    keys = row.keys()
    return BanditArm(
        arm_key=row["arm_key"],
        theme=row["theme"],
        hook_type=row["hook_type"],
        alpha=row["alpha"],
        beta=row["beta"],
        last_updated=row["last_updated"],
    )


def list_bandit_arms() -> list[BanditArm]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bandit_state ORDER BY theme, hook_type"
        ).fetchall()
    return [
        BanditArm(
            arm_key=row["arm_key"],
            theme=row["theme"],
            hook_type=row["hook_type"],
            alpha=row["alpha"],
            beta=row["beta"],
            last_updated=row["last_updated"],
        )
        for row in rows
    ]


def get_bandit_arms(product_sku: str | None = None) -> list[BanditArm]:
    return list_bandit_arms()


def increment_bandit(arm_key: str, success: bool) -> None:
    arm = get_bandit_arm(arm_key)
    if arm is None:
        raise ValueError(f"Bandit arm not found: {arm_key}")

    col = "alpha" if success else "beta"
    with _connect() as conn:
        conn.execute(
            f"""UPDATE bandit_state
                SET {col}={col}+1,
                    last_updated=datetime('now')
                WHERE arm_key=?""",
            (arm_key,),
        )
    

def has_bandit_observation_for_content(content_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM bandit_observations WHERE content_id=?",
            (content_id,),
        ).fetchone()
    return row is not None


def get_bandit_observation_for_content(content_id: str) -> BanditObservation | None:
    """Return the bandit observation for a content item, if any."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM bandit_observations WHERE content_id=?",
            (content_id,),
        ).fetchone()
    if not row:
        return None
    keys = row.keys()
    return BanditObservation(
        id=row["id"],
        content_id=row["content_id"],
        product_sku=row["product_sku"],
        arm_key=row["arm_key"],
        theme=row["theme"],
        hook_type=row["hook_type"],
        aggregated_engagement_rate=float(row["aggregated_engagement_rate"]),
        success=bool(row["success"]),
        observed_at=row["observed_at"],
    )


def insert_bandit_observation(observation: BanditObservation) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO bandit_observations
               (content_id, product_sku, arm_key, theme, hook_type, aggregated_engagement_rate, success)
               VALUES (?,?,?,?,?,?,?)""",
            (
                observation.content_id,
                observation.product_sku,
                observation.arm_key,
                observation.theme,
                observation.hook_type,
                observation.aggregated_engagement_rate,
                int(observation.success),
            ),
        )
        return cur.lastrowid  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------

def insert_cost(c: Cost) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO costs (content_id, step, api_provider, tokens_or_units, cost_usd)
               VALUES (?,?,?,?,?)""",
            (c.content_id, c.step, c.api_provider, c.tokens_or_units, c.cost_usd),
        )
        return cur.lastrowid  # type: ignore[return-value]


def total_cost_today() -> float:
    with _connect() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(cost_usd), 0) AS total
               FROM costs
               WHERE date(created_at, 'localtime') = date('now', 'localtime')"""
        ).fetchone()
    return float(row["total"])


def costs_for_content(content_id: str) -> list[Cost]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM costs WHERE content_id=?", (content_id,)
        ).fetchall()
    return [
        Cost(
            id=r["id"], content_id=r["content_id"], step=r["step"],
            api_provider=r["api_provider"], tokens_or_units=r["tokens_or_units"],
            cost_usd=r["cost_usd"], created_at=r["created_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Commerce Facts (Phase 6)
# ---------------------------------------------------------------------------

def upsert_commerce_fact(fact: CommerceFact) -> int:
    """Insert or replace commerce fact. Idempotent on (content_id, platform, event_date)."""
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO commerce_facts
               (content_id, platform, event_date, sessions, add_to_cart, checkout_started,
                purchases, revenue, source, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(content_id, platform, event_date) DO UPDATE SET
                 sessions=excluded.sessions,
                 add_to_cart=excluded.add_to_cart,
                 checkout_started=excluded.checkout_started,
                 purchases=excluded.purchases,
                 revenue=excluded.revenue,
                 source=excluded.source,
                 ingested_at=datetime('now')
               RETURNING id""",
            (
                fact.content_id,
                fact.platform,
                fact.event_date,
                fact.sessions,
                fact.add_to_cart,
                fact.checkout_started,
                fact.purchases,
                fact.revenue,
                fact.source,
            ),
        )
        row = cur.fetchone()
        return int(row["id"])


def aggregate_commerce_for_content(
    content_id: str,
    days: int = 30,
    start_date: str | None = None,
    end_date: str | None = None,
    platform: str | None = None,
) -> dict[str, int | float]:
    """Sum sessions, add_to_cart, checkout_started, purchases, revenue for a content item."""
    clauses = ["content_id=?"]
    params: list = [content_id]
    if platform:
        clauses.append("platform=?")
        params.append(platform)
    if days > 0:
        clauses.append("event_date >= date('now', ?)")
        params.append(f"-{days} days")
    if start_date:
        clauses.append("event_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("event_date <= ?")
        params.append(end_date)
    where = " AND ".join(clauses)
    with _connect() as conn:
        row = conn.execute(
            f"""SELECT
                 COALESCE(SUM(sessions), 0) AS sessions,
                 COALESCE(SUM(add_to_cart), 0) AS add_to_cart,
                 COALESCE(SUM(checkout_started), 0) AS checkout_started,
                 COALESCE(SUM(purchases), 0) AS purchases,
                 COALESCE(SUM(revenue), 0) AS revenue
               FROM commerce_facts
               WHERE {where}""",
            params,
        ).fetchone()
    return {
        "sessions": int(row["sessions"] or 0),
        "add_to_cart": int(row["add_to_cart"] or 0),
        "checkout_started": int(row["checkout_started"] or 0),
        "purchases": int(row["purchases"] or 0),
        "revenue": float(row["revenue"] or 0),
    }


def list_commerce_facts_since(days: int = 30) -> list[CommerceFact]:
    """List commerce facts in the last N days."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM commerce_facts
               WHERE event_date >= date('now', ?)
               ORDER BY event_date DESC, content_id, platform""",
            (f"-{days} days",),
        ).fetchall()
    return [_row_to_commerce_fact(r) for r in rows]


def _row_to_commerce_fact(row: sqlite3.Row) -> CommerceFact:
    return CommerceFact(
        id=row["id"],
        content_id=row["content_id"],
        platform=row["platform"],
        event_date=row["event_date"],
        sessions=row["sessions"] or 0,
        add_to_cart=row["add_to_cart"] or 0,
        checkout_started=row["checkout_started"] or 0,
        purchases=row["purchases"] or 0,
        revenue=float(row["revenue"] or 0),
        source=row["source"] or "shopify_import",
        ingested_at=row["ingested_at"],
    )


# ---------------------------------------------------------------------------
# Research Snapshots (Phase 3)
# ---------------------------------------------------------------------------

def insert_research_snapshot(snap: ResearchSnapshot) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO research_snapshots
               (id, product_sku, platform, creative_format, summary, source_type)
               VALUES (?,?,?,?,?,?)""",
            (
                snap.id,
                snap.product_sku,
                snap.platform,
                snap.creative_format,
                snap.summary,
                snap.source_type,
            ),
        )


def get_research_snapshot(snapshot_id: str) -> ResearchSnapshot | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM research_snapshots WHERE id=?", (snapshot_id,)
        ).fetchone()
    return _row_to_research_snapshot(row) if row else None


def list_research_snapshots(
    product_sku: str | None = None,
    platform: str | None = None,
    creative_format: str | None = None,
    limit: int = 50,
) -> list[ResearchSnapshot]:
    clauses = []
    params: list[str | None] = []
    if product_sku is not None:
        clauses.append("(product_sku IS NULL OR product_sku = ?)")
        params.append(product_sku)
    if platform is not None:
        clauses.append("(platform IS NULL OR platform = ?)")
        params.append(platform)
    if creative_format is not None:
        clauses.append("(creative_format IS NULL OR creative_format = ?)")
        params.append(creative_format)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT * FROM research_snapshots {where}
                ORDER BY created_at DESC LIMIT ?""",
            params,
        ).fetchall()
    return [_row_to_research_snapshot(r) for r in rows]


def get_best_matching_snapshot(
    product_sku: str,
    platform: str | None = None,
    creative_format: str = "ai_video_15s",
) -> ResearchSnapshot | None:
    """Retrieve the best matching snapshot by product, platform, and format.

    Precedence: product+platform+format > product+platform > product+format > product only.
    Within each tier, most recent wins.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM research_snapshots
               WHERE (product_sku IS NULL OR product_sku = ?)
                 AND (platform IS NULL OR platform = ? OR ? IS NULL)
                 AND (creative_format IS NULL OR creative_format = ?)
               ORDER BY
                 (CASE WHEN product_sku = ? THEN 1 ELSE 0 END +
                  CASE WHEN platform IS NOT NULL AND platform = ? THEN 1 ELSE 0 END +
                  CASE WHEN creative_format = ? THEN 1 ELSE 0 END) DESC,
                 created_at DESC
               LIMIT 1""",
            (product_sku, platform, platform, creative_format, product_sku, platform, creative_format),
        ).fetchall()
    return _row_to_research_snapshot(rows[0]) if rows else None


def _row_to_research_snapshot(row: sqlite3.Row) -> ResearchSnapshot:
    return ResearchSnapshot(
        id=row["id"],
        product_sku=row["product_sku"],
        platform=row["platform"],
        creative_format=row["creative_format"],
        summary=row["summary"],
        source_type=row["source_type"] or "manual",
        created_at=row["created_at"],
    )
