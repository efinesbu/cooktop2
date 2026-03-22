"""Shared ffmpeg discovery for renderers."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from src import config

logger = logging.getLogger(__name__)

# If video and audio are within this of each other, mux with -shortest (clips at most ~25 ms).
_MUX_NEAR_EQUAL_SECONDS = 0.025


def _refresh_windows_path() -> None:
    """Refresh os.environ PATH from registry so winget installs are visible."""
    if sys.platform != "win32":
        return
    try:
        import winreg
        machine_path = ""
        user_path = ""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            )
            machine_path = winreg.QueryValueEx(key, "Path")[0]
            key.Close()
        except OSError:
            pass
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
            user_path = winreg.QueryValueEx(key, "Path")[0]
            key.Close()
        except OSError:
            pass
        if machine_path or user_path:
            os.environ["PATH"] = f"{machine_path};{user_path}"
    except Exception:
        pass


def find_ffmpeg() -> str:
    """Return path to ffmpeg executable. Raises RuntimeError if not found."""
    # 1. Config override
    try:
        path = config.get("ffmpeg.path") or config.get("ffmpeg.bin")
        if path:
            p = Path(path).expanduser()
            if p.is_file():
                return str(p)
            exe = p / "ffmpeg.exe" if sys.platform == "win32" else p / "ffmpeg"
            if exe.is_file():
                return str(exe)
    except Exception:
        pass

    # 2. Standard PATH lookup
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    # 3. Windows: refresh PATH from registry (picks up winget installs without shell restart)
    if sys.platform == "win32":
        _refresh_windows_path()
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            return ffmpeg

        # 4. Fallback: scan WinGet packages for ffmpeg
        winget_base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        if winget_base.is_dir():
            for pkg in winget_base.glob("Gyan.FFmpeg*"):
                for sub in pkg.iterdir():
                    if sub.is_dir():
                        exe = sub / "bin" / "ffmpeg.exe"
                        if exe.is_file():
                            return str(exe)

    raise RuntimeError(
        "ffmpeg is required. Install it (winget install ffmpeg, apt install ffmpeg, brew install ffmpeg) "
        "and ensure it's on PATH, or set ffmpeg.path in config.yaml."
    )


def find_ffprobe() -> str | None:
    """Return path to ffprobe next to ffmpeg, on PATH, or None."""
    try:
        ffmpeg_path = Path(find_ffmpeg())
    except RuntimeError:
        return None
    if ffmpeg_path.name.lower() == "ffmpeg.exe":
        probe = ffmpeg_path.parent / "ffprobe.exe"
    else:
        probe = ffmpeg_path.parent / "ffprobe"
    if probe.is_file():
        return str(probe)
    return shutil.which("ffprobe")


def _ffprobe_duration_seconds(ffprobe: str, media_path: Path) -> float | None:
    """Return container duration in seconds, or None if probing fails."""
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        line = (proc.stdout or "").strip().splitlines()[0] if proc.stdout else ""
        if not line:
            return None
        return float(line)
    except (subprocess.CalledProcessError, ValueError, IndexError, OSError):
        return None


def _mux_audio_into_video_shortest(
    ffmpeg: str, video_path: Path, audio_path: Path, output_path: Path
) -> None:
    """Mux with -shortest (legacy): output length is min(video, audio)."""
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-shortest",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _mux_audio_into_video(ffmpeg: str, video_path: Path, audio_path: Path, output_path: Path) -> None:
    """Mux audio into video without clipping narration when TTS runs slightly longer than video.

    TTS duration can exceed the word-budget estimate by a small margin; the previous
    implementation used ``-shortest``, which always trimmed the *longer* stream and cut off
    the end of the voiceover. We ffprobe both inputs and pad/extend the shorter stream so
    the output covers max(video, audio).
    """
    ffprobe = find_ffprobe()
    v_dur = _ffprobe_duration_seconds(ffprobe, video_path) if ffprobe else None
    a_dur = _ffprobe_duration_seconds(ffprobe, audio_path) if ffprobe else None

    if v_dur is None or a_dur is None:
        logger.warning(
            "ffprobe unavailable or duration probe failed; mux uses -shortest (may clip TTS). "
            "Install ffmpeg with ffprobe on PATH."
        )
        _mux_audio_into_video_shortest(ffmpeg, video_path, audio_path, output_path)
        return

    diff = abs(v_dur - a_dur)
    if diff <= _MUX_NEAR_EQUAL_SECONDS:
        _mux_audio_into_video_shortest(ffmpeg, video_path, audio_path, output_path)
        return

    if v_dur < a_dur:
        # Narration longer than video: hold last frame (typical TTS overrun vs rendered frames).
        v_pad = a_dur - v_dur
        fc = f"[0:v]tpad=stop_mode=clone:stop_duration={v_pad:.6f}[vout]"
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-filter_complex",
            fc,
            "-map",
            "[vout]",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return

    # Video longer than audio: pad narration with silence at the end.
    a_pad = v_dur - a_dur
    fc = f"[1:a]apad=pad_dur={a_pad:.6f}[aout]"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-filter_complex",
        fc,
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _tts_enabled_for_format(creative_format: str) -> bool:
    """True if TTS is enabled for this creative format."""
    enabled = config.get("tts.enabled_formats")
    if enabled is None:
        enabled = config.get("openai.tts_enabled_formats")
    if enabled is None:
        return creative_format in ("image_motion_15s", "ai_video_flex_15s")
    if not isinstance(enabled, list):
        return False
    return creative_format in enabled
