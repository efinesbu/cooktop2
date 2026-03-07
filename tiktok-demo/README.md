# TikTok Review Demo

This directory contains a standalone FastAPI app used for TikTok Login Kit verification and app-review demonstrations without changing the main Velura posting workflow.

It is intentionally separate from the main `src/posters/tiktok.py` integration:

- The main Velura TikTok integration uses the `tiktok` config block for posting and analytics.
- This demo app uses the `tiktok-sandbox` config block and is meant to be opened by TikTok approvers in a browser.

## What This App Does

- Serves a public login flow for TikTok reviewers.
- Handles TikTok OAuth callback at `/callback`.
- Stores demo session state in memory.
- Lets an authenticated reviewer upload a video to TikTok drafts from the web UI.

Important limitations:

- This app keeps OAuth state and demo sessions in memory, so restarting the app clears any in-progress login state.
- For overnight review windows, do not run with `--reload`.
- The redirect URI configured in TikTok must exactly match the URI this app is using.

## Config Required

Add this block to the root `config.yaml`:

```yaml
tiktok-sandbox:
  client_key: "YOUR_TIKTOK_CLIENT_KEY"
  client_secret: "YOUR_TIKTOK_CLIENT_SECRET"
  redirect_uri: "https://demo.veluraesthetics.com/callback"
```

Notes:

- `redirect_uri` can also be provided with the `TIKTOK_REDIRECT_URI` environment variable.
- If neither is set, the app defaults to `http://localhost:8000/callback`.
- Use a value that exactly matches one of the redirect URIs configured in TikTok Developer Portal.

## Install

From the repo root:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Then move into the demo folder:

```powershell
cd tiktok-demo
```

## Local Development

Use this when you are actively editing the demo app and do not need the public review URL:

```powershell
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

Local checks:

- Home page: `http://127.0.0.1:8000/`
- Health/status: `http://127.0.0.1:8000/api/status`

Only use local mode if your TikTok app is configured for `http://localhost:8000/callback` or `http://127.0.0.1:8000/callback`.

## Public Review Setup With Cloudflare Tunnel

Use this when TikTok approvers need to access the app at any time from the public internet.

### One-time Cloudflare tunnel config

Ensure `C:\Users\Emil - Local\.cloudflared\config.yml` contains:

```yaml
tunnel: 877baf69-9961-4df5-b6f8-f095bbffa6fd
credentials-file: C:\Users\Emil - Local\.cloudflared\877baf69-9961-4df5-b6f8-f095bbffa6fd.json

ingress:
  - hostname: demo.veluraesthetics.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

The DNS route for `demo.veluraesthetics.com` must point at this tunnel.

### Nightly launch steps

Start from a clean state:

1. Close any old `ngrok` window.
2. Close any old `uvicorn app:app --reload` window.
3. Close any duplicate old `cloudflared tunnel run velura-demo` window if you plan to relaunch cleanly.

Then launch the review stack:

```powershell
cd "C:\Users\Emil - Local\Documents\Cursor Folders\Marketing Automation\tiktok-demo"
.\start-review-demo.ps1
```

That launcher opens two long-running windows:

- App window: `uvicorn app:app --host 127.0.0.1 --port 8000`
- Tunnel window: `cloudflared tunnel run velura-demo`

Keep both of those windows open overnight.

### Verification

Check all of these before you leave it running:

- `http://127.0.0.1:8000/api/status`
- `https://demo.veluraesthetics.com/`
- `https://demo.veluraesthetics.com/api/status`

If the public URL works, the Cloudflare tunnel is forwarding correctly.

## TikTok Developer Portal Checklist

For OAuth to succeed, the redirect URI in the app and TikTok portal must match exactly.

In TikTok Developer Portal:

1. Open your app in [TikTok for Developers](https://developers.tiktok.com/).
2. Go to **Login Kit**.
3. Add the redirect URI you will actually use.

Common values:

- Local testing: `http://localhost:8000/callback`
- Cloudflare review URL: `https://demo.veluraesthetics.com/callback`

The value in `config.yaml` under `tiktok-sandbox.redirect_uri` must be the same as the portal entry.

## Overnight Stability Checklist

To keep the demo reachable overnight on a Windows machine:

- Plug the machine in.
- Set sleep to `Never` while plugged in.
- Set lid close behavior to `Do nothing`.
- Avoid running the demo in `--reload` mode.
- Avoid closing the spawned app and tunnel windows.
- Avoid Windows Update auto-restarts during the review window.
- Re-test `https://demo.veluraesthetics.com/api/status` after starting the stack.

## Troubleshooting

### Public URL returns `503`

Your Cloudflare tunnel is up, but the ingress mapping is missing or incorrect. Recheck `C:\Users\Emil - Local\.cloudflared\config.yml` and confirm it points to `http://127.0.0.1:8000`.

### `start-review-demo.ps1` says Uvicorn is missing

The launcher now tries:

- `.venv\Scripts\uvicorn.exe`
- `uvicorn` from `PATH`
- `.venv\Scripts\python.exe -m uvicorn`

If all three fail, install dependencies again and verify that `uvicorn` is available.

### TikTok login redirects back with an error

Usually the configured redirect URI does not exactly match the TikTok developer portal entry.

### Login state is invalid after a restart

This demo app stores OAuth request state in memory. Restarting the app clears that state, so a login that started before the restart may fail and need to be started again.
