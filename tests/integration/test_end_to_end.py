"""End-to-end coverage using real ExifTool, ImageMagick, CLI, and pipeline code."""

from __future__ import annotations

import csv
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from photo_caption_print.cli import main
from photo_caption_print.geocode import GeocodeResult
from photo_caption_print.layout import geometry_for
from photo_caption_print.metadata import run_exiftool


MAGICK = shutil.which("magick")
EXIFTOOL = shutil.which("exiftool")
SRGB_PROFILE = Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc")

_MISSING_BASE_DEPENDENCIES = [
    name for name, path in (("magick", MAGICK), ("exiftool", EXIFTOOL)) if path is None
]
if _MISSING_BASE_DEPENDENCIES:
    pytest.skip(
        "native integration module skipped: missing required binary: "
        + ", ".join(_MISSING_BASE_DEPENDENCIES),
        allow_module_level=True,
    )
if not SRGB_PROFILE.is_file():
    pytest.skip(
        f"native integration module skipped: missing sRGB ICC profile: {SRGB_PROFILE}",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def render_tools():
    return MAGICK, EXIFTOOL


def test_cli_pipeline_handles_synthetic_folder_without_mutating_inputs(tmp_path: Path, render_tools):
    input_dir = tmp_path / "已选照片"
    output_dir = tmp_path / "打印成品"
    report_path = tmp_path / "reports" / "处理报告.csv"
    input_dir.mkdir()

    landscape = _marker_image(tmp_path / "landscape.png", 160, 100)
    _convert(landscape, input_dir / "landscape.jpg")
    _set_tags(input_dir / "landscape.jpg", "2024:05:06 07:08:09", "Complete Camera", gps=True)

    portrait = _marker_image(tmp_path / "portrait.png", 160, 100)
    _convert(portrait, input_dir / "portrait.jpg")
    _set_tags(input_dir / "portrait.jpg", orientation="6")

    square = _marker_image(tmp_path / "square.png", 100, 100)
    _convert(square, input_dir / "square.jpg")
    _strip_tags(input_dir / "square.jpg")

    dated = _marker_image(tmp_path / "dated.png", 120, 80)
    _convert(dated, input_dir / "dated.jpg")
    _set_tags(input_dir / "dated.jpg", "2023:02:03 04:05:06", "Date-only Camera")

    duplicate_jpg = _marker_image(tmp_path / "duplicate-jpg.png", 120, 80)
    _convert(duplicate_jpg, input_dir / "duplicate.jpg")
    duplicate_png = _marker_image(tmp_path / "duplicate-png.png", 120, 80)
    _convert(duplicate_png, input_dir / "duplicate.png")

    unreadable = input_dir / "unreadable.jpg"
    unreadable.write_bytes(b"this is intentionally not an image")

    before = {path.name: _identity(path) for path in input_dir.iterdir()}

    geocoders = []

    class FakeGeocoder:
        def __init__(self, *_args, **_kwargs):
            self.calls = []
            geocoders.append(self)

        def reverse(self, latitude, longitude):
            self.calls.append((latitude, longitude))
            return GeocodeResult("Test City")

    def metadata_reader(paths):
        rows = run_exiftool(paths)
        # FileModifyDate is a useful production fallback, but this fixture
        # explicitly exercises the all-metadata-missing rendering contract.
        for row in rows:
            if Path(row.get("SourceFile", "")).name == "square.jpg":
                row.pop("File:FileModifyDate", None)
        return rows

    result = main(
        [
            "--base-dir",
            str(tmp_path),
            "--input",
            str(input_dir),
            "--output",
            str(output_dir),
            "--report",
            str(report_path),
            "--offline",
            "--srgb-profile",
            str(SRGB_PROFILE),
        ],
        services={"geocoder_factory": FakeGeocoder, "metadata_reader": metadata_reader},
    )

    assert result == 1
    assert len(geocoders) == 1
    assert geocoders[0].calls == [(35.12345, 139.54321)]
    rows = _read_report(report_path)
    assert len(rows) == 7
    by_source = {Path(row["source"]).name: row for row in rows}
    assert set(by_source) == set(before)
    assert by_source["unreadable.jpg"]["status"] == "failed"
    assert by_source["unreadable.jpg"]["output"] == ""
    assert by_source["square.jpg"]["missing_fields"]
    assert by_source["landscape.jpg"]["location"] == "Test City"
    assert by_source["dated.jpg"]["location"] == ""
    assert by_source["dated.jpg"]["device"] == "Date-only Camera"
    assert (output_dir / "duplicate-print.jpg").is_file()
    assert (output_dir / "duplicate-print-2.jpg").is_file()

    _assert_output(output_dir / "landscape-print.jpg", (160, 100), ("blue", "cyan", "green", "yellow"))
    _assert_output(output_dir / "portrait-print.jpg", (100, 160), ("yellow", "blue", "cyan", "green"))
    _assert_output(output_dir / "square-print.jpg", (100, 100), ("blue", "cyan", "green", "yellow"))
    _assert_output(output_dir / "dated-print.jpg", (120, 80), ("blue", "cyan", "green", "yellow"))
    _assert_output(output_dir / "duplicate-print.jpg", (120, 80), ("blue", "cyan", "green", "yellow"))
    _assert_output(output_dir / "duplicate-print-2.jpg", (120, 80), ("blue", "cyan", "green", "yellow"))

    square_geometry = geometry_for(100, 100)
    _assert_blank_caption(output_dir / "square-print.jpg", square_geometry)

    for name, identity in before.items():
        assert _identity(input_dir / name) == identity


def test_cli_pipeline_renders_portrait_heic_when_heic_delegate_is_available(tmp_path: Path, render_tools):
    format_result = subprocess.run([MAGICK, "-list", "format"], check=True, capture_output=True, text=True)
    if not re.search(r"(?im)^\s*HEIC\b", format_result.stdout):
        pytest.skip("HEIC-specific case skipped: ImageMagick has no HEIC delegate")

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    report_path = tmp_path / "report.csv"
    input_dir.mkdir()
    raw = _marker_image(tmp_path / "portrait-heic.png", 160, 100)
    heic = input_dir / "portrait.heic"
    try:
        _convert(raw, heic)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        if "delegate" in detail.lower() or "heic" in detail.lower():
            pytest.skip(f"HEIC-specific case skipped: encoder delegate unavailable ({detail})")
        raise
    _set_tags(heic, orientation="6")
    before = _identity(heic)

    class FakeGeocoder:
        def __init__(self, *_args, **_kwargs):
            pass

    result = main(
        [
            "--input", str(input_dir), "--output", str(output_dir), "--report", str(report_path),
            "--offline", "--srgb-profile", str(SRGB_PROFILE),
        ],
        services={"geocoder_factory": FakeGeocoder},
    )
    assert result == 0
    rows = _read_report(report_path)
    assert len(rows) == 1 and rows[0]["status"] in {"success", "warning"}
    output = output_dir / "portrait-print.jpg"
    _assert_output(output, (100, 160), ("yellow", "blue", "cyan", "green"))
    assert _identity(heic) == before


def _marker_image(path: Path, width: int, height: int) -> Path:
    subprocess.run(
        [
            MAGICK,
            "-size",
            f"{width}x{height}",
            "xc:red",
            "-fill",
            "blue",
            "-draw",
            f"rectangle 0,0 {width - 1},9",
            "-fill",
            "cyan",
            "-draw",
            f"rectangle {width - 10},0 {width - 1},{height - 1}",
            "-fill",
            "green",
            "-draw",
            f"rectangle 0,{height - 10} {width - 1},{height - 1}",
            "-fill",
            "yellow",
            "-draw",
            f"rectangle 0,0 9,{height - 1}",
            str(path),
        ],
        check=True,
    )
    return path


def _convert(source: Path, destination: Path):
    subprocess.run([MAGICK, str(source), str(destination)], check=True, capture_output=True, text=True)


def _set_tags(path: Path, captured_at: str | None = None, device: str | None = None, *, gps: bool = False, orientation: str | None = None):
    command = [EXIFTOOL, "-overwrite_original"]
    if captured_at:
        command.append(f"-EXIF:DateTimeOriginal={captured_at}")
    if device:
        command.append(f"-EXIF:Model={device}")
    if gps:
        command.extend(["-GPSLatitude=35.12345", "-GPSLatitudeRef=N", "-GPSLongitude=139.54321", "-GPSLongitudeRef=E"])
    if orientation:
        command.append(f"-EXIF:Orientation#={orientation}")
    command.append(str(path))
    _run_exiftool(command)
    if orientation:
        assert _exiftool(path, "-n", "-s3", "-EXIF:Orientation").strip() == orientation


def _strip_tags(path: Path):
    _run_exiftool([EXIFTOOL, "-overwrite_original", "-all=", str(path)])


def _run_exiftool(command):
    subprocess.run(command, check=True, capture_output=True, text=True)


def _exiftool(path: Path, *arguments: str) -> str:
    return subprocess.run([EXIFTOOL, *arguments, str(path)], check=True, capture_output=True, text=True).stdout


def _identify(path: Path, *arguments: str) -> str:
    return subprocess.run([MAGICK, str(path), *arguments], check=True, capture_output=True, text=True).stdout.strip()


def _assert_output(path: Path, source_size: tuple[int, int], expected_edges: tuple[str, str, str, str]):
    assert path.is_file()
    source_width, source_height = source_size
    geometry = geometry_for(source_width, source_height)
    dimensions = _identify(path, "-format", "%wx%h %[resolution.x] %[resolution.y]", "info:").split()
    expected_canvas = "1200x1800" if source_height > source_width else "1800x1200"
    assert dimensions[0] == expected_canvas
    assert float(dimensions[1]) == pytest.approx(300)
    assert float(dimensions[2]) == pytest.approx(300)
    assert _is_white(_pixel(path, 0, 0))
    points = (
        (geometry.photo_x + geometry.photo_width // 2, geometry.photo_y + 2),
        (geometry.photo_x + geometry.photo_width - 3, geometry.photo_y + geometry.photo_height // 2),
        (geometry.photo_x + geometry.photo_width // 2, geometry.photo_y + geometry.photo_height - 3),
        (geometry.photo_x + 2, geometry.photo_y + geometry.photo_height // 2),
    )
    assert [_classify(_pixel(path, *point)) for point in points] == list(expected_edges)
    profile_text = _identify(path, "-format", "%[profiles]", "info:")
    assert "icc" in profile_text.lower()
    gps_text = _exiftool(path, "-GPSLatitude", "-GPSLongitude")
    assert "GPS Latitude" not in gps_text
    assert "GPS Longitude" not in gps_text


def _assert_blank_caption(path: Path, geometry):
    regions = [
        (geometry.caption_top, geometry.canvas_height),
        (geometry.caption_top, geometry.secondary_y),
        (geometry.secondary_y, geometry.canvas_height),
    ]
    for top, bottom in regions:
        ink = _identify(
            path,
            "-crop",
            f"{geometry.canvas_width}x{bottom - top}+0+{top}",
            "-threshold",
            "90%",
            "-format",
            "%[fx:mean]",
            "info:",
        )
        assert float(ink) > 0.999


def _pixel(path: Path, x: int, y: int) -> tuple[int, int, int]:
    text = _identify(path, "-format", f"%[pixel:p{{{x},{y}}}]", "info:")
    values = [int(float(value)) for value in re.findall(r"\d+(?:\.\d+)?", text)]
    return tuple(values[:3])


def _classify(pixel: tuple[int, int, int]) -> str:
    red, green, blue = pixel
    if red <= 35 and green >= 100 and blue <= 35:
        return "green"
    channels = (red >= 180, green >= 180, blue >= 180)
    if channels == (False, False, True):
        return "blue"
    if channels == (False, True, True):
        return "cyan"
    if channels == (True, True, False):
        return "yellow"
    return "other"


def _is_white(pixel: tuple[int, int, int]) -> bool:
    return min(pixel) >= 245


def _identity(path: Path) -> tuple[str, int, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), path.stat().st_size, path.stat().st_mtime_ns


def _read_report(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))
