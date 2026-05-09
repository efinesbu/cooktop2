"""Tests for panel layout loading, ROI helpers, and serialization."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from cooktop_monitor.rois import (
    clamp_rect,
    iter_element_rois,
    load_layout,
    save_layout,
    to_absolute,
)

PANEL_LAYOUT_PATH = Path(__file__).resolve().parents[1] / "panel_layout.yaml"


def test_load_existing_panel_layout_yaml() -> None:
    layout = load_layout(PANEL_LAYOUT_PATH)
    assert set(layout.elements) >= {"A", "B", "C", "E", "F"}
    assert layout.elements["F"].zone_size_dots is not None
    assert layout.elements["A"].zone_size_dots is None


def test_load_layout_missing_required_element(tmp_path: Path) -> None:
    data = yaml.safe_load(PANEL_LAYOUT_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    elements = data["elements"]
    assert isinstance(elements, dict)
    del elements["B"]
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_layout(bad)


def test_load_layout_rejects_inverted_rect(tmp_path: Path) -> None:
    data = yaml.safe_load(PANEL_LAYOUT_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    elements = data["elements"]
    assert isinstance(elements, dict)
    a = elements["A"]
    assert isinstance(a, dict)
    a["cluster_bbox"] = [0.5, 0.5, 0.4, 0.6]
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_layout(bad)


def test_load_layout_rejects_out_of_range(tmp_path: Path) -> None:
    data = yaml.safe_load(PANEL_LAYOUT_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    elements = data["elements"]
    assert isinstance(elements, dict)
    a = elements["A"]
    assert isinstance(a, dict)
    a["cluster_bbox"] = [-0.1, 0.0, 0.5, 0.5]
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_layout(bad)


def test_to_absolute_basic() -> None:
    assert to_absolute((0.0, 0.0, 1.0, 1.0), (100, 200, 300, 500)) == (
        100,
        200,
        300,
        500,
    )
    assert to_absolute((0.5, 0.0, 1.0, 0.5), (0, 0, 200, 100)) == (100, 0, 200, 50)


def test_clamp_rect_clips_and_raises_on_empty() -> None:
    assert clamp_rect((8, 8, 20, 20), (10, 10)) == (8, 8, 10, 10)
    with pytest.raises(ValueError, match="clamped ROI has zero size"):
        clamp_rect((20, 20, 30, 30), (10, 10))


def test_iter_element_rois_emits_zone_dots_only_for_dual_elements() -> None:
    layout = load_layout(PANEL_LAYOUT_PATH)
    panel_bbox: tuple[int, int, int, int] = (0, 0, 1000, 1000)
    image_wh = (2000, 2000)
    by_element: dict[str, set[str]] = {}
    for element_id, roi_name, _rect in iter_element_rois(layout, panel_bbox, image_wh):
        by_element.setdefault(element_id, set()).add(roi_name)

    base = {
        "cluster_bbox",
        "on_indicator",
        "digit_area",
        "bar_graph",
        "label_melt",
        "label_kwarm",
        "label_simmer",
        "label_boil",
    }
    for eid in sorted(layout.elements):
        assert base <= by_element[eid], (eid, by_element.get(eid))

    with_dots = {eid for eid, names in by_element.items() if "zone_size_dots" in names}
    assert with_dots == {"C", "F"}


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    layout = load_layout(PANEL_LAYOUT_PATH)
    before = layout.model_dump()
    out = tmp_path / "out.yaml"
    save_layout(layout, out)
    reloaded = load_layout(out)
    assert reloaded.model_dump() == before
