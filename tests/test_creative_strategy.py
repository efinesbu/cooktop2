"""Tests for resolve_deterministic_fields and creative strategy."""

from __future__ import annotations

import pytest

from src.creative_strategy import resolve_deterministic_fields
from src.models import CTA_TYPES, HOOK_TYPES, PROOF_TYPES, SCRIPT_STYLES, THEMES


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
