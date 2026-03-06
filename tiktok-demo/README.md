# TikTok Sandbox Demo Application

This is a standalone, lightweight FastAPI application designed to verify TikTok sandbox login with `user.info.basic` and support production Content Posting API approval for `video.upload` without impacting the main Veluraesthetics codebase.

## Prerequisites

1.  Ensure you have your TikTok Sandbox credentials set in the root `config.yaml`:
    ```yaml
    tiktok-sandbox:
      client_key: "YOUR_CLIENT_KEY"
      client_secret: "YOUR_CLIENT_SECRET"
    ```

## Installation & Running Locally

1.  Navigate to this directory:
    ```bash
    cd tiktok-demo
    ```

2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

3.  Run the application:
    ```bash
    uvicorn app:app --host 0.0.0.0 --port 8000 --reload
    ```
    The app will be available at `http://localhost:8000`. 
    *(Note: TikTok OAuth requires a valid redirect URI, so local testing might require a tunneling service like ngrok if your TikTok Developer Portal is configured for the production demo URL).*

## Deployment to demo.veluraesthetics.com

To deploy this application to `demo.veluraesthetics.com` to pass the TikTok app review:

1.  **Server Setup**: Deploy this code to your server.
2.  **Environment Setup**: Install Python 3.8+ and install the requirements via `pip install -r requirements.txt`.
3.  **Process Manager**: Run the application using a process manager like `systemd` or `pm2`:
    ```bash
    uvicorn app:app --host 127.0.0.1 --port 8000
    ```
4.  **Reverse Proxy (Nginx)**: Configure Nginx to route traffic from `demo.veluraesthetics.com` to the Uvicorn server:

    ```nginx
    server {
        listen 80;
        server_name demo.veluraesthetics.com;
        return 301 https://$host$request_uri;
    }

    server {
        listen 443 ssl;
        server_name demo.veluraesthetics.com;

        # Add your SSL configuration here (e.g., Let's Encrypt)

        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
    ```

## Critical TikTok Developer Portal Configuration

For the OAuth flow to work, the `redirect_uri` in your app **MUST EXACTLY** match one of the URIs registered in your TikTok Developer Portal.

1.  Go to [TikTok for Developers](https://developers.tiktok.com/) → your app → **Login Kit** → **Redirect URIs**
2.  Add the redirect URI you will use:
    - **Local testing**: `http://localhost:8000/callback`
    - **Production**: `https://demo.veluraesthetics.com/callback`
3.  Set the matching URI in `config.yaml` under `tiktok-sandbox.redirect_uri`, or via env var `TIKTOK_REDIRECT_URI`

The default is `http://localhost:8000/callback` for local development.
