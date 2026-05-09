# Cooktop Monitor

Milestone 1: read a photo of a KitchenAid 30″ radiant electric cooktouch panel and emit structured JSON plus a debug overlay.

Design and roadmap: see `coreidea.md` in the repo root (one directory up).

## Layout

Package source lives under `src/cooktop_monitor/`. A `tools/` symlink at the project root points at `src/cooktop_monitor/tools/` so the tree matches the plan and `python -m cooktop_monitor.tools.calibrate` resolves after `pip install -e .`.

## Setup

```bash
cd cooktop_monitor
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

System `tesseract` is required for OCR fallback (`pytesseract`).

## Smoke test

```bash
python -m cooktop_monitor --help
```
