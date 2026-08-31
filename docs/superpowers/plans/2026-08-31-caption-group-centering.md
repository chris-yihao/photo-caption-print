# Caption Group Vertical Centering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Center the existing two-line caption pair vertically in the bottom white area without changing its line separation.

**Architecture:** Derive the already-approved baseline gap from the current relative offsets, then place that fixed-height pair around the caption area's vertical center. Keep the single-line rendering path unchanged.

**Tech Stack:** Python 3.13, pytest, ImageMagick command generation

---

## File Structure

- Modify `tests/test_layout.py`: update the three-orientation baseline test and assert the preserved gaps.
- Modify `src/photo_caption_print/layout.py`: center the two-baseline coordinate pair.

### Task 1: Center the Caption Coordinate Pair

**Files:**
- Modify: `tests/test_layout.py`
- Modify: `src/photo_caption_print/layout.py:112-114`

- [ ] **Step 1: Change the geometry test to the centered coordinates**

Replace the existing expected baselines with `(1119, 1161)`, `(1681, 1739)`,
and `(1119, 1161)`. Assert that the pair midpoint equals the caption-area center
and that the baseline gap remains 42 pixels for landscape/square and 58 pixels
for portrait.

```python
@pytest.mark.parametrize(
    ("source_size", "expected_baselines", "expected_gap"),
    [
        ((4032, 3024), (1119, 1161), 42),
        ((3024, 4032), (1681, 1739), 58),
        ((3000, 3000), (1119, 1161), 42),
    ],
)
def test_caption_pair_is_centered_without_changing_line_gap(
    source_size, expected_baselines, expected_gap
):
    geometry = geometry_for(*source_size)

    assert (geometry.primary_y, geometry.secondary_y) == expected_baselines
    assert geometry.secondary_y - geometry.primary_y == expected_gap
    assert geometry.primary_y + geometry.secondary_y == geometry.caption_top + geometry.canvas_height
```

- [ ] **Step 2: Verify the new test fails for the current off-center pair**

Run:

```bash
python3 -m pytest tests/test_layout.py::test_caption_pair_is_centered_without_changing_line_gap -q
```

Expected: three failures showing the current pairs `(1123, 1165)`,
`(1687, 1745)`, and `(1123, 1165)`.

- [ ] **Step 3: Center the pair while deriving its existing gap**

Replace the two direct baselines with:

```python
primary_offset = int(caption_height * 0.40) - 5
secondary_offset = int(caption_height * 0.67) + 5
baseline_gap = secondary_offset - primary_offset
caption_center = caption_top + caption_height // 2
primary_y = caption_center - baseline_gap // 2
secondary_y = primary_y + baseline_gap
```

- [ ] **Step 4: Run focused tests**

```bash
python3 -m pytest tests/test_layout.py tests/integration/test_render.py -q
```

Expected: all layout and render tests pass, including the existing single-line
center test.

- [ ] **Step 5: Run complete verification**

```bash
PYTHONPATH=/Users/chris/Library/Python/3.9/lib/python/site-packages /opt/homebrew/opt/python@3.13/bin/python3.13 -m pytest -q
bash -n "scripts/Install.command" "scripts/Photo Caption Print.command"
zsh -n "scripts/Install.command" "scripts/Photo Caption Print.command"
git diff --check
```

Expected: the full Python 3.13 suite passes, launchers have valid syntax, and
Git reports no whitespace errors.

- [ ] **Step 6: Commit**

```bash
git add tests/test_layout.py src/photo_caption_print/layout.py
git commit -m "feat: center caption group vertically"
```
