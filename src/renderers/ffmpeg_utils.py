"""Shared ffmpeg discovery for renderers."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


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
        from src import config
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
