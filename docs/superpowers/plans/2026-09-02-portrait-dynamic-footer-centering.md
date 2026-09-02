# Portrait Dynamic Footer Centering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Center portrait caption ink within all whitespace between the actual photo bottom and the canvas bottom.

**Architecture:** Reuse the existing trimmed transparent caption layer. Derive portrait layer top and height from `photo_y + photo_height`; retain the fixed square layer and direct landscape annotations unchanged.

**Tech Stack:** Python 3, pytest, ImageMagick CLI

---

### Task 1: Specify dynamic portrait caption geometry

**Files:**
- Modify: `tests/test_layout.py`

- [ ] Add command tests for short and tall portrait sources asserting layer height is `canvas_height - (photo_y + photo_height)` and final composite y is `photo_y + photo_height`.
- [ ] Assert square layer remains at `caption_top - 3` and landscape direct positions remain unchanged.
- [ ] Run `python3 -m pytest tests/test_layout.py -k portrait_commands -q` and verify RED.

### Task 2: Implement portrait-specific layer bounds

**Files:**
- Modify: `src/photo_caption_print/layout.py`
- Modify: `tests/test_layout.py`

- [ ] Add a helper returning `(layer_top, layer_height)`.
- [ ] For portrait, return photo bottom and remaining canvas height with no 3-pixel shift.
- [ ] For square, return the existing fixed caption top minus 3 and existing caption height.
- [ ] Use these values in `_caption_layer_arguments` without changing line spacing or visible-ink trimming.
- [ ] Run `python3 -m pytest tests/test_layout.py -q` and verify GREEN.

### Task 3: Verify visible ink centering

**Files:**
- Modify: `tests/integration/test_render.py`

- [ ] Update portrait one-line and two-line integration tests to crop the full dynamic footer.
- [ ] Assert visible ink top and bottom whitespace differ by no more than 1 pixel.
- [ ] Keep the square assertion at 3 pixels above its fixed footer center.
- [ ] Run `python3 -m pytest tests/test_layout.py tests/integration/test_render.py -q`.

### Task 4: Preview and commit

**Files:**
- No additional source files expected

- [ ] Locate the screenshot's likely source photo from `已选照片` when possible; otherwise use a short portrait fixture.
- [ ] Render an isolated preview and inspect the full footer.
- [ ] Run `python3 -m pytest -q`, recording the known unrelated NUL-in-CSV failure separately.
- [ ] Run `git diff --check` and commit only focused source, tests, and plan.
