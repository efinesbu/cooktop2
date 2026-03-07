CREATE TABLE IF NOT EXISTS products (
    sku             TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    price           REAL,
    product_url     TEXT,
    shopify_image_url TEXT,
    image_dir       TEXT,
    generation_ready INTEGER DEFAULT 0,
    active          INTEGER DEFAULT 1,
    excluded        INTEGER DEFAULT 0,
    exclude_reason  TEXT,
    last_content_date TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS product_images (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_sku     TEXT NOT NULL REFERENCES products(sku),
    file_path       TEXT NOT NULL,
    image_type      TEXT NOT NULL CHECK(image_type IN ('hero', 'lifestyle', 'detail')),
    registered_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS content (
    id                    TEXT PRIMARY KEY,
    product_sku           TEXT NOT NULL REFERENCES products(sku),
    theme                 TEXT NOT NULL,
    hook_type             TEXT NOT NULL,
    hook_text             TEXT,
    starting_image_prompt TEXT,
    scene_1_desc          TEXT,
    scene_2_desc          TEXT,
    scene_1_script        TEXT,
    scene_2_script        TEXT,
    video_local_path      TEXT,
    approved              INTEGER DEFAULT 0,
    review_status         TEXT DEFAULT 'pending',
    review_notes          TEXT,
    approved_at           TEXT,
    rejected_at           TEXT,
    created_at            TEXT DEFAULT (datetime('now'))
);

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

CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id      TEXT NOT NULL REFERENCES content(id),
    platform        TEXT NOT NULL,
    post_id         TEXT,
    caption         TEXT,
    hashtags        TEXT,
    utm_url         TEXT,
    published_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS metrics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id             INTEGER NOT NULL REFERENCES posts(id),
    platform            TEXT NOT NULL,
    views               INTEGER DEFAULT 0,
    likes               INTEGER DEFAULT 0,
    shares              INTEGER DEFAULT 0,
    comments            INTEGER DEFAULT 0,
    saves               INTEGER DEFAULT 0,
    watch_through_rate  REAL,
    avg_watch_time      REAL,
    pulled_at           TEXT DEFAULT (datetime('now'))
);

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

CREATE TABLE IF NOT EXISTS costs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id      TEXT NOT NULL REFERENCES content(id),
    step            TEXT NOT NULL CHECK(step IN ('prompt_gen', 'image_gen', 'video_gen')),
    api_provider    TEXT NOT NULL,
    tokens_or_units INTEGER,
    cost_usd        REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_content_product    ON content(product_sku);
CREATE INDEX IF NOT EXISTS idx_content_created    ON content(created_at);
CREATE INDEX IF NOT EXISTS idx_content_review     ON content(review_status);
CREATE INDEX IF NOT EXISTS idx_payloads_content   ON platform_payloads(content_id);
CREATE INDEX IF NOT EXISTS idx_payloads_publish   ON platform_payloads(publish_at);
CREATE INDEX IF NOT EXISTS idx_payloads_status    ON platform_payloads(status);
CREATE INDEX IF NOT EXISTS idx_posts_content      ON posts(content_id);
CREATE INDEX IF NOT EXISTS idx_posts_platform     ON posts(platform);
CREATE INDEX IF NOT EXISTS idx_metrics_post       ON metrics(post_id);
CREATE INDEX IF NOT EXISTS idx_metrics_pulled     ON metrics(pulled_at);
CREATE INDEX IF NOT EXISTS idx_bandit_state_theme ON bandit_state(theme, hook_type);
CREATE INDEX IF NOT EXISTS idx_bandit_obs_arm     ON bandit_observations(arm_key);
CREATE INDEX IF NOT EXISTS idx_bandit_obs_product ON bandit_observations(product_sku);
CREATE INDEX IF NOT EXISTS idx_costs_content      ON costs(content_id);
CREATE INDEX IF NOT EXISTS idx_product_images_sku ON product_images(product_sku);
