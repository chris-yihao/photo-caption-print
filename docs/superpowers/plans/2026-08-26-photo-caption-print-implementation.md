# Photo Caption Print Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a free macOS folder-based tool that converts selected iPhone photos into uncropped, print-ready 6×4 JPEGs with metadata-aware white caption borders and a CSV report.

**Architecture:** A Python package orchestrates ExifTool and ImageMagick through small, typed modules. Metadata extraction, caption formatting, reverse geocoding, print geometry, image rendering, and batch reporting remain independently testable; a `.command` launcher supplies the double-click Mac experience. Network geocoding is optional and cached, while every photo is processed independently so one failure never aborts the batch.

**Tech Stack:** Python 3.11+, standard library, pytest, ExifTool, ImageMagick 7, Nominatim-compatible reverse geocoding, macOS shell launcher, GitHub Actions.

---

## File map

- `pyproject.toml`: package metadata, console command, pytest configuration.
- `src/photo_caption_print/models.py`: immutable metadata, geocode, job, and result types.
- `src/photo_caption_print/metadata.py`: ExifTool JSON adapter and metadata precedence.
- `src/photo_caption_print/captions.py`: Chinese date, weekday, location, device, and missing-field formatting.
- `src/photo_caption_print/geocode.py`: rate-limited reverse geocoder with persistent JSON cache.
- `src/photo_caption_print/layout.py`: 6×4 geometry and ImageMagick command construction.
- `src/photo_caption_print/pipeline.py`: batch discovery, overrides, rendering, collision-safe naming, and reports.
- `src/photo_caption_print/cli.py`: dependency checks, arguments, status summary, and exit codes.
- `scripts/Photo Caption Print.command`: Finder double-click entry point.
- `scripts/Install.command`: Homebrew dependency setup and local virtual environment.
- `tests/`: unit and integration coverage using generated fixtures and fake subprocess runners.
- `.github/workflows/test.yml`: Python test matrix without requiring macOS-only UI interaction.

### Task 1: Package skeleton and core types

**Files:**
- Create: `pyproject.toml`
- Create: `src/photo_caption_print/__init__.py`
- Create: `src/photo_caption_print/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing model test**

```python
from pathlib import Path
from photo_caption_print.models import PhotoMetadata


def test_photo_metadata_has_safe_empty_defaults():
    meta = PhotoMetadata(source=Path("edited.jpg"))
    assert meta.captured_at is None
    assert meta.latitude is None
    assert meta.longitude is None
    assert meta.device is None
    assert meta.location is None
```

- [ ] **Step 2: Add package configuration and run the failing test**

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "photo-caption-print"
version = "0.1.0"
requires-python = ">=3.11"
authors = [{name = "Chris"}]
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
photo-caption-print = "photo_caption_print.cli:main"

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

Run: `python3 -m pytest tests/test_models.py -v`
Expected: FAIL because `photo_caption_print.models` does not exist.

- [ ] **Step 3: Implement immutable core types**

```python
# src/photo_caption_print/models.py
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class PhotoMetadata:
    source: Path
    captured_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    device: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class ProcessResult:
    source: Path
    output: Path | None
    status: str
    warning: str = ""
    error: str = ""
```

Export `__version__ = "0.1.0"` from `src/photo_caption_print/__init__.py`.

- [ ] **Step 4: Verify and commit**

Run: `python3 -m pytest tests/test_models.py -v`
Expected: 1 passed.

```bash
git add pyproject.toml src tests/test_models.py
git commit -m "feat: add package skeleton and core models"
```

### Task 2: ExifTool metadata extraction and precedence

**Files:**
- Create: `src/photo_caption_print/metadata.py`
- Create: `tests/test_metadata.py`

- [ ] **Step 1: Write failing tests for complete and missing metadata**

```python
from pathlib import Path
from photo_caption_print.metadata import metadata_from_exiftool


def test_metadata_prefers_original_capture_time_and_readable_model():
    row = {
        "SourceFile": "IMG_0001.HEIC",
        "EXIF:DateTimeOriginal": "2021:10:16 16:42:03",
        "EXIF:GPSLatitude": 30.2431,
        "EXIF:GPSLongitude": 120.1502,
        "EXIF:Model": "iPhone 13 Pro",
    }
    meta = metadata_from_exiftool(row)
    assert meta.source == Path("IMG_0001.HEIC")
    assert meta.captured_at.isoformat() == "2021-10-16T16:42:03"
    assert (meta.latitude, meta.longitude) == (30.2431, 120.1502)
    assert meta.device == "iPhone 13 Pro"


def test_metadata_accepts_a_file_with_no_tags():
    meta = metadata_from_exiftool({"SourceFile": "processed.jpg"})
    assert meta.captured_at is None
    assert meta.location is None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/test_metadata.py -v`
Expected: FAIL because `metadata_from_exiftool` is undefined.

- [ ] **Step 3: Implement the ExifTool adapter**

Implement `metadata_from_exiftool(row)` with this exact precedence:

```python
DATE_KEYS = (
    "EXIF:DateTimeOriginal", "QuickTime:CreationDate",
    "XMP:DateCreated", "IPTC:DateCreated", "File:FileModifyDate",
)
MODEL_KEYS = ("EXIF:Model", "QuickTime:Model", "XMP:Model")
LAT_KEYS = ("EXIF:GPSLatitude", "QuickTime:GPSLatitude")
LON_KEYS = ("EXIF:GPSLongitude", "QuickTime:GPSLongitude")
```

Parse EXIF dates with `%Y:%m:%d %H:%M:%S`, accept ISO-8601 offsets, and treat file modification time only as a last-resort warning source. Add `run_exiftool(paths, runner=subprocess.run)` that executes one JSON batch:

```python
["exiftool", "-json", "-G1", "-n", "-api", "QuickTimeUTC=1", *map(str, paths)]
```

Reject non-zero exit status with a typed `MetadataError` containing stderr; never mutate the source file.

- [ ] **Step 4: Verify complete, missing, and malformed date cases**

Run: `python3 -m pytest tests/test_metadata.py -v`
Expected: all metadata tests pass, including a malformed date returning `None` instead of aborting the batch.

- [ ] **Step 5: Commit**

```bash
git add src/photo_caption_print/metadata.py tests/test_metadata.py
git commit -m "feat: read photo metadata with ExifTool"
```

### Task 3: Caption formatting and manual overrides

**Files:**
- Create: `src/photo_caption_print/captions.py`
- Create: `tests/test_captions.py`
- Create: `examples/人工补录.csv`

- [ ] **Step 1: Write the formatting matrix tests**

```python
from datetime import datetime
from pathlib import Path
from photo_caption_print.captions import format_caption
from photo_caption_print.models import PhotoMetadata


def test_complete_caption_uses_two_documentary_lines():
    meta = PhotoMetadata(
        source=Path("a.jpg"),
        captured_at=datetime(2021, 10, 16, 16, 42),
        location="杭州 · 西湖风景区",
        device="iPhone 13 Pro",
    )
    assert format_caption(meta) == (
        "2021年10月16日 · 星期六 · 16:42",
        "杭州 · 西湖风景区 / iPhone 13 Pro",
    )


def test_missing_fields_collapse_without_empty_separators():
    meta = PhotoMetadata(source=Path("a.jpg"), device="iPhone 8")
    assert format_caption(meta) == ("", "iPhone 8")


def test_all_missing_fields_leave_both_lines_blank():
    assert format_caption(PhotoMetadata(source=Path("a.jpg"))) == ("", "")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/test_captions.py -v`
Expected: FAIL because the caption module does not exist.

- [ ] **Step 3: Implement caption composition and CSV overrides**

Use the weekday tuple below and construct separators only after removing blank parts:

```python
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

def format_caption(meta: PhotoMetadata) -> tuple[str, str]:
    first = ""
    if meta.captured_at:
        dt = meta.captured_at
        first = f"{dt:%Y年%m月%d日} · {WEEKDAYS[dt.weekday()]} · {dt:%H:%M}"
    second = " / ".join(value for value in (meta.location, meta.device) if value)
    return first, second
```

Add `load_overrides(path)` for UTF-8 CSV columns `filename,captured_at,location,device`. Blank cells do not erase extracted values; nonblank cells override them. Invalid dates produce a row-level warning.

- [ ] **Step 4: Add the example CSV and verify**

```csv
filename,captured_at,location,device
processed-photo.jpg,2018-05-01 14:30,上海 · 外滩,iPhone 8
```

Run: `python3 -m pytest tests/test_captions.py -v`
Expected: all caption and override tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/photo_caption_print/captions.py tests/test_captions.py examples
git commit -m "feat: format captions and apply manual metadata"
```

### Task 4: Cached reverse geocoding

**Files:**
- Create: `src/photo_caption_print/geocode.py`
- Create: `tests/test_geocode.py`

- [ ] **Step 1: Write failing tests for place preference, cache, and offline fallback**

```python
from photo_caption_print.geocode import choose_location


def test_place_name_is_preferred_over_district():
    payload = {"address": {"city": "杭州市", "attraction": "西湖风景区", "suburb": "西湖区"}}
    assert choose_location(payload) == "杭州 · 西湖风景区"


def test_district_is_the_fallback():
    payload = {"address": {"city": "上海市", "city_district": "徐汇区"}}
    assert choose_location(payload) == "上海 · 徐汇区"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/test_geocode.py -v`
Expected: FAIL because `choose_location` is undefined.

- [ ] **Step 3: Implement the geocoder**

Implement `ReverseGeocoder(cache_path, opener, clock, sleeper)` with:

- cache key rounded to five decimal places: `"30.24310,120.15020"`;
- Nominatim `/reverse?format=jsonv2&lat=...&lon=...&zoom=18&addressdetails=1`;
- descriptive `User-Agent: photo-caption-print/0.1 (Chris; GitHub project)`;
- at least one second between uncached requests;
- UTF-8 JSON cache written atomically through a temporary sibling file;
- network/HTTP/JSON failure returned as `None` plus a warning, never as a batch exception.

Select city from `city`, `town`, `municipality`, or `county`; select place from `attraction`, `tourism`, `historic`, `amenity`, `leisure`, `building`, or `suburb`; fall back to `city_district`, `district`, or `county`. Remove only the common administrative suffixes `市` from city names while preserving meaningful place names.

- [ ] **Step 4: Verify cache prevents a second request**

Add a fake opener that raises on its second call. Run:

`python3 -m pytest tests/test_geocode.py -v`

Expected: all geocoder tests pass and the fake opener is called once for two identical coordinates.

- [ ] **Step 5: Commit**

```bash
git add src/photo_caption_print/geocode.py tests/test_geocode.py
git commit -m "feat: add cached reverse geocoding"
```

### Task 5: Print geometry and ImageMagick rendering

**Files:**
- Create: `src/photo_caption_print/layout.py`
- Create: `tests/test_layout.py`
- Create: `tests/integration/test_render.py`

- [ ] **Step 1: Write failing geometry tests**

```python
from photo_caption_print.layout import geometry_for


def test_landscape_is_1800_by_1200():
    g = geometry_for(4032, 3024)
    assert (g.canvas_width, g.canvas_height) == (1800, 1200)
    assert g.photo_width <= 1800
    assert g.photo_height <= g.caption_top


def test_portrait_is_1200_by_1800():
    g = geometry_for(3024, 4032)
    assert (g.canvas_width, g.canvas_height) == (1200, 1800)


def test_square_is_never_cropped():
    g = geometry_for(3000, 3000)
    assert g.source_crop is None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/test_layout.py -v`
Expected: FAIL because `geometry_for` does not exist.

- [ ] **Step 3: Implement fixed print geometry**

Reserve 20% of the canvas height for the caption, with a 60-pixel print-safe inset on the long-edge layout and the proportional equivalent for portrait. Scale with `min(available_width/source_width, available_height/source_height)` and center the photo in the area above the caption. Never use ImageMagick crop operators.

Create `build_magick_command(source, output, geometry, lines, font)` using this operator order:

```text
magick source -auto-orient -resize WIDTHxHEIGHT white-canvas
  scaled-photo -geometry +X+Y -composite
  -font FONT -fill #171717 -gravity north -annotate +0+FIRST_Y FIRST_LINE
  -fill #666666 -annotate +0+SECOND_Y SECOND_LINE
  -units PixelsPerInch -density 300 -colorspace sRGB -strip -quality 94 output.jpg
```

Use fixed readable print sizes with separate landscape/portrait values; if a line exceeds the safe width, remove low-value location detail first, then reduce only to a documented minimum font size. Return a warning if truncation is still required. Preserve output density and ICC conversion while stripping private GPS metadata from generated public/print files.

- [ ] **Step 4: Add an integration test using generated landscape, portrait, and square fixtures**

The integration test must skip with `pytest.skip` when `magick` is absent. When present, generate solid-color fixtures, render them, inspect with `magick identify -format "%wx%h %[resolution.x]"`, and assert 1800×1200 or 1200×1800 at 300 PPI. Compare corner and photo-area pixels to confirm white margins and that no source edge was cropped.

- [ ] **Step 5: Verify and commit**

Run: `python3 -m pytest tests/test_layout.py tests/integration/test_render.py -v`
Expected: geometry tests pass; integration tests pass when ImageMagick is installed or report a clear skip.

```bash
git add src/photo_caption_print/layout.py tests/test_layout.py tests/integration
git commit -m "feat: render uncropped print layouts"
```

### Task 6: Batch pipeline and CSV report

**Files:**
- Create: `src/photo_caption_print/pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing batch behavior tests**

Cover these exact scenarios with fake metadata, geocoder, and renderer dependencies:

```python
def test_one_bad_photo_does_not_abort_the_batch(tmp_path, fake_services):
    results = fake_services.pipeline.process([tmp_path / "good.jpg", tmp_path / "bad.jpg"])
    assert [r.status for r in results] == ["success", "failed"]


def test_duplicate_stems_get_stable_non_overwriting_names(tmp_path, fake_services):
    outputs = fake_services.pipeline.output_paths([tmp_path / "a.jpg", tmp_path / "a.heic"])
    assert [p.name for p in outputs] == ["a-print.jpg", "a-print-2.jpg"]
```

Also test supported extensions `.jpg`, `.jpeg`, `.heic`, `.png`, and `.tif`; ignore hidden files and directories.

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/test_pipeline.py -v`
Expected: FAIL because `BatchPipeline` does not exist.

- [ ] **Step 3: Implement batch orchestration**

`BatchPipeline.process_folder(input_dir, output_dir, report_path, overrides_path=None)` must:

1. discover supported files in case-insensitive filename order;
2. call ExifTool once for the batch;
3. merge nonblank manual overrides;
4. geocode only photos with both coordinates and no manual location;
5. probe dimensions with `magick identify`;
6. render each output independently;
7. calculate effective print PPI and warn below 240 PPI;
8. write `处理报告.csv` atomically with columns `source,output,status,captured_at,location,device,missing_fields,effective_ppi,warning,error`;
9. return success, warning, skipped, and failure counts.

Never overwrite input files or existing unrelated outputs. Re-running with identical inputs replaces only the deterministically named generated outputs through atomic temporary files.

- [ ] **Step 4: Verify failure isolation and report content**

Run: `python3 -m pytest tests/test_pipeline.py -v`
Expected: all pipeline tests pass; the report contains one row per discovered input even when rendering fails.

- [ ] **Step 5: Commit**

```bash
git add src/photo_caption_print/pipeline.py tests/test_pipeline.py
git commit -m "feat: add resilient batch processing and reports"
```

### Task 7: CLI, dependency installer, and double-click launcher

**Files:**
- Create: `src/photo_caption_print/cli.py`
- Create: `tests/test_cli.py`
- Create: `scripts/Photo Caption Print.command`
- Create: `scripts/Install.command`

- [ ] **Step 1: Write failing CLI tests**

```python
def test_missing_dependency_returns_setup_exit_code(cli_runner):
    result = cli_runner(missing=("exiftool",))
    assert result.exit_code == 2
    assert "ExifTool" in result.stderr


def test_batch_failures_return_nonzero_but_keep_summary(cli_runner):
    result = cli_runner(success=3, warning=1, failed=1)
    assert result.exit_code == 1
    assert "成功 3" in result.stdout
    assert "警告 1" in result.stdout
    assert "失败 1" in result.stdout
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/test_cli.py -v`
Expected: FAIL because the CLI does not exist.

- [ ] **Step 3: Implement the CLI contract**

Use `argparse` options `--input`, `--output`, `--report`, `--overrides`, `--cache`, `--offline`, and `--nominatim-url`. Defaults resolve relative to the project launcher directory. Exit `0` when every file succeeds or only has warnings, `1` when any photo fails, and `2` for missing dependencies or invalid folders. Print a Chinese summary and the absolute report path.

- [ ] **Step 4: Add the macOS launchers**

`scripts/Photo Caption Print.command` must resolve its own directory without relying on the current Finder directory, activate `.venv`, create `已选照片`, `打印成品`, `cache`, and `reports`, invoke the CLI, print the summary, and keep the terminal open only when an error needs attention.

`scripts/Install.command` must:

```bash
#!/bin/zsh
set -euo pipefail
command -v brew >/dev/null || { echo "请先安装 Homebrew: https://brew.sh"; exit 2; }
brew install python@3.13 exiftool imagemagick
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
chmod +x "scripts/Photo Caption Print.command" "scripts/Install.command"
```

Do not install or modify anything silently; the user starts `Install.command` explicitly.

- [ ] **Step 5: Verify and commit**

Run: `python3 -m pytest tests/test_cli.py -v`
Expected: all CLI tests pass.

Run: `zsh -n "scripts/Photo Caption Print.command" "scripts/Install.command"`
Expected: exit 0 with no syntax errors.

```bash
git add src/photo_caption_print/cli.py tests/test_cli.py scripts
git commit -m "feat: add macOS install and double-click workflow"
```

### Task 8: End-to-end fixtures, documentation, and CI

**Files:**
- Create: `tests/fixtures/README.md`
- Create: `tests/integration/test_end_to_end.py`
- Create: `.github/workflows/test.yml`
- Modify: `README.md`

- [ ] **Step 1: Add deterministic synthetic fixtures**

Generate, during tests rather than committing personal photos:

- landscape JPEG with complete EXIF;
- portrait HEIC when the local ImageMagick delegate supports HEIC;
- square JPEG with no metadata;
- JPEG with date and device but no GPS;
- two files sharing the same stem;
- one intentionally unreadable file.

Document that real iPhone test photos must never be committed because they can contain GPS and personal information.

- [ ] **Step 2: Write the end-to-end acceptance test**

The test runs the CLI against the synthetic folder with a fake geocoder response, then asserts:

```python
assert landscape.size == (1800, 1200)
assert portrait.size == (1200, 1800)
assert square_photo_edges_are_present(output_square)
assert report_rows == discovered_inputs
assert no_output_contains_gps_metadata(output_dir)
assert cli_summary.failed == 1
```

Run: `python3 -m pytest tests/integration/test_end_to_end.py -v`
Expected: PASS when ExifTool and ImageMagick are installed; otherwise one explicit dependency skip.

- [ ] **Step 3: Add CI and finish the README**

Configure GitHub Actions for Python 3.11 and 3.13 on `macos-latest`, install `exiftool` and `imagemagick` with Homebrew, install `.[dev]`, and run `python -m pytest -v`.

Expand README with:

- Apple Photos export instructions for the edited visible version and retained location data;
- XnView MP rating/filtering steps;
- install and double-click usage;
- folder meanings and manual CSV example;
- privacy notice covering GPS lookup and stripped output metadata;
- troubleshooting for HEIC delegate, missing metadata, offline mode, long locations, and low resolution.

- [ ] **Step 4: Run the full verification suite**

```bash
python3 -m pytest -v
zsh -n "scripts/Photo Caption Print.command" "scripts/Install.command"
git diff --check
```

Expected: all tests pass, shell syntax exits 0, and `git diff --check` produces no output.

- [ ] **Step 5: Perform a private-photo manual acceptance run**

Outside the repository, process a temporary folder containing one landscape, one portrait, one square, one partially missing, and one fully missing metadata photo. Visually inspect at 100% and print-preview scale. Confirm no cropping, centered caption lines, clean blank caption area, safe margins, correct Chinese weekday, readable location fallback, and no modification timestamps on source files.

- [ ] **Step 6: Commit and push**

```bash
git add README.md tests .github/workflows/test.yml
git commit -m "test: add end-to-end coverage and user guide"
git push origin main
```

GitHub Actions must finish green before tagging the first usable release.
