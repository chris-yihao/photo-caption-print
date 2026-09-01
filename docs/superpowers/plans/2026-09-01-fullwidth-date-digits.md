# Fullwidth Date Digits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render only the calendar-date digits in fullwidth form while preserving halfwidth time digits and every existing layout rule.

**Architecture:** Keep the change inside the caption-formatting boundary. Add a small ASCII-to-fullwidth digit translator in `captions.py`, apply it only to the separately formatted date fragment, and leave weekday, time, metadata, and layout code untouched.

**Tech Stack:** Python 3, pytest, ImageMagick integration tests

---

### Task 1: Specify fullwidth date output

**Files:**
- Modify: `tests/test_captions.py`

- [ ] **Step 1: Update the focused caption assertion**

Change the complete-metadata expectation to:

```python
assert caption == ("２０１８年０５月０１日 · 星期二 · 14:30", "上海 · 外滩 / iPhone 8")
```

Also update every existing `format_caption` expectation in this file so date digits are fullwidth but time digits remain halfwidth.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m pytest tests/test_captions.py::test_format_caption_formats_complete_metadata_exactly -q
```

Expected: FAIL because the formatter still returns ASCII date digits.

### Task 2: Implement the scoped conversion

**Files:**
- Modify: `src/photo_caption_print/captions.py`
- Test: `tests/test_captions.py`

- [ ] **Step 1: Add the minimal digit mapping**

Define a translation table and format the date separately:

```python
_FULLWIDTH_DATE_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")

date = metadata.captured_at.strftime("%Y年%m月%d日").translate(_FULLWIDTH_DATE_DIGITS)
date_line = (
    f"{date} · {WEEKDAYS[metadata.captured_at.weekday()]} · "
    f"{metadata.captured_at:%H:%M}"
)
```

- [ ] **Step 2: Run the caption tests and verify GREEN**

Run:

```bash
python3 -m pytest tests/test_captions.py -q
```

Expected: all caption tests pass.

- [ ] **Step 3: Update downstream exact caption fixtures**

Replace only date digits in exact expected/generated caption strings under `tests/`; keep time digits halfwidth. Do not change arbitrary layout-only sample strings unless they represent formatter output.

- [ ] **Step 4: Run the full suite**

Run:

```bash
python3 -m pytest -q
```

Expected: all tests pass with the existing platform skips only.

### Task 3: Verify a real print preview

**Files:**
- No source changes expected

- [ ] **Step 1: Locate the existing real-photo preview workflow**

Use the repository CLI/help and existing integration-test commands; do not introduce a second rendering path.

- [ ] **Step 2: Render the previously used portrait photo**

Generate a print output from the available desktop test photo into a temporary directory. Verify visually that the date digits are fullwidth, time digits remain halfwidth, and the caption block keeps its current vertical centering.

- [ ] **Step 3: Check the final diff and commit**

Run:

```bash
git diff --check
git status --short
```

Commit only the plan, focused tests, and implementation; do not add generated previews or Python cache files.
