# Cooktop monitor — Python tests index

Quick map of **pytest** modules under `cooktop_monitor/tests/`. Use this to find coverage without opening every file.

## How to run

From repo root `cooktop2/`:

```bash
cd cooktop_monitor && PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
```

(`pyproject.toml` sets `pythonpath = ["src"]` for pytest when run from `cooktop_monitor/` with `pytest tests/`.)

Tests that read JPEGs expect **`../../groundtruths/`** relative to `cooktop_monitor/tests/` (i.e. `cooktop2/groundtruths/`).

---

## Files

| File | Primary modules under test | What it covers |
|------|---------------------------|----------------|
| **`test_load_smoke.py`** | `load.load_image`, `MAX_LONG_EDGE` | Loads a small subset of ground-truth JPEGs (skips if missing); asserts BGR `uint8`, max edge bound, contiguity; unsupported extension and missing file errors. |
| **`test_mask.py`** | `mask.bright_red_mask`, `dim_red_mask`, `lit_fraction`, `crop_roi` | HSV red masking (bright vs dim), lit fraction math, ROI crop clamping / zero-size error. |
| **`test_locate.py`** | `locate.locate_panel` | Synthetic images: no-red fallback quadrant, 20% bbox expansion around red blob, clamping to image bounds. |
| **`test_rois.py`** | `rois.load_layout`, `save_layout`, `to_absolute`, `clamp_rect`, `iter_element_rois` | Loads bundled `panel_layout.yaml`; YAML mutation tests for validation (missing element, inverted rect, out-of-range frac); geometry helpers; save/load round-trip to temp dir. |
| **`test_detect_element.py`** | `detect_element.*`, `detect_element.detect_element`, `reconcile.reconcile_element` | **Largest suite**: synthetic masks for `bar_segment_count`, `label_lit_flags`, `zone_dot_count`, `detect_h`, cluster/on-indicator fractions; orchestrator `detect_element` signal keys and synthetic layouts; **`reconcile_element`** branches (label vs bar, multi-label precedence, off/hot, zone dots); **three optional ground-truth checks** on real JPEGs (`A0`, `A10boil`, `AOffH`) when files exist. |

---

## Ground-truth usage in tests

- **`test_load_smoke.py`** — parametrized: `A0.jpeg`, `A10boil.jpeg`, `AOffH.jpeg`, `timer2minutes.jpeg` (skip if absent).
- **`test_detect_element.py`** — `test_groundtruth_*` functions under `GROUNDTRUTHS` parent traversal (`parents[2] / "groundtruths"`).

---

## Related tooling (not pytest)

| Path | Role |
|------|------|
| `cooktop_monitor/scripts/embed_phase3_visual_canvas.py` | Regenerates Cursor canvas thumbnails / optional `PHASE3_JSON`; writes `reports/phase3_visual_report.html` (embedded JPEG data URLs). |

---

## Gaps / not covered here

- **`pipeline.run_single`** / CLI integration tests are not in this folder yet.
- **Phase 4+** (timer, control lock, debug overlays) are stubbed in pipeline; no dedicated tests in this index beyond warnings in JSON fixtures.

When adding tests, append a row to the table above so this file stays the single navigation entry point.
