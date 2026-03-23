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
        id="problem_solution",
        label="Problem-Solution",
        summary="Start with a familiar friction point, then show the path to resolution.",
        prompt_guidance="Frame the offer as a straightforward answer to a recognizable problem without sounding alarmist or overly technical. ex: you have X problem, This fixes it.",
        default_weight=1.2,
    ),
    ThemeDefinition(
        id="benefit_spotlight",
        label="Benefit Spotlight",
        summary="Lead with the clearest payoff or positive outcome the audience wants.",
        prompt_guidance="Center the creative on the most compelling benefit and why it feels immediately valuable or worthwhile. ex: The one thing about this that matters most.",
        default_weight=1.15,
    ),
    ThemeDefinition(
        id="stakes_cost_of_inaction",
        label="Stakes / Cost Of Inaction",
        summary="Clarify what is lost, delayed, or made harder when the problem goes unaddressed.",
        prompt_guidance="Surface believable consequences of waiting or doing nothing, then position the offer as the practical next step. ex: Here's what happens if you ignore this.",
        default_weight=1.2,
    ),
    ThemeDefinition(
        id="hidden_knowledge",
        label="Hidden Knowledge",
        summary="Open a curiosity loop around an insight, reveal, or underused perspective.",
        prompt_guidance="Tease a useful idea, overlooked detail, or surprising takeaway that rewards attention and keeps the viewer engaged. ex: Most people don''t know this exists / works this way.",
        default_weight=1.1,
    ),
    ThemeDefinition(
        id="identity_tribe",
        label="Identity / Tribe",
        summary="Anchor the message in belonging, shared values, or signals of who this is for.",
        prompt_guidance="Show how the offer aligns with the audience's self-image, standards, or the kind of people they want to identify with. ex: This is what serious people in X space use.",
        default_weight=1.15,
    ),
    ThemeDefinition(
        id="mechanism_reveal",
        label="Mechanism Reveal",
        summary="Explain the underlying reason something works in a clear, concrete way.",
        prompt_guidance="Highlight the key mechanism, process, or driver behind the result so the audience feels they understand what makes it effective. ex: Here's what actually happens when you use this.",
        default_weight=1.0,
    ),
    ThemeDefinition(
        id="mythbust",
        label="Myth Bust",
        summary="Challenge a common assumption and replace it with a sharper truth.",
        prompt_guidance="Call out a mistaken belief, oversimplification, or bad habit, then reframe it with a more credible explanation. ex: What you've been told is wrong. Here's the truth.",
        default_weight=1.0,
    ),
    ThemeDefinition(
        id="contrast_versus",
        label="Contrast / Versus",
        summary="Create clarity by comparing two options, approaches, or outcomes side by side.",
        prompt_guidance="Use a crisp contrast to show why one path, choice, or behavior leads to a better result than the alternative. ex: X vs Y, and why it matters.",
        default_weight=1.0,
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
        prompt_guidance="Open on a recognizable frustration or confidence dip that feels common, specific, and human.",
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

ZODIAC_SIGNS = [
    "aries",
    "taurus",
    "gemini",
    "cancer",
    "leo",
    "virgo",
    "libra",
    "scorpio",
    "sagittarius",
    "capricorn",
    "aquarius",
    "pisces",
]

V5_NAMES = [
    "jessica",
    "ashley",
    "emily",
    "sarah",
    "hannah",
    "taylor",
    "madison",
    "rachel",
    "emma",
    "olivia",
    "chloe",
    "samantha",
]

V5_VIBES = ["playful_roast", "lucky_era"]

GENERATION_STEPS = [
    "prompt_gen",
    "voiceover_plan_gen",
    "v3_classify",
    "paid_variant_gen",
    "image_gen",
    "video_gen",
    "slideshow_render",
    "image_motion_render",
    "tts_gen",
]

# Phase 2: creative metadata for reporting and learning
CREATIVE_FORMATS = ["ai_video_15s", "ai_video_flex_15s", "image_motion_15s"]
CTA_TYPES = ["see_product", "shop_now", "soft_cta"]
PROOF_TYPES = ["test_result", "testimonial", "before_after", "ingredient", "none"]
SCRIPT_STYLES = ["conversational", "direct", "storytelling", "tip_based"]

IMAGE_TYPES = ["hero", "lifestyle", "detail"]

REVIEW_STATUSES = ["pending", "approved", "rejected", "posted", "partial_failure"]

PAYLOAD_STATUSES = ["pending", "scheduled", "submitted", "posted", "failed"]


@dataclass
class Product:
    sku: str
    name: str
    category: Optional[str] = None
    price: Optional[float] = None
    description: Optional[str] = None
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
    # Phase 2: first-class metadata for reporting and learning
    creative_format: str = "ai_video_15s"
    cta_type: str = "see_product"
    cta_text: Optional[str] = None
    problem_angle: Optional[str] = None
    proof_type: Optional[str] = None
    script_style: Optional[str] = None
    research_snapshot_id: Optional[str] = None
    asset_manifest_json: Optional[str] = None
    # Phase 7: lineage from paid variant back to organic winner
    source_content_id: Optional[str] = None
    # Video V2: strategy metadata for learning (style_family, audience clusters, etc.)
    strategy_metadata_json: Optional[str] = None
    # Phase 8: eval scoring
    eval_score: Optional[int] = None


@dataclass
class PlatformPayload:
    id: Optional[int] = None
    content_id: str = ""
    platform: str = ""
    caption: Optional[str] = None
    hashtags: Optional[str] = None
    utm_url: Optional[str] = None
    destination_url: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    link_mode: str = "direct"
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
    destination_url: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    link_mode: str = "direct"
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
class CommerceFact:
    """Phase 6: Attribution-linked commerce events (sessions, purchases, revenue)."""
    id: Optional[int] = None
    content_id: str = ""
    platform: str = ""
    event_date: str = ""
    sessions: int = 0
    add_to_cart: int = 0
    checkout_started: int = 0
    purchases: int = 0
    revenue: float = 0.0
    source: str = "shopify_import"
    ingested_at: Optional[str] = None


@dataclass
class BanditArm:
    arm_key: str = ""
    theme: str = ""
    hook_type: str = ""
    alpha: float = 1.0
    beta: float = 1.0
    last_updated: Optional[str] = None


@dataclass
class BanditObservation:
    id: Optional[int] = None
    content_id: str = ""
    product_sku: str = ""
    arm_key: str = ""
    theme: str = ""
    hook_type: str = ""
    aggregated_engagement_rate: float = 0.0
    success: bool = False
    observed_at: Optional[str] = None


@dataclass
class ContentEval:
    id: Optional[int] = None
    content_id: str = ""
    criterion: str = ""
    passed: bool = False
    evaluated_at: Optional[str] = None


EVAL_CRITERIA = [
    "hook",
    "first_frame",
    "narrative_arc",
    "specificity",
    "caption",
    "scene_progression",
    "standalone_value",
]


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
class ResearchSnapshot:
    """Stored research insight for prompt injection. Matched by product, platform, format."""
    id: str = ""
    product_sku: Optional[str] = None
    platform: Optional[str] = None
    creative_format: Optional[str] = None
    summary: str = ""
    source_type: str = "manual"  # manual, creatives, comments, platform_notes
    created_at: Optional[str] = None


@dataclass
class TextInsight:
    """Stored text-level insight matched by product, platform, and format scope."""
    id: str = ""
    product_sku: Optional[str] = None
    platform: Optional[str] = None
    creative_format: Optional[str] = None
    insight_text: str = ""
    source_post_count: int = 0
    created_at: Optional[str] = None


@dataclass
class BanditRecommendation:
    """Output from bandit.recommend() — global allocation across products."""
    allocations: list[ThemeHookAllocation] = field(default_factory=list)


@dataclass
class ThemeHookAllocation:
    theme: str
    hook_type: str
    count: int
    score: float = 0.0
    arm_key: str = ""  # For lookup of the selected theme/hook arm
