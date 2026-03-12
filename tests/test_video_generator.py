from __future__ import annotations

import json as jsonlib
from pathlib import Path

import httpx
import pytest

from src import db
from src.models import Content, Product
from src.video_generator import (
    ANATOMY_GUARDRAIL,
    XAI_VIDEO_GENERATIONS_ENDPOINT,
    XAI_VIDEO_STATUS_ENDPOINT,
    _build_flex_video_prompt,
    generate_video,
)


class FakeClient:
    def __init__(self, post_handler, get_handler, **_: object) -> None:
        self._post_handler = post_handler
        self._get_handler = get_handler

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def post(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        json: dict | None = None,
    ) -> httpx.Response:
        json_body = json or {}
        request = httpx.Request(
            "POST",
            url,
            headers=headers,
            content=jsonlib.dumps(json_body).encode("utf-8"),
        )
        return self._post_handler(request, json_body)

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        del timeout
        request = httpx.Request("GET", url, headers=headers)
        return self._get_handler(request)


def test_generate_video_uses_async_xai_flow_and_downloads_video(
    tmp_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "src.config._config",
        {
            "xai": {
                "api_key": "test-xai-key",
                "model": "grok-imagine-video",
                "resolution": "720p",
                "aspect_ratio": "9:16",
                "poll_interval_seconds": 15,
                "poll_timeout_seconds": 900,
            },
            "data_root": str(tmp_path / "velura-data"),
        },
    )
    monkeypatch.setattr("src.video_generator.time.sleep", lambda _: None)

    product = Product(sku="serum-x", name="Serum X")
    content = Content(
        id="content-123",
        product_sku=product.sku,
        theme="benefit",
        hook_type="question",
        hook_text="Want brighter skin overnight?",
        scene_1_desc="Slow push-in on the product as soft light sweeps across the bottle.",
        scene_1_script="Wake up your routine with one serum that makes tired skin look expensive.",
        scene_2_desc="Final glow reveal with the bottle centered and clean CTA framing.",
        scene_2_script="Try Serum X now and get the glow before everyone else catches on.",
    )
    db.upsert_product(product)
    db.insert_content(content)

    starting_image = tmp_path / "start.png"
    starting_image.write_bytes(b"fake-png-image")

    start_payloads: list[dict] = []

    def post_handler(request: httpx.Request, payload: dict) -> httpx.Response:
        start_payloads.append(payload)
        if "image" in payload:
            return httpx.Response(
                400,
                json={"error": "unsupported image field"},
                request=request,
            )
        return httpx.Response(200, json={"request_id": "req-123"}, request=request)

    poll_count = {"value": 0}

    def get_handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://cdn.example.com/generated.mp4":
            return httpx.Response(200, content=b"video-bytes", request=request)

        poll_count["value"] += 1
        if poll_count["value"] == 1:
            return httpx.Response(200, json={"status": "pending"}, request=request)
        return httpx.Response(
            200,
            json={
                "status": "done",
                "video": {
                    "url": "https://cdn.example.com/generated.mp4",
                    "duration": 12,
                },
            },
            request=request,
        )

    monkeypatch.setattr(
        "src.video_generator.httpx.Client",
        lambda **kwargs: FakeClient(post_handler=post_handler, get_handler=get_handler, **kwargs),
    )

    video_path = generate_video(content, starting_image, product)

    assert len(start_payloads) == 2
    assert "image" in start_payloads[0]
    assert start_payloads[1]["image_url"].startswith("data:image/png;base64,")
    assert start_payloads[1]["model"] == "grok-imagine-video"
    assert start_payloads[1]["duration"] == 15
    assert start_payloads[1]["resolution"] == "720p"
    assert start_payloads[1]["aspect_ratio"] == "9:16"

    assert video_path.read_bytes() == b"video-bytes"
    stored = db.get_content(content.id)
    assert stored is not None
    assert stored.video_local_path == str(video_path)

    costs = db.costs_for_content(content.id)
    assert len(costs) == 1
    assert costs[0].api_provider == "xai"
    assert costs[0].tokens_or_units == 12


def test_build_flex_video_prompt_includes_anatomy_guardrail() -> None:
    product = Product(sku="lipstick", name="Lux Lipstick")
    content = Content(
        id="content-flex",
        product_sku=product.sku,
        theme="curiosity",
        hook_type="question",
        creative_format="ai_video_flex_15s",
        asset_manifest_json=jsonlib.dumps(
            {
                "format": "ai_video_flex_15s",
                "video_plan": {
                    "total_duration_seconds": 9,
                    "style_family": "realistic_cinematic",
                    "style_rationale": "Premium beauty finish.",
                    "scenes": [
                        {
                            "duration_seconds": 2.0,
                            "scene_description": "Macro lipstick hero shot.",
                            "script": "Why does this lipstick look expensive instantly?",
                        },
                        {
                            "duration_seconds": 2.0,
                            "scene_description": "HARD CUT to a close swipe across lips and an arm swatch.",
                            "script": "It is the smooth glide and rich color.",
                        },
                        {
                            "duration_seconds": 2.5,
                            "scene_description": "HARD CUT to finished lip look in mirror.",
                            "script": "The creamy formula helps lips look polished fast.",
                        },
                        {
                            "duration_seconds": 2.5,
                            "scene_description": "HARD CUT to product on vanity end frame.",
                            "script": "One swipe, and the whole look feels elevated.",
                        },
                    ],
                },
            }
        ),
    )

    prompt = _build_flex_video_prompt(content, product)

    assert ANATOMY_GUARDRAIL in prompt
    assert "realistic hands, lips, teeth, and facial structure" in prompt


def test_generate_video_surfaces_413_for_original_image(
    tmp_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "src.config._config",
        {
            "xai": {
                "api_key": "test-xai-key",
                "model": "grok-imagine-video",
            },
            "data_root": str(tmp_path / "velura-data"),
        },
    )
    monkeypatch.setattr("src.video_generator.time.sleep", lambda _: None)

    product = Product(sku="serum-x", name="Serum X")
    content = Content(
        id="content-413",
        product_sku=product.sku,
        theme="benefit",
        hook_type="question",
        scene_1_desc="Scene one.",
        scene_1_script="Scene one voiceover.",
        scene_2_desc="Scene two.",
        scene_2_script="Scene two voiceover.",
    )
    db.upsert_product(product)
    db.insert_content(content)

    starting_image = tmp_path / "start.png"
    starting_image.write_bytes(b"fake-png-image")

    def post_handler(request: httpx.Request, payload: dict) -> httpx.Response:
        del payload
        return httpx.Response(
            413,
            json={"error": "Payload Too Large"},
            request=request,
        )

    def get_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected GET request: {request.url}")

    monkeypatch.setattr(
        "src.video_generator.httpx.Client",
        lambda **kwargs: FakeClient(post_handler=post_handler, get_handler=get_handler, **kwargs),
    )

    with pytest.raises(RuntimeError, match="starting image as too large"):
        generate_video(content, starting_image, product)
