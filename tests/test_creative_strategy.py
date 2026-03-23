"""Tests for resolve_deterministic_fields and creative strategy."""

from __future__ import annotations

from src.creative_strategy import resolve_deterministic_fields, resolve_v5_fields
from src.models import (
    CTA_TYPES,
    HOOK_TYPES,
    PROOF_TYPES,
    SCRIPT_STYLES,
    THEMES,
    V5_NAMES,
    V5_VIBES,
    ZODIAC_SIGNS,
)


def test_resolve_deterministic_fields_cli_overrides_all() -> None:
    """CLI-provided values take precedence over round-robin."""
    resolved = resolve_deterministic_fields(
        theme="stakes_cost_of_inaction",
        hook_type="relatable_pain",
        cta_type="shop_now",
        proof_type="testimonial",
        script_style="storytelling",
        generation_index=99,
    )
    assert resolved["theme"] == "stakes_cost_of_inaction"
    assert resolved["hook_type"] == "relatable_pain"
    assert resolved["cta_type"] == "shop_now"
    assert resolved["proof_type"] == "testimonial"
    assert resolved["script_style"] == "storytelling"


def test_resolve_deterministic_fields_round_robin_when_none_provided() -> None:
    """Round-robin selects from whitelist when no CLI values."""
    resolved0 = resolve_deterministic_fields(
        theme=None,
        hook_type=None,
        cta_type=None,
        proof_type=None,
        script_style=None,
        generation_index=0,
    )
    resolved1 = resolve_deterministic_fields(
        theme=None,
        hook_type=None,
        cta_type=None,
        proof_type=None,
        script_style=None,
        generation_index=1,
    )
    assert resolved0["theme"] == THEMES[0]
    assert resolved1["theme"] == THEMES[1]
    assert resolved0["hook_type"] == HOOK_TYPES[0]
    assert resolved0["cta_type"] == CTA_TYPES[0]
    assert resolved0["proof_type"] == PROOF_TYPES[0]
    assert resolved0["script_style"] == SCRIPT_STYLES[0]


def test_resolve_deterministic_fields_invalid_cli_falls_to_round_robin() -> None:
    """Invalid CLI value falls through to round-robin."""
    resolved = resolve_deterministic_fields(
        theme="invalid_theme",
        hook_type=None,
        cta_type="invalid_cta",
        proof_type=None,
        script_style=None,
        generation_index=0,
    )
    assert resolved["theme"] == THEMES[0]
    assert resolved["hook_type"] == HOOK_TYPES[0]
    assert resolved["cta_type"] == CTA_TYPES[0]


def test_resolve_v5_fields_cli_overrides_valid_name_and_horoscope() -> None:
    """Explicit horoscope/name win; vibe follows generation_index round-robin."""
    resolved = resolve_v5_fields(
        name="jessica",
        horoscope="aries",
        generation_index=3,
    )
    assert resolved["theme"] == "aries"
    assert resolved["hook_type"] == "jessica"
    assert resolved["vibe"] == V5_VIBES[3 % len(V5_VIBES)]
    assert resolved["cta_type"] == "soft_cta"
    assert resolved["proof_type"] == "none"
    assert resolved["script_style"] == "conversational"


def test_resolve_v5_fields_invalid_values_fall_back_to_generation_index() -> None:
    """Invalid or missing horoscope/name use whitelist slots from generation_index."""
    idx = 4
    resolved = resolve_v5_fields(
        name="not_a_v5_presenter",
        horoscope="not_a_sign",
        generation_index=idx,
    )
    assert resolved["theme"] == ZODIAC_SIGNS[idx % len(ZODIAC_SIGNS)]
    assert resolved["hook_type"] == V5_NAMES[idx % len(V5_NAMES)]
    assert resolved["vibe"] == V5_VIBES[idx % len(V5_VIBES)]


def test_resolve_v5_fields_whitespace_and_case_normalized() -> None:
    resolved = resolve_v5_fields(name="  EMILY  ", horoscope=" Leo ")
    assert resolved["theme"] == "leo"
    assert resolved["hook_type"] == "emily"
