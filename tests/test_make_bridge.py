from __future__ import annotations

import pytest

from src import make_bridge


def test_load_bridge_settings_rejects_placeholder_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.config._config",
        {
            "make_bridge": {
                "webhook_url": "https://hook.us2.make.com/your-webhook-id",
                "r2": {
                    "account_id": "YOUR_CLOUDFLARE_R2_ACCOUNT_ID",
                    "access_key_id": "YOUR_CLOUDFLARE_R2_ACCESS_KEY_ID",
                    "secret_access_key": "YOUR_CLOUDFLARE_R2_SECRET_ACCESS_KEY",
                    "bucket_name": "your-r2-bucket-name",
                },
            }
        },
    )
    monkeypatch.setattr("src.make_bridge.load_dotenv", lambda: None)
    monkeypatch.setattr("src.config.load_dotenv", lambda: None)
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("R2_BUCKET_NAME", raising=False)
    monkeypatch.delenv("MAKE_WEBHOOK_URL", raising=False)

    with pytest.raises(ValueError, match="Missing required Make/R2 configuration"):
        make_bridge.load_bridge_settings()
