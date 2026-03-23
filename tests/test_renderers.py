"""Tests for format-aware renderer orchestration and selection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models import Content, Product, ProductImage
from src.renderers import get_renderer, render_media


def test_tts_enabled_for_format_defaults_include_ai_video_flex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default TTS gating covers stitched flex videos when config is unset."""
    from src.renderers.ffmpeg_utils import _tts_enabled_for_format

    monkeypatch.setattr(
        "src.config._config",
        {"openai": {"api_key": "test-key"}, "platforms": {"enabled": []}},
    )

    assert _tts_enabled_for_format("image_motion_15s") is True
    assert _tts_enabled_for_format("ai_video_flex_15s") is True
    assert _tts_enabled_for_format("ai_video_15s") is False


def test_tts_enabled_for_format_respects_tts_enabled_formats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tts.enabled_formats takes precedence over legacy openai.tts_enabled_formats."""
    from src.renderers.ffmpeg_utils import _tts_enabled_for_format

    monkeypatch.setattr(
        "src.config._config",
        {
            "tts": {"enabled_formats": ["image_motion_15s"]},
            "openai": {
                "api_key": "test-key",
                "tts_enabled_formats": ["ai_video_flex_15s"],
            },
            "platforms": {"enabled": []},
        },
    )

    assert _tts_enabled_for_format("image_motion_15s") is True
    assert _tts_enabled_for_format("ai_video_flex_15s") is False


def test_get_renderer_returns_ai_video_for_ai_video_15s() -> None:
    r = get_renderer("ai_video_15s")
    assert r is not None
    assert r.format_id == "ai_video_15s"


def test_get_renderer_returns_renderer_for_all_builtin_formats() -> None:
    r = get_renderer("image_motion_15s")
    assert r is not None
    assert r.format_id == "image_motion_15s"

    r = get_renderer("ai_video_15s")
    assert r is not None
    assert r.format_id == "ai_video_15s"

    r = get_renderer("ai_video_flex_15s")
    assert r is not None
    assert r.format_id == "ai_video_flex_15s"


def test_slideshow_15s_retired_from_creative_formats() -> None:
    """slideshow_15s is no longer in CREATIVE_FORMATS for new generation."""
    from src.models import CREATIVE_FORMATS

    assert "slideshow_15s" not in CREATIVE_FORMATS
    assert "image_motion_15s" in CREATIVE_FORMATS
    assert "ai_video_15s" in CREATIVE_FORMATS
    assert "ai_video_flex_15s" in CREATIVE_FORMATS


def test_get_renderer_returns_ai_video_flex_for_ai_video_flex_15s() -> None:
    r = get_renderer("ai_video_flex_15s")
    assert r is not None
    assert r.format_id == "ai_video_flex_15s"


def test_get_renderer_returns_none_for_unknown_format() -> None:
    r = get_renderer("unknown_format")
    assert r is None


def test_render_media_raises_for_unknown_format(
    tmp_db: Path,
    sample_product: Product,
    sample_content: Content,
) -> None:
    from src import db

    db.upsert_product(sample_product)

    content = Content(
        id="test-001",
        product_sku=sample_product.sku,
        theme="benefit_spotlight",
        hook_type="bold_claim",
        creative_format="unknown_format",
    )
    images: list[ProductImage] = []

    with pytest.raises(ValueError) as exc_info:
        render_media(content, sample_product, images)

    assert "No renderer registered for format 'unknown_format'" in str(exc_info.value)
    assert "Supported:" in str(exc_info.value)


def test_render_media_ai_video_invokes_image_and_video_generators(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
    sample_product: Product,
) -> None:
    from src import db

    db.upsert_product(sample_product)

    content = Content(
        id="test-002",
        product_sku=sample_product.sku,
        theme="benefit_spotlight",
        hook_type="bold_claim",
        creative_format="ai_video_15s",
        starting_image_prompt="A product on a counter",
        scene_1_desc="Scene 1",
        scene_2_desc="Scene 2",
        scene_1_script="First line.",
        scene_2_script="Second line.",
    )
    images: list[ProductImage] = []

    video_path = tmp_path / "output.mp4"
    video_path.write_bytes(b"fake-video")

    image_path = tmp_path / "start.png"
    image_path.write_bytes(b"fake-image")

    image_calls: list[tuple] = []
    video_calls: list[tuple] = []

    def fake_generate_starting_image(c, p):
        image_calls.append((c, p))
        return image_path

    def fake_generate_video(c, start_path, p):
        video_calls.append((c, start_path, p))
        db.update_content_video_path(c.id, str(video_path))
        return video_path

    monkeypatch.setattr("src.renderers.ai_video.generate_starting_image", fake_generate_starting_image)
    monkeypatch.setattr("src.renderers.ai_video.generate_video", fake_generate_video)

    monkeypatch.setattr(
        "src.config._config",
        {"data_root": str(tmp_path / "velura-data")},
    )

    db.insert_content(content)
    result = render_media(content, sample_product, images)

    assert result == video_path
    assert len(image_calls) == 1
    assert image_calls[0][0].id == content.id
    assert image_calls[0][1].sku == sample_product.sku
    assert len(video_calls) == 1
    assert video_calls[0][0].id == content.id
    assert video_calls[0][1] == image_path
    assert video_calls[0][2].sku == sample_product.sku

    updated = db.get_content(content.id)
    assert updated is not None
    assert updated.video_local_path == str(video_path)


def test_render_media_ai_video_flex_uses_manifest_plan(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
    sample_product: Product,
) -> None:
    """ai_video_flex_15s reads video_plan from asset_manifest_json for prompt and duration."""
    import json

    from src import db
    from src.renderers import render_media

    db.upsert_product(sample_product)

    video_plan = {
        "strategy_summary": "Quick-cut product showcase",
        "total_duration_seconds": 9.5,
        "style_family": "realistic_cinematic",
        "style_rationale": "Fits premium skincare positioning",
        "script_total_words": 24,
        "scenes": [
            {"duration_seconds": 2.0, "scene_description": "Hook closeup.", "script": "First line."},
            {"duration_seconds": 2.0, "scene_description": "HARD CUT to side angle.", "script": "Second line."},
            {"duration_seconds": 2.0, "scene_description": "HARD CUT to texture.", "script": "Third line."},
            {"duration_seconds": 2.0, "scene_description": "HARD CUT to CTA.", "script": "Fourth line."},
            {"duration_seconds": 1.5, "scene_description": "Hold.", "script": "End."},
        ],
    }
    content = Content(
        id="test-flex-001",
        product_sku=sample_product.sku,
        theme="benefit_spotlight",
        hook_type="bold_claim",
        creative_format="ai_video_flex_15s",
        starting_image_prompt="A product on a counter",
        asset_manifest_json=json.dumps({
            "format": "ai_video_flex_15s",
            "video_plan": video_plan,
            "generation_metadata": {"total_duration_seconds": 9.5, "scene_count": 5},
        }),
    )
    db.insert_content(content)
    images: list[ProductImage] = []

    video_path = tmp_path / "output.mp4"
    video_path.write_bytes(b"fake-video")
    image_path = tmp_path / "start.png"
    image_path.write_bytes(b"fake-image")

    video_calls: list[tuple] = []

    def fake_generate_starting_image(c, p):
        return image_path

    def fake_generate_video(c, start_path, p):
        video_calls.append((c, start_path, p))
        db.update_content_video_path(c.id, str(video_path))
        return video_path

    monkeypatch.setattr("src.renderers.ai_video_flex.generate_starting_image", fake_generate_starting_image)
    monkeypatch.setattr("src.renderers.ai_video_flex.generate_video", fake_generate_video)
    monkeypatch.setattr(
        "src.config._config",
        {"data_root": str(tmp_path / "velura-data")},
    )

    result = render_media(content, sample_product, images)

    assert result == video_path
    assert len(video_calls) == 1
    # Video generator receives content with manifest; prompt builder uses it
    from src.video_generator import _build_video_prompt, _get_request_duration
    prompt = _build_video_prompt(content, sample_product)
    assert "9-second" in prompt
    assert "realistic_cinematic" in prompt
    assert "Scene 1 visual direction" in prompt
    assert "Scene 5 voiceover" in prompt
    assert _get_request_duration(content) == 9


def test_render_media_ai_video_flex_with_voiceover_generates_tts_and_muxes(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
    sample_product: Product,
) -> None:
    """ai_video_flex_15s with voiceover_plan generates TTS and muxes a voiced MP4."""
    import json

    from src import db
    from src.renderers import render_media

    db.upsert_product(sample_product)

    frame1 = tmp_path / "frame0.png"
    frame2 = tmp_path / "frame1.png"
    frame3 = tmp_path / "frame2.png"
    frame1.write_bytes(b"fake-png-1")
    frame2.write_bytes(b"fake-png-2")
    frame3.write_bytes(b"fake-png-3")

    plan = {
        "strategy_summary": "Hero-led sequence",
        "total_duration_seconds": 6.0,
        "style_family": "realistic_cinematic",
        "style_rationale": "default",
        "script_total_words": 12,
        "scenes": [
            {
                "duration_seconds": 2.0,
                "scene_description": "Hook closeup.",
                "script": "First line.",
            },
            {
                "duration_seconds": 2.0,
                "scene_description": "HARD CUT to proof.",
                "script": "Second line.",
            },
            {
                "duration_seconds": 2.0,
                "scene_description": "HARD CUT to CTA.",
                "script": "Third line.",
            },
        ],
    }
    voiceover_plan = {
        "script_template_id": "timeline_stitch_v3",
        "voiceover_script": "First line. Second line. Third line.",
        "voice": "marin",
        "voice_instructions": "Speak in a calm, premium, reassuring tone for a premium consumer brand.",
        "language": "english",
    }
    content = Content(
        id="test-flex-tts",
        product_sku=sample_product.sku,
        theme="benefit_spotlight",
        hook_type="bold_claim",
        creative_format="ai_video_flex_15s",
        asset_manifest_json=json.dumps({
            "format": "ai_video_flex_15s",
            "schema_version": 3,
            "video_plan": plan,
            "voiceover_plan": voiceover_plan,
        }),
    )
    db.insert_content(content)

    tts_calls: list[tuple] = []
    mux_calls: list[tuple] = []

    def fake_generate_starting_image(c, p):
        return frame1

    def fake_generate_video(c, start_path, p):
        del start_path, p
        video_dir = tmp_path / "velura-data" / "videos" / c.product_sku
        video_dir.mkdir(parents=True, exist_ok=True)
        video_path = video_dir / f"{c.id}.mp4"
        video_path.write_bytes(b"fake-silent-mp4")
        db.update_content_video_path(c.id, str(video_path))
        return video_path

    def fake_generate_voiceover(script, voice, voice_instructions, output_path, content_id, **kwargs):
        tts_calls.append((script, voice, voice_instructions, content_id, kwargs.get("language")))
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-wav-header-and-data")
        return output_path

    def fake_mux(ffmpeg, video_path, audio_path, output_path):
        del ffmpeg
        mux_calls.append((str(video_path), str(audio_path), str(output_path)))
        Path(output_path).write_bytes(b"fake-voiced-mp4")

    monkeypatch.setattr("src.renderers.ffmpeg_utils.find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("src.renderers.ai_video_flex.find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("src.renderers.ai_video_flex.generate_starting_image", fake_generate_starting_image)
    monkeypatch.setattr("src.renderers.ai_video_flex.generate_video", fake_generate_video)
    monkeypatch.setattr("src.renderers.ai_video_flex.generate_voiceover", fake_generate_voiceover)
    monkeypatch.setattr("src.renderers.ffmpeg_utils._mux_audio_into_video", fake_mux)
    monkeypatch.setattr("src.renderers.ai_video_flex._mux_audio_into_video", fake_mux)
    monkeypatch.setattr(
        "src.config._config",
        {
            "data_root": str(tmp_path / "velura-data"),
            "openai": {"api_key": "test-key", "tts_enabled_formats": ["ai_video_flex_15s"]},
        },
    )

    result = render_media(content, sample_product, [])

    assert len(tts_calls) == 1
    assert tts_calls[0][0] == "First line. Second line. Third line."
    assert tts_calls[0][1] == "marin"
    assert "calm, premium, reassuring" in tts_calls[0][2]
    assert tts_calls[0][3] == content.id
    assert tts_calls[0][4] == "english"

    assert len(mux_calls) == 1
    assert mux_calls[0][0].endswith("_silent.mp4")
    assert mux_calls[0][1].endswith("_voiceover.wav")
    assert mux_calls[0][2].endswith(".mp4")

    assert result.suffix == ".mp4"
    assert result.name == "test-flex-tts.mp4"
    assert result.read_bytes() == b"fake-voiced-mp4"

    updated = db.get_content(content.id)
    assert updated is not None
    assert updated.video_local_path == str(result)
    manifest = json.loads(updated.asset_manifest_json or "{}")
    assert manifest["audio_local_path"].endswith("_voiceover.wav")
    assert manifest["silent_video_local_path"].endswith("_silent.mp4")
    assert "render-artifacts" in Path(manifest["audio_local_path"]).parts
    assert "render-artifacts" in Path(manifest["silent_video_local_path"]).parts


def test_render_media_ai_video_flex_v5_uses_v5_starting_image_and_voice_settings(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
    sample_product: Product,
) -> None:
    """schema_version 5 flex renderer calls generate_v5_starting_image and passes V5 ElevenLabs settings."""
    import json

    from src import db
    from src.renderers import render_media

    db.upsert_product(sample_product)

    vo_script = " ".join([f"w{i}" for i in range(30)])
    voiceover_plan = {
        "script_template_id": "horoscope_v5_single",
        "voiceover_script": vo_script,
        "voice": "K7W7zLWeGoxU9YqWoB7A",
        "voice_instructions": "Warm, playful, premium.",
        "language": "english",
        "provider_options": {
            "elevenlabs": {
                "language_code": "en",
                "apply_text_normalization": "auto",
                "voice_settings": {"speed": 1.03, "use_speaker_boost": True},
            }
        },
    }
    video_plan = {
        "strategy_summary": "Horoscope V5 four-beat arc",
        "total_duration_seconds": 15.0,
        "style_family": "anamorphic",
        "style_rationale": "V5 horoscope reel",
        "scenes": [
            {"duration_seconds": 3.5, "scene_description": "A", "script": "a"},
            {"duration_seconds": 4.0, "scene_description": "B", "script": "b"},
            {"duration_seconds": 4.0, "scene_description": "C", "script": "c"},
            {"duration_seconds": 3.5, "scene_description": "D", "script": "d"},
        ],
    }
    content = Content(
        id="test-flex-v5",
        product_sku=sample_product.sku,
        theme="aries",
        hook_type="jessica",
        creative_format="ai_video_flex_15s",
        asset_manifest_json=json.dumps({
            "format": "ai_video_flex_15s",
            "schema_version": 5,
            "video_plan": video_plan,
            "voiceover_plan": voiceover_plan,
        }),
    )
    db.insert_content(content)

    v5_start_calls: list[tuple] = []
    standard_start_calls: list[tuple] = []
    tts_calls: list[tuple] = []

    def fake_v5_start(c, horoscope, name):
        v5_start_calls.append((c.id, horoscope, name))
        p = tmp_path / "v5start.png"
        p.write_bytes(b"png")
        return p

    def fake_standard_start(c, p):
        standard_start_calls.append((c.id, p.sku))
        out = tmp_path / "std.png"
        out.write_bytes(b"x")
        return out

    def fake_generate_video(c, start_path, p):
        video_dir = tmp_path / "velura-data" / "videos" / c.product_sku
        video_dir.mkdir(parents=True, exist_ok=True)
        video_path = video_dir / f"{c.id}.mp4"
        video_path.write_bytes(b"silent-mp4")
        db.update_content_video_path(c.id, str(video_path))
        return video_path

    def fake_generate_voiceover(script, voice, voice_instructions, output_path, content_id, **kwargs):
        tts_calls.append(
            (
                script,
                voice,
                voice_instructions,
                content_id,
                kwargs.get("elevenlabs_voice_settings"),
                kwargs.get("elevenlabs_request_options"),
            )
        )
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"wav")
        return output_path

    def fake_mux(ffmpeg, video_path, audio_path, output_path):
        Path(output_path).write_bytes(b"muxed")

    fake_v5_settings = {"stability": 0.99, "similarity_boost": 0.88, "style": 0.77}

    monkeypatch.setattr("src.renderers.ffmpeg_utils.find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("src.renderers.ai_video_flex.find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("src.renderers.ai_video_flex.generate_v5_starting_image", fake_v5_start)
    monkeypatch.setattr("src.renderers.ai_video_flex.generate_starting_image", fake_standard_start)
    monkeypatch.setattr("src.renderers.ai_video_flex.generate_video", fake_generate_video)
    monkeypatch.setattr("src.renderers.ai_video_flex.generate_voiceover", fake_generate_voiceover)
    monkeypatch.setattr("src.renderers.ai_video_flex.elevenlabs_v5_voice_settings", lambda: fake_v5_settings)
    monkeypatch.setattr("src.renderers.ffmpeg_utils._mux_audio_into_video", fake_mux)
    monkeypatch.setattr("src.renderers.ai_video_flex._mux_audio_into_video", fake_mux)
    monkeypatch.setattr(
        "src.config._config",
        {
            "data_root": str(tmp_path / "velura-data"),
            "openai": {"api_key": "test-key", "tts_enabled_formats": ["ai_video_flex_15s"]},
        },
    )

    render_media(content, sample_product, [])

    assert len(v5_start_calls) == 1
    assert v5_start_calls[0][0] == content.id
    assert v5_start_calls[0][1] == "aries"
    assert v5_start_calls[0][2] == "jessica"
    assert len(standard_start_calls) == 0

    assert len(tts_calls) == 1
    assert tts_calls[0][4] == fake_v5_settings
    assert tts_calls[0][5] == voiceover_plan["provider_options"]["elevenlabs"]


def test_render_media_image_motion_uses_plan_when_present(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
    sample_product: Product,
) -> None:
    """image_motion_15s with asset_manifest_json image_plan uses Gemini flow and shorter clip."""
    import json

    from src import db
    from src.renderers import render_media

    db.upsert_product(sample_product)

    frame1 = tmp_path / "frame0.png"
    frame2 = tmp_path / "frame1.png"
    frame3 = tmp_path / "frame2.png"
    frame1.write_bytes(b"fake-png-1")
    frame2.write_bytes(b"fake-png-2")
    frame3.write_bytes(b"fake-png-3")

    plan = {
        "strategy_summary": "Hero-led sequence",
        "total_duration_seconds": 6.0,
        "performance_rationale": "default",
        "strategy_metadata": {
            "content_goal": "engagement",
            "primary_engagement_intent": "save",
            "audience_question_cluster": "Which ingredient actually matters?",
            "audience_fear_cluster": None,
        },
        "frames": [
            {
                "role": "hero_macro",
                "narrative_role": "hook",
                "frame_intent": "Open with a premium macro reveal.",
                "mood": "intrigue",
                "duration_seconds": 2.0,
                "image_prompt": "Frame 1",
            },
            {
                "role": "hero_tabletop",
                "narrative_role": "proof",
                "frame_intent": "Ground the sequence in a believable routine context.",
                "mood": "calm_confidence",
                "duration_seconds": 2.0,
                "image_prompt": "Frame 2",
            },
            {
                "role": "texture_detail",
                "narrative_role": "cta",
                "frame_intent": "Close with a tactile payoff that invites action.",
                "mood": "invitation",
                "duration_seconds": 2.0,
                "image_prompt": "Frame 3",
            },
        ],
    }
    content = Content(
        id="test-im-001",
        product_sku=sample_product.sku,
        theme="benefit_spotlight",
        hook_type="bold_claim",
        creative_format="image_motion_15s",
        asset_manifest_json=json.dumps({"format": "image_motion_15s", "image_plan": plan}),
    )
    db.insert_content(content)

    frame_output_dirs: list[Path | None] = []

    def fake_generate_frames(c, p, pl, output_dir=None):
        frame_output_dirs.append(output_dir)
        return [frame1, frame2, frame3]

    monkeypatch.setattr(
        "src.renderers.image_motion.generate_frame_images_for_plan",
        fake_generate_frames,
    )
    monkeypatch.setattr(
        "src.config._config",
        {"data_root": str(tmp_path / "velura-data")},
    )

    video_dir = tmp_path / "velura-data" / "videos" / sample_product.sku
    video_dir.mkdir(parents=True, exist_ok=True)
    video_path = video_dir / "test-im-001.mp4"
    video_path.write_bytes(b"fake-mp4")

    def fake_render(ffmpeg, paths, out_path, duration_sec=None, per_frame_durations=None):
        out_path.write_bytes(b"fake-mp4")
        assert per_frame_durations == [2.0, 2.0, 2.0]
        assert duration_sec == 6

    monkeypatch.setattr(
        "src.renderers.image_motion._render_multi_image_concatenated",
        fake_render,
    )
    monkeypatch.setattr(
        "src.renderers.image_motion.find_ffmpeg",
        lambda: "ffmpeg",
    )

    result = render_media(content, sample_product, [])
    assert result.suffix == ".mp4"
    assert result.name == "test-im-001.mp4"

    updated = db.get_content(content.id)
    assert updated is not None
    manifest = json.loads(updated.asset_manifest_json or "{}")
    assert "generated_frame_paths" in manifest
    assert manifest["total_duration_seconds"] == 6.0
    assert manifest["image_plan"]["frames"][0]["narrative_role"] == "hook"
    assert len(frame_output_dirs) == 1
    assert frame_output_dirs[0] is not None
    assert frame_output_dirs[0] != video_dir
    assert frame_output_dirs[0].parts[-4:] == (
        "render-artifacts",
        "image-motion",
        sample_product.sku,
        content.id,
    )


def test_render_media_image_motion_with_voiceover_generates_tts_and_muxes(
    tmp_db: Path,
    monkeypatch,
    tmp_path: Path,
    sample_product: Product,
) -> None:
    """image_motion_15s with voiceover_plan generates TTS and muxes to final MP4."""
    import json

    from src import db
    from src.renderers import render_media

    db.upsert_product(sample_product)

    frame1 = tmp_path / "frame0.png"
    frame2 = tmp_path / "frame1.png"
    frame3 = tmp_path / "frame2.png"
    frame1.write_bytes(b"fake-png-1")
    frame2.write_bytes(b"fake-png-2")
    frame3.write_bytes(b"fake-png-3")

    plan = {
        "strategy_summary": "Hero-led sequence",
        "total_duration_seconds": 6.0,
        "performance_rationale": "default",
        "strategy_metadata": {
            "content_goal": "conversion",
            "primary_engagement_intent": "click",
            "audience_question_cluster": None,
            "audience_fear_cluster": "Wasting money on hype",
        },
        "frames": [
            {
                "role": "hero_macro",
                "narrative_role": "hook",
                "frame_intent": "Lead with a clear hero closeup.",
                "mood": "intrigue",
                "duration_seconds": 2.0,
                "image_prompt": "Frame 1",
            },
            {
                "role": "hero_tabletop",
                "narrative_role": "proof",
                "frame_intent": "Make the product feel credible and premium.",
                "mood": "delight",
                "duration_seconds": 2.0,
                "image_prompt": "Frame 2",
            },
            {
                "role": "texture_detail",
                "narrative_role": "cta",
                "frame_intent": "Close with a tactile invitation.",
                "mood": "invitation",
                "duration_seconds": 2.0,
                "image_prompt": "Frame 3",
            },
        ],
    }
    voiceover_plan = {
        "script_template_id": "caption_led",
        "voiceover_script": "Want fresher skin? Try me.",
        "voice": "marin",
        "voice_instructions": "Speak in a calm, premium tone.",
        "language": "english",
    }
    content = Content(
        id="test-im-tts",
        product_sku=sample_product.sku,
        theme="benefit_spotlight",
        hook_type="bold_claim",
        creative_format="image_motion_15s",
        asset_manifest_json=json.dumps({
            "format": "image_motion_15s",
            "image_plan": plan,
            "voiceover_plan": voiceover_plan,
        }),
    )
    db.insert_content(content)

    tts_calls: list[tuple] = []
    mux_calls: list[tuple] = []
    frame_output_dirs: list[Path | None] = []

    def fake_generate_frames(c, p, pl, output_dir=None):
        frame_output_dirs.append(output_dir)
        return [frame1, frame2, frame3]

    def fake_generate_voiceover(script, voice, voice_instructions, output_path, content_id, **kwargs):
        tts_calls.append((script, voice, str(output_path)))
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-wav-header-and-data")
        return output_path

    def fake_render(ffmpeg, paths, out_path, duration_sec=None, per_frame_durations=None):
        out_path.write_bytes(b"fake-silent-mp4")
        assert per_frame_durations == [2.0, 2.0, 2.0]
        assert duration_sec == 6

    def fake_mux(ffmpeg, video_path, audio_path, output_path):
        mux_calls.append((str(video_path), str(audio_path), str(output_path)))
        Path(output_path).write_bytes(b"fake-voiced-mp4")

    monkeypatch.setattr(
        "src.renderers.image_motion.generate_frame_images_for_plan",
        fake_generate_frames,
    )
    monkeypatch.setattr(
        "src.renderers.image_motion.generate_voiceover",
        fake_generate_voiceover,
    )
    monkeypatch.setattr(
        "src.renderers.image_motion._render_multi_image_concatenated",
        fake_render,
    )
    monkeypatch.setattr(
        "src.renderers.image_motion._mux_audio_into_video",
        fake_mux,
    )
    monkeypatch.setattr(
        "src.renderers.image_motion.find_ffmpeg",
        lambda: "ffmpeg",
    )
    monkeypatch.setattr(
        "src.config._config",
        {
            "data_root": str(tmp_path / "velura-data"),
            "openai": {"api_key": "test-key", "tts_enabled_formats": ["image_motion_15s"]},
        },
    )

    result = render_media(content, sample_product, [])

    assert len(tts_calls) == 1
    assert tts_calls[0][0] == "Want fresher skin? Try me."
    assert tts_calls[0][1] == "marin"
    assert tts_calls[0][2].endswith("_voiceover.wav")
    assert "render-artifacts" in Path(tts_calls[0][2]).parts

    assert len(mux_calls) == 1
    assert mux_calls[0][0].endswith("_silent.mp4")
    assert mux_calls[0][1].endswith("_voiceover.wav")
    assert mux_calls[0][2].endswith(".mp4")
    assert "render-artifacts" in Path(mux_calls[0][0]).parts
    assert "render-artifacts" in Path(mux_calls[0][1]).parts

    assert result.suffix == ".mp4"
    assert result.name == "test-im-tts.mp4"
    assert result.read_bytes() == b"fake-voiced-mp4"
    assert "render-artifacts" not in result.parts

    updated = db.get_content(content.id)
    assert updated is not None
    assert updated.video_local_path == str(result)
    manifest = json.loads(updated.asset_manifest_json or "{}")
    assert "audio_local_path" in manifest
    assert "silent_video_local_path" in manifest
    assert manifest["audio_local_path"].endswith("_voiceover.wav")
    assert "render-artifacts" in Path(manifest["audio_local_path"]).parts
    assert "render-artifacts" in Path(manifest["silent_video_local_path"]).parts
    assert len(frame_output_dirs) == 1
    assert frame_output_dirs[0] is not None
    assert frame_output_dirs[0].parts[-4:] == (
        "render-artifacts",
        "image-motion",
        sample_product.sku,
        content.id,
    )
