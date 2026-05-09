"""Pydantic / dataclass models for JSON output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ElementState = Literal["off", "active", "unknown"]
ElementLabel = Literal["MELT", "K. WARM", "SIMMER", "BOIL"]


class ElementReading(BaseModel):
    state: ElementState
    level: int | None = None
    label: ElementLabel | None = None
    hot: bool = False
    zone_size: int | None = None


class TimerReading(BaseModel):
    running: bool
    minutes_remaining: int | None = None


class CooktopState(BaseModel):
    image_path: str
    timestamp: str
    cooktop_on: bool
    control_lock: bool
    timer: TimerReading
    elements: dict[str, ElementReading]
    confidence: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
