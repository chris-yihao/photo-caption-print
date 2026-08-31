# Caption Line Spacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the first caption line up 5 pixels and the second caption line down 5 pixels for every supported photo orientation.

**Architecture:** Keep the existing geometry calculation and rendering pipeline intact. Encode the two fixed offsets only where caption baselines are derived, and lock the exact coordinates with geometry tests for landscape, portrait, and square inputs.

**Tech Stack:** Python 3.13, pytest, ImageMagick command generation

---

## File Structure

- Modify `tests/test_layout.py`: assert exact caption baselines for all three layout shapes.
- Modify `src/photo_caption_print/layout.py`: apply the two five-pixel baseline offsets.

### Task 1: Increase Caption Baseline Separation

**Files:**
- Modify: `tests/test_layout.py`
- Modify: `src/photo_caption_print/layout.py:112-114`

- [ ] **Step 1: Write the failing geometry test**

Add a parameterized test that proves the exact requested coordinates:

```python
@pytest.mark.parametrize(
    ("source_size", "expected_baselines"),
    [
        ((4032, 3024), (1123, 1165)),
        ((3024, 4032), (1687, 1745)),
        ((3000, 3000), (1123, 1165)),
    ],
)
def test_caption_lines_move_apart_by_five_pixels_each(source_size, expected_baselines):
    geometry = geometry_for(*source_size)

    assert (geometry.primary_y, geometry.secondary_y) == expected_baselines
```

- [ ] **Step 2: Run the new test and verify the red state**

Run:

```bash
python3 -m pytest tests/test_layout.py::test_caption_lines_move_apart_by_five_pixels_each -q
```

Expected: all three cases fail with the existing baselines `(1128, 1160)`, `(1692, 1740)`, and `(1128, 1160)`.

- [ ] **Step 3: Apply the minimal baseline offsets**

Change only the two baseline expressions in `geometry_for`:

```python
primary_y = caption_top + int(caption_height * 0.40) - 5
secondary_y = caption_top + int(caption_height * 0.67) + 5
```

- [ ] **Step 4: Verify focused behavior**

Run:

```bash
python3 -m pytest tests/test_layout.py tests/integration/test_render.py -q
```

Expected: all focused geometry and rendering tests pass.

- [ ] **Step 5: Verify the complete project**

Run:

```bash
PYTHONPATH=/Users/chris/Library/Python/3.9/lib/python/site-packages /opt/homebrew/opt/python@3.13/bin/python3.13 -m pytest -q
bash -n "scripts/Install.command" "scripts/Photo Caption Print.command"
zsh -n "scripts/Install.command" "scripts/Photo Caption Print.command"
git diff --check
```

Expected: the full Python 3.13 suite passes, both launchers have valid shell syntax, and Git reports no whitespace errors.

- [ ] **Step 6: Commit the isolated layout change**

```bash
git add tests/test_layout.py src/photo_caption_print/layout.py
git commit -m "feat: increase caption line spacing"
```
