from __future__ import annotations

import json
import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src import config
from src.posters.base import BasePoster, TransientError, retry_transient

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
_CATEGORY_PEOPLE_BLOGS = "22"
_TRANSIENT_STATUSES = {429, 500, 502, 503}


class YouTubePoster(BasePoster):
    platform = "youtube"

    def __init__(self) -> None:
        self._service = None

    def _get_service(self):
        if self._service is not None:
            return self._service
        self._service = build("youtube", "v3", credentials=self._load_credentials())
        return self._service

    def _load_credentials(self) -> Credentials:
        secrets_file = Path(config.get("youtube.client_secrets_file")).expanduser()
        token_path = Path(config.get("youtube.token_file", "youtube_token.json")).expanduser()
        login_hint = config.get("youtube.login_hint")

        creds: Credentials | None = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif not creds or not creds.valid:
            flow = self._build_oauth_flow(secrets_file)
            creds = flow.run_local_server(
                port=0,
                redirect_uri_trailing_slash=False,
                prompt="select_account",
                login_hint=login_hint or None,
            )

        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def _build_oauth_flow(self, secrets_file: Path) -> InstalledAppFlow:
        client_config = json.loads(secrets_file.read_text(encoding="utf-8"))
        if "installed" not in client_config:
            if "web" in client_config:
                raise ValueError(
                    "YouTube OAuth requires a Google Cloud Desktop app client secrets JSON. "
                    "The configured file contains a web client, which causes redirect_uri_mismatch "
                    "with the local browser callback. Create a Desktop app OAuth client, download "
                    "its JSON, update youtube.client_secrets_file, and retry."
                )
            raise ValueError(
                "Invalid YouTube client secrets file. Expected a Google OAuth client JSON "
                "with an 'installed' section."
            )
        return InstalledAppFlow.from_client_config(client_config, _SCOPES)

    @retry_transient()
    def upload(self, video_path: Path, caption: str, hashtags: list[str]) -> str:
        service = self._get_service()

        # YouTube captions must end with "Link in bio"
        caption = caption.strip()
        if caption and not caption.rstrip().lower().endswith("link in bio"):
            caption = f"{caption}\n\nLink in bio"
        elif not caption:
            caption = "Link in bio"

        tags = list(hashtags) + ["Shorts"]
        title = caption.split("\n", 1)[0][:100]

        body = {
            "snippet": {
                "title": title,
                "description": f"{caption}\n\n#Shorts",
                "tags": tags,
                "categoryId": _CATEGORY_PEOPLE_BLOGS,
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
        request = service.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        try:
            response = None
            while response is None:
                _, response = request.next_chunk()
        except HttpError as exc:
            if exc.resp.status in _TRANSIENT_STATUSES:
                raise TransientError(str(exc)) from exc
            raise

        video_id: str = response["id"]
        logger.info("Uploaded YouTube Short: %s", video_id)
        return video_id
