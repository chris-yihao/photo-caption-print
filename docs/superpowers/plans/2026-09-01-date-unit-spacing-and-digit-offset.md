# Date Unit Spacing and Digit Offset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add exact 1-pixel gaps around Chinese date units and lower only the date digits by 1 pixel while preserving the existing caption layout.

**Architecture:** Parse only the standard primary caption produced by `format_caption` into date runs. Measure those runs with the existing injected measurement function, and render them as same-font transparent label fragments joined by explicit 1-pixel transparent spacers; digit fragments receive a one-pixel top splice and matching bottom chop. Nonstandard text keeps the current single-annotation path.

**Tech Stack:** Python 3, pytest, ImageMagick CLI

---

### Task 1: Parse standard documentary date captions

**Files:**
- Modify: `src/photo_caption_print/layout.py`
- Test: `tests/test_layout.py`

- [ ] **Step 1: Add failing parser tests**

Add tests for a helper that recognizes exactly the formatter shape and returns:

```python
(
    ("2017", True),
    ("年", False),
    ("12", True),
    ("月", False),
    ("18", True),
    ("日", False),
    (" · 星期一 · 11:25", False),
)
```

Verify arbitrary primary text and truncated text return `None` so existing rendering remains unchanged.

- [ ] **Step 2: Run parser tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_layout.py -k date_caption_runs -q
```

Expected: FAIL because the helper does not exist.

- [ ] **Step 3: Implement the strict parser**

Use a compiled regular expression matching `YYYY年MM月DD日 · 星期[一二三四五六日] · HH:MM`. Return run text plus a boolean that identifies the three digit runs. Do not parse arbitrary user text.

- [ ] **Step 4: Run parser tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

### Task 2: Include exact unit gaps in caption fitting

**Files:**
- Modify: `src/photo_caption_print/layout.py`
- Test: `tests/test_layout.py`

- [ ] **Step 1: Add a failing measurement test**

Inject a measurement function that returns `len(text) * 10`. Verify the standard date caption width is the sum of all seven run widths plus five explicit 1-pixel gaps: before and after `年`, before and after `月`, and before `日`.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m pytest tests/test_layout.py -k date_caption_measurement -q
```

Expected: FAIL because fitting still measures the original string as one label.

- [ ] **Step 3: Add one shared measurement helper**

For recognized date captions, sum the seven run measurements and add five pixels. For all other text, call the injected measurement function once exactly as before. Route `_fit_one` width checks through this helper; keep ellipsis behavior unchanged.

- [ ] **Step 4: Run the focused fitting tests**

Run:

```bash
python3 -m pytest tests/test_layout.py -k 'date_caption_measurement or fit_captions' -q
```

Expected: all selected tests pass.

### Task 3: Render date fragments with exact spacing and baseline offset

**Files:**
- Modify: `src/photo_caption_print/layout.py`
- Modify: `tests/test_layout.py`
- Modify: `tests/integration/test_render.py`

- [ ] **Step 1: Add failing command tests**

For standard portrait, square, and landscape captions, verify the generated ImageMagick command:

- creates one label fragment for each of the seven runs;
- inserts exactly five `1x1` transparent spacer fragments;
- applies `-splice 0x1` followed by `-chop 0x1` only to `2017`, `12`, and `18`;
- horizontally appends the fragments and composites the completed row at the existing primary-line position;
- keeps the secondary annotation at its current baseline gap.

Also verify arbitrary primary text still produces the existing direct `-annotate` command.

- [ ] **Step 2: Run command tests and verify RED**

Run:

```bash
python3 -m pytest tests/test_layout.py -k 'date_caption_command or portrait_commands or square_command or direct_annotations' -q
```

Expected: new date-command tests fail while legacy behavior tests expose only intentional command-shape updates.

- [ ] **Step 3: Implement a shared date-row command builder**

Build each run as a transparent `label:` image using the current font and primary point size. Insert five explicit `1x1` transparent images around the date units according to the approved rule. For digit runs, splice one transparent row at the top and chop one row from the bottom. Join all fragments with `+append`, then composite the result with north gravity at the same primary y used by the current layout path.

Use the row builder inside both the visible caption layer and direct landscape/single-line path. Keep the existing whole-line annotation for unrecognized primary strings.

- [ ] **Step 4: Run layout and render integration tests**

Run:

```bash
python3 -m pytest tests/test_layout.py tests/integration/test_render.py -q
```

Expected: all selected tests pass, apart from documented platform skips.

### Task 4: Verify the actual portrait photo

**Files:**
- No repository source changes expected

- [ ] **Step 1: Run the full regression suite**

Run:

```bash
python3 -m pytest -q
```

Record any pre-existing unrelated failure separately; do not expand this feature to fix it.

- [ ] **Step 2: Generate an isolated preview**

Run the installed CLI against only `已选照片/IMG_0451.jpeg`, writing to `/private/tmp/photo-caption-print-date-spacing-20260901/`. Use the existing geocoding cache in offline mode.

- [ ] **Step 3: Inspect the rendered caption**

Confirm visually that the date remains halfwidth, the unit gaps are subtle, only date digits sit one pixel lower, the separator spacing is unchanged, and the whole caption block remains vertically centered.

- [ ] **Step 4: Commit the focused implementation**

Run:

```bash
git diff --check
git status --short
```

Commit only `layout.py` and its focused tests. Do not add generated previews or Python cache directories.
