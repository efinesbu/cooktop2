from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class ThemeDefinition:
    id: str
    label: str
    summary: str
    prompt_guidance: str
    default_weight: float = 1.0
    enabled: bool = True


@dataclass(frozen=True)
class HookDefinition:
    id: str
    label: str
    summary: str
    prompt_guidance: str
    default_weight: float = 1.0
    enabled: bool = True


THEME_DEFINITIONS = [
    ThemeDefinition(
        id="benefit",
        label="Benefit-Led",
        summary="Lead with the clearest visible payoff the customer wants.",
        prompt_guidance="Center the creative on the most desirable visible outcome and why it feels worth trying.",
        default_weight=1.25,
    ),
    ThemeDefinition(
        id="problem_solution",
        label="Problem-Solution",
        summary="Start from a familiar beauty frustration, then resolve it simply.",
        prompt_guidance="Frame the product as a straightforward answer to a common beauty pain point without sounding alarming.",
        default_weight=1.2,
    ),
    ThemeDefinition(
        id="curiosity",
        label="Curiosity Gap",
        summary="Create an open loop that makes the viewer want the reveal.",
        prompt_guidance="Tease an interesting insight, reveal, or angle that invites the viewer to keep watching.",
        default_weight=1.1,
    ),
    ThemeDefinition(
        id="social_proof",
        label="Social Proof",
        summary="Anchor the message in popularity, community validation, or repeat use.",
        prompt_guidance="Highlight that people keep reaching for this product or that it has become a trusted favorite.",
        default_weight=1.15,
    ),
    ThemeDefinition(
        id="routine",
        label="Routine Ritual",
        summary="Position the product as an easy step in a premium daily ritual.",
        prompt_guidance="Show how the product fits naturally into a simple, repeatable beauty routine.",
        default_weight=1.0,
    ),
    ThemeDefinition(
        id="urgency",
        label="Timely Reason",
        summary="Create a believable reason to try it now, not someday.",
        prompt_guidance="Use light urgency around timing, season, or readiness to act now without sounding pushy.",
        default_weight=0.95,
    ),
    ThemeDefinition(
        id="fear",
        label="Fear / Consequence",
        summary="Touch on what the viewer risks if they ignore the problem or skip the solution.",
        prompt_guidance="Acknowledge a gentle consequence of inaction or delay, then position the product as the way to avoid it, without being alarmist.",
        default_weight=1.3,
    ),
]

HOOK_DEFINITIONS = [
    HookDefinition(
        id="question",
        label="Direct Question",
        summary="Open with a question that makes the viewer self-diagnose or lean in.",
        prompt_guidance="Start with a short question that feels personal and easy to answer in the viewer's head.",
        default_weight=1.15,
    ),
    HookDefinition(
        id="bold_claim",
        label="Bold Statement",
        summary="Open with a confident but compliant promise of value.",
        prompt_guidance="Use a strong opening statement that sounds confident without making medical or exaggerated claims.",
        default_weight=1.0,
    ),
    HookDefinition(
        id="relatable_pain",
        label="Relatable Pain",
        summary="Lead with a familiar frustration or insecurity the product addresses.",
        prompt_guidance="Open on a recognizable beauty annoyance or confidence dip that feels common and human.",
        default_weight=1.2,
    ),
    HookDefinition(
        id="visual_surprise",
        label="Visual Pattern Interrupt",
        summary="Begin with a slightly unexpected visual or reveal that stops the scroll.",
        prompt_guidance="Use an unusual but simple visual moment that is easy for the video model to render and immediately eye-catching.",
        default_weight=1.1,
    ),
    HookDefinition(
        id="quick_tip",
        label="Quick Tip",
        summary="Open like a fast insider trick or mini lesson.",
        prompt_guidance="Start with a short practical tip that makes the product feel useful right away.",
        default_weight=1.0,
    ),
]

THEMES = [theme.id for theme in THEME_DEFINITIONS if theme.enabled]
HOOK_TYPES = [hook.id for hook in HOOK_DEFINITIONS if hook.enabled]
THEME_MAP = {theme.id: theme for theme in THEME_DEFINITIONS}
HOOK_TYPE_MAP = {hook.id: hook for hook in HOOK_DEFINITIONS}

PLATFORMS = ["youtube", "instagram", "tiktok", "x"]

GENERATION_STEPS = ["prompt_gen", "image_gen", "video_gen"]

IMAGE_TYPES = ["hero", "lifestyle", "detail"]

REVIEW_STATUSES = ["pending", "approved", "rejected", "posted", "partial_failure"]

PAYLOAD_STATUSES = ["pending", "scheduled", "posted", "failed"]


@dataclass
class Product:
    sku: str
    name: str
    category: Optional[str] = None
    price: Optional[float] = None
    product_url: Optional[str] = None
    shopify_image_url: Optional[str] = None
    image_dir: Optional[str] = None
    generation_ready: bool = False
    active: bool = True
    excluded: bool = False
    exclude_reason: Optional[str] = None
    last_content_date: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class ProductImage:
    id: Optional[int] = None
    product_sku: str = ""
    file_path: str = ""
    image_type: str = "hero"
    registered_at: Optional[str] = None


@dataclass
class Content:
    id: str = ""
    product_sku: str = ""
    theme: str = ""
    hook_type: str = ""
    hook_text: Optional[str] = None
    starting_image_prompt: Optional[str] = None
    scene_1_desc: Optional[str] = None
    scene_2_desc: Optional[str] = None
    scene_1_script: Optional[str] = None
    scene_2_script: Optional[str] = None
    video_local_path: Optional[str] = None
    approved: bool = False
    review_status: str = "pending"
    review_notes: Optional[str] = None
    approved_at: Optional[str] = None
    rejected_at: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class PlatformPayload:
    id: Optional[int] = None
    content_id: str = ""
    platform: str = ""
    caption: Optional[str] = None
    hashtags: Optional[str] = None
    utm_url: Optional[str] = None
    publish_at: Optional[str] = None
    status: str = "pending"
    last_error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Post:
    id: Optional[int] = None
    content_id: str = ""
    platform: str = ""
    post_id: Optional[str] = None
    caption: Optional[str] = None
    hashtags: Optional[str] = None
    utm_url: Optional[str] = None
    published_at: Optional[str] = None


@dataclass
class Metric:
    id: Optional[int] = None
    post_id: int = 0
    platform: str = ""
    views: int = 0
    likes: int = 0
    shares: int = 0
    comments: int = 0
    saves: int = 0
    watch_through_rate: Optional[float] = None
    avg_watch_time: Optional[float] = None
    pulled_at: Optional[str] = None


@dataclass
class BanditArm:
    product_sku: str = ""
    theme: str = ""
    hook_type: str = ""
    successes: int = 1
    failures: int = 1
    last_updated: Optional[str] = None


@dataclass
class BanditObservation:
    id: Optional[int] = None
    post_id: int = 0
    metric_id: int = 0
    product_sku: str = ""
    theme: str = ""
    hook_type: str = ""
    engagement_rate: float = 0.0
    success: bool = False
    observed_at: Optional[str] = None


@dataclass
class Cost:
    id: Optional[int] = None
    content_id: str = ""
    step: str = ""
    api_provider: str = ""
    tokens_or_units: Optional[int] = None
    cost_usd: Optional[float] = None
    created_at: Optional[str] = None


@dataclass
class BanditRecommendation:
    """Output from bandit.recommend() — allocation for one product."""
    product_sku: str
    allocations: list[ThemeHookAllocation] = field(default_factory=list)


@dataclass
class ThemeHookAllocation:
    theme: str
    hook_type: str
    count: int
    score: float = 0.0
