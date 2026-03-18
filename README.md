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
# `openai.model` is used for content and paid-variant generation; keep `openai.voiceover_model` on `gpt-4.1` for image-motion voiceover planning.

# 6. Add a product to the local catalog
python cli.py add-product --sku <product-slug> --name "Product Name" --url https://your-site.com/products/<handle>

# 7. Register product images
python cli.py register-images --product <product-slug>

# 8. Optional: sync from Shopify instead of adding products manually
python cli.py sync-products
```

## Daily Workflow

```
8:00 AM   pull-analytics + report (cron)
          Operator reviews briefing
          Operator runs: python cli.py run --auto --count 8
          Preview: python cli.py preview --today
          Approve: python cli.py approve --content-id <id>
          Schedule: python cli.py schedule --today
          Publish due posts: python cli.py post-due
          Periodic learning: python cli.py review-text --min-posts 5
8:00 PM   pull-analytics (cron)
```

## 8-Creative Matrix Launch

For a controlled first-wave test: **2 products × 2 formats × 2 themes = 8 creatives**. Use this when launching a new product pair (e.g. moisturizer + eye cream).

### Prerequisites

```bash
# Add products (repeat --product for each; use your real URLs)
python cli.py add-product --sku moisturizer --name "Embrace Collagen Moisturizer" --url https://veluraesthetics.com/moisturizer
python cli.py add-product --sku eye-cream --name "Eye Cream" --url https://veluraesthetics.com/products/eye-cream

# Place images in ~/.velura/product-images/{sku}/ then register
python cli.py register-images --product moisturizer
python cli.py register-images --product eye-cream
```

### Generate exactly 8 creatives

**CLI syntax:** Repeat `--product`, `--theme`, and `--hook` for each value. Run each format command **once** (running twice produces duplicates).

```bash
# AI video (4 clips: 2 products × 2 theme/hook pairs)
python cli.py run --product moisturizer --product eye-cream --theme problem_solution --theme hidden_knowledge --hook relatable_pain --hook question --rotate-theme-hook --count 2 --format ai_video_15s

# AI video flex (experimental: 3–7 scenes, 6–15s, flexible style)
python cli.py run --product moisturizer --product eye-cream --theme problem_solution --theme hidden_knowledge --hook relatable_pain --hook question --rotate-theme-hook --count 2 --format ai_video_flex_15s

# Image motion (4 clips)
python cli.py run --product moisturizer --product eye-cream --theme problem_solution --theme hidden_knowledge --hook relatable_pain --hook question --rotate-theme-hook --count 2 --format image_motion_15s
```

**Result:** 8 creatives total (4 per product). Then: `preview --today` → `approve` → `schedule --today` → `post-due` or `post --today`.

### Bandit-driven alternative

To let the bandit choose theme/hook pairs instead of locking them:

```bash
python cli.py run --product moisturizer --product eye-cream --count 4 --format ai_video_15s
python cli.py run --product moisturizer --product eye-cream --count 4 --format ai_video_flex_15s
python cli.py run --product moisturizer --product eye-cream --count 4 --format image_motion_15s
```

This yields 8 creatives with bandit-recommended strategies from the current theme taxonomy (for example `hidden_knowledge`, `identity_tribe`, or `stakes_cost_of_inaction`) and the current hook whitelist.

## CLI Commands

| Command | Purpose |
|---------|---------|
| `add-product --sku SLUG --name "Name"` | Create or update a product in the local catalog |
| `sync-products` | Pull product catalog from Shopify if configured |
| `register-images --product SLUG` | Scan and register local product images |
| `report` | Generate daily performance + recommendation report |
| `run --auto --count N` | Generate N total clips across eligible products using the shared bandit allocation |
| `run --product SLUG --count N` | Generate N clips using shared bandit theme/hook recommendations (starter arms) |
| `run --product SLUG --theme T --hook H --count N` | Generate with manual strategy overrides |
| `run --product SLUG --product SLUG2 --theme T --theme T2 --hook H --hook H2 --count N --rotate-theme-hook` | Cycle through provided theme/hook pairs; repeat `--product`, `--theme`, `--hook` for each value |
| `exclude --product SLUG --reason "..."` | Exclude product from generation |
| `include --product SLUG` | Re-include excluded product |
| `preview --today` | Review today's generated content |
| `preview --last-24h` | Review generated content in past 24hrs |
| `preview --all` | Review all content generated |  
| `approve --content-id ID` | Approve generated content for scheduling/posting |
| `reject --content-id ID --reason "..."` | Reject generated content with review notes |
| `schedule --today` | Schedule approved content using staggered platform offsets |
| `schedule --content-id ID` | Schedule one approved content item |
| `post-due` | Publish payloads whose `publish_at` is due |
| `post --today` | Immediately post approved content from the last 24 hours; repeated posts on the same platform wait 5 minutes by default |
| `post --content-id ID` | Manually post a specific content piece; add `--delay-XXX` to change the same-platform wait or `--nodelay` to post everything immediately |
| `pull-analytics` | Pull metrics from all platforms |
| `review-text [--product SLUG] [--platform PLATFORM] [--format FORMAT] [--min-posts N] [--lookback-days N]` | Analyze recent post performance and store one reusable text insight for future prompt injection |
| `commerce-ingest PATH` | Ingest commerce facts (sessions, purchases, revenue) from CSV for revenue-aware ranking |
| `paid-seed-clone --content-id ID [--variants N]` | Clone an organic winner into 3–5 ad-safe variants for paid promotion |
| `report-product --product SLUG` | Product performance report, including total tracked spend |
| `archive` | Archive old videos to GCS |

To see tracked costs for a product:

```bash
python cli.py report-product --product eye-cream
```

For immediate posting with `post`, Velura now waits 5 minutes between the second and later posts on the same platform during that command. Each delay has a random ±20% variance (e.g. 4–6 minutes for the default 5-minute wait) so repeated posts are less predictable. Use `--delay-XXX` with `XXX` from `0` to `999` to override the base wait, for example `python cli.py post --today --delay-15`. Use `--nodelay` to keep the old behavior and post everything back-to-back. During each wait, the CLI prints a progress line every 30 seconds (or every 15 minutes when the delay is over 15 minutes) so you know when the next same-platform post will start.

**Quiet hours:** Velura never posts between 10pm and 8am Eastern Time. If you run `post --today` or `post --content-id` during that window, the first post is delayed until 8am ET (±0–5 minutes random). Subsequent same-platform posts then follow the normal `--delay-XXX` interval (e.g. with `--delay-120`, the second post is ~120 minutes after the first, ±20%). If a long run (e.g. 100 posts with `--delay-999`) would extend past 10pm, the process pauses at 10pm and resumes at 8am ET (±0–5 min) the next day. The same restriction applies to `post-due`. Use `--allow-quiet-hours` to bypass quiet hours and post immediately during 10pm–8am EST (e.g. `post --today --allow-quiet-hours` or `post-due --allow-quiet-hours`).

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

- Themes: `problem_solution`, `benefit_spotlight`, `stakes_cost_of_inaction`, `hidden_knowledge`, `identity_tribe`, `mechanism_reveal`, `mythbust`, `contrast_versus`
- Hook types: `question`, `bold_claim`, `relatable_pain`, `visual_surprise`, `quick_tip`

Starter shared bandit arms:

- `stakes_cost_of_inaction__relatable_pain`
- `problem_solution__relatable_pain`
- `hidden_knowledge__question`
- `identity_tribe__bold_claim`

Prompt reuse and feedback:

- `research-add` stores scoped `RESEARCH INSIGHT` text for future generations.
- `review-text` analyzes recent posts with metrics and stores one scoped `TEXT_LEVEL_INSIGHTS` paragraph focused on hook wording, framing, proof language, and CTA phrasing.
- When a matching research snapshot or text insight exists, generation automatically injects it into the prompt for `ai_video_15s`, `ai_video_flex_15s`, `video-v2`, and `image_motion_15s`.

### Commerce and Revenue-Aware Ranking

Velura can optimize for commercial value (sessions, purchases, revenue) in addition to engagement:

1. **Ingest commerce facts** — Export orders from Shopify with UTM attribution (utm_content = content_id, utm_source = platform). Aggregate by (content_id, platform, event_date) and produce a CSV with columns: `content_id`, `platform`, `event_date`, `sessions`, `add_to_cart`, `checkout_started`, `purchases`, `revenue`. Run:
   ```bash
   python cli.py commerce-ingest path/to/commerce.csv
   ```

2. **Set ranking objective** — In `config.yaml`, set `bandit.ranking_objective` to `engagement_rate` (default), `views`, `revenue`, `sessions`, or `purchases`. The morning briefing and bandit updates use this objective; when commerce data is sparse, the bandit falls back to engagement.

3. **Reporting** — The organic evaluation section in the morning briefing shows commerce totals when available and ranks cohorts by the configured objective.

### Paid Promotion Path (Organic → Paid)

Velura supports a lightweight handoff from organic winners to paid ad variants:

1. **Identify winners** — Use the morning briefing or `report-product` to find top-performing creatives (engagement, sessions, or revenue).

2. **Clone for paid** — Run:
   ```bash
   python cli.py paid-seed-clone --content-id <winner-id> --variants 5
   ```
   This creates 3–5 ad-safe variants by varying CTA, opening hook, and platform captions while preserving the winning core concept and video asset. Lineage is stored (`source_content_id`) for attribution.

3. **Review and hand off** — Preview with `preview --last-24h`, approve variants, then manually upload to Meta Ads, Google Ads, or your ad platform. Same product page destination; evaluate CTR, CPC, add-to-cart, and purchase rate.

This is a manual or semi-manual handoff, not a full paid automation system.

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
│   ├── voiceover_generator.py # OpenAI TTS for image_motion voiceover
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
- **Shopify** — optional store URL + Client ID + Client Secret if you want automatic product sync
- **OpenAI** — API key + model for script generation. For image_motion_15s voiceover, TTS uses the same key; optional `openai.tts_model` (default `gpt-4o-mini-tts`), `openai.tts_voice_cycle` (default `[marin]`), `openai.tts_response_format` (default `wav`), `openai.tts_language` (default `english`), and `openai.tts_enabled_formats` (default `[image_motion_15s]`)
- **Gemini** — Google AI API key, optional `gemini.aspect_ratio` (default `9:16` for vertical short-form)
- **xAI** — Video generation API key, optional `xai.model` (default `grok-imagine-video`), optional `xai.resolution`/`xai.aspect_ratio` (default `9:16` for vertical short-form), and polling controls via `xai.poll_interval_seconds` and `xai.poll_timeout_seconds`
- **YouTube** — Google OAuth Desktop app client secrets JSON for posting, optional `youtube.token_file` for the cached login token, optional `youtube.login_hint` to suggest the correct Google account during auth, plus `youtube.api_key` for analytics pulls
- **Instagram** — Graph API access token + account ID; set `instagram.gcs_bucket` if you want automatic public video hosting for Reels. If you post through the Make bridge, your Make scenario must persist the final Instagram media ID back into Velura or `pull-analytics` cannot fetch Reel metrics.
- **Instagram sync** — optional `instagram_sync.spreadsheet_id`, `instagram_sync.worksheet_name`, and `instagram_sync.credentials_file` if you want `pull-analytics` to read a Google Sheet exported from Make and replace temporary `make:...` handoff ids with real Instagram media ids before analytics pulls run
- **TikTok** — `tiktok.client_key`, `tiktok.client_secret`, `tiktok.access_token`, and `tiktok.refresh_token` for posting; Content Posting API approval is required. Analytics only need the client key + secret. The separate review demo uses `tiktok-sandbox.*` settings.
- **Bandit** — optional shared-bandit controls for `bandit.daily_slots`, `bandit.min_top_k`, `bandit.allocation_ceiling`, `bandit.expand_after_creatives`, and `bandit.starter_arms`; used by `run --auto` and `run --product SLUG` (when no `--theme`/`--hook` is given)
- **Text review** — optional `text_review.min_posts` and `text_review.lookback_days` defaults for `review-text`, which creates reusable `TEXT_LEVEL_INSIGHTS` from recent post performance
- **X** — API key/secret + access token/secret (Basic tier for posting)
- **Make bridge** — optional `make_bridge.webhook_url` plus `make_bridge.r2.account_id`, `make_bridge.r2.access_key_id`, `make_bridge.r2.secret_access_key`, and `make_bridge.r2.bucket_name` if you want to upload finished `.mp4` files to Cloudflare R2 and forward a presigned URL to Make.com
- **GCS** — Bucket name + service account credentials (optional, for archival)
- **Briefing email** — optional SMTP settings if you want `report` emailed

If you are managing inventory manually, use `add-product` to maintain your catalog and `exclude --product SLUG --reason "out of stock"` to keep a product out of generation or posting rotation.

If `platforms.enabled` is omitted, Velura auto-enables only the platforms that have the required credentials present. Missing Instagram, TikTok, or X credentials will no longer block a YouTube-only workflow.

For YouTube posting, `youtube.client_secrets_file` must be a Google Cloud OAuth client JSON for a `Desktop app`. A `Web application` client will fail with `Error 400: redirect_uri_mismatch` because Velura uses a localhost callback during the installed-app auth flow.

The upload goes to whichever Google/YouTube account completes the OAuth browser flow when `youtube_token.json` is created. Set `youtube.login_hint` if you want Google to prefill the intended account. If Google later revokes that cached refresh token, Velura now falls back to a fresh browser login automatically; you can still delete the token file manually before retrying if you want to switch accounts completely.

xAI video generation now uses the async `POST /v1/videos/generations` flow and polls `GET /v1/videos/{request_id}` until the video is ready. The default poll interval is 15 seconds, and you can change it with `xai.poll_interval_seconds` in `config.yaml`.

## Make.com Upload Bridge

Use `upload_to_make.py` when a generated video has already been approved, scheduled, and posted in your workflow and you want to hand the final `.mp4` off to a Make.com scenario for Instagram follow-up.

The script:

- uploads the local `.mp4` to Cloudflare R2 with `boto3`
- generates a 30-minute presigned URL for that object
- sends `{"video_url": "...", "caption": "..."}` to your Make.com webhook with `requests`

If you use the built-in Instagram Make bridge in `post-due`, Velura stores a temporary handoff id like `make:videos/...` at post time. That value is only enough to track the upload handoff, not to pull Instagram analytics later. To capture Instagram metrics reliably, make sure your Make scenario:

- publishes the Reel and captures the real Instagram media id returned by Meta
- calls back into your Velura database or follow-up sync step to replace the temporary `make:...` value in `posts.post_id` with that real media id
- keeps using the same Instagram Business account whose token is configured in `instagram.access_token`

Without that write-back step, `python cli.py pull-analytics` will skip Instagram metrics for Make-bridged posts because Meta's insights API only accepts real media ids.

If you do not want to expose a callback server, Velura can also sync the final Instagram ids from a public Google Sheet when `pull-analytics` runs. Configure:

```yaml
instagram_sync:
  spreadsheet_id: "1xqShI6fSYiIlYIlJI-nE9GvuNPH6qM-1F4h_vEfjrf4"
  worksheet_gid: "0"
```

The worksheet should contain these headers:

- `handoff_id`
- `handoff_object_key`
- `platform`
- `instagram_post_id`
- `content_id`
- `posted_at`

Mapping behavior during `pull-analytics`:

- Velura reads the sheet first, before platform analytics pulls begin
- it only considers rows where `platform=instagram` and `instagram_post_id` is populated
- it matches rows to local posts by `handoff_id` first
- if `handoff_id` is blank, it falls back to `content_id`
- it updates the local Instagram `posts.post_id` from the temporary `make:...` handoff id to the real `instagram_post_id`

Public Google Sheet requirements:

- publish or share the sheet so the CSV export is readable without auth
- set `instagram_sync.spreadsheet_id` to the Google Sheet id
- set `instagram_sync.worksheet_gid` to the tab gid, usually `0` for the first tab

Optional private-sheet mode:

- you can still use a private sheet by setting `instagram_sync.credentials_file`
- if `credentials_file` is present, Velura uses the Google Sheets API instead of public CSV export
- `worksheet_name` is only needed for the authenticated Sheets API path

If the public CSV is not readable, `pull-analytics` will print an Instagram sheet sync warning and continue with the normal platform pulls.

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
- **image_motion_15s reference folders:** `~/.velura/brand/` (brand-kit images, always used), `~/.velura/models/` (human-model images for lifestyle frames only). When TTS is enabled, voiceover WAV and silent MP4 sidecars are stored alongside the final voiced `{content-id}.mp4`.
- **ai_video_flex_15s:** Experimental format; uses the same Gemini + xAI pipeline as ai_video_15s but with a flexible multi-scene plan (3–7 scenes, 6–15 seconds) stored in `asset_manifest_json`

On Windows, `~` resolves to `%USERPROFILE%`.

## Restore Points

Before making significant changes, create a restore point so you can return to the current state if something goes wrong.

### Create a restore point

```bash
# Tag the current commit (recommended)
git tag backup-before-changes-YYYY-MM-DD -m "Restore point: current state before future changes"

# Optional: also create a branch
git branch backup/current-state
```

### Return to a restore point

```bash
# Option A: Reset current branch to the tagged state (discards newer commits)
git reset --hard backup-before-changes-YYYY-MM-DD

# Option B: Switch to the backup branch
git checkout backup/current-state

# Option C: Create a new branch from the backup
git checkout -b recovery backup-before-changes-YYYY-MM-DD
```

### Push restore points to remote

Tags and branches are local until pushed. To back them up on the remote:

```bash
git push origin backup-before-changes-YYYY-MM-DD
git push origin backup/current-state
```

## Testing

```bash
pytest tests/ -v
```

## Scheduling

### Windows (Task Scheduler)

Create two tasks:
- **Morning (8:00 AM):** `.venv\Scripts\python cli.py pull-analytics` then `cli.py report`
- **Evening (8:00 PM):** `.venv\Scripts\python cli.py pull-analytics`
- **Optional publish worker (every 15 minutes):** `.venv\Scripts\python cli.py post-due`

### Linux/macOS (crontab)

```cron
0 7 * * * cd /path/to/repo && .venv/bin/python cli.py pull-analytics && .venv/bin/python cli.py report
0 19 * * * cd /path/to/repo && .venv/bin/python cli.py pull-analytics
*/15 * * * * cd /path/to/repo && .venv/bin/python cli.py post-due
```
