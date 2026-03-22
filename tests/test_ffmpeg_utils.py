"""Tests for ffmpeg mux helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.renderers import ffmpeg_utils


def test_mux_audio_into_video_falls_back_to_shortest_when_probe_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: None)
    calls: list[list[str]] = []

    def capture_run(cmd: list, **kwargs):
        calls.append(cmd)

    monkeypatch.setattr(ffmpeg_utils.subprocess, "run", capture_run)

    v = tmp_path / "v.mp4"
    a = tmp_path / "a.wav"
    out = tmp_path / "out.mp4"
    v.write_bytes(b"x")
    a.write_bytes(b"y")

    ffmpeg_utils._mux_audio_into_video("ffmpeg", v, a, out)

    assert len(calls) == 1
    assert "-shortest" in calls[0]


def test_mux_audio_into_video_extends_video_when_audio_longer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: "ffprobe")

    def fake_probe(_ffprobe: str, path: Path) -> float:
        return 10.0 if "silent" in path.name else 10.8

    monkeypatch.setattr(ffmpeg_utils, "_ffprobe_duration_seconds", fake_probe)
    calls: list[list[str]] = []

    def capture_run(cmd: list, **kwargs):
        calls.append(cmd)

    monkeypatch.setattr(ffmpeg_utils.subprocess, "run", capture_run)

    v = tmp_path / "silent.mp4"
    a = tmp_path / "a.wav"
    out = tmp_path / "out.mp4"
    v.write_bytes(b"x")
    a.write_bytes(b"y")

    ffmpeg_utils._mux_audio_into_video("ffmpeg", v, a, out)

    assert len(calls) == 1
    fc = calls[0][calls[0].index("-filter_complex") + 1]
    assert "tpad=stop_mode=clone" in fc
    assert "stop_duration=0.800000" in fc


def test_mux_audio_into_video_pads_audio_when_video_longer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: "ffprobe")

    def fake_probe(_ffprobe: str, path: Path) -> float:
        return 12.0 if "silent" in path.name else 11.0

    monkeypatch.setattr(ffmpeg_utils, "_ffprobe_duration_seconds", fake_probe)
    calls: list[list[str]] = []

    def capture_run(cmd: list, **kwargs):
        calls.append(cmd)

    monkeypatch.setattr(ffmpeg_utils.subprocess, "run", capture_run)

    v = tmp_path / "silent.mp4"
    a = tmp_path / "a.wav"
    out = tmp_path / "out.mp4"
    v.write_bytes(b"x")
    a.write_bytes(b"y")

    ffmpeg_utils._mux_audio_into_video("ffmpeg", v, a, out)

    assert len(calls) == 1
    fc = calls[0][calls[0].index("-filter_complex") + 1]
    assert "apad=pad_dur=" in fc


def test_mux_audio_into_video_near_equal_uses_shortest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: "ffprobe")

    def fake_probe(_ffprobe: str, path: Path) -> float:
        return 10.0 if "silent" in path.name else 10.01

    monkeypatch.setattr(ffmpeg_utils, "_ffprobe_duration_seconds", fake_probe)
    calls: list[list[str]] = []

    def capture_run(cmd: list, **kwargs):
        calls.append(cmd)

    monkeypatch.setattr(ffmpeg_utils.subprocess, "run", capture_run)

    v = tmp_path / "silent.mp4"
    a = tmp_path / "a.wav"
    out = tmp_path / "out.mp4"
    v.write_bytes(b"x")
    a.write_bytes(b"y")

    ffmpeg_utils._mux_audio_into_video("ffmpeg", v, a, out)

    assert len(calls) == 1
    assert "-shortest" in calls[0]
