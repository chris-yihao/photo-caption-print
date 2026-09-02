# Restore Halfwidth Date Digits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore compact halfwidth date digits without changing any current photo or caption positioning.

**Architecture:** Limit the production change to `format_caption` in `captions.py`. Remove the date digit translation and retain whole-line measurement/rendering in `layout.py` unchanged.

**Tech Stack:** Python 3, pytest, ImageMagick CLI

---

### Task 1: Restore halfwidth formatter output

**Files:**
- Modify: `tests/test_captions.py`
- Modify: `tests/test_pipeline.py`
- Modify: `src/photo_caption_print/captions.py`

- [ ] Change exact date expectations to `2018年05月01日 · 星期二 · 14:30` and equivalent halfwidth values.
- [ ] Run the focused formatter test and verify RED against fullwidth output.
- [ ] Delete `_FULLWIDTH_DATE_DIGITS` and format the date directly with `%Y年%m月%d日`.
- [ ] Run caption and affected pipeline tests; expect all except the known NUL-in-CSV test to pass.

### Task 2: Verify layout and preview

**Files:**
- Modify: `tests/test_layout.py`
- Modify: `tests/integration/test_render.py`

- [ ] Update production-like date fixtures from fullwidth to halfwidth; keep layout expectations unchanged.
- [ ] Run layout and render integration tests.
- [ ] Render `IMG_0783.jpeg` into `/private/tmp/photo-caption-print-halfwidth-img0783-20260902/` and inspect compact date spacing plus dynamic footer centering.
- [ ] Run the full suite, record the known unrelated NUL-in-CSV failure separately, run `git diff --check`, and commit focused files.
