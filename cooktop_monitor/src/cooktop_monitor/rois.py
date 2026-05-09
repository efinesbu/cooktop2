"""Load panel_layout.yaml and compute absolute ROI rectangles."""

from __future__ import annotations

import math
import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Annotated, Any, Optional

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    model_validator,
)

_REQUIRED_ELEMENT_IDS: frozenset[str] = frozenset({"A", "B", "C", "E", "F"})
_FRAC_UPPER = 1.0 + 1e-9


FractionalRect = tuple[float, float, float, float]
PixelRect = tuple[int, int, int, int]


def _validate_fractional_rect(value: object) -> FractionalRect:
    if isinstance(value, (tuple, list)):
        seq: Sequence[Any] = value
    else:
        raise TypeError("fractional rectangle must be a sequence of four numbers")

    if len(seq) != 4:
        raise ValueError("fractional rectangle must have exactly four components")

    x1, y1, x2, y2 = (float(seq[0]), float(seq[1]), float(seq[2]), float(seq[3]))
    for i, v in enumerate((x1, y1, x2, y2)):
        if not math.isfinite(v):
            raise ValueError(f"rectangle component {i} is not finite")
        if v < 0.0 or v > _FRAC_UPPER:
            raise ValueError(f"rectangle component {i} must be within [0.0, 1.0]")
    if not (x1 < x2 and y1 < y2):
        raise ValueError("rectangle must have x1 < x2 and y1 < y2 with positive area")

    return (x1, y1, x2, y2)


def _validate_optional_zone_dots(value: object) -> FractionalRect | None:
    if value is None:
        return None
    return _validate_fractional_rect(value)


ValidatedFractionalRect = Annotated[FractionalRect, BeforeValidator(_validate_fractional_rect)]
OptionalZoneDots = Annotated[
    Optional[FractionalRect],
    BeforeValidator(_validate_optional_zone_dots),
]


class ElementLayout(BaseModel):
    cluster_bbox: ValidatedFractionalRect
    on_indicator: ValidatedFractionalRect
    digit_area: ValidatedFractionalRect
    bar_graph: ValidatedFractionalRect
    label_melt: ValidatedFractionalRect
    label_kwarm: ValidatedFractionalRect
    label_simmer: ValidatedFractionalRect
    label_boil: ValidatedFractionalRect
    zone_size_dots: OptionalZoneDots = None


class TimerLayout(BaseModel):
    digit_area: ValidatedFractionalRect
    minutes_label: ValidatedFractionalRect


class ControlLockLayout(BaseModel):
    text_area: ValidatedFractionalRect


class PanelLayout(BaseModel):
    elements: dict[str, ElementLayout]
    timer: TimerLayout
    control_lock: ControlLockLayout

    @model_validator(mode="after")
    def _elements_contain_required_ids(self) -> PanelLayout:
        missing = _REQUIRED_ELEMENT_IDS - self.elements.keys()
        if missing:
            raise ValueError(
                f"panel layout missing required element keys: {sorted(missing)}"
            )
        return self


def load_layout(path: Path | str) -> PanelLayout:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    try:
        raw = p.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ValueError(f"invalid YAML in panel layout: {p}") from e

    if data is None:
        raise ValueError(f"invalid YAML in panel layout: {p}")
    return PanelLayout.model_validate(data)


def _fractional_rect_to_yaml_list(rect: FractionalRect) -> list[float]:
    return [round(x, 6) for x in rect]


def _layout_dict_for_yaml(layout: PanelLayout) -> dict[str, Any]:
    dumped = layout.model_dump(mode="python")
    elements: dict[str, Any] = {}
    for eid, edata in dumped["elements"].items():
        el: dict[str, Any] = {}
        for key, val in edata.items():
            if key == "zone_size_dots":
                el[key] = None if val is None else _fractional_rect_to_yaml_list(val)
            else:
                el[key] = _fractional_rect_to_yaml_list(val)
        elements[eid] = el

    timer = {
        "digit_area": _fractional_rect_to_yaml_list(dumped["timer"]["digit_area"]),
        "minutes_label": _fractional_rect_to_yaml_list(
            dumped["timer"]["minutes_label"]
        ),
    }
    control_lock = {
        "text_area": _fractional_rect_to_yaml_list(
            dumped["control_lock"]["text_area"]
        ),
    }
    return {"elements": elements, "timer": timer, "control_lock": control_lock}


def save_layout(
    layout: PanelLayout,
    path: Path | str,
    *,
    header_comment: str | None = None,
) -> None:
    p = Path(path)
    payload_dict = _layout_dict_for_yaml(layout)
    body = yaml.safe_dump(
        payload_dict,
        sort_keys=False,
        default_flow_style=None,
    )

    lines: list[str] = []
    if header_comment is not None:
        for line in header_comment.splitlines():
            lines.append(f"# {line}")
        if lines and body:
            lines.append("")
    lines.append(body)

    out_text = "\n".join(lines)
    if body and not out_text.endswith("\n"):
        out_text += "\n"

    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(out_text, encoding="utf-8")
    os.replace(tmp, p)


def to_absolute(rect_frac: FractionalRect, panel_bbox: PixelRect) -> PixelRect:
    px1, py1, px2, py2 = panel_bbox
    pw = px2 - px1
    ph = py2 - py1
    if pw <= 0 or ph <= 0:
        raise ValueError("panel_bbox must have positive width and height")

    x1, y1, x2, y2 = rect_frac
    return (
        int(round(px1 + x1 * pw)),
        int(round(py1 + y1 * ph)),
        int(round(px1 + x2 * pw)),
        int(round(py1 + y2 * ph)),
    )


def clamp_rect(rect: PixelRect, image_size_wh: tuple[int, int]) -> PixelRect:
    w, h = image_size_wh
    x1, y1, x2, y2 = rect
    x1c = max(0, min(int(x1), w))
    x2c = max(0, min(int(x2), w))
    y1c = max(0, min(int(y1), h))
    y2c = max(0, min(int(y2), h))
    if x2c <= x1c or y2c <= y1c:
        raise ValueError(
            f"clamped ROI has zero size: ({x1c}, {y1c}, {x2c}, {y2c}) for image size {w}x{h}"
        )
    return (x1c, y1c, x2c, y2c)


def _yield_element_rects(
    element_id: str,
    element: ElementLayout,
) -> Iterator[tuple[str, str, FractionalRect]]:
    yield (element_id, "cluster_bbox", element.cluster_bbox)
    yield (element_id, "on_indicator", element.on_indicator)
    yield (element_id, "digit_area", element.digit_area)
    yield (element_id, "bar_graph", element.bar_graph)
    yield (element_id, "label_melt", element.label_melt)
    yield (element_id, "label_kwarm", element.label_kwarm)
    yield (element_id, "label_simmer", element.label_simmer)
    yield (element_id, "label_boil", element.label_boil)
    if element.zone_size_dots is not None:
        yield (element_id, "zone_size_dots", element.zone_size_dots)


def iter_element_rois(
    layout: PanelLayout,
    panel_bbox: PixelRect,
    image_size_wh: tuple[int, int],
) -> Iterator[tuple[str, str, PixelRect]]:
    for element_id in sorted(layout.elements):
        element = layout.elements[element_id]
        for eid, roi_name, frac in _yield_element_rects(element_id, element):
            abs_rect = to_absolute(frac, panel_bbox)
            yield (eid, roi_name, clamp_rect(abs_rect, image_size_wh))


def iter_global_rois(
    layout: PanelLayout,
    panel_bbox: PixelRect,
    image_size_wh: tuple[int, int],
) -> Iterator[tuple[str, str, PixelRect]]:
    for roi_name, frac in (
        ("digit_area", layout.timer.digit_area),
        ("minutes_label", layout.timer.minutes_label),
    ):
        abs_rect = to_absolute(frac, panel_bbox)
        yield ("timer", roi_name, clamp_rect(abs_rect, image_size_wh))

    abs_lock = to_absolute(layout.control_lock.text_area, panel_bbox)
    yield ("control_lock", "text_area", clamp_rect(abs_lock, image_size_wh))
