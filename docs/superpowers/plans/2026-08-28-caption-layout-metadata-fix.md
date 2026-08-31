# Caption Metadata and Landscape Layout Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore date, Chinese weekday, GPS-derived location, and device captions, then apply the approved narrow-margin center crop and smaller type to landscape 6×4 prints.

**Architecture:** Keep metadata extraction, caption formatting, reverse geocoding, and rendering as separate existing units. Stabilize ExifTool output at family-0 group names, represent landscape crop intent in `PrintGeometry`, and let the existing pipeline feed decoded GPS coordinates through the cached geocoder before rendering.

**Tech Stack:** Python 3.13, pytest, ExifTool, ImageMagick 7, macOS Helvetica TTC, Nominatim reverse geocoding.

---

## File map

- `src/photo_caption_print/metadata.py`: request stable ExifTool group names and decode EXIF fields.
- `src/photo_caption_print/captions.py`: retain the established Chinese weekday and two-line caption format.
- `src/photo_caption_print/layout.py`: calculate the approved landscape crop frame, smaller type sizes, and ImageMagick center-crop command.
- `src/photo_caption_print/pipeline.py`: unchanged production flow; tests prove GPS and geocoding warnings reach captions and reports.
- `tests/test_metadata.py`: protect the ExifTool command contract and real key parsing.
- `tests/test_captions.py`: protect the exact synthetic Monday caption.
- `tests/test_layout.py`: protect landscape, square, and portrait geometry and rendering argv.
- `tests/test_pipeline.py`: protect GPS-to-location-to-caption flow and failure reporting.
- `tests/integration/test_desktop_fixture.py`: opt-in native verification against an external fixture without modifying it.
- `README.md`: document cropping and network-dependent location behavior in its existing English and Chinese sections.

### Task 1: Stabilize metadata extraction and weekday captions

**Files:**
- Modify: `src/photo_caption_print/metadata.py:90-102`
- Test: `tests/test_metadata.py`
- Test: `tests/test_captions.py`

- [ ] **Step 1: Add a regression test for family-0 ExifTool output**

Update the expected command in `test_run_exiftool_runs_one_non_mutating_json_batch_command` and add:

```python
def test_synthetic_exif_fields_produce_complete_metadata():
    metadata = metadata_from_exiftool({
        "SourceFile": "synthetic-photo.jpeg",
        "EXIF:DateTimeOriginal": "2030:01:07 08:09:10",
        "EXIF:GPSLatitude": 30.2431,
        "EXIF:GPSLongitude": 120.1502,
        "EXIF:Model": "Test Camera",
    })
    assert metadata.captured_at == datetime(2030, 1, 7, 8, 9, 10)
    assert (metadata.latitude, metadata.longitude) == (
        30.2431,
        120.1502,
    )
    assert metadata.device == "Test Camera"
```

- [ ] **Step 2: Run the metadata regression tests and confirm the command mismatch fails**

Run:

```bash
python3 -m pytest tests/test_metadata.py::test_run_exiftool_runs_one_non_mutating_json_batch_command tests/test_metadata.py::test_synthetic_exif_fields_produce_complete_metadata -v
```

Expected before implementation: the command assertion reports `-G1` where `-G` is required.

- [ ] **Step 3: Request family-0 groups from ExifTool**

In `run_exiftool`, use this command prefix:

```python
command = [
    "exiftool",
    "-json",
    "-G",
    "-n",
    "-api",
    "QuickTimeUTC=1",
    "--",
    *(str(path) for path in paths),
]
```

- [ ] **Step 4: Add and run the exact weekday regression**

Add to `tests/test_captions.py`:

```python
def test_monday_date_formats_with_monday():
    metadata = make_metadata(captured_at=datetime(2030, 1, 7, 8, 9, 10))
    assert format_caption(metadata)[0] == "2030年01月07日 · 星期一 · 08:09"
```

Run:

```bash
python3 -m pytest tests/test_metadata.py tests/test_captions.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the metadata fix**

```bash
git add src/photo_caption_print/metadata.py tests/test_metadata.py tests/test_captions.py
git commit -m "fix: restore photo metadata captions"
```

### Task 2: Implement the approved landscape geometry

**Files:**
- Modify: `src/photo_caption_print/layout.py:1-127`
- Test: `tests/test_layout.py`

- [ ] **Step 1: Replace old landscape expectations with exact approved geometry**

Add this test and update existing font-size assertions:

```python
def test_landscape_geometry_uses_narrow_margin_center_crop_and_smaller_type():
    geometry = geometry_for(3264, 2448)
    assert (geometry.canvas_width, geometry.canvas_height) == (1800, 1200)
    assert (geometry.caption_top, geometry.photo_x, geometry.photo_y) == (960, 80, 0)
    assert (geometry.photo_width, geometry.photo_height) == (1640, 960)
    assert geometry.source_crop == (1640, 960)
    assert (geometry.primary_font_size, geometry.secondary_font_size) == (28, 20)
    assert (geometry.primary_min_font_size, geometry.secondary_min_font_size) == (18, 15)
```

Also protect the unaffected shapes:

```python
def test_square_and_portrait_remain_uncropped_with_reduced_type():
    square = geometry_for(3000, 3000)
    portrait = geometry_for(3024, 4032)
    assert square.source_crop is None
    assert portrait.source_crop is None
    assert (square.primary_font_size, square.secondary_font_size) == (28, 20)
    assert (portrait.primary_font_size, portrait.secondary_font_size) == (42, 30)
```

- [ ] **Step 2: Run the geometry tests and confirm failure**

Run:

```bash
python3 -m pytest tests/test_layout.py -k 'landscape_geometry or square_and_portrait' -v
```

Expected before implementation: old 60-pixel inset, uncropped dimensions, and 34/24 type differ from the assertions.

- [ ] **Step 3: Give `PrintGeometry` an explicit optional crop frame**

Change the field to:

```python
source_crop: tuple[int, int] | None = None
```

In `geometry_for`, distinguish strict landscape from square/portrait:

```python
portrait = source_height > source_width
landscape = source_width > source_height
canvas_width, canvas_height = (1200, 1800) if portrait else (1800, 1200)
caption_height = canvas_height // 5
caption_top = canvas_height - caption_height

if landscape:
    area_x, area_y, area_width, area_height = 80, 0, 1640, 960
    photo_width, photo_height = area_width, area_height
    photo_x, photo_y = area_x, area_y
    scale = max(area_width / source_width, area_height / source_height)
    source_crop = (area_width, area_height)
else:
    inset = 40 if portrait else 60
    area_x = area_y = inset
    area_width = canvas_width - 2 * inset
    area_height = caption_top - 2 * inset
    scale = min(area_width / source_width, area_height / source_height)
    photo_width = max(1, _round_half_up(source_width * scale))
    photo_height = max(1, _round_half_up(source_height * scale))
    photo_x = area_x + (area_width - photo_width) // 2
    photo_y = area_y + (area_height - photo_height) // 2
    source_crop = None

if portrait:
    primary_font_size, secondary_font_size = 42, 30
    primary_min_font_size, secondary_min_font_size = 27, 22
else:
    primary_font_size, secondary_font_size = 28, 20
    primary_min_font_size, secondary_min_font_size = 18, 15
```

Pass `source_crop=source_crop` in the returned `PrintGeometry`.

- [ ] **Step 4: Run all pure layout tests**

Run:

```bash
python3 -m pytest tests/test_layout.py -q
```

Expected: all layout tests pass after updating old exact geometry assertions to the approved values; no unrelated assertions are weakened.

- [ ] **Step 5: Commit geometry separately**

```bash
git add src/photo_caption_print/layout.py tests/test_layout.py
git commit -m "feat: refine landscape print geometry"
```

### Task 3: Render the center crop safely

**Files:**
- Modify: `src/photo_caption_print/layout.py:273-352`
- Test: `tests/test_layout.py`

- [ ] **Step 1: Add a command-construction regression test**

```python
def test_landscape_render_centers_and_crops_to_the_exact_photo_frame(tmp_path):
    command = build_magick_command(
        Path("source.jpg"),
        tmp_path / "output.jpg",
        geometry_for(3264, 2448),
        ("2030年01月07日 · 星期一 · 08:09", "重庆 · 合川区 / Test Camera"),
        "Helvetica",
        profile_path=_profile(tmp_path),
    )
    resize = command.index("-resize")
    assert command[resize : resize + 7] == [
        "-resize", "1640x960^", "-gravity", "center",
        "-extent", "1640x960", ")",
    ]
    assert "+80+0" in command
```

- [ ] **Step 2: Run the new command test and confirm failure**

Run:

```bash
python3 -m pytest tests/test_layout.py::test_landscape_render_centers_and_crops_to_the_exact_photo_frame -v
```

Expected before implementation: the command contains a one-axis fit resize and no crop extent.

- [ ] **Step 3: Build different ImageMagick transforms for cropped and uncropped geometry**

Add this focused helper:

```python
def _photo_transform(geometry: PrintGeometry) -> list[str]:
    if geometry.source_crop is not None:
        width, height = geometry.source_crop
        return [
            "-resize", f"{width}x{height}^",
            "-gravity", "center",
            "-extent", f"{width}x{height}",
        ]
    return ["-resize", _resize_spec(geometry)]
```

Use `*_photo_transform(geometry)` inside the source-image parenthesis in `build_magick_command`, after `-profile` and before `")"`. Keep all arguments as a list; do not invoke a shell.

- [ ] **Step 4: Run command, safety, and text-fitting tests**

Run:

```bash
python3 -m pytest tests/test_layout.py -q
```

Expected: all tests pass, including leading-hyphen paths and annotation escaping.

- [ ] **Step 5: Commit rendering behavior**

```bash
git add src/photo_caption_print/layout.py tests/test_layout.py
git commit -m "feat: center crop landscape photos"
```

### Task 4: Prove GPS location flow and transparent failures

**Files:**
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_geocode.py`

- [ ] **Step 1: Add an end-to-end unit test from EXIF row to rendered captions**

```python
def test_exif_gps_resolves_location_and_reaches_caption_and_report(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir()
    make_photo(source, "photo.jpeg")
    reader = FakeMetadataReader([{
        "SourceFile": "photo.jpeg",
        "EXIF:DateTimeOriginal": "2030:01:07 08:09:10",
        "EXIF:GPSLatitude": 35.12345,
        "EXIF:GPSLongitude": 139.54321,
        "EXIF:Model": "Test Camera",
    }])
    geocoder = FakeGeocoder(GeocodeResult("重庆 · 合川区"))
    renderer = FakeRenderer()

    summary = pipeline(reader, geocoder, renderer).process_folder(source, output, report)

    assert geocoder.calls == [(35.12345, 139.54321)]
    assert renderer.calls[0][3] == (
        "2030年01月07日 · 星期一 · 08:09",
        "重庆 · 合川区 / Test Camera",
    )
    assert read_report(report)[0]["location"] == "重庆 · 合川区"
    assert summary.failed_count == 0
```

- [ ] **Step 2: Add a network-failure report assertion**

Extend the existing geocode warning test to assert:

```python
row = read_report(report)[0]
assert row["location"] == ""
assert "Offline cache miss" in row["warning"]
assert renderer.calls[0][3][0] == ""
```

- [ ] **Step 3: Run pipeline and geocoder tests**

Run:

```bash
python3 -m pytest tests/test_pipeline.py tests/test_geocode.py -q
```

Expected: all tests pass without making real network requests.

- [ ] **Step 4: Commit flow coverage**

```bash
git add tests/test_pipeline.py tests/test_geocode.py
git commit -m "test: cover GPS caption and location warnings"
```

### Task 5: Native fixture verification and documentation

**Files:**
- Create: `tests/integration/test_desktop_fixture.py`
- Modify: `README.md`

- [ ] **Step 1: Add an opt-in, read-only desktop fixture test**

```python
import hashlib
import os
from pathlib import Path

import pytest

from photo_caption_print.captions import format_caption
from photo_caption_print.metadata import metadata_from_exiftool, run_exiftool


FIXTURE_ENV = "PHOTO_CAPTION_PRINT_DESKTOP_FIXTURE"


@pytest.mark.integration
def test_desktop_fixture_metadata_is_complete_and_source_is_unchanged():
    configured = os.environ.get(FIXTURE_ENV)
    if not configured:
        pytest.skip(f"set {FIXTURE_ENV} for the native desktop check")
    source = Path(configured)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    metadata = metadata_from_exiftool(run_exiftool([source])[0])
    primary, secondary = format_caption(metadata)
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    assert primary == "2030年01月07日 · 星期一 · 08:09"
    assert metadata.latitude is not None and metadata.longitude is not None
    assert "Test Camera" in secondary
    assert before == after
```

- [ ] **Step 2: Document the approved behavior in both READMEs**

Add these exact facts to the corresponding English and Chinese output/layout sections in `README.md`:

```text
Landscape photos use a centered, mild crop inside a 1640×960 photo frame,
leaving 80 px side margins. Portrait and square photos remain uncropped.
GPS coordinates are reverse-geocoded on first use and cached; if the service is
unavailable, the report explains why the location is missing.

横图会在 1640×960 像素的照片区域内居中轻微裁切，左右各保留 80 像素白边；
竖图和正方形照片仍然完整保留、不裁切。GPS 坐标首次使用时会联网转换为地点并
写入缓存；如果地点服务不可用，处理报告会明确说明地点缺失的原因。
```

- [ ] **Step 3: Run the desktop metadata check and pure suite**

Run:

```bash
export PHOTO_CAPTION_PRINT_DESKTOP_FIXTURE='/path/to/photo.jpeg'
python3 -m pytest tests/integration/test_desktop_fixture.py -v
python3 -m pytest --ignore=tests/integration -q
```

Expected: desktop fixture passes without changing its SHA-256; all non-native tests pass.

- [ ] **Step 4: Render one network-enabled preview to a temporary directory**

Run:

```bash
preview_dir="$(mktemp -d /private/tmp/photo-caption-final.XXXXXX)"
mkdir -p "$preview_dir/input"
cp "$PHOTO_CAPTION_PRINT_DESKTOP_FIXTURE" "$preview_dir/input/photo.jpeg"
.venv/bin/python -m photo_caption_print.cli \
  --input "$preview_dir/input" \
  --output "$preview_dir/output" \
  --report "$preview_dir/report.csv" \
  --cache "$preview_dir/cache.json"
magick identify -format '%wx%h %[resolution.x]x%[resolution.y]' \
  "$preview_dir/output/photo-print.jpg"
```

Expected: `1800x1200 300x300`; visual inspection shows `星期一`, a resolved location, `Test Camera`, 80-pixel side margins, smaller type, and a centered crop.

- [ ] **Step 5: Commit docs and native verification**

```bash
git add tests/integration/test_desktop_fixture.py README.md
git commit -m "docs: explain caption and crop behavior"
```

### Task 6: Final verification

**Files:**
- Verify only; no planned source changes.

- [ ] **Step 1: Run syntax and whitespace checks**

```bash
zsh -n scripts/Install.command scripts/Photo\ Caption\ Print.command
git diff --check
```

Expected: both commands exit 0 with no output.

- [ ] **Step 2: Run the complete non-native suite**

```bash
python3 -m pytest --ignore=tests/integration -q
```

Expected: all tests pass.

- [ ] **Step 3: Review the final diff and repository state**

```bash
git status --short
git log --oneline -6
```

Expected: no uncommitted implementation changes; recent commits correspond to metadata, geometry, rendering, flow coverage, and documentation.
