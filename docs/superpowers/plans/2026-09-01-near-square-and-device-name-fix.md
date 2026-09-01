# Near-Square Layout and Device Name Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render visually square edited photos with the square print layout and display `iPhone7,2` as `iPhone 6`.

**Architecture:** Normalize confirmed device identifiers at the EXIF metadata boundary so all downstream reports and captions receive a readable name. Classify oriented dimensions within an inclusive 2% aspect difference as square-like before selecting the existing square, landscape, or portrait geometry.

**Tech Stack:** Python 3.13, pytest, ExifTool, ImageMagick

---

## File Structure

- Modify `src/photo_caption_print/metadata.py`: normalize the confirmed Apple model identifier.
- Modify `tests/test_metadata.py`: cover mapped, readable, and unknown device values.
- Modify `src/photo_caption_print/layout.py`: add the inclusive 2% square-like classification.
- Modify `tests/test_layout.py`: cover the production dimensions, threshold boundary, and out-of-threshold orientations.
- Modify `tests/integration/test_render.py`: cover near-square canvas selection and actual square-caption ink centering.

### Task 1: Normalize the Confirmed Apple Device Identifier

**Files:**
- Modify: `tests/test_metadata.py`
- Modify: `src/photo_caption_print/metadata.py`

- [ ] **Step 1: Write failing metadata tests**

```python
@pytest.mark.parametrize(
    ("raw_model", "expected"),
    [
        ("iPhone7,2", "iPhone 6"),
        ("iPhone 13 Pro", "iPhone 13 Pro"),
        ("iPhone99,9", "iPhone99,9"),
        ("Test Camera", "Test Camera"),
    ],
)
def test_metadata_normalizes_only_confirmed_device_identifiers(raw_model, expected):
    metadata = metadata_from_exiftool(
        {"SourceFile": "photo.jpg", "EXIF:Model": raw_model}
    )

    assert metadata.device == expected
```

- [ ] **Step 2: Verify the mapped case fails**

Run:

```bash
python3 -m pytest tests/test_metadata.py::test_metadata_normalizes_only_confirmed_device_identifiers -q
```

Expected: only `iPhone7,2` fails because the current value remains raw.

- [ ] **Step 3: Add the explicit normalizer**

Add to `metadata.py`:

```python
DEVICE_NAMES = {"iPhone7,2": "iPhone 6"}


def _friendly_device_name(value: Any) -> Any:
    return DEVICE_NAMES.get(value, value)
```

Use it when constructing `PhotoMetadata`:

```python
device=_friendly_device_name(_first_value(row, MODEL_KEYS)),
```

- [ ] **Step 4: Run metadata tests**

```bash
python3 -m pytest tests/test_metadata.py -q
```

Expected: all metadata tests pass.

- [ ] **Step 5: Commit the device fix**

```bash
git add src/photo_caption_print/metadata.py tests/test_metadata.py
git commit -m "fix: display readable iPhone 6 model"
```

### Task 2: Classify Near-Square Photos with the Square Layout

**Files:**
- Modify: `tests/test_layout.py`
- Modify: `tests/integration/test_render.py`
- Modify: `src/photo_caption_print/layout.py`

- [ ] **Step 1: Add failing near-square and boundary tests**

```python
def test_img_0466_dimensions_use_centered_square_layout():
    geometry = geometry_for(956, 961)

    assert (geometry.canvas_width, geometry.canvas_height) == (1800, 1200)
    assert geometry.source_crop is None
    assert geometry.primary_y + geometry.secondary_y == geometry.caption_top + geometry.canvas_height


@pytest.mark.parametrize("source_size", [(1000, 980), (980, 1000)])
def test_two_percent_boundary_is_square_like(source_size):
    geometry = geometry_for(*source_size)

    assert (geometry.canvas_width, geometry.canvas_height) == (1800, 1200)
    assert geometry.source_crop is None


@pytest.mark.parametrize(
    ("source_size", "expected_canvas", "expects_crop"),
    [
        ((1000, 979), (1800, 1200), True),
        ((979, 1000), (1200, 1800), False),
    ],
)
def test_dimensions_beyond_two_percent_keep_their_orientation(
    source_size, expected_canvas, expects_crop
):
    geometry = geometry_for(*source_size)

    assert (geometry.canvas_width, geometry.canvas_height) == expected_canvas
    assert (geometry.source_crop is not None) is expects_crop
```

Update existing tests that use `(1001, 1000)` or `(1000, 1001)` as strict
landscape/portrait examples to use `(1021, 1000)` and `(1000, 1021)`. Keep the
landscape expectation `(1720, 1080)` with resize `1720x1080^`; change the
portrait expectation to `(1160, 1184)` with resize `1160x`. Use the same new
source dimensions in the integration canvas parameterization.

- [ ] **Step 2: Verify the production and boundary cases fail**

```bash
python3 -m pytest tests/test_layout.py tests/integration/test_render.py -q
```

Expected: `956×961` and both inclusive-boundary cases still select the old
strict orientation behavior.

- [ ] **Step 3: Implement the inclusive ratio classification**

Replace the current strict booleans in `geometry_for` with:

```python
square_like = abs(source_width - source_height) / max(source_width, source_height) <= 0.02
portrait = not square_like and source_height > source_width
landscape = not square_like and source_width > source_height
```

The existing non-landscape branch remains the square fitting path.

- [ ] **Step 4: Run focused layout and rendering tests**

```bash
python3 -m pytest tests/test_layout.py tests/integration/test_render.py -q
```

Expected: all focused tests pass.

- [ ] **Step 5: Commit the layout fix**

```bash
git add src/photo_caption_print/layout.py tests/test_layout.py tests/integration/test_render.py
git commit -m "fix: treat near-square photos as square"
```

### Task 3: Center the Visible Square Caption Group

**Files:**
- Modify: `tests/test_layout.py`
- Modify: `tests/integration/test_render.py`
- Modify: `src/photo_caption_print/layout.py`

- [ ] **Step 1: Add a failing command-construction test**

Build a command for `geometry_for(956, 961)` with two caption lines and assert
that it contains a separate transparent `1800x120` group with:

```python
[
    "(", "-size", "1800x120", "xc:none", "-font", font,
    "-gravity", "north",
    "-pointsize", "28", "-fill", "#171717", "-annotate", "+0+0", primary,
    "-pointsize", "20", "-fill", "#666666", "-annotate", "+0+42", secondary,
    "-trim", "+repage", "-gravity", "center", "-background", "none",
    "-extent", "1800x120", ")",
    "-gravity", "northwest", "-geometry", "+0+1080", "-composite",
]
```

Also assert that landscape, portrait, and square single-line commands retain
their existing direct-annotation path.

- [ ] **Step 2: Verify the command test fails**

```bash
python3 -m pytest tests/test_layout.py::test_two_line_square_command_centers_a_trimmed_caption_layer -q
```

Expected: failure because the current command directly annotates both lines on
the full canvas and has no transparent caption layer.

- [ ] **Step 3: Add the square-only caption-layer command**

Add a predicate that recognizes the square print geometry:

```python
def _uses_square_layout(geometry: PrintGeometry) -> bool:
    return (
        geometry.canvas_width == 1800
        and geometry.canvas_height == 1200
        and geometry.source_crop is None
    )
```

For `_uses_square_layout(geometry) and primary and secondary`, append the exact
transparent group arguments from Step 1. Derive its relative line offset from
`geometry.secondary_y - geometry.primary_y`, and composite it at
`geometry.caption_top`. Keep the existing direct annotations in the `else`
branch for every other layout and for single-line square captions.

- [ ] **Step 4: Add a live pixel-centering integration test**

Render a `60×60` marker source to PNG with two representative Chinese caption
lines. Crop the `1800×120+0+1080` caption area, reset its page offset, threshold
the dark text against white, and trim it. Parse the returned ink bounds and
assert:

```python
assert 2 * ink_y + ink_height == 120
```

This proves the visible pixels, rather than only annotation coordinates, are
vertically centered.

- [ ] **Step 5: Run focused tests**

```bash
python3 -m pytest tests/test_layout.py tests/integration/test_render.py -q
```

Expected: all focused command and live ImageMagick tests pass.

- [ ] **Step 6: Commit the visible-centering fix**

```bash
git add src/photo_caption_print/layout.py tests/test_layout.py tests/integration/test_render.py
git commit -m "fix: center visible square caption text"
```

### Task 4: Verify the Complete Project and Production Example

**Files:**
- Read: `/Users/chris/Desktop/照片/IMG_0466.jpeg`
- Create: `/private/tmp/photo-caption-print-img0466-preview/IMG_0466-print.jpg`

- [ ] **Step 1: Run complete automated verification**

```bash
PYTHONPATH=/Users/chris/Library/Python/3.9/lib/python/site-packages /opt/homebrew/opt/python@3.13/bin/python3.13 -m pytest -q
bash -n "scripts/Install.command" "scripts/Photo Caption Print.command"
zsh -n "scripts/Install.command" "scripts/Photo Caption Print.command"
git diff --check
```

Expected: the complete Python 3.13 suite passes, both launchers have valid
syntax, and Git reports no whitespace errors.

- [ ] **Step 2: Generate an isolated preview from `IMG_0466.jpeg`**

Create dedicated input/output/report directories under `/private/tmp`, copy
only `IMG_0466.jpeg` into the input directory, and run:

```bash
./.venv/bin/python -m photo_caption_print.cli \
  --input /private/tmp/photo-caption-print-img0466-input \
  --output /private/tmp/photo-caption-print-img0466-preview \
  --report /private/tmp/photo-caption-print-img0466-report.csv \
  --offline
```

Expected: the preview is `1800×1200`, its report device is `iPhone 6`, and the
original file in `打印成品` is untouched.

- [ ] **Step 3: Inspect the preview visually**

Open `/private/tmp/photo-caption-print-img0466-preview/IMG_0466-print.jpg` and
confirm the square photo uses the intended uncropped layout and the two caption
lines are centered in the bottom white area.
