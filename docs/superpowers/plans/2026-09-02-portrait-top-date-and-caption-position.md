# Portrait Top, Date, and Caption Position Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Top-align portrait photos at the 20-pixel safe margin, restore fullwidth date digits without digit baseline offset, retain exact unit gaps, and move every caption block upward by 3 pixels.

**Architecture:** Keep metadata text conversion in `captions.py` and geometry/render positioning in `layout.py`. Reuse the existing segmented date-row renderer, widening its strict parser to fullwidth date digits and removing only the digit-run vertical transform; shift completed caption placement rather than changing line spacing.

**Tech Stack:** Python 3, pytest, ImageMagick CLI

---

### Task 1: Restore fullwidth date digits only

**Files:**
- Modify: `tests/test_captions.py`
- Modify: `tests/test_pipeline.py`
- Modify: `src/photo_caption_print/captions.py`

- [ ] **Step 1: Update focused expectations to fullwidth date digits**

Expect `２０１８年０５月０１日 · 星期二 · 14:30`; keep `14:30` halfwidth. Update exact pipeline caption expectations the same way.

- [ ] **Step 2: Run the focused formatter test and verify RED**

Run `python3 -m pytest tests/test_captions.py::test_format_caption_formats_complete_metadata_exactly -q`.

Expected: FAIL because date digits are still halfwidth.

- [ ] **Step 3: Add the scoped translation**

Add `str.maketrans("0123456789", "０１２３４５６７８９")`, translate only the separately formatted `%Y年%m月%d日` fragment, and concatenate the unchanged weekday and `%H:%M` fragment.

- [ ] **Step 4: Run caption and affected pipeline tests**

Run `python3 -m pytest tests/test_captions.py tests/test_pipeline.py -q`.

Expected: all tests pass except the documented pre-existing NUL-in-CSV test.

### Task 2: Top-align portrait photos

**Files:**
- Modify: `tests/test_layout.py`
- Modify: `src/photo_caption_print/layout.py`

- [ ] **Step 1: Add a failing portrait geometry assertion**

For a portrait whose scaled height is shorter than the photo area, assert `photo_y == photo_area_y == 20`, while retaining the existing scaled width and height.

- [ ] **Step 2: Run the focused test and verify RED**

Run `python3 -m pytest tests/test_layout.py -k portrait_geometry -q`.

Expected: FAIL because portrait photos are vertically centered inside the area.

- [ ] **Step 3: Implement portrait-only top alignment**

Set `photo_y = area_y` for portrait geometry. Keep the current centered calculation for square-like geometry and leave landscape cropping unchanged.

- [ ] **Step 4: Run geometry tests**

Run `python3 -m pytest tests/test_layout.py -k geometry -q`.

Expected: all selected tests pass.

### Task 3: Preserve unit gaps and remove digit lowering

**Files:**
- Modify: `tests/test_layout.py`
- Modify: `src/photo_caption_print/layout.py`

- [ ] **Step 1: Change date-run and command expectations**

Expect the strict parser to accept `２０１７年１２月１８日 · 星期一 · 11:25`. Verify the command still creates seven text fragments and five `1x1` spacers, but contains no `-splice` or `-chop` operations.

- [ ] **Step 2: Run focused tests and verify RED**

Run `python3 -m pytest tests/test_layout.py -k 'date_caption_runs or date_caption_command' -q`.

Expected: FAIL because the parser accepts halfwidth date digits and digit runs still move down.

- [ ] **Step 3: Update parsing and row rendering**

Match exactly four, two, and two fullwidth digits in the date portion. Remove digit-run booleans and vertical transforms from the row builder. Keep five transparent spacers between the first six runs and keep the suffix as one unchanged fragment.

- [ ] **Step 4: Update exact width calculation**

Continue summing all seven run widths plus five pixels. Do not change nonstandard text measurement.

- [ ] **Step 5: Run layout and rendering tests**

Run `python3 -m pytest tests/test_layout.py tests/integration/test_render.py -q`.

Expected: all tests pass.

### Task 4: Move all caption blocks upward by 3 pixels

**Files:**
- Modify: `tests/test_layout.py`
- Modify: `tests/integration/test_render.py`
- Modify: `src/photo_caption_print/layout.py`

- [ ] **Step 1: Add failing placement assertions**

Verify direct landscape annotations use existing primary/secondary or single-line y values minus 3. Verify portrait and square visible caption layers composite at `caption_top - 3`. Confirm the primary-to-secondary baseline gap is unchanged.

- [ ] **Step 2: Run focused tests and verify RED**

Run `python3 -m pytest tests/test_layout.py -k 'caption_position or caption_layer or direct_annotations' -q`.

Expected: FAIL because current placement has no uniform 3-pixel upward offset.

- [ ] **Step 3: Apply one shared offset**

Define a private constant of 3 pixels. Subtract it from direct annotation y coordinates and final visible-layer placement. Remove the earlier one-off portrait date adjustment because date digits no longer have a separate vertical transform.

- [ ] **Step 4: Run all layout and rendering tests**

Run `python3 -m pytest tests/test_layout.py tests/integration/test_render.py -q`.

Expected: all tests pass with updated placement expectations.

### Task 5: Verify real output and commit

**Files:**
- No additional source files expected

- [ ] **Step 1: Run full regression**

Run `python3 -m pytest -q`. Record the pre-existing NUL-in-CSV failure separately if it remains the only failure.

- [ ] **Step 2: Generate isolated preview**

Process only `已选照片/IMG_0451.jpeg` to `/private/tmp/photo-caption-print-portrait-top-fullwidth-20260902/` with the existing geocoding cache in offline mode.

- [ ] **Step 3: Inspect geometry and caption visually**

Confirm the portrait top and side margins are both 20 pixels, the date digits are fullwidth with subtle 1-pixel unit gaps, all date glyphs share a baseline, and the caption is 3 pixels higher.

- [ ] **Step 4: Commit focused files**

Run `git diff --check` and relevant tests, then commit source and tests without generated previews or Python cache files.
