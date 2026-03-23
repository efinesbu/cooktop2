from __future__ import annotations

from typing import Iterable

from src.models import (
    CTA_TYPES,
    HOOK_DEFINITIONS,
    HOOK_TYPE_MAP,
    HOOK_TYPES,
    PROOF_TYPES,
    SCRIPT_STYLES,
    THEME_DEFINITIONS,
    THEME_MAP,
    THEMES,
    V5_NAMES,
    V5_VIBES,
    ZODIAC_SIGNS,
    HookDefinition,
    ThemeDefinition,
)


def list_themes(ids: Iterable[str] | None = None) -> list[ThemeDefinition]:
    if ids is None:
        return list(THEME_DEFINITIONS)
    ordered = []
    for theme_id in ids:
        ordered.append(get_theme(theme_id))
    return ordered


def list_hooks(ids: Iterable[str] | None = None) -> list[HookDefinition]:
    if ids is None:
        return list(HOOK_DEFINITIONS)
    ordered = []
    for hook_id in ids:
        ordered.append(get_hook(hook_id))
    return ordered


def get_theme(theme_id: str) -> ThemeDefinition:
    if theme_id in THEME_MAP:
        return THEME_MAP[theme_id]
    # Legacy/unknown: use neutral weight so reports and bandit display still work
    return ThemeDefinition(
        id=theme_id,
        label=theme_id,
        summary="Legacy or unknown theme",
        prompt_guidance="",
        default_weight=1.0,
    )


def get_hook(hook_id: str) -> HookDefinition:
    if hook_id in HOOK_TYPE_MAP:
        return HOOK_TYPE_MAP[hook_id]
    # Legacy/unknown: use neutral weight so reports and bandit display still work
    return HookDefinition(
        id=hook_id,
        label=hook_id,
        summary="Legacy or unknown hook type",
        prompt_guidance="",
        default_weight=1.0,
    )


def base_weight(theme_id: str, hook_id: str) -> float:
    theme = get_theme(theme_id)
    hook = get_hook(hook_id)
    return theme.default_weight * hook.default_weight


def resolve_deterministic_fields(
    theme: str | None,
    hook_type: str | None,
    cta_type: str | None,
    proof_type: str | None,
    script_style: str | None,
    generation_index: int = 0,
    video_v3: bool = False,
) -> dict[str, str]:
    """Resolve all deterministic strategy fields before the LLM call.

    Priority: CLI-provided value > round-robin over whitelist.
    Bandit recommendations are passed in by the caller as theme/hook_type.

    When video_v3=True, only theme is resolved upfront. The remaining fields
    (hook_type, cta_type, proof_type, script_style) are classified
    post-generation and set to placeholders here.
    """
    resolved: dict[str, str] = {}

    resolved["theme"] = (
        theme.strip() if theme and theme.strip() in THEMES else
        THEMES[generation_index % len(THEMES)]
    )

    if video_v3:
        resolved["hook_type"] = "bold_claim"
        resolved["cta_type"] = "soft_cta"
        resolved["proof_type"] = "none"
        resolved["script_style"] = "conversational"
        return resolved

    resolved["hook_type"] = (
        hook_type.strip() if hook_type and hook_type.strip() in HOOK_TYPES else
        HOOK_TYPES[generation_index % len(HOOK_TYPES)]
    )
    resolved["cta_type"] = (
        cta_type.strip() if cta_type and cta_type.strip() in CTA_TYPES else
        CTA_TYPES[generation_index % len(CTA_TYPES)]
    )
    resolved["proof_type"] = (
        proof_type.strip() if proof_type and proof_type.strip() in PROOF_TYPES else
        PROOF_TYPES[generation_index % len(PROOF_TYPES)]
    )
    resolved["script_style"] = (
        script_style.strip() if script_style and script_style.strip() in SCRIPT_STYLES else
        SCRIPT_STYLES[generation_index % len(SCRIPT_STYLES)]
    )

    return resolved


def resolve_v5_fields(
    name: str | None,
    horoscope: str | None,
    generation_index: int = 0,
) -> dict[str, str]:
    """Resolve V5 horoscope reel strategy fields before generation.

    Maps horoscope sign to `theme`, presenter name to `hook_type`, and picks `vibe`
    from V5_VIBES using the same index-stable round-robin as `resolve_deterministic_fields`.
    Missing or invalid name/horoscope values fall back to whitelists by `generation_index`.
    """
    def _norm(token: str | None) -> str | None:
        if token is None:
            return None
        t = token.strip().lower()
        return t if t else None

    n = _norm(name)
    h = _norm(horoscope)

    resolved_name = n if n in V5_NAMES else V5_NAMES[generation_index % len(V5_NAMES)]
    resolved_sign = (
        h if h in ZODIAC_SIGNS else ZODIAC_SIGNS[generation_index % len(ZODIAC_SIGNS)]
    )
    vibe = V5_VIBES[generation_index % len(V5_VIBES)]

    return {
        "theme": resolved_sign,
        "hook_type": resolved_name,
        "vibe": vibe,
        "cta_type": "soft_cta",
        "proof_type": "none",
        "script_style": "conversational",
    }


def whitelist_prompt_lines(
    theme_ids: Iterable[str] | None = None,
    hook_ids: Iterable[str] | None = None,
) -> list[str]:
    lines = ["Allowed themes:"]
    for theme in list_themes(theme_ids):
        lines.append(
            f"  - {theme.id}: {theme.summary} Guidance: {theme.prompt_guidance}"
        )

    lines.append("Allowed hook types:")
    for hook in list_hooks(hook_ids):
        lines.append(
            f"  - {hook.id}: {hook.summary} Guidance: {hook.prompt_guidance}"
        )
    return lines
