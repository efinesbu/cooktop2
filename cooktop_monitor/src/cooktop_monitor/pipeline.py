"""Single-image pipeline: load, masks, locate, optional element F signals + CooktopState JSON."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from cooktop_monitor.detect_element import detect_element
from cooktop_monitor.load import load_image
from cooktop_monitor.locate import locate_panel
from cooktop_monitor.mask import bright_red_mask, dim_red_mask
from cooktop_monitor.reconcile import reconcile_element
from cooktop_monitor.rois import PanelLayout, load_layout
from cooktop_monitor.schema import CooktopState, ElementReading, TimerReading


_ELEMENT_IDS: tuple[str, ...] = ("A", "B", "C", "E", "F")


def _bundled_panel_layout_path() -> Path:
    # package: .../src/cooktop_monitor/pipeline.py -> .../src -> .../cooktop_monitor/panel_layout.yaml
    here = Path(__file__).resolve()
    return here.parents[2] / "panel_layout.yaml"


def _try_load_layout(candidate: Path) -> PanelLayout:
    # Propagate FileNotFoundError / ValueError to caller for contextual warnings.
    return load_layout(candidate)


def _maybe_load_panel_layout(
    image_path: Path,
    layout_path: Path | None,
    *,
    warnings: list[str],
) -> PanelLayout | None:
    if layout_path is not None:
        p = Path(layout_path).expanduser()
        if not p.is_file():
            warnings.append(f"panel layout path not found: {p}; skipped element detection")
            return None
        try:
            return _try_load_layout(p)
        except (OSError, ValueError) as e:
            warnings.append(f"panel layout invalid or unreadable ({p}): {e}")
            return None

    searches = [
        image_path.resolve().parent / "panel_layout.yaml",
        Path.cwd() / "panel_layout.yaml",
        _bundled_panel_layout_path(),
    ]
    found: Path | None = None
    for cand in searches:
        if cand.is_file():
            found = cand
            break
    if found is None:
        warnings.append(
            "panel_layout.yaml not found; skipped element detection"
        )
        return None

    try:
        return _try_load_layout(found)
    except (OSError, ValueError) as e:
        warnings.append(f"panel layout invalid or unreadable ({found}): {e}")
        return None


def run_single(
    image_path: Path,
    layout_path: Path | None = None,
    write_debug: bool = True,
) -> dict[str, Any]:
    p = Path(image_path).expanduser()
    try:
        image = load_image(p)
    except Exception as e:
        raise RuntimeError(f"failed to load cooktop image: {p}") from e

    bright_mask = bright_red_mask(image)
    dim_mask = dim_red_mask(image)

    panel_bbox = locate_panel(image)

    red_any = bool(np.any(bright_mask == 255))

    warnings: list[str] = []
    if not red_any:
        warnings.append("no red pixels detected")

    warnings.append("control lock not detected (Phase 4)")
    warnings.append("timer not detected (Phase 4)")

    if write_debug:
        warnings.append("debug image not implemented until Phase 5")

    layout = _maybe_load_panel_layout(p, layout_path, warnings=warnings)

    elements: dict[str, ElementReading] = {}
    confidence: dict[str, float] = {}

    default_reading = ElementReading(
        state="off",
        level=None,
        label=None,
        hot=False,
        zone_size=None,
    )

    for eid in _ELEMENT_IDS:
        confidence[eid] = 0.0
        elements[eid] = default_reading.model_copy(deep=True)
        if eid != "F":
            warnings.append(f"element {eid} not detected (Phase 3 only wires F)")

    if layout is not None:
        try:
            f_layout = layout.elements["F"]
            det = detect_element(
                "F",
                image,
                bright_mask,
                dim_mask,
                f_layout,
                panel_bbox=panel_bbox,
            )
            f_read, f_conf, f_warn = reconcile_element(
                det["signals"],
                has_zone_size=f_layout.zone_size_dots is not None,
            )
            elements["F"] = f_read
            confidence["F"] = float(f_conf)
            warnings.extend(f_warn)
        except Exception as e:  # noqa: BLE001 — CLI must stay robust across ROI anomalies
            warnings.append(f"element F detection failed: {e}")

    cooktop_on = any(
        er.state == "active" or er.hot for er in elements.values()
    )

    state = CooktopState(
        image_path=str(p.resolve()),
        timestamp=_mtime_iso8601_z(p),
        cooktop_on=cooktop_on,
        control_lock=False,
        timer=TimerReading(running=False, minutes_remaining=None),
        elements=elements,
        confidence=confidence,
        warnings=warnings,
    )
    validated = CooktopState.model_validate(state.model_dump(mode="python"))
    print(validated.model_dump_json(indent=2))
    return validated.model_dump(mode="json")


def _mtime_iso8601_z(path: Path) -> str:
    return datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
