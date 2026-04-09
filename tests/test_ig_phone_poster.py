from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.posters.ig_phone import IgPhonePoster


def test_upload_queues_row_relative_path_caption_and_returns_prefixed_id(
    mock_config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ig_db = tmp_path / "ig_queue.db"
    monkeypatch.setenv("IG_POSTER_DB", str(ig_db))

    data_root = Path(mock_config["data_root"])
    videos_root = data_root / "videos"
    videos_root.mkdir(parents=True, exist_ok=True)
    rel_dir = videos_root / "campaigns" / "q1"
    rel_dir.mkdir(parents=True, exist_ok=True)
    video = rel_dir / "clip.mp4"
    video.write_bytes(b"v")

    poster = IgPhonePoster()
    post_id = poster.upload(
        video,
        "Summer drop",
        ["velura", "skincare"],
    )

    assert post_id.startswith("ig_phone:")
    queue_id = post_id.split(":", 1)[1]

    conn = sqlite3.connect(str(ig_db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT file, caption FROM queue WHERE id = ?", (queue_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    expected_rel = str(video.resolve().relative_to(videos_root.resolve()))
    assert row["file"] == expected_rel
    assert row["caption"] == "Summer drop\n\n#velura #skincare"


def test_upload_empty_hashtags_uses_caption_only(
    mock_config: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ig_db = tmp_path / "ig_queue.db"
    monkeypatch.setenv("IG_POSTER_DB", str(ig_db))

    data_root = Path(mock_config["data_root"])
    videos_root = data_root / "videos"
    videos_root.mkdir(parents=True, exist_ok=True)
    video = videos_root / "solo.mp4"
    video.write_bytes(b"v")

    poster = IgPhonePoster()
    post_id = poster.upload(video, "No tags", [])

    queue_id = post_id.split(":", 1)[1]
    conn = sqlite3.connect(str(ig_db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT caption FROM queue WHERE id = ?", (queue_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["caption"] == "No tags"


def test_upload_rejects_video_outside_videos_dir(
    mock_config: dict, tmp_path: Path
) -> None:
    data_root = Path(mock_config["data_root"])
    videos_root = data_root / "videos"
    videos_root.mkdir(parents=True, exist_ok=True)
    outsider = tmp_path / "not_under_videos.mp4"
    outsider.write_bytes(b"x")

    poster = IgPhonePoster()
    with pytest.raises(ValueError, match="Video path must be under videos_dir"):
        poster.upload(outsider, "x", ["y"])
