"""Interactive ROI calibration: walk panel and element rectangles on a reference photo.

Loads a reference image, collects pixel rectangles via OpenCV click-drag where available,
maps them to fractional coordinates inside the confirmed panel ROI, validates with
`PanelLayout`, and writes `panel_layout.yaml` via `save_layout`.

Use ``--no-display`` (or headless fallback when no GUI is available) to print the ROI
prompt order without opening a window or mutating disk layout files.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cooktop_monitor.rois import PanelLayout, clamp_rect, load_layout, save_layout, to_absolute

_ELEMENT_ORDER = ("A", "B", "C", "E", "F")
_ELEMENT_ROIS = (
    "cluster_bbox",
    "on_indicator",
    "digit_area",
    "bar_graph",
    "label_melt",
    "label_kwarm",
    "label_simmer",
    "label_boil",
    "zone_size_dots",
)
_VALID_START_FROM: frozenset[str]


def ordered_roi_keys() -> list[str]:
    keys: list[str] = ["panel_bbox"]
    for eid in _ELEMENT_ORDER:
        for roi in _ELEMENT_ROIS:
            keys.append(f"{eid}.{roi}")
    keys.extend(["timer.digit_area", "timer.minutes_label", "control_lock.text_area"])
    return keys


_VALID_START_FROM = frozenset(ordered_roi_keys())


def is_optional_zone(roi_key: str) -> bool:
    return roi_key in ("A.zone_size_dots", "B.zone_size_dots", "E.zone_size_dots")


def fractional_from_pixels(
    rect_px: tuple[int, int, int, int],
    panel_bbox: tuple[int, int, int, int],
) -> tuple[float, float, float, float]:
    rx1, ry1, rx2, ry2 = rect_px
    px1, py1, px2, py2 = panel_bbox
    pw = float(px2 - px1)
    ph = float(py2 - py1)
    if pw <= 0.0 or ph <= 0.0:
        raise ValueError("panel_bbox must have positive width and height")

    def _clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    return (
        _clamp((float(rx1) - float(px1)) / pw),
        _clamp((float(ry1) - float(py1)) / ph),
        _clamp((float(rx2) - float(px1)) / pw),
        _clamp((float(ry2) - float(py1)) / ph),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m cooktop_monitor.tools.calibrate",
        description=(
            "Interactively calibrate panel_layout.yaml ROIs on a reference cooktop photo."
        ),
    )
    p.add_argument(
        "reference_image",
        type=Path,
        help="Reference photo (JPEG/PNG/…) used for drawing ROIs.",
    )
    p.add_argument(
        "--layout",
        type=Path,
        default=Path("panel_layout.yaml"),
        help="Output YAML path (default: ./panel_layout.yaml).",
    )
    p.add_argument(
        "--panel-bbox",
        dest="panel_bbox",
        nargs=4,
        type=int,
        metavar=("X1", "Y1", "X2", "Y2"),
        help="Optional panel rectangle in image pixels (overrides auto-detect suggestion).",
    )
    p.add_argument(
        "--start-from",
        dest="start_from",
        action="append",
        default=None,
        metavar="KEY",
        help=(
            "Re-prompt only these ROI keys (subset). May be repeated. "
            "Requires an existing --layout file."
        ),
    )
    p.add_argument(
        "--no-display",
        action="store_true",
        help="Print ROI prompt order, validate inputs, do not open a window or write YAML.",
    )
    return p


def _keys_to_walk(start_from: list[str] | None) -> list[str]:
    full = ordered_roi_keys()
    if not start_from:
        return list(full)
    fs = [x.strip() for x in start_from if x.strip()]
    return [k for k in full if k in fs]


def _validate_start_from_keys(start_from: list[str] | None) -> None:
    if not start_from:
        return
    for raw in start_from:
        k = raw.strip()
        if not k or k not in _VALID_START_FROM:
            raise ValueError(f"invalid --start-from key: {raw!r}")


def _deepest_existing_writable_ancestor(path: Path) -> Path:
    p = path.expanduser().resolve()
    cur: Path = p.parent
    while not cur.exists():
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    if not cur.exists():
        raise FileNotFoundError(f"cannot create layout path: no existing parent for {p}")
    if not os.access(cur, os.W_OK):
        raise PermissionError(f"layout directory not writable: {cur}")
    return cur


def _roi_label(key: str) -> str:
    hints: dict[str, str] = {
        "panel_bbox": "full glass control panel",
        "cluster_bbox": "full element touch cluster",
        "on_indicator": "power/on LED region",
        "digit_area": "7-segment digit block",
        "bar_graph": "vertical power bar",
        "label_melt": "Melt label",
        "label_kwarm": "Keep Warm label",
        "label_simmer": "Simmer label",
        "label_boil": "Boil label",
        "zone_size_dots": "dual-zone size indicator dots",
        "digit_area:timer": "timer digits",
        "minutes_label": "MIN label next to timer",
        "text_area": "Control Lock text region",
    }
    if key == "panel_bbox":
        return f"{key} ({hints['panel_bbox']})"
    if key.startswith("timer."):
        name = key.split(".", 1)[1]
        if name == "digit_area":
            return f"{key} (timer digits)"
        return f"{key} ({hints.get(name, name)})"
    if key.startswith("control_lock."):
        return f"{key} ({hints['text_area']})"
    eid, roi = key.split(".", 1)
    h = hints.get(roi, roi.replace("_", " "))
    return f"{key} (element {eid} {h})"


def _validation_error_to_key(err: ValidationError) -> str | None:
    for e in err.errors():
        loc = e.get("loc", ())
        if len(loc) >= 1 and loc[0] == "elements" and len(loc) >= 3:
            return f"{loc[1]}.{loc[2]}"
        if len(loc) >= 2 and loc[0] == "timer":
            return f"timer.{loc[1]}"
        if len(loc) >= 2 and loc[0] == "control_lock":
            return f"control_lock.{loc[1]}"
    return None


def _build_layout_dict_unified(
    existing: PanelLayout | None,
    session_results: dict[str, tuple[int, int, int, int] | None],
    assumed_panel: tuple[int, int, int, int],
    panel_final: tuple[int, int, int, int],
    image_wh: tuple[int, int],
) -> dict[str, Any]:
    """Combine session pixel ROIs with existing fractionals (rebased through assumed_panel)."""
    w, h = image_wh
    pb = panel_final

    def pixel_for_element(eid: str, roi: str) -> tuple[int, int, int, int] | None:
        k = f"{eid}.{roi}"
        if k in session_results:
            return session_results[k]
        if existing is None:
            raise ValueError(f"missing ROI capture for {k}")
        frac = getattr(existing.elements[eid], roi)
        if frac is None:
            return None
        return clamp_rect(to_absolute(frac, assumed_panel), (w, h))

    elements: dict[str, Any] = {}
    for eid in _ELEMENT_ORDER:
        row: dict[str, Any] = {}
        for roi in _ELEMENT_ROIS:
            px = pixel_for_element(eid, roi)
            if roi == "zone_size_dots" and px is None:
                row[roi] = None
            elif px is None:
                raise ValueError(f"missing pixel ROI for {eid}.{roi}")
            else:
                row[roi] = list(fractional_from_pixels(px, pb))
        elements[eid] = row

    def px_timer_roi(name: str) -> tuple[int, int, int, int]:
        k = f"timer.{name}"
        if k in session_results:
            p = session_results[k]
            if p is None:
                raise ValueError(f"missing {k}")
            return p
        if existing is None:
            raise ValueError(f"missing {k}")
        return clamp_rect(to_absolute(getattr(existing.timer, name), assumed_panel), (w, h))

    timer = {
        "digit_area": list(fractional_from_pixels(px_timer_roi("digit_area"), pb)),
        "minutes_label": list(fractional_from_pixels(px_timer_roi("minutes_label"), pb)),
    }

    ck = "control_lock.text_area"
    if ck in session_results:
        pc = session_results[ck]
        if pc is None:
            raise ValueError(f"missing {ck}")
    elif existing is None:
        raise ValueError(f"missing {ck}")
    else:
        pc = clamp_rect(to_absolute(existing.control_lock.text_area, assumed_panel), (w, h))

    control_lock = {"text_area": list(fractional_from_pixels(pc, pb))}
    return {"elements": elements, "timer": timer, "control_lock": control_lock}


def _pixel_hints_from_existing(
    layout: PanelLayout,
    panel_bbox: tuple[int, int, int, int],
    image_wh: tuple[int, int],
) -> dict[str, tuple[int, int, int, int]]:
    w, h = image_wh
    out: dict[str, tuple[int, int, int, int]] = {}
    pb = panel_bbox
    for eid in _ELEMENT_ORDER:
        el = layout.elements[eid]
        for roi in _ELEMENT_ROIS:
            k = f"{eid}.{roi}"
            frac = getattr(el, roi)
            if frac is None:
                continue
            out[k] = clamp_rect(to_absolute(frac, pb), (w, h))
    td = clamp_rect(to_absolute(layout.timer.digit_area, pb), (w, h))
    tm = clamp_rect(to_absolute(layout.timer.minutes_label, pb), (w, h))
    tc = clamp_rect(to_absolute(layout.control_lock.text_area, pb), (w, h))
    out["timer.digit_area"] = td
    out["timer.minutes_label"] = tm
    out["control_lock.text_area"] = tc
    return out


def _try_display_available() -> bool:
    try:
        import cv2

        cv2.namedWindow("_cooktop_cal_probe", cv2.WINDOW_NORMAL)
        cv2.destroyWindow("_cooktop_cal_probe")
        cv2.waitKey(1)
    except Exception:
        return False
    return True


def _normalize_rect(x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    nx1, nx2 = (x0, x1) if x0 <= x1 else (x1, x0)
    ny1, ny2 = (y0, y1) if y0 <= y1 else (y1, y0)
    return (nx1, ny1, nx2, ny2)


def _valid_pixel_rect(r: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = r
    return x1 < x2 and y1 < y2


def _interactive_session(
    image: Any,
    keys: list[str],
    *,
    initial_panel_suggestion: tuple[int, int, int, int],
    existing_layout: PanelLayout | None,
    hint_reference_panel: tuple[int, int, int, int],
    clip_panel_fallback: tuple[int, int, int, int],
    image_wh: tuple[int, int, int],
) -> tuple[dict[str, tuple[int, int, int, int] | None], tuple[int, int, int, int]]:
    import cv2

    import numpy as np

    h, w = int(image_wh[0]), int(image_wh[1])
    win = "Cooktop ROI calibrate"

    class _UI:
        dragging = False
        drag_start: tuple[int, int] = (0, 0)
        drag_cur: tuple[int, int] = (0, 0)
        pending: tuple[int, int, int, int] | None = None

    ui = _UI()
    results: dict[str, tuple[int, int, int, int] | None] = {}
    hints: dict[str, tuple[int, int, int, int]] = {}
    if existing_layout is not None:
        hints = _pixel_hints_from_existing(
            existing_layout, hint_reference_panel, (w, h)
        )

    def display_panel() -> tuple[int, int, int, int]:
        pb = results.get("panel_bbox")
        if pb is not None:
            return pb
        return clip_panel_fallback

    def draw_overlay(canvas: np.ndarray, cur_key: str) -> None:
        pb = display_panel()
        faded = (60, 200, 60)
        bright = (60, 60, 255)
        # Draw saved ROIs (except current key) in green
        for rk, rv in results.items():
            if rk == cur_key or rv is None:
                continue
            if rk == "panel_bbox":
                x1, y1, x2, y2 = rv
                cv2.rectangle(canvas, (x1, y1), (x2, y2), faded, 2)
                continue
            x1, y1, x2, y2 = rv
            cv2.rectangle(canvas, (x1, y1), (x2, y2), faded, 1)
        # Non-walked hints from YAML (merge mode): light green dashed effect via thin
        if existing_layout is not None:
            for hk, hv in hints.items():
                if hk in results or hk == cur_key:
                    continue
                x1, y1, x2, y2 = hv
                cv2.rectangle(canvas, (x1, y1), (x2, y2), (40, 160, 40), 1)
        # Current rect
        rect_draw: tuple[int, int, int, int] | None = None
        if ui.dragging:
            rect_draw = _normalize_rect(
                ui.drag_start[0],
                ui.drag_start[1],
                ui.drag_cur[0],
                ui.drag_cur[1],
            )
        elif ui.pending is not None:
            rect_draw = ui.pending
        if rect_draw is not None and _valid_pixel_rect(rect_draw):
            x1, y1, x2, y2 = rect_draw
            cv2.rectangle(canvas, (x1, y1), (x2, y2), bright, 2)
        msg = _roi_label(cur_key)
        cv2.putText(
            canvas,
            msg,
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            "Enter=accept r=reset s=skip b=back q=quit",
            (10, h - 16),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )

    def on_mouse(event: int, mx: int, my: int, _flags: int, _param: Any) -> None:
        mx = max(0, min(mx, w - 1))
        my = max(0, min(my, h - 1))
        if event == cv2.EVENT_LBUTTONDOWN:
            ui.dragging = True
            ui.drag_start = (mx, my)
            ui.drag_cur = (mx, my)
            ui.pending = None
        elif event == cv2.EVENT_MOUSEMOVE and ui.dragging:
            ui.drag_cur = (mx, my)
        elif event == cv2.EVENT_LBUTTONUP and ui.dragging:
            ui.dragging = False
            ui.drag_cur = (mx, my)
            ui.pending = _normalize_rect(ui.drag_start[0], ui.drag_start[1], mx, my)

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(win, on_mouse)

    idx = 0
    aborted = False

    try:
        while idx < len(keys):
            key = keys[idx]
            optional_skip = key.endswith("zone_size_dots") and is_optional_zone(
                key
            )
            ui.pending = None
            ui.dragging = False
            if key == "panel_bbox":
                ui.pending = _normalize_rect(*initial_panel_suggestion)
            elif key in hints:
                ui.pending = hints[key]

            while True:
                canvas = image.copy()
                draw_overlay(canvas, key)
                cv2.imshow(win, canvas)
                ch = cv2.waitKey(30) & 0xFF
                if ch in (13, 10):  # Enter / LF
                    cand = ui.pending
                    if cand is None or not _valid_pixel_rect(cand):
                        sys.stderr.write(
                            f"{key}: drag a rectangle with positive area, then press Enter.\n"
                        )
                        continue
                    cx1, cy1, cx2, cy2 = cand
                    if key == "panel_bbox":
                        try:
                            clamped = clamp_rect(cand, (w, h))
                        except ValueError as exc:
                            sys.stderr.write(f"{key}: {exc}\n")
                            continue
                        results[key] = clamped
                        idx += 1
                        break
                    pb = display_panel()
                    if not (
                        cx1 >= pb[0]
                        and cy1 >= pb[1]
                        and cx2 <= pb[2]
                        and cy2 <= pb[3]
                    ):
                        sys.stderr.write(
                            f"{key}: rectangle should lie inside the panel "
                            f"({pb[0]}, {pb[1]}, {pb[2]}, {pb[3]}). Redraw or refine panel (b).\n"
                        )
                        continue
                    results[key] = cand
                    idx += 1
                    break
                if ch == ord("q"):
                    aborted = True
                    break
                if ch == ord("r"):
                    ui.pending = None
                    ui.dragging = False
                if ch == ord("s"):
                    if not optional_skip:
                        sys.stderr.write(f"{key}: skip is only allowed for A/B/E zone_size_dots.\n")
                        continue
                    results[key] = None
                    idx += 1
                    break
                if ch == ord("b"):
                    if idx == 0:
                        sys.stderr.write("Already at first ROI; nothing to go back to.\n")
                        continue
                    idx -= 1
                    prev = keys[idx]
                    results.pop(prev, None)
                    break
            if aborted:
                break
    finally:
        cv2.destroyWindow(win)
        cv2.waitKey(1)

    if aborted:
        raise KeyboardInterrupt("user aborted")

    panel_bbox_final = results.get("panel_bbox")
    if panel_bbox_final is None:
        panel_bbox_final = clip_panel_fallback
    return results, panel_bbox_final


def _run_headless_list(args: argparse.Namespace, keys: list[str]) -> int:
    ref = args.reference_image.expanduser().resolve()
    if not ref.is_file():
        sys.stderr.write(f"reference image not found: {ref}\n")
        return 2
    try:
        _deepest_existing_writable_ancestor(args.layout)
    except (OSError, PermissionError) as e:
        sys.stderr.write(f"cannot write layout path {args.layout}: {e}\n")
        return 2
    if args.start_from:
        lp = args.layout.expanduser().resolve()
        if not lp.is_file():
            sys.stderr.write(f"--start-from requires existing layout file: {lp}\n")
            return 2
        try:
            load_layout(lp)
        except (OSError, ValueError, ValidationError) as e:
            sys.stderr.write(f"failed to load layout {lp}: {e}\n")
            return 2
    for line in keys:
        sys.stdout.write(f"{line}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 2

    try:
        _validate_start_from_keys(args.start_from)
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        return 2

    keys = _keys_to_walk(args.start_from)

    if args.no_display or not _try_display_available():
        return _run_headless_list(args, keys)

    from cooktop_monitor.load import load_image
    from cooktop_monitor.locate import locate_panel

    ref = args.reference_image.expanduser().resolve()
    if not ref.is_file():
        sys.stderr.write(f"reference image not found: {ref}\n")
        return 2

    if args.start_from:
        lp = args.layout.expanduser().resolve()
        if not lp.is_file():
            sys.stderr.write(f"--start-from requires existing layout file: {lp}\n")
            return 2
        try:
            existing = load_layout(lp)
        except (OSError, ValueError, ValidationError) as e:
            sys.stderr.write(f"failed to load layout {lp}: {e}\n")
            return 2
    else:
        existing = None

    try:
        _deepest_existing_writable_ancestor(args.layout)
    except (OSError, PermissionError) as e:
        sys.stderr.write(f"cannot write layout path {args.layout}: {e}\n")
        return 2

    try:
        image = load_image(ref)
    except (OSError, ValueError, FileNotFoundError) as e:
        sys.stderr.write(f"failed to load image {ref}: {e}\n")
        return 2

    h, w = int(image.shape[0]), int(image.shape[1])
    image_wh = (h, w, image.shape[2])

    if args.panel_bbox is not None:
        x1, y1, x2, y2 = args.panel_bbox
        suggestion = _normalize_rect(x1, y1, x2, y2)
    else:
        suggestion = locate_panel(image)

    walked = set(keys)
    if existing is not None:
        if "panel_bbox" in walked:
            assumed = suggestion
            sys.stderr.write(
                "Panel bbox will be re-captured; initial suggestion from "
                f"{'--panel-bbox' if args.panel_bbox is not None else 'locate_panel(image)'}: "
                f"{suggestion}\n"
            )
        else:
            if args.panel_bbox is not None:
                assumed = _normalize_rect(*args.panel_bbox)
                sys.stderr.write(
                    f"Using panel bbox from --panel-bbox (not re-captured): {assumed}\n"
                )
            else:
                assumed = locate_panel(image)
                sys.stderr.write(
                    f"Using panel bbox from locate_panel(image) (not re-captured): {assumed}\n"
                )
    else:
        assumed = suggestion

    try:
        session_results, panel_final = _interactive_session(
            image,
            keys,
            initial_panel_suggestion=suggestion,
            existing_layout=existing,
            hint_reference_panel=assumed,
            clip_panel_fallback=assumed,
            image_wh=image_wh,
        )
    except KeyboardInterrupt:
        return 1

    panel_bbox = panel_final
    img_wh_xy = (w, h)

    while True:
        try:
            data = _build_layout_dict_unified(
                existing, session_results, assumed, panel_bbox, img_wh_xy
            )
            layout = PanelLayout.model_validate(data)
            break
        except ValidationError as e:
            sys.stderr.write(f"layout validation failed:\n{e}\n")
            bad_key = _validation_error_to_key(e)
            rk = bad_key if bad_key is not None else ordered_roi_keys()[0]
            panel_presuggest = panel_bbox
            sys.stderr.write(f"Redo ROI: {rk} ({_roi_label(rk)})\n")
            try:
                redo, _pf = _interactive_session(
                    image,
                    [rk],
                    initial_panel_suggestion=panel_presuggest,
                    existing_layout=existing,
                    hint_reference_panel=assumed,
                    clip_panel_fallback=panel_bbox,
                    image_wh=image_wh,
                )
            except KeyboardInterrupt:
                return 1
            session_results.update(redo)
            if "panel_bbox" in redo and redo["panel_bbox"] is not None:
                panel_bbox = redo["panel_bbox"]

    hdr = (
        f"Generated by cooktop_monitor.tools.calibrate from reference image:\n"
        f"{ref}\n"
        f"Coordinates are fractions of the panel bounding box (see coreidea.md)."
    )
    try:
        save_layout(layout, args.layout, header_comment=hdr)
        round_trip = load_layout(args.layout)
        _ = round_trip  # confirms read
    except (OSError, ValueError, ValidationError) as e:
        sys.stderr.write(f"failed to save layout: {e}\n")
        return 2

    out_path = args.layout.expanduser().resolve()
    sys.stdout.write(f"wrote panel layout: {out_path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
