from __future__ import annotations

from pathlib import Path

from ig_poster import enqueue

from src import config
from src.posters.base import BasePoster


class IgPhonePoster(BasePoster):
    platform = "instagram"

    def upload(self, video_path: Path, caption: str, hashtags: list[str]) -> str:
        videos_root = config.videos_dir().resolve()
        resolved = video_path.resolve()
        try:
            relative = str(resolved.relative_to(videos_root))
        except ValueError as exc:
            raise ValueError(
                f"Video path must be under videos_dir ({videos_root}): {resolved}"
            ) from exc

        if hashtags:
            tag_line = " ".join(f"#{h}" for h in hashtags)
            full_caption = f"{caption}\n\n{tag_line}"
        else:
            full_caption = caption

        queue_id = enqueue(relative, full_caption)
        return f"ig_phone:{queue_id}"
