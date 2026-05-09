"""Synthetic tests for panel localization."""

from __future__ import annotations

import numpy as np

from cooktop_monitor.locate import locate_panel


def test_locate_panel_no_red_falls_back_to_lower_right_quadrant() -> None:
    img = np.zeros((200, 100, 3), dtype=np.uint8)
    assert locate_panel(img) == (50, 100, 100, 200)


def test_locate_panel_expands_bbox_by_20_percent() -> None:
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[40:50, 40:50] = (0, 0, 255)

    x1, y1, x2, y2 = locate_panel(img)

    assert 45 - 6 <= x1 <= 45 - 5
    assert 45 - 6 <= y1 <= 45 - 5
    assert 45 + 5 <= x2 <= 45 + 6
    assert 45 + 5 <= y2 <= 45 + 6


def test_locate_panel_clamps_to_image_bounds() -> None:
    img = np.zeros((50, 80, 3), dtype=np.uint8)
    img[0:15, 0:20] = (0, 0, 255)

    x1, y1, x2, y2 = locate_panel(img)

    assert 0 <= x1 < x2 <= 80
    assert 0 <= y1 < y2 <= 50
