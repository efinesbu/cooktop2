# Velura Content Automation System

Human-in-the-loop content automation for short-form video ads. Generates 15-second product videos using AI (OpenAI + Gemini + xAI), posts to YouTube, Instagram, TikTok, and X with UTM attribution, pulls analytics, and optimizes theme/hook allocation via Thompson Sampling.

## Quick Start

```bash
# 1. Clone and enter the repo
git clone <repo-url> && cd velura-content-automation

# 2. Create virtual environment
python -m venv .venv

# 3. Activate
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Configure
cp config.example.yaml config.yaml
cp .env.example .env
# Put API keys and tokens in .env (never commit .env). Env vars override config.yaml.
# Fill in config.yaml for non-secret settings. Set `platforms.enabled` to the platforms you want.

# 6. Add a product to the local catalog
python cli.py add-product --sku <product-slug> --name "Product Name" --url https://your-site.com/products/<handle>

# 7. Register product images
python cli.py register-images --product <product-slug>

# 8. Optional: sync from Shopify instead of adding products manually
python cli.py sync-products
```

## Daily Workflow

```
8:00 AM   pull-analytics + morning-briefing (cron)
          Operator reviews briefing
          Operator runs: python cli.py run --auto --count 8
          Preview: python cli.py preview --today
          Approve: python cli.py approve --content-id <id>
          Schedule: python cli.py schedule --today
          Publish due posts: python cli.py post-due
8:00 PM   pull-analytics (cron)
```

## CLI Commands

| Command | Purpose |
|---------|---------|
| `add-product --sku SLUG --name "Name"` | Create or update a product in the local catalog |
| `sync-products` | Pull product catalog from Shopify if configured |
| `register-images --product SLUG` | Scan and register local product images |
| `morning-briefing` | Generate daily performance + recommendation report |
| `run --auto --count N` | Generate N total clips across eligible products using the shared bandit allocation |
| `run --product SLUG --count N` | Generate N clips using shared bandit theme/hook recommendations (starter arms) |
| `run --product SLUG --theme T --hook H --count N` | Generate with manual strategy overrides |
| `run --product SLUG --theme T... --hook H... --count N --rotate-theme-hook` | Cycle through provided manual theme/hook overrides once per clip |
| `exclude --product SLUG --reason "..."` | Exclude product from generation |
| `include --product SLUG` | Re-include excluded product |
| `preview --today` | Review today's generated content |
| `approve --content-id ID` | Approve generated content for scheduling/posting |
| `reject --content-id ID --reason "..."` | Reject generated content with review notes |
| `schedule --today` | Schedule approved content using staggered platform offsets |
| `schedule --content-id ID` | Schedule one approved content item |
| `post-due` | Publish payloads whose `publish_at` is due |
| `post --today` | Convenience wrapper: schedule approved content, then publish due payloads |
| `post --content-id ID` | Manually post a specific content piece |
| `pull-analytics` | Pull metrics from all platforms |
| `report --product SLUG` | Product performance report |
| `archive` | Archive old videos to GCS |

## Architecture

```
Python CLI (local machine)
├── Prompt Generation ─── OpenAI API
├── Image Generation ──── Gemini API
├── Video Generation ──── xAI API
├── Thompson Sampling ─── Bandit optimizer
├── Social Posting ────── YT / IG / TT / X APIs
├── Analytics Pull ────── Platform analytics APIs
├── Morning Briefing ──── Rich terminal / email
└── SQLite DB + Local video storage
```

## Creative Strategy Selection

Velura now treats `theme` and `hook_type` as real creative strategy labels instead of pure CLI metadata.

- If you run `python cli.py run --product <slug> --count N` without `--theme` or `--hook`, the shared bandit recommends theme/hook pairs from the starter arms; the prompt generator then fills in the creative details.
- If you pass `--theme` or `--hook`, those values become locked overrides and the generator fills in the rest.
- If you also pass `--rotate-theme-hook`, manual runs cycle through the provided `--theme` and `--hook` values clip-by-clip when `count > 1` instead of repeating the same locked pair.
- `run --auto` now uses one shared global bandit across eligible products, allocates a daily total number of clips, and learns once per creative instead of once per platform post.
- Reporting, UTM campaign naming, morning briefing recommendations, and bandit learning all use the persisted labels returned by generation.

Current curated whitelist:

- Themes: `benefit`, `problem_solution`, `curiosity`, `social_proof`, `routine`, `urgency`, `fear`
- Hook types: `question`, `bold_claim`, `relatable_pain`, `visual_surprise`, `quick_tip`

Starter shared bandit arms:

- `problem_solution__relatable_pain`
- `benefit__bold_claim`
- `curiosity__question`
- `benefit__visual_surprise`

## Project Structure

```
├── cli.py                    # Main CLI entry point
├── config.example.yaml       # Configuration template
├── requirements.txt          # Pinned dependencies
├── db/
│   └── schema.sql            # SQLite schema
├── src/
│   ├── config.py             # Config loading
│   ├── models.py             # Data models
│   ├── db.py                 # Database operations
│   ├── shopify.py            # Shopify product sync
│   ├── product_images.py     # Local image registration
│   ├── storage.py            # File storage + GCS archival
│   ├── bandit.py             # Thompson Sampling optimizer
│   ├── prompt_generator.py   # OpenAI-powered script generation
│   ├── image_generator.py    # Gemini-powered image generation
│   ├── video_generator.py    # xAI-powered video generation
│   ├── cost_tracker.py       # API cost tracking + budget
│   ├── utm.py                # UTM link builder
│   ├── morning_briefing.py   # Daily briefing generator
│   ├── posters/              # Platform-specific uploaders
│   │   ├── base.py, youtube.py, instagram.py, tiktok.py, x.py
│   └── analytics/            # Platform-specific metrics pullers
│       ├── base.py, youtube.py, instagram.py, tiktok.py, x.py
└── tests/                    # Test suite
```

## Configuration

Copy `config.example.yaml` to `config.yaml` and fill in:

- **Site URL** — public storefront base URL used to build product links and UTMs
- **Platforms** — optional `platforms.enabled` list to limit the workflow to only the platforms you are ready to test, e.g. `["youtube"]`
- **Shopify** — optional store URL + Admin API token if you want automatic product sync
- **OpenAI** — API key + model for script generation
- **Gemini** — Google AI API key, optional `gemini.aspect_ratio` (default `9:16` for vertical short-form)
- **xAI** — Video generation API key, optional `xai.model` (default `grok-imagine-video`), optional `xai.resolution`/`xai.aspect_ratio` (default `9:16` for vertical short-form), and polling controls via `xai.poll_interval_seconds` and `xai.poll_timeout_seconds`
- **YouTube** — Google OAuth Desktop app client secrets JSON for posting, optional `youtube.token_file` for the cached login token, optional `youtube.login_hint` to suggest the correct Google account during auth, plus `youtube.api_key` for analytics pulls
- **Instagram** — Graph API access token + account ID; set `instagram.gcs_bucket` if you want automatic public video hosting for Reels
- **TikTok** — `tiktok.client_key`, `tiktok.client_secret`, `tiktok.access_token`, and `tiktok.refresh_token` for posting; Content Posting API approval is required. Analytics only need the client key + secret. The separate review demo uses `tiktok-sandbox.*` settings.
- **Bandit** — optional shared-bandit controls for `bandit.daily_slots`, `bandit.min_top_k`, `bandit.allocation_ceiling`, `bandit.expand_after_creatives`, and `bandit.starter_arms`; used by `run --auto` and `run --product SLUG` (when no `--theme`/`--hook` is given)
- **X** — API key/secret + access token/secret (Basic tier for posting)
- **Make bridge** — optional `make_bridge.webhook_url` plus `make_bridge.r2.account_id`, `make_bridge.r2.access_key_id`, `make_bridge.r2.secret_access_key`, and `make_bridge.r2.bucket_name` if you want to upload finished `.mp4` files to Cloudflare R2 and forward a presigned URL to Make.com
- **GCS** — Bucket name + service account credentials (optional, for archival)
- **Briefing email** — optional SMTP settings if you want `morning-briefing` emailed

If you are managing inventory manually, use `add-product` to maintain your catalog and `exclude --product SLUG --reason "out of stock"` to keep a product out of generation or posting rotation.

If `platforms.enabled` is omitted, Velura auto-enables only the platforms that have the required credentials present. Missing Instagram, TikTok, or X credentials will no longer block a YouTube-only workflow.

For YouTube posting, `youtube.client_secrets_file` must be a Google Cloud OAuth client JSON for a `Desktop app`. A `Web application` client will fail with `Error 400: redirect_uri_mismatch` because Velura uses a localhost callback during the installed-app auth flow.

The upload goes to whichever Google/YouTube account completes the OAuth browser flow when `youtube_token.json` is created. Set `youtube.login_hint` if you want Google to prefill the intended account, and delete the token file before retrying if you need to switch accounts.

xAI video generation now uses the async `POST /v1/videos/generations` flow and polls `GET /v1/videos/{request_id}` until the video is ready. The default poll interval is 15 seconds, and you can change it with `xai.poll_interval_seconds` in `config.yaml`.

## Make.com Upload Bridge

Use `upload_to_make.py` when a generated video has already been approved, scheduled, and posted in your workflow and you want to hand the final `.mp4` off to a Make.com scenario for Instagram follow-up.

The script:

- uploads the local `.mp4` to Cloudflare R2 with `boto3`
- generates a 30-minute presigned URL for that object
- sends `{"video_url": "...", "caption": "..."}` to your Make.com webhook with `requests`

Configuration can come from either a local `.env` file or `config.yaml`. Environment variables take precedence:

```env
R2_ACCOUNT_ID=YOUR_CLOUDFLARE_R2_ACCOUNT_ID
R2_ACCESS_KEY_ID=YOUR_CLOUDFLARE_R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY=YOUR_CLOUDFLARE_R2_SECRET_ACCESS_KEY
R2_BUCKET_NAME=your-r2-bucket-name
MAKE_WEBHOOK_URL=https://hook.us2.make.com/your-webhook-id
```

Equivalent `config.yaml` block:

```yaml
make_bridge:
  webhook_url: "https://hook.us2.make.com/your-webhook-id"
  r2:
    account_id: "YOUR_CLOUDFLARE_R2_ACCOUNT_ID"
    access_key_id: "YOUR_CLOUDFLARE_R2_ACCESS_KEY_ID"
    secret_access_key: "YOUR_CLOUDFLARE_R2_SECRET_ACCESS_KEY"
    bucket_name: "your-r2-bucket-name"
```

Run it with:

```bash
python upload_to_make.py --video path/to/video.mp4 --caption "Your Instagram caption"
```

Optional:

- pass `--object-key` if you want to control the R2 path instead of using the default timestamped key
- pass `--config path/to/config.yaml` if your config file is not at the repo root

## TikTok Setup

Velura has two separate TikTok paths:

- **Main Velura posting/analytics** uses the `tiktok` config block and the code under `src/posters/tiktok.py` and `src/analytics/tiktok.py`.
- **TikTok app review demo** uses the standalone FastAPI app in `tiktok-demo/` and the `tiktok-sandbox` config block.

### Main Velura TikTok config

Use the `tiktok` block in `config.yaml` when you want Velura itself to post or pull TikTok analytics:

```yaml
tiktok:
  client_key: "YOUR_PRODUCTION_CLIENT_KEY"
  client_secret: "YOUR_PRODUCTION_CLIENT_SECRET"
  access_token: "YOUR_USER_ACCESS_TOKEN"
  refresh_token: "YOUR_USER_REFRESH_TOKEN"
```

Notes:

- Posting requires all four values above because the poster uploads with the current access token and refreshes it when TikTok returns `401`.
- Analytics only require `tiktok.client_key` and `tiktok.client_secret`, but leaving the full block populated is the simplest setup if you plan to post.
- If `platforms.enabled` is omitted, TikTok posting is only auto-enabled when the required posting keys are present.

### TikTok review demo

Use the standalone app in `tiktok-demo/` when TikTok approvers need a public site to log into and test:

```yaml
tiktok-sandbox:
  client_key: "YOUR_SANDBOX_OR_REVIEW_APP_CLIENT_KEY"
  client_secret: "YOUR_SANDBOX_OR_REVIEW_APP_CLIENT_SECRET"
  redirect_uri: "https://demo.veluraesthetics.com/callback"
```

For the full runbook, see `tiktok-demo/README.md`.

## Data Storage

- **Database:** `db/velura.db` (SQLite, auto-created)
- **Videos:** `~/.velura/videos/{product-sku}/{content-id}.mp4`
- **Product images:** `~/.velura/product-images/{product-slug}/`

On Windows, `~` resolves to `%USERPROFILE%`.

## Testing

```bash
pytest tests/ -v
```

## Scheduling

### Windows (Task Scheduler)

Create two tasks:
- **Morning (8:00 AM):** `.venv\Scripts\python cli.py pull-analytics` then `cli.py morning-briefing`
- **Evening (8:00 PM):** `.venv\Scripts\python cli.py pull-analytics`
- **Optional publish worker (every 15 minutes):** `.venv\Scripts\python cli.py post-due`

### Linux/macOS (crontab)

```cron
0 7 * * * cd /path/to/repo && .venv/bin/python cli.py pull-analytics && .venv/bin/python cli.py morning-briefing
0 19 * * * cd /path/to/repo && .venv/bin/python cli.py pull-analytics
*/15 * * * * cd /path/to/repo && .venv/bin/python cli.py post-due
```
