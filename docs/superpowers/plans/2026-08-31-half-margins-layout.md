# Half-Margins Print Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Halve side and bottom margins for every print orientation without changing non-layout behavior.

**Architecture:** Keep the existing `PrintGeometry` and ImageMagick command pipeline. Change only the constants and derived dimensions in `geometry_for`, then update geometry and native-render expectations.

**Tech Stack:** Python 3.13, pytest, ImageMagick 7.

---

### Task 1: Lock the new geometry with failing tests

**Files:**
- Modify: `tests/test_layout.py`
- Modify: `tests/integration/test_render.py`

- [ ] Update landscape expectations to caption top `1080`, frame `1720×1080`, position `(40, 0)`, and crop tuple `(1720, 1080)`.
- [ ] Update portrait expectations to 20-pixel inset and 180-pixel caption area while retaining `source_crop is None`.
- [ ] Update square expectations to 30-pixel inset and 120-pixel caption area while retaining `source_crop is None`.
- [ ] Update native landscape boundary assertions to `1720×1080+40+0`.
- [ ] Run focused tests and confirm they fail against the old geometry.

### Task 2: Implement the exact half-margin geometry

**Files:**
- Modify: `src/photo_caption_print/layout.py`

- [ ] Set landscape frame constants to `(40, 0, 1720, 1080)` and caption height to 120.
- [ ] Set portrait caption height to 180 and inset to 20.
- [ ] Set square caption height to 120 and inset to 30.
- [ ] Keep font sizes, cropping rules, canvas dimensions, and all non-layout behavior unchanged.
- [ ] Run focused layout/native-render tests until they pass.

### Task 3: Verify and commit

- [ ] Run the full Python 3.13 suite.
- [ ] Run `zsh -n` for both `.command` scripts and `git diff --check`.
- [ ] Commit the layout and tests with `feat: halve print margins`.
