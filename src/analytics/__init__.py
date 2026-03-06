from src.analytics.base import BaseAnalyticsPuller
from src.analytics.youtube import YouTubeAnalyticsPuller
from src.analytics.instagram import InstagramAnalyticsPuller
from src.analytics.tiktok import TikTokAnalyticsPuller
from src.analytics.x import XAnalyticsPuller

PULLERS: dict[str, type[BaseAnalyticsPuller]] = {
    "youtube": YouTubeAnalyticsPuller,
    "instagram": InstagramAnalyticsPuller,
    "tiktok": TikTokAnalyticsPuller,
    "x": XAnalyticsPuller,
}

__all__ = [
    "BaseAnalyticsPuller",
    "YouTubeAnalyticsPuller",
    "InstagramAnalyticsPuller",
    "TikTokAnalyticsPuller",
    "XAnalyticsPuller",
    "PULLERS",
]
