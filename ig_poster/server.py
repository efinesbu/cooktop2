"""Flask server: queue, video serving, and Gemini vision steps for phone IG automation."""

import json
import os
import sqlite3
import struct
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file
from google import genai
from google.genai import types

from . import db_path, ensure_db

load_dotenv()

app = Flask(__name__)

VIDEOS_DIR = os.getenv("IG_POSTER_VIDEOS_DIR", "videos")
SYSTEM_PROMPT_FILE = os.getenv(
    "IG_POSTER_SYSTEM_PROMPT_FILE", "ig_poster/system_prompt.txt"
)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_STEPS = int(os.getenv("IG_POSTER_MAX_STEPS", "40"))
PORT = int(os.getenv("IG_POSTER_PORT", "5055"))

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT_CACHE = None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Return (width, height) from JPEG bytes via SOF marker. No external deps."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h = struct.unpack(">H", data[i + 5 : i + 7])[0]
            w = struct.unpack(">H", data[i + 7 : i + 9])[0]
            return (w, h)
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        i += 2 + length
    return None


def _percent_to_pixels(action: dict, width: int, height: int) -> dict:
    """Convert Gemini's *_percent coords to integer pixel coords in-place."""
    for prefix in ("", "start_", "end_"):
        xk, yk = f"{prefix}x_percent", f"{prefix}y_percent"
        if xk in action and yk in action:
            action[f"{prefix}x"] = round(width * float(action[xk]))
            action[f"{prefix}y"] = round(height * float(action[yk]))
            del action[xk]
            del action[yk]
    return action


def init_db() -> None:
    ensure_db()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def get_system_prompt() -> str:
    global SYSTEM_PROMPT_CACHE
    if SYSTEM_PROMPT_CACHE is None:
        SYSTEM_PROMPT_CACHE = Path(SYSTEM_PROMPT_FILE).read_text(encoding="utf-8")
    return SYSTEM_PROMPT_CACHE


@app.route("/health")
def health():
    conn = get_db()
    counts = {}
    for status in ("ready", "processing", "posted", "failed"):
        counts[status] = conn.execute(
            "SELECT COUNT(*) FROM queue WHERE status=?", (status,)
        ).fetchone()[0]
    conn.close()
    return jsonify({"status": "ok", **counts})


@app.route("/queue")
def queue_list():
    conn = get_db()
    rows = conn.execute("SELECT * FROM queue ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/next", methods=["GET"])
def next_video():
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM queue WHERE status='ready' ORDER BY created_at ASC LIMIT 1"
    ).fetchone()

    if not row:
        conn.close()
        return jsonify({"status": "queue_empty"})

    conn.execute(
        "UPDATE queue SET status='processing', started_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
        (row["id"],),
    )
    conn.commit()
    conn.close()

    return jsonify(
        {
            "status": "ready",
            "id": row["id"],
            "caption": row["caption"],
            "file": row["file"],
        }
    )


@app.route("/video/<video_id>", methods=["GET"])
def serve_video(video_id: str):
    conn = get_db()
    row = conn.execute("SELECT file FROM queue WHERE id=?", (video_id,)).fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "not found"}), 404

    video_path = Path(VIDEOS_DIR) / row["file"]
    if not video_path.exists():
        return jsonify({"error": "file missing on disk"}), 404

    return send_file(video_path, mimetype="video/mp4")


@app.route("/step", methods=["POST"])
def step():
    video_id = request.args.get("id", "")
    step_num = int(request.args.get("step", "0"))

    if step_num >= MAX_STEPS:
        return jsonify(
            {
                "action": "error",
                "message": f"Safety limit: {MAX_STEPS} steps exceeded",
                "wait_after_ms": 0,
                "description": "Max steps reached -- aborting",
            }
        )

    conn = get_db()
    row = conn.execute("SELECT caption FROM queue WHERE id=?", (video_id,)).fetchone()
    conn.close()
    caption = row["caption"] if row else ""

    screenshot_bytes = request.get_data()
    if not screenshot_bytes or len(screenshot_bytes) < 1000:
        return jsonify({"action": "error", "message": "No screenshot received"}), 400

    system_prompt = get_system_prompt()
    context = (
        f"Step {step_num} of {MAX_STEPS}.\n"
        f"Caption to type when you reach the caption field:\n"
        f'"""\n{caption}\n"""'
    )

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                context,
                types.Part.from_bytes(data=screenshot_bytes, mime_type="image/jpeg"),
            ],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        action = json.loads(response.text)
        action.setdefault("wait_after_ms", 1500)
        dims = _jpeg_dimensions(screenshot_bytes)
        if dims:
            _percent_to_pixels(action, *dims)
        return jsonify(action)

    except json.JSONDecodeError as e:
        return jsonify(
            {
                "action": "error",
                "message": f"Gemini returned invalid JSON: {e}",
                "wait_after_ms": 0,
                "description": "Response parse failure",
            }
        )
    except Exception as e:
        return jsonify(
            {
                "action": "error",
                "message": f"Gemini API error: {e}",
                "wait_after_ms": 0,
                "description": "API call failed",
            }
        )


@app.route("/complete", methods=["POST"])
def complete():
    data = request.json or {}
    video_id = data.get("id", "")
    status = data.get("status", "failed")
    ig_url = data.get("ig_url", "")
    error = data.get("error", "")
    step_count = data.get("step_count", 0)

    conn = get_db()
    conn.execute(
        """UPDATE queue
           SET status=?, posted_url=?, error=?, step_count=?,
               completed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
           WHERE id=?""",
        (status, ig_url, error, step_count, video_id),
    )
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "id": video_id, "status": status})


@app.route("/reset/<video_id>", methods=["POST"])
def reset_video(video_id: str):
    conn = get_db()
    conn.execute(
        "UPDATE queue SET status='ready', started_at=NULL WHERE id=? AND status='processing'",
        (video_id,),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    print(f"IG Poster running -> http://0.0.0.0:{PORT}")
    print(f"  Model : {GEMINI_MODEL}")
    print(f"  Videos: {Path(VIDEOS_DIR).resolve()}")
    print(f"  DB    : {db_path().resolve()}")
    app.run(host="0.0.0.0", port=PORT)
