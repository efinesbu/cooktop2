from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from src import config
from src.models import (
    Product, ProductImage, Content, PlatformPayload, Post, Metric, BanditArm,
    BanditObservation, Cost,
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
    ):
        if name not in content_columns:
            conn.execute(ddl)

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
                review_status, review_notes, approved_at, rejected_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (c.id, c.product_sku, c.theme, c.hook_type, c.hook_text,
             c.starting_image_prompt, c.scene_1_desc, c.scene_2_desc,
             c.scene_1_script, c.scene_2_script, c.video_local_path,
             int(c.approved), c.review_status, c.review_notes,
             c.approved_at, c.rejected_at),
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


def update_content_video_path(content_id: str, path: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE content SET video_local_path=? WHERE id=?", (path, content_id)
        )


def list_content_for_product(sku: str, limit: int = 50) -> list[Content]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM content WHERE product_sku=? ORDER BY created_at DESC LIMIT ?",
            (sku, limit),
        ).fetchall()
    return [_row_to_content(r) for r in rows]


def _row_to_content(row: sqlite3.Row) -> Content:
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
    )


# ---------------------------------------------------------------------------
# Platform Payloads
# ---------------------------------------------------------------------------

def upsert_platform_payload(payload: PlatformPayload) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO platform_payloads
               (content_id, platform, caption, hashtags, utm_url, publish_at, status, last_error, updated_at)
               VALUES (?,?,?,?,?,?,?,?, datetime('now'))
               ON CONFLICT(content_id, platform) DO UPDATE SET
                 caption=excluded.caption,
                 hashtags=excluded.hashtags,
                 utm_url=excluded.utm_url,
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


def _row_to_platform_payload(row: sqlite3.Row) -> PlatformPayload:
    return PlatformPayload(
        id=row["id"],
        content_id=row["content_id"],
        platform=row["platform"],
        caption=row["caption"],
        hashtags=row["hashtags"],
        utm_url=row["utm_url"],
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
            """INSERT INTO posts (content_id, platform, post_id, caption, hashtags, utm_url)
               VALUES (?,?,?,?,?,?)""",
            (p.content_id, p.platform, p.post_id, p.caption, p.hashtags, p.utm_url),
        )
        return cur.lastrowid  # type: ignore[return-value]


def get_post(post_id: int) -> Post | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
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


def _row_to_post(row: sqlite3.Row) -> Post:
    return Post(
        id=row["id"], content_id=row["content_id"], platform=row["platform"],
        post_id=row["post_id"], caption=row["caption"],
        hashtags=row["hashtags"], utm_url=row["utm_url"],
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
