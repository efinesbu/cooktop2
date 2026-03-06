import os
import hashlib
import base64
import logging
import yaml
import requests
import secrets
from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import urlencode

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")
with open(CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)

# TikTok Sandbox Credentials
tiktok_config = config.get("tiktok-sandbox", {})
TIKTOK_CLIENT_KEY = tiktok_config.get("client_key")
TIKTOK_CLIENT_SECRET = tiktok_config.get("client_secret")

# Redirect URI must EXACTLY match one registered in TikTok Developer Portal.
# For local dev: use http://localhost:8000/callback (add it in Login Kit > Redirect URIs)
# For production: use https://demo.veluraesthetics.com/callback
REDIRECT_URI = os.environ.get("TIKTOK_REDIRECT_URI") or tiktok_config.get("redirect_uri") or "http://localhost:8000/callback"
COOKIE_SECURE = REDIRECT_URI.startswith("https://")
OAUTH_COOKIE_SAMESITE = "none" if COOKIE_SECURE else "lax"

app = FastAPI(title="TikTok Sandbox Demo")

# Mount static files
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

# Simple in-memory stores for demo purposes.
# In production, use a proper session middleware or database.
sessions = {}
oauth_requests = {}

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open(os.path.join(os.path.dirname(__file__), "static", "index.html"), "r") as f:
        return f.read()

@app.get("/login")
async def login(request: Request, response: Response):
    """Initiates the TikTok OAuth flow with PKCE."""
    csrf_state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )

    params = {
        "client_key": TIKTOK_CLIENT_KEY,
        "response_type": "code",
        # Sandbox supports Login Kit, but Content Posting scopes like video.upload
        # must be requested from a production app configuration.
        "scope": "user.info.basic",
        "redirect_uri": REDIRECT_URI,
        "state": csrf_state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    oauth_requests[csrf_state] = {"code_verifier": code_verifier}

    url = f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}"
    logging.warning("TikTok auth redirect URL: %s", url)

    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        key="tiktok_oauth_state",
        value=csrf_state,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=OAUTH_COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(
        key="tiktok_code_verifier",
        value=code_verifier,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=OAUTH_COOKIE_SAMESITE,
        path="/",
    )
    return response

@app.get("/callback")
async def callback(request: Request, response: Response):
    """Handles the TikTok OAuth callback."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")
    
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
        
    stored_state = request.cookies.get("tiktok_oauth_state")
    oauth_request = oauth_requests.get(state) if state else None
    if not state or oauth_request is None or (stored_state and state != stored_state):
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    code_verifier = request.cookies.get("tiktok_code_verifier") or oauth_request.get("code_verifier")
    if not code_verifier:
        raise HTTPException(status_code=400, detail="Missing PKCE code_verifier")
        
    token_url = "https://open.tiktokapis.com/v2/oauth/token/"
    
    data = {
        "client_key": TIKTOK_CLIENT_KEY,
        "client_secret": TIKTOK_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
    }
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    token_res = requests.post(token_url, data=data, headers=headers)
    token_data = token_res.json()
    
    if "error" in token_data:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_data.get('error_description', token_data['error'])}")

    oauth_requests.pop(state, None)
        
    # Store token in session
    session_id = secrets.token_urlsafe(32)
    sessions[session_id] = {
        "access_token": token_data.get("access_token"),
        "open_id": token_data.get("open_id")
    }
    
    # Redirect back to index with the session ID
    response = RedirectResponse(url="/?logged_in=true")
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.delete_cookie("tiktok_oauth_state", path="/")
    response.delete_cookie("tiktok_code_verifier", path="/")
    
    return response

@app.get("/api/status")
async def status(request: Request):
    """Check if user is logged in."""
    session_id = request.cookies.get("session_id")
    if session_id and session_id in sessions:
        return {"logged_in": True}
    return {"logged_in": False}

@app.get("/api/logout")
async def logout(response: Response):
    """Clear session."""
    response.delete_cookie("session_id")
    return {"success": True}

@app.post("/api/upload")
async def upload_video(
    request: Request,
    video: UploadFile = File(...),
    privacy: str = Form("private")
):
    """Uploads a video to TikTok Drafts."""
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in sessions:
        raise HTTPException(status_code=401, detail="Not authenticated")
        
    access_token = sessions[session_id].get("access_token")
    open_id = sessions[session_id].get("open_id")
    
    if not access_token:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Read the file content
    content = await video.read()
    file_size = len(content)
    
    # 1. Initialize the upload
    init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8"
    }
    
    init_data = {
        "post_info": {
            "title": f"Draft upload via Veluraesthetics API Demo",
            "privacy_level": "MUTUAL_FOLLOW_FRIENDS" if privacy == "friends" else "SELF_ONLY" if privacy == "private" else "EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1
        }
    }
    
    try:
        init_res = requests.post(init_url, json=init_data, headers=headers)
        init_res.raise_for_status()
        init_response_data = init_res.json()
        
        if init_response_data.get("error", {}).get("code") != "ok":
            raise HTTPException(
                status_code=400, 
                detail=f"Init failed: {init_response_data.get('error', {}).get('message', 'Unknown error')}"
            )
            
        upload_url = init_response_data["data"]["upload_url"]
        publish_id = init_response_data["data"]["publish_id"]
        
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to initialize upload: {str(e)}")

    # 2. Upload the video chunk
    try:
        # TikTok expects Content-Range for chunk uploads
        chunk_headers = {
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{file_size-1}/{file_size}"
        }
        
        upload_res = requests.put(upload_url, data=content, headers=chunk_headers)
        upload_res.raise_for_status()
        
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload video chunk: {str(e)}")
        
    return {
        "success": True, 
        "message": "Video uploaded to drafts successfully",
        "publish_id": publish_id
    }
