# Cooktop Monitor — Build Plan (Milestone 1: Photo → State)

## Goal

Build a Python program that takes a photo of a KitchenAid 30" radiant electric cooktop (touch-control variant, model covered by manual W11439186) and outputs the current state of every burner control, the timer, and the control lock.

This is **milestone 1 of a larger project**. It runs on a laptop against still images, with no live camera, no Pi, and no UI. Once detection is reliable on photos, later milestones will add a Raspberry Pi camera capture loop, an LCD dashboard, and a SQLite history log.

The deliverable for this milestone is a CLI tool: pass it an image path, get back a structured JSON description of cooktop state, plus an annotated debug image showing what was detected where.

---

## What the cooktop displays

The control panel is a compact area in the front-right corner of the cooktop containing controls for 5 elements, a shared timer, and a control lock indicator. The elements are:

| ID | Position | Type | Special features |
|----|----------|------|------------------|
| A | Left rear | Single element | — |
| B | Center rear | Single (Even-Heat Melt) | — |
| C | Right rear | Dual element | Has Zone Size button |
| E | Center front | Single element | — |
| F | Left front | Dual element (Even-Heat Ultra Power) | Has Zone Size button |

Layout in the panel (from the manual diagram):

```
Top row:    [A]    [B]    [C with ZONE SIZE]
Bottom row: [F with ZONE SIZE]    [E]    [Lock]  [Timer]
```

Each element control cluster shows, when active, a vertically arranged display:
- **Bar graph** (vertical strip of 10 segments, fills proportionally to power level 1–10)
- **Two seven-segment digits** showing the level (01–10)
- **Named label** that lights only at specific levels:
  - `MELT` at level 01
  - `K. WARM` at level 02
  - `SIMMER` at level 03
  - `BOIL` at level 10
  - Levels 04–09 show no label, only digits and bar
- **`H`** in the digit area when the surface is hot but the element is off (dimmer red)
- **Vertical red bar** next to the On/Off icon = element is currently powered on
- **Zone size dots** (1, 2, or 3 dots, on dual/triple elements only; one may blink during selection)

Shared cooktop areas:
- **Timer**: two-digit display + `MINUTES` label, runs/counts down independently of elements
- **Control Lock**: text "Control Lock Hold 3 Sec" glows red when locked

All lit indicators are **saturated red** on near-black background. This is the single most important property of the system and the foundation of the detection pipeline.

---

## State output schema

The program must produce JSON in this shape:

```json
{
  "image_path": "string",
  "timestamp": "ISO 8601 string (file mtime or now)",
  "cooktop_on": true,
  "control_lock": false,
  "timer": {
    "running": true,
    "minutes_remaining": 20
  },
  "elements": {
    "A": { "state": "off", "level": null, "label": null, "hot": false, "zone_size": null },
    "B": { "state": "active", "level": 3, "label": "SIMMER", "hot": false, "zone_size": null },
    "C": { "state": "off", "level": null, "label": null, "hot": true, "zone_size": null },
    "E": { "state": "off", "level": null, "label": null, "hot": false, "zone_size": null },
    "F": { "state": "active", "level": 10, "label": "BOIL", "hot": false, "zone_size": 2 }
  },
  "confidence": {
    "A": 0.98,
    "B": 0.95,
    "...": "..."
  },
  "warnings": ["array of strings; e.g. 'Element D digit OCR uncertain, used bar graph fallback'"]
}
```

`state` is one of: `"off"`, `"active"`, `"unknown"`.
`level` is null when off, otherwise integer 1–10.
`label` is null, `"MELT"`, `"K. WARM"`, `"SIMMER"`, or `"BOIL"`.
`hot` is true if H indicator is lit (independent of state — an active element can also be hot).
`zone_size` is null for single elements, 1/2/3 for dual/triple elements.

---

## Detection pipeline

The program runs this sequence per image:

### 1. Load and orient

- Accept any common image format (JPG, PNG, HEIC).
- HEIC support via `pillow-heif`.
- If the image is rotated (EXIF), correct it.
- Resize so the long edge is at most 2400px to keep processing fast.

### 2. Locate the control panel

- The control panel is a roughly rectangular dark region containing the lit displays.
- Approach: find the **bounding box of all saturated-red pixels** in the image. This will roughly enclose the active displays. If no red pixels found above threshold, return `cooktop_on: false` for all elements (with `hot` checks still attempted, see below).
- Expand that bounding box by ~20% to capture the full panel including unlit element areas.
- This becomes the **panel region of interest (ROI)**, used as the coordinate frame for sub-ROIs.

If no red is found at all, we cannot localize from light alone. In that case, fall back to: assume the panel occupies a known region of the image (configurable, e.g. lower-right quadrant), and proceed with H-detection only.

### 3. Red-channel masking

For all subsequent detection:

- Convert the panel ROI to HSV.
- Build a binary mask of pixels matching saturated red:
  - Hue near 0° or 180° (red wraps around)
  - Saturation > 120 (out of 255)
  - Value > 80 (out of 255)
- This mask is the **lit-pixel mask**. Everything that's lit on the display becomes white; reflections, hands, ambient light, glass, all become black.
- Also produce a **dim-red mask** (lower thresholds, S > 60, V > 40) for detecting the H indicator, which is dimmer than active digits.

### 4. Define sub-ROIs

The control panel layout is fixed relative to the panel bounding box. Each element cluster occupies a known relative position. ROI definitions are stored in a config file `panel_layout.yaml`:

```yaml
# Coordinates as fractions of panel bounding box (0.0 to 1.0)
# Origin is top-left of panel ROI
elements:
  A:
    cluster_bbox: [0.05, 0.05, 0.30, 0.45]   # x1,y1,x2,y2 of whole element area
    on_indicator: [0.08, 0.08, 0.12, 0.18]   # vertical red bar
    digit_area:   [0.10, 0.20, 0.22, 0.40]   # the two digits / H
    bar_graph:    [0.06, 0.20, 0.10, 0.42]   # vertical bar graph strip
    label_melt:   [0.10, 0.40, 0.20, 0.43]
    label_kwarm:  [0.10, 0.40, 0.20, 0.43]
    label_simmer: [0.10, 0.40, 0.20, 0.43]
    label_boil:   [0.10, 0.40, 0.20, 0.43]
    zone_size_dots: null   # not applicable to A
  B:
    # ... etc.
timer:
  digit_area: [...]
  minutes_label: [...]
control_lock:
  text_area: [...]
```

**Initial coordinates will be wrong.** They're defined by running the calibration tool (see "Calibration" below) once against a reference photo and refined iteratively.

### 5. Per-element classification

For each element, run these checks against its sub-ROIs:

#### 5a. Element on/off/hot detection

- Count lit pixels in `cluster_bbox` using the bright-red mask.
- Above threshold → element is `active`.
- Below threshold → check dim-red mask in `digit_area` for the `H` shape.
  - If H detected → element is `off`, `hot: true`.
  - Otherwise → element is `off`, `hot: false`.

#### 5b. Power level detection (when active)

Three independent detectors, used together for cross-validation:

1. **Bar graph segment count (primary).** The bar graph is a vertical strip of 10 segments. Within the `bar_graph` ROI, divide into 10 equal vertical slices, count how many slices have >50% lit pixels. Result: integer 0–10.

2. **Digit OCR (cross-check).** Within the `digit_area` ROI:
   - Mask to lit pixels.
   - Use template matching against pre-built reference templates for digits "01" through "10". Templates are generated from reference photos during install.
   - Falls back to `tesseract` with seven-segment-display config if template matching fails.

3. **Label detection (confirmation).** For each label region (MELT, K.WARM, SIMMER, BOIL), compute the fraction of lit pixels. Above threshold → that label is on. At most one should be on at a time.

**Reconciliation logic:**
- If bar count and digit OCR agree → high confidence, use that level.
- If they disagree by 1 → use bar count, log warning.
- If they disagree by more → use whichever matches the lit label (e.g. if BOIL is lit, level is 10).
- If no agreement and no label → return `state: "unknown"`, `level: null`, log warning.

#### 5c. Zone size detection (dual/triple elements only)

Within the `zone_size_dots` ROI, count lit dots (saturated red). Result: 1, 2, or 3.

If one dot is blinking, it will appear lit in some frames and not others. Since this is a single-image pipeline, just report the current count. Blink detection is a milestone-2 concern.

### 6. Timer detection

- In timer `digit_area`, run digit OCR on lit pixels.
- Check if `MINUTES` label is lit.
- If digits detected and label lit → timer is running, return digit value.
- If digits absent and label absent → timer is off.

### 7. Control Lock detection

- In control lock `text_area`, count lit pixels.
- Above threshold → locked.
- Below → unlocked.

### 8. Confidence scoring

Each detection produces a confidence value 0.0–1.0:
- High confidence: multiple independent signals agree (bar graph + digit + label match).
- Medium: two of three agree.
- Low: only one signal, or signals disagree.

Confidences below 0.5 add a warning to the output.

### 9. Output

- Print JSON to stdout.
- Save annotated debug image to `<input_filename>_debug.jpg`:
  - Draw all sub-ROIs as colored rectangles.
  - Overlay detected state (level, label, on/off) as text on each element.
  - Show the lit-pixel mask as a red overlay at low opacity.

---

## File structure

```
cooktop_monitor/
├── README.md
├── pyproject.toml             # or requirements.txt
├── panel_layout.yaml          # ROI coordinates, calibrated once
├── reference_templates/       # digit templates, captured at install
│   ├── digit_01.png
│   ├── digit_02.png
│   ├── ...
│   └── digit_H.png
├── reference_photos/          # input photos for development
│   ├── ref_level_06.jpg
│   ├── ref_level_10_boil.jpg
│   └── ...
├── src/
│   └── cooktop_monitor/
│       ├── __init__.py
│       ├── cli.py             # entry point, argparse
│       ├── pipeline.py        # main orchestration
│       ├── load.py            # image loading, HEIC, EXIF rotation, resize
│       ├── locate.py          # find panel bounding box from red pixels
│       ├── mask.py            # HSV masking helpers
│       ├── rois.py            # load layout YAML, compute absolute ROI coords
│       ├── detect_element.py  # per-element on/off/hot/level/label/zone
│       ├── detect_timer.py
│       ├── detect_lock.py
│       ├── ocr.py             # digit recognition: template match + tesseract fallback
│       ├── reconcile.py       # combine multiple signals into final state + confidence
│       ├── debug_render.py    # annotated output image
│       └── schema.py          # dataclasses / pydantic models for output
├── tools/
│   ├── calibrate.py           # interactive ROI definition GUI
│   └── capture_templates.py   # extract digit templates from reference photos
└── tests/
    ├── test_mask.py
    ├── test_detect_element.py
    └── fixtures/              # small test images with known state
```

---

## CLI interface

```bash
# Process a single image
python -m cooktop_monitor path/to/photo.jpg

# Output JSON to stdout, debug image to path/to/photo_debug.jpg
# Use --no-debug to skip the debug image
# Use --layout path/to/custom_layout.yaml to override default

# Batch mode
python -m cooktop_monitor --batch path/to/photos/ --output results.json

# Calibration tool (separate entry point)
python -m cooktop_monitor.tools.calibrate path/to/reference_photo.jpg
# Opens a window where you click-drag rectangles for each ROI, saves to panel_layout.yaml

# Template capture
python -m cooktop_monitor.tools.capture_templates path/to/photos/
# Reads photos with known states (encoded in filename, e.g. "level_06.jpg") and saves digit crops as templates
```

---

## Calibration tool requirements

The calibration tool is a one-time setup step. It must:

1. Load a reference image and display the panel ROI.
2. Walk the user through each required ROI in sequence: A's cluster, A's on-indicator, A's digit area, A's bar graph, A's MELT label, ... etc.
3. For each, prompt the user to click two corners of a rectangle on the displayed image.
4. Show the rectangle in real-time as it's drawn.
5. Allow re-doing any ROI before saving.
6. Convert pixel coordinates to fractional coordinates (relative to panel bounding box) before writing to `panel_layout.yaml`.
7. Save with comments indicating what each entry represents.

Use OpenCV's `imshow` + mouse callbacks, or Tkinter, whichever is simpler. No web UI needed.

---

## Tech stack

- **Python 3.11+**
- **OpenCV (`opencv-python`)** for image processing
- **NumPy** for array math
- **`pillow-heif`** for HEIC support
- **`PyYAML`** for layout config
- **`pydantic`** for output schema validation
- **`pytesseract`** for digit OCR fallback (requires system `tesseract` binary)
- **`click`** or `argparse` for CLI
- **`pytest`** for tests

---

## Reference assets to provide alongside the code

The user has 5 reference photos already, plus the manual:

1. `reference_photos/level_06.jpg` — element F at level 6, no label
2. `reference_photos/level_10_boil.jpg` — element F at level 10 with BOIL label
3. `reference_photos/active_and_hot.jpg` — element F active at level 10, element E showing H
4. `reference_photos/timer_running.jpg` — timer at 20 minutes
5. `reference_photos/full_cooktop.jpg` — entire cooktop, all elements off

These should be committed to `reference_photos/` in the repo. The `level_10_boil.jpg` and `active_and_hot.jpg` are the most important for initial template capture.

**Additional photos to capture during development** (the user will need to take these):
- One photo per power level (01 through 10) for one element, to build the digit template library. About 10 minutes of work cycling the burner.
- One photo of each label state (MELT at 01, K.WARM at 02, SIMMER at 03, BOIL at 10) — overlaps with above.
- One photo with an H showing on a cool-down element (which the user already has).
- One photo with control lock active.
- One photo with zone size at 1, 2, and 3 (for elements C and F).

---

## Known challenges and how to handle them

### Reflections on glass

The black glass is mirror-like. Hands, kitchen lights, ceiling features will all reflect into the camera. **The red-channel mask makes this a non-issue for detection** — reflections are not saturated red. But it means: never run any detection on the unmasked image. Always mask first, then analyze.

### H is dimmer than active digits

The hot-surface H indicator uses lower brightness than active-element digits. The pipeline uses two thresholds: a strict mask for active detection (V > 80) and a permissive mask for H detection (V > 40). Don't lower the strict threshold to catch H — that will introduce false positives from reflections.

### Photos are taken handheld at variable angles

Until the camera is mounted, every photo will be slightly different in framing. The panel-bounding-box approach handles this: the layout YAML defines positions *relative to* the detected panel, not absolute pixel coordinates. As long as the panel can be located, ROIs will track.

### One display per element vs shared display

Confirmed by the user: **one display per element**. Each element's state is shown in its own dedicated area of the panel. The pipeline treats each element's ROIs as independent.

### Element naming

Use the manual's letter codes (A, B, C, E, F — note: D is the timer, G is the front-left dual element). When presenting to humans (e.g., the future LCD), translate to position names: "Left Rear", "Right Rear", etc. Mapping lives in `panel_layout.yaml`.

### Zone size during selection

When the user presses Zone Size, one dot blinks. A single still image can't detect blinking. Milestone 1 just reports the current count of lit dots. Milestone 2 (live capture) will add temporal logic.

---

## Development order

Build in this sequence. Each step produces a runnable, testable artifact.

1. **Project skeleton.** `pyproject.toml`, package layout, CLI entry point that prints "hello" — verify the structure works.
2. **Image loading.** `load.py` handles JPG, PNG, HEIC, EXIF rotation, resizing. Test with the 5 reference photos.
3. **Red-channel masking.** `mask.py` produces bright-red and dim-red masks. Output the masks as debug images, eyeball them — they should clearly isolate the lit segments.
4. **Panel locator.** `locate.py` finds the bounding box of red pixels. Test against `level_10_boil.jpg` (where one display is fully lit).
5. **Calibration tool.** `tools/calibrate.py`. Use it on `level_10_boil.jpg` to define ROIs for at least element F. Save to `panel_layout.yaml`.
6. **Single-element detection.** Get element F detection working end-to-end on `level_10_boil.jpg`: bar graph count, label detection, on-indicator. Output should match `{state: active, level: 10, label: BOIL}`.
7. **Multi-element detection.** Define ROIs for all 5 elements. Test against `active_and_hot.jpg` — should report F active and E hot.
8. **Digit OCR.** Add template-based digit recognition. Capture templates from reference photos. Cross-check against bar graph result.
9. **Timer and lock.** Add timer detection and lock detection. Test against `timer_running.jpg`.
10. **Reconciliation and confidence.** Implement the multi-signal voting logic and confidence scoring.
11. **Output schema and JSON output.** Pydantic models, structured output.
12. **Debug image rendering.** Annotated overlay showing all detections.
13. **Batch mode and tests.** Process a folder of images, write a test suite with fixtures of known state.

Each step should end with a working CLI invocation that produces inspectable output. Don't build steps 7-13 before steps 1-6 are solid.

---

## Acceptance criteria for milestone 1

The milestone is complete when:

1. Running `python -m cooktop_monitor reference_photos/level_10_boil.jpg` correctly outputs element F as `state: active, level: 10, label: BOIL`.
2. Running it on `active_and_hot.jpg` correctly outputs F as active and E as hot, others off.
3. Running it on `timer_running.jpg` correctly outputs `timer.running: true, timer.minutes_remaining: 20`.
4. Running it on `full_cooktop.jpg` (all off) correctly outputs all elements off, no false positives.
5. Confidence scores are populated and reasonable (high when signals agree, lower when they don't).
6. Debug images are produced and visually correct.
7. The pipeline is robust to handheld photo variation (verified by trying photos taken at different angles/distances).

When all six pass, the design is validated and the project can move to milestone 2 (Pi camera + live loop).

---

## Out of scope for milestone 1

- Live camera capture
- Raspberry Pi deployment
- LCD dashboard
- SQLite history logging
- Web server / API
- Notifications or alerts
- Real-time performance optimization (just needs to run; speed comes later)
- Blink detection for zone size selection
- Power consumption estimation
- Multi-camera support

These all build on milestone 1 and require a working detection pipeline first.