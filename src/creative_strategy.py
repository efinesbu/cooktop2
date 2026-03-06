from __future__ import annotations

from typing import Iterable

from src.models import (
    HOOK_DEFINITIONS,
    HOOK_TYPE_MAP,
    HOOK_TYPES,
    THEME_DEFINITIONS,
    THEME_MAP,
    THEMES,
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
    try:
        return THEME_MAP[theme_id]
    except KeyError as exc:
        raise ValueError(
            f"Unknown theme '{theme_id}'. Allowed values: {', '.join(THEMES)}"
        ) from exc


def get_hook(hook_id: str) -> HookDefinition:
    try:
        return HOOK_TYPE_MAP[hook_id]
    except KeyError as exc:
        raise ValueError(
            f"Unknown hook_type '{hook_id}'. Allowed values: {', '.join(HOOK_TYPES)}"
        ) from exc


def base_weight(theme_id: str, hook_id: str) -> float:
    theme = get_theme(theme_id)
    hook = get_hook(hook_id)
    return theme.default_weight * hook.default_weight


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
