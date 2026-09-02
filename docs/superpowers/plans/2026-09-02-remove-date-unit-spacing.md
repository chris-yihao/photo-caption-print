# Remove Date Unit Spacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all added date-unit spacing while preserving fullwidth date digits, shared baseline, portrait top alignment, and the 3-pixel caption shift.

**Architecture:** The date no longer needs per-run positioning, so remove the strict date parser and segmented row renderer from `layout.py`. Return standard dates to the same whole-line measurement and annotation path used by ordinary captions.

**Tech Stack:** Python 3, pytest, ImageMagick CLI

---

### Task 1: Specify whole-line date rendering

**Files:**
- Modify: `tests/test_layout.py`

- [ ] Change standard fullwidth-date command expectations to one `-annotate` containing the complete primary line.
- [ ] Assert there are no `label:` date fragments, `1x1` spacer images, `-splice`, or `-chop` operations.
- [ ] Run `python3 -m pytest tests/test_layout.py -k date_caption -q` and verify failure against the current segmented renderer.

### Task 2: Remove segmented date handling

**Files:**
- Modify: `src/photo_caption_print/layout.py`
- Modify: `tests/test_layout.py`

- [ ] Delete the date regular expression, parser, special width helper, and date-row command builder.
- [ ] Route `_fit_one` back through the injected whole-line measurement function.
- [ ] Route all primary captions through the existing whole-line `-annotate` path.
- [ ] Preserve `_CAPTION_SHIFT_UP`, portrait `photo_y`, visible caption-layer centering, and secondary-line logic unchanged.
- [ ] Run `python3 -m pytest tests/test_layout.py tests/integration/test_render.py -q` and verify all selected tests pass.

### Task 3: Verify and preview

**Files:**
- No additional source files expected

- [ ] Run `python3 -m pytest -q`; record the known unrelated NUL-in-CSV failure separately if it remains the only failure.
- [ ] Render `已选照片/IMG_0451.jpeg` into `/private/tmp/photo-caption-print-no-date-spacing-20260902/`.
- [ ] Confirm visually that fullwidth date digits directly adjoin `年、月、日`, with unchanged top margin and caption height.
- [ ] Run `git diff --check` and commit only the focused source, tests, and plan.
