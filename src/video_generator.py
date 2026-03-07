from __future__ import annotations

import base64
import logging
import time
from pathlib import Path

import httpx

from src import config, db
from src.models import Content, Cost, Product

logger = logging.getLogger(__name__)

XAI_VIDEO_GENERATIONS_ENDPOINT = "https://api.x.ai/v1/videos/generations"
XAI_VIDEO_STATUS_ENDPOINT = "https://api.x.ai/v1/videos/{request_id}"
TARGET_DURATION_SECONDS = 15
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0
DEFAULT_POLL_INTERVAL_SECONDS = 15.0
DEFAULT_POLL_TIMEOUT_SECONDS = 15 * 60.0
DEFAULT_RESOLUTION = "720p"


def generate_video(
    content: Content,
    starting_image_path: Path,
    product: Product,
) -> Path:
    api_key = config.get("xai.api_key")
    if not api_key:
        raise ValueError("xai.api_key not set in config")

    model = config.get("xai.model", "grok-imagine-video")
    poll_interval = float(config.get("xai.poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS))
    poll_timeout = float(config.get("xai.poll_timeout_seconds", DEFAULT_POLL_TIMEOUT_SECONDS))
    aspect_ratio = config.get("xai.aspect_ratio", "9:16")
    resolution = config.get("xai.resolution", DEFAULT_RESOLUTION)

    if poll_interval <= 0:
        raise ValueError("xai.poll_interval_seconds must be greater than 0")
    if poll_timeout <= 0:
        raise ValueError("xai.poll_timeout_seconds must be greater than 0")

    prompt = _build_video_prompt(content, product)
    image_data_uri = _image_to_data_uri(starting_image_path)
    request_payload = {
        "model": model,
        "prompt": prompt,
        "duration": TARGET_DURATION_SECONDS,
        "resolution": resolution,
    }
    request_payload["aspect_ratio"] = aspect_ratio

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        request_id = _start_generation(client, headers, request_payload, image_data_uri)
        response_data = _poll_until_complete(
            client,
            headers,
            request_id,
            poll_interval=poll_interval,
            poll_timeout=poll_timeout,
        )
        video_url = _video_url_from_response(response_data)
        video_bytes = _download_video(client, video_url)

    video_dir = config.videos_dir() / content.product_sku
    video_dir.mkdir(parents=True, exist_ok=True)
    video_path = video_dir / f"{content.id}.mp4"
    video_path.write_bytes(video_bytes)
    logger.info("Saved video to %s", video_path)

    db.update_content_video_path(content.id, str(video_path))

    video_meta = response_data.get("video", {})
    cost_usd = response_data.get("cost_usd")
    duration = int(video_meta.get("duration") or TARGET_DURATION_SECONDS)
    db.insert_cost(Cost(
        content_id=content.id,
        step="video_gen",
        api_provider="xai",
        tokens_or_units=duration,
        cost_usd=cost_usd,
    ))

    return video_path


def _build_video_prompt(content: Content, product: Product) -> str:
    parts = [
        f"Create a 15-second product video for {product.name} ({product.sku}).",
        "Animate the supplied starting image into a polished short-form ad.",
    ]
    if content.scene_1_desc:
        parts.append(f"Scene 1 visual direction: {content.scene_1_desc}")
    if content.scene_1_script:
        parts.append(f"Scene 1 voiceover: {content.scene_1_script}")
    if content.scene_2_desc:
        parts.append(f"Scene 2 visual direction: {content.scene_2_desc}")
    if content.scene_2_script:
        parts.append(f"Scene 2 voiceover: {content.scene_2_script}")
    parts.append("Keep the product appearance, colors, and branding consistent with the provided image.")
    parts.append("Use smooth motion, premium lighting, and ad-ready pacing.")
    return "\n".join(parts)


def _image_to_data_uri(path: Path) -> str:
    suffix = path.suffix.lower()
    mime_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _start_generation(
    client: httpx.Client,
    headers: dict[str, str],
    payload: dict,
    image_data_uri: str,
) -> str:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = _post_generation_request(client, headers, payload, image_data_uri)
            request_id = response.get("request_id")
            if not request_id:
                raise RuntimeError(f"xAI did not return a request_id: {response}")
            logger.info("Started xAI video generation request %s", request_id)
            return str(request_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 413:
                raise RuntimeError(
                    "xAI rejected the starting image as too large for video generation. "
                    "This workflow is currently sending the original image without resizing."
                ) from exc
            last_exc = exc
            if attempt < MAX_RETRIES:
                _sleep_before_retry(attempt, exc)
                continue
            break
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                _sleep_before_retry(attempt, exc)
                continue
            break
    raise RuntimeError(
        f"xAI video generation failed after {MAX_RETRIES} attempts"
    ) from last_exc


def _post_generation_request(
    client: httpx.Client,
    headers: dict[str, str],
    payload: dict,
    image_data_uri: str,
) -> dict:
    candidate_payloads = [
        {
            **payload,
            "image": {
                "url": image_data_uri,
                "type": "image_url",
            },
        },
        {
            **payload,
            "image_url": image_data_uri,
        },
    ]

    last_exc: httpx.HTTPStatusError | None = None
    for idx, candidate in enumerate(candidate_payloads):
        try:
            resp = client.post(
                XAI_VIDEO_GENERATIONS_ENDPOINT,
                headers=headers,
                json=candidate,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code in (400, 422) and idx < len(candidate_payloads) - 1:
                logger.info("xAI rejected video payload shape, trying alternate image field")
                continue
            raise

    raise RuntimeError("xAI rejected all supported image payload shapes") from last_exc


def _poll_until_complete(
    client: httpx.Client,
    headers: dict[str, str],
    request_id: str,
    *,
    poll_interval: float,
    poll_timeout: float,
) -> dict:
    deadline = time.monotonic() + poll_timeout
    status_url = XAI_VIDEO_STATUS_ENDPOINT.format(request_id=request_id)

    while True:
        try:
            resp = client.get(status_url, headers={"Authorization": headers["Authorization"]})
            resp.raise_for_status()
            data = resp.json()
        except httpx.TransportError as exc:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"xAI video polling timed out after {poll_timeout:.0f}s"
                ) from exc
            logger.warning("xAI polling failed (%s), retrying in %.1fs", exc, poll_interval)
            time.sleep(poll_interval)
            continue

        status = data.get("status")
        if status == "done":
            return data
        if status == "expired":
            raise RuntimeError(f"xAI video request expired before completion: {request_id}")
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"xAI video polling timed out after {poll_timeout:.0f}s"
            )

        logger.info(
            "xAI video request %s still %s; polling again in %.1fs",
            request_id,
            status or "pending",
            poll_interval,
        )
        time.sleep(poll_interval)


def _video_url_from_response(response_data: dict) -> str:
    video = response_data.get("video", {})
    video_url = video.get("url")
    if not video_url:
        raise RuntimeError(f"xAI completed without returning a video URL: {response_data}")
    return str(video_url)


def _download_video(client: httpx.Client, video_url: str) -> bytes:
    response = client.get(video_url, timeout=300.0)
    response.raise_for_status()
    return response.content


def _sleep_before_retry(attempt: int, exc: Exception) -> None:
    wait = INITIAL_BACKOFF * (2 ** (attempt - 1))
    logger.warning(
        "xAI API attempt %d/%d failed (%s), retrying in %.1fs",
        attempt,
        MAX_RETRIES,
        exc,
        wait,
    )
    time.sleep(wait)
