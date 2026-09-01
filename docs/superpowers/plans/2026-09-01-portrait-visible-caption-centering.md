# Portrait Visible Caption Centering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vertically center the actual visible glyphs of one-line and two-line portrait captions in the 180-pixel bottom white area.

**Architecture:** Generalize the existing square transparent-caption-layer command into a small reusable builder. Use it for every nonblank portrait caption and for the existing two-line square case, while retaining direct annotations for landscape and square single-line captions.

**Tech Stack:** Python 3.13, pytest, ImageMagick 7

---

## File Structure

- Modify `tests/test_layout.py`: require transparent portrait caption layers and preserve unaffected paths.
- Modify `tests/integration/test_render.py`: verify actual visible portrait ink centering for one and two lines.
- Modify `src/photo_caption_print/layout.py`: reuse a caption-layer builder for portrait and square layouts.

### Task 1: Center Visible Portrait Caption Glyphs

**Files:**
- Modify: `tests/test_layout.py`
- Modify: `tests/integration/test_render.py`
- Modify: `src/photo_caption_print/layout.py`

- [ ] **Step 1: Write failing portrait command tests**

Add parameterized tests for `geometry_for(40, 80)`:

```python
@pytest.mark.parametrize(
    ("captions", "expected_annotations"),
    [
        (("date", ""), [("42", "#171717", "+0+0", "date")]),
        (("", "device"), [("30", "#666666", "+0+0", "device")]),
        (
            ("date", "device"),
            [
                ("42", "#171717", "+0+0", "date"),
                ("30", "#666666", "+0+58", "device"),
            ],
        ),
    ],
)
def test_portrait_commands_center_trimmed_caption_layers(
    tmp_path, captions, expected_annotations
):
    command = build_magick_command(
        Path("source.jpg"),
        tmp_path / "output.jpg",
        geometry_for(40, 80),
        captions,
        "Helvetica",
        profile_path=_profile(tmp_path),
    )

    assert "xc:none" in command
    layer_start = command.index("(", command.index("xc:none") - 4)
    layer_end = command.index(")", command.index("xc:none"))
    layer = command[layer_start : layer_end + 1]
    assert layer[:8] == [
        "(", "-size", "1200x180", "xc:none", "-font", "Helvetica",
        "-gravity", "north",
    ]
    assert layer[-10:] == [
        "-trim", "+repage", "-gravity", "center", "-background", "none",
        "-extent", "1200x180", ")",
    ]
    for size, color, y_position, text in expected_annotations:
        sequence = [
            "-pointsize", size, "-fill", color,
            "-annotate", y_position, text,
        ]
        assert any(
            layer[index : index + len(sequence)] == sequence
            for index in range(len(layer) - len(sequence) + 1)
        )
    assert command[layer_end + 1 : layer_end + 7] == [
        "-gravity", "northwest", "-geometry", "+0+1620", "-composite",
    ]
```

For every case, assert the command contains `-size 1200x180 xc:none`, ends the
layer with `-trim +repage -gravity center -background none -extent 1200x180`,
and composites it with `-geometry +0+1620 -composite`. Assert the listed point
sizes, colors, relative Y offsets, and text arguments.

Update the unaffected-path characterization test so it still requires direct
annotations for landscape one/two-line captions and square one-line captions.
Add an empty portrait case that contains neither a transparent layer nor an
annotation.

- [ ] **Step 2: Verify portrait tests fail for the current direct path**

```bash
python3 -m pytest tests/test_layout.py -k 'portrait_commands_center_trimmed or empty_portrait' -q
```

Expected: the nonblank portrait cases fail because `xc:none` is absent; the
empty case passes as a preserved behavior.

- [ ] **Step 3: Generalize the caption-layer builder**

Add a predicate:

```python
def _uses_visible_caption_layer(
    geometry: PrintGeometry, primary: str, secondary: str
) -> bool:
    portrait = geometry.canvas_width == 1200 and geometry.canvas_height == 1800
    square_two_line = _uses_square_layout(geometry) and bool(primary and secondary)
    return bool(primary or secondary) and (portrait or square_two_line)
```

Extract the current square layer arguments into:

```python
def _caption_layer_arguments(
    geometry: PrintGeometry,
    primary: str,
    secondary: str,
    primary_size: int,
    secondary_size: int,
    font: str,
) -> list[str]:
    caption_height = geometry.canvas_height - geometry.caption_top
    baseline_gap = geometry.secondary_y - geometry.primary_y
    arguments = [
        "(", "-size", f"{geometry.canvas_width}x{caption_height}", "xc:none",
        "-font", str(font), "-gravity", "north",
    ]
    if primary:
        arguments.extend([
            "-pointsize", str(primary_size), "-fill", "#171717",
            "-annotate", "+0+0", _safe_annotation(primary),
        ])
    if secondary:
        secondary_y = baseline_gap if primary else 0
        arguments.extend([
            "-pointsize", str(secondary_size), "-fill", "#666666",
            "-annotate", f"+0+{secondary_y}", _safe_annotation(secondary),
        ])
    arguments.extend([
        "-trim", "+repage", "-gravity", "center", "-background", "none",
        "-extent", f"{geometry.canvas_width}x{caption_height}", ")",
        "-gravity", "northwest", "-geometry", f"+0+{geometry.caption_top}",
        "-composite",
    ])
    return arguments
```

In `build_magick_command`, use this helper when
`_uses_visible_caption_layer(...)` is true; otherwise retain the current direct
annotation fallback.

- [ ] **Step 4: Verify command tests pass**

```bash
python3 -m pytest tests/test_layout.py -q
```

Expected: all layout and command-construction tests pass.

- [ ] **Step 5: Add live one-line and two-line portrait pixel tests**

Render a `40×80` marker source to PNG for captions `("date", "")` and
`("date", "device")`. Crop `1200×180+0+1620`, reset the page, threshold and
trim the dark ink, then assert:

```python
assert 2 * ink_y + ink_height == 180
```

- [ ] **Step 6: Run focused rendering tests**

```bash
python3 -m pytest tests/test_layout.py tests/integration/test_render.py -q
```

Expected: all focused tests pass, including existing square visible-centering
coverage and direct landscape rendering.

- [ ] **Step 7: Run complete verification**

```bash
PYTHONPATH=/Users/chris/Library/Python/3.9/lib/python/site-packages /opt/homebrew/opt/python@3.13/bin/python3.13 -m pytest -q
bash -n "scripts/Install.command" "scripts/Photo Caption Print.command"
zsh -n "scripts/Install.command" "scripts/Photo Caption Print.command"
git diff --check
```

Expected: the complete Python 3.13 suite passes, launcher syntax is valid, and
Git reports no whitespace errors.

- [ ] **Step 8: Commit**

```bash
git add src/photo_caption_print/layout.py tests/test_layout.py tests/integration/test_render.py
git commit -m "fix: center visible portrait caption text"
```
