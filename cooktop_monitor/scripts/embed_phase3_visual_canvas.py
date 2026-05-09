#!/usr/bin/env python3
"""Embed JPEG thumbnails + Phase 3 report into the Cursor Phase 3 canvas.

Reads:
  - ../reports/phase3_groundtruths.json
  - ../../groundtruths/*.jpeg (matching report rows)

Patches:
  - ~/.cursor/projects/Users-valerifine-Code-cooktop2/canvases/phase3-groundtruths-grid.canvas.tsx
    (override with --canvas)

Writes:
  - ../reports/phase3_visual_report.html — thumbnails inlined as data URLs so images work under file://
    and local HTTP preview (relative ../../groundtruths/ paths break outside the server root).

Also re-embeds PHASE3_JSON from the report file when --refresh-json is passed.

Usage:
  cd cooktop_monitor && ./scripts/embed_phase3_visual_canvas.py
  ./scripts/embed_phase3_visual_canvas.py --canvas /path/to/phase3-groundtruths-grid.canvas.tsx
  ./scripts/embed_phase3_visual_canvas.py --refresh-json
  ./scripts/embed_phase3_visual_canvas.py --html-only
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import re
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "cooktop_monitor" / "reports" / "phase3_groundtruths.json"
HTML_REPORT_PATH = REPO_ROOT / "cooktop_monitor" / "reports" / "phase3_visual_report.html"
GROUND_DIR = REPO_ROOT / "groundtruths"
DEFAULT_CANVAS = (
    Path.home()
    / ".cursor/projects/Users-valerifine-Code-cooktop2/canvases/phase3-groundtruths-grid.canvas.tsx"
)


def _thumb_data_urls(report: list[dict], *, max_size: tuple[int, int], quality: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in report:
        if "error" in row:
            continue
        fn = row["file"]
        path = GROUND_DIR / fn
        if not path.is_file():
            continue
        im = Image.open(path).convert("RGB")
        im.thumbnail(max_size)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
        out[fn] = f"data:image/jpeg;base64,{b64}"
    return out


def _fmt_thumb_block(thumbs: dict[str, str]) -> str:
    lines = ["const FIXTURE_THUMB_DATA_URL: Record<string, string> = {"]
    for k in sorted(thumbs.keys()):
        lines.append(f"  {json.dumps(k)}: {json.dumps(thumbs[k])},")
    lines.append("};")
    return "\n".join(lines)


def _embed_phase3_json(canvas: str, report_path: Path) -> str:
    raw = json.dumps(json.loads(report_path.read_text(encoding="utf-8")), separators=(",", ":"))
    blob_esc = raw.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    marker_start = "const PHASE3_JSON = `"
    marker_end = "`;\n\ntype LabelLit"
    i0 = canvas.find(marker_start)
    if i0 < 0:
        raise ValueError("PHASE3_JSON start not found")
    i1 = i0 + len(marker_start)
    i2 = canvas.find(marker_end, i1)
    if i2 < 0:
        raise ValueError("PHASE3_JSON end not found")
    return canvas[:i1] + blob_esc + canvas[i2:]


_EL_IDS = ("A", "B", "C", "E", "F")


def write_phase3_visual_report_html(report: list[dict], thumbs: dict[str, str]) -> None:
    """Write browseable HTML with inlined JPEG data URLs (works with Live Preview / http.server)."""

    def fmt_sig(s: dict) -> str:
        lit = s["label_lit"]
        lbls = [k for k in ("MELT", "K. WARM", "SIMMER", "BOIL") if lit.get(k)]
        lbl = ",".join(lbls) if lbls else "none"
        zd = s["zone_dot_count"]
        zds = "null" if zd is None else str(zd)
        return (
            f"cluster={s['cluster_lit_fraction']:.3f} on={s['on_indicator_lit_fraction']:.3f} "
            f"bar={s['bar_count']} h={s['h_detected']} labels={lbl} zoneDots={zds}"
        )

    def reading_short(r: dict) -> str:
        lv = "-" if r["level"] is None else str(r["level"])
        lb = r["label"] if r["label"] else "-"
        return f"{r['state']} L{lv} {lb} hot={r['hot']}"

    def active_ids(det: dict) -> str:
        return ",".join(e for e in _EL_IDS if det["elements"][e]["reading"]["state"] == "active") or "none"

    def max_cluster(det: dict) -> tuple[str, float]:
        best, v = "A", -1.0
        for e in _EL_IDS:
            x = float(det["elements"][e]["signals"]["cluster_lit_fraction"])
            if x > v:
                best, v = e, x
        return best, v

    cards: list[str] = []
    for row in report:
        if "error" in row:
            cards.append(
                f'<article class="card err"><h2>{html.escape(row["file"])}</h2>'
                f'<p class="err">{html.escape(row["error"])}</p></article>'
            )
            continue
        fn = row["file"]
        off = row["official_pipeline_json"]
        det = row["detect_reconcile_all_elements"]
        f_off = off["elements"]["F"]
        f_det = det["elements"]["F"]
        mc_id, mc_v = max_cluster(det)
        bbox = det.get("panel_bbox_xyxy")
        bbox_s = ",".join(str(x) for x in bbox) if bbox else "null"
        all_lines = "\n".join(
            f"[{e}] {reading_short(det['elements'][e]['reading'])} | "
            f"{fmt_sig(det['elements'][e]['signals'])}"
            for e in _EL_IDS
        )
        thumb_src = thumbs.get(fn, "")
        if thumb_src:
            img_html = (
                f'<img src="{thumb_src}" alt="{html.escape(fn)}" loading="lazy" width="180" />'
            )
        else:
            img_html = f'<div class="noimg">No thumbnail for {html.escape(fn)}</div>'
        warn_tail = ""
        if f_det["warnings"]:
            warn_tail = " · " + html.escape("; ".join(f_det["warnings"]))
        cards.append(
            f'<article class="card">\n  <div class="row">\n    <figure class="thumb">{img_html}</figure>\n'
            f'    <div class="body">\n      <h2>{html.escape(fn)}</h2>\n'
            '      <p class="pipeline">Pipeline: load → red masks → panel bbox → ROI crops → '
            "signals → reconcile</p>\n"
            f'      <p class="meta">cooktop_on={off["cooktop_on"]} · panel bbox (px) '
            f"{html.escape(bbox_s)}</p>\n"
            f"      <p><strong>Official F</strong> {html.escape(reading_short(f_off))} "
            f'conf={off["confidence"]["F"]:.2f}</p>\n'
            f"      <p class=\"meta\">Active (all ROIs): {html.escape(active_ids(det))} · "
            f"max cluster {mc_id}={mc_v:.3f}</p>\n"
            f"      <p><strong>F signals</strong> {html.escape(fmt_sig(f_det['signals']))}</p>\n"
            f'      <p class="meta">F reconcile conf {f_det["confidence"]:.2f}{warn_tail}</p>\n'
            "      <details><summary>All elements (signals + reading)</summary>"
            f"<pre>{html.escape(all_lines)}</pre></details>\n"
            "    </div>\n  </div>\n</article>"
        )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Phase 3 groundtruths — visualization</title>
  <style>
    :root {{ font-family: system-ui, sans-serif; background: #1e1e1e; color: #e4e4e4; }}
    body {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 1.35rem; font-weight: 600; margin-bottom: 8px; }}
    .sub {{ color: #9d9d9d; font-size: 0.85rem; margin-bottom: 28px; }}
    .card {{ border: 1px solid #3c3c3c; border-radius: 8px; margin-bottom: 20px; padding: 16px;
      background: #252526; }}
    .card.err {{ border-color: #8b6914; }}
    .row {{ display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; }}
    .thumb img {{ display: block; width: 180px; height: auto; border-radius: 4px;
      border: 1px solid #3c3c3c; }}
    .noimg {{ width: 180px; min-height: 120px; display: flex; align-items: center;
      justify-content: center; background: #1e1e1e; border: 1px dashed #3c3c3c;
      border-radius: 4px; font-size: 0.75rem; color: #9d9d9d; padding: 8px; text-align: center; }}
    .body {{ flex: 1; min-width: 240px; }}
    .body h2 {{ font-size: 1rem; margin: 0 0 10px 0; }}
    .pipeline {{ color: #9d9d9d; font-size: 0.8rem; margin: 0 0 8px 0; }}
    .meta {{ color: #9d9d9d; font-size: 0.8rem; margin: 8px 0; }}
    p {{ margin: 6px 0; font-size: 0.88rem; line-height: 1.45; }}
    details {{ margin-top: 12px; }}
    summary {{ cursor: pointer; color: #569cd6; font-size: 0.85rem; }}
    pre {{ margin: 8px 0 0 0; padding: 10px; background: #1e1e1e; border: 1px solid #3c3c3c;
      border-radius: 4px; font-size: 0.72rem; overflow: auto; white-space: pre-wrap; }}
    .err {{ color: #f48771; }}
  </style>
</head>
<body>
  <h1>Phase 3 pipeline — groundtruths visualization</h1>
  <p class="sub">Images are embedded (JPEG data URLs) so previews work in the browser and in IDE Live Preview.
    Regenerate with cooktop_monitor/scripts/embed_phase3_visual_canvas.py --html-only</p>
  {"".join(cards)}
</body>
</html>
"""
    HTML_REPORT_PATH.write_text(doc, encoding="utf-8")


def patch_canvas(canvas_path: Path, *, refresh_json: bool, write_html: bool) -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    canvas = canvas_path.read_text(encoding="utf-8")

    if refresh_json:
        canvas = _embed_phase3_json(canvas, REPORT_PATH)

    thumbs = _thumb_data_urls(report, max_size=(280, 420), quality=52)
    thumb_block = _fmt_thumb_block(thumbs)

    if "FIXTURE_THUMB_DATA_URL" in canvas:
        canvas = re.sub(
            r"\nconst FIXTURE_THUMB_DATA_URL: Record<string, string> = \{[\s\S]*?\n\};\n",
            "\n" + thumb_block + "\n",
            canvas,
            count=1,
        )
    else:
        needle = "const REPORT: ReportRow[] = JSON.parse(PHASE3_JSON);\n"
        if needle not in canvas:
            raise ValueError("REPORT anchor not found")
        canvas = canvas.replace(needle, needle + "\n" + thumb_block + "\n", 1)

    canvas_path.write_text(canvas, encoding="utf-8")
    print(f"Patched {canvas_path} ({len(thumbs)} thumbnails" + (", JSON refreshed)" if refresh_json else ")"))
    if write_html:
        write_phase3_visual_report_html(report, thumbs)
        print(f"Wrote {HTML_REPORT_PATH}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--canvas", type=Path, default=DEFAULT_CANVAS, help="Target .canvas.tsx file")
    p.add_argument(
        "--refresh-json",
        action="store_true",
        help="Also replace PHASE3_JSON from phase3_groundtruths.json",
    )
    p.add_argument(
        "--skip-html",
        action="store_true",
        help="Do not write phase3_visual_report.html",
    )
    p.add_argument(
        "--html-only",
        action="store_true",
        help="Only regenerate phase3_visual_report.html (no canvas patch)",
    )
    args = p.parse_args()
    if args.html_only:
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        thumbs = _thumb_data_urls(report, max_size=(280, 420), quality=52)
        write_phase3_visual_report_html(report, thumbs)
        print(f"Wrote {HTML_REPORT_PATH} ({len(thumbs)} thumbnails)")
        return
    patch_canvas(args.canvas.expanduser(), refresh_json=args.refresh_json, write_html=not args.skip_html)


if __name__ == "__main__":
    main()
