import shutil
import subprocess
import re
from pathlib import Path

import pytest

from photo_caption_print.layout import build_magick_command, geometry_for, probe_oriented_dimensions, run_render
from photo_caption_print.cli import _default_font


@pytest.mark.parametrize(
    ("source_size", "expected_canvas"),
    [
        ((80, 40), (1800, 1200)),
        ((40, 80), (1200, 1800)),
        ((60, 60), (1800, 1200)),
        ((1001, 1000), (1800, 1200)),
        ((1000, 1001), (1200, 1800)),
    ],
)
def test_magick_render_preserves_photo_frame_margin_and_print_density(tmp_path, source_size, expected_canvas):
    if shutil.which("magick") is None:
        pytest.skip("ImageMagick 'magick' executable is unavailable")

    raw_source = tmp_path / "raw.png"
    source = tmp_path / "source.jpg"
    output = tmp_path / "output.jpg"
    width, height = source_size
    _edge_marker_fixture(raw_source, width, height)
    subprocess.run(
        ["magick", str(raw_source), str(source)],
        check=True,
    )
    exiftool = shutil.which("exiftool")
    if exiftool is not None:
        subprocess.run(
            [
                exiftool, "-overwrite_original", "-Make=PrivateCamera",
                "-GPSLatitude=1.2", "-GPSLongitude=3.4", str(source),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        input_metadata = subprocess.run(
            [exiftool, "-n", "-s3", "-Make", "-GPSLatitude", "-GPSLongitude", str(source)],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "PrivateCamera" in input_metadata.stdout
        assert "1.2" in input_metadata.stdout
        assert "3.4" in input_metadata.stdout
    geometry = geometry_for(width, height)
    run_render(build_magick_command(source, output, geometry, ("", ""), "Helvetica"))

    identify = subprocess.run(
        ["magick", "identify", "-format", "%wx%h %[resolution.x] %[resolution.y]", str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    canvas, density_x, density_y = identify.stdout.split()
    assert canvas == f"{expected_canvas[0]}x{expected_canvas[1]}"
    assert float(density_x) == pytest.approx(300)
    assert float(density_y) == pytest.approx(300)
    profiles = subprocess.run(["magick", "identify", "-format", "%[profiles]", str(output)], capture_output=True, text=True, check=True)
    assert "icc" in profiles.stdout.lower()
    if exiftool is not None:
        metadata = subprocess.run(
            [exiftool, "-n", "-s3", "-GPSLatitude", "-GPSLongitude", "-Make", str(output)],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "PrivateCamera" not in metadata.stdout
        assert "1.2" not in metadata.stdout
        assert "3.4" not in metadata.stdout
    assert _is_white(_pixel(output, 0, 0))
    if geometry.photo_x > 0:
        assert _is_white(_pixel(output, geometry.photo_x - 1, geometry.photo_y + geometry.photo_height // 2))
    if geometry.photo_y > 0:
        assert _is_white(_pixel(output, geometry.photo_x + geometry.photo_width // 2, geometry.photo_y - 1))
    if geometry.photo_x + geometry.photo_width < geometry.canvas_width:
        assert _is_white(_pixel(output, geometry.photo_x + geometry.photo_width, geometry.photo_y + geometry.photo_height // 2))
    assert _is_white(_pixel(output, geometry.photo_x + geometry.photo_width // 2, geometry.photo_y + geometry.photo_height))
    assert _is_white(_pixel(output, geometry.canvas_width // 2, geometry.caption_top + 5))
    if geometry.source_crop is None:
        assert _is_color(_pixel(output, geometry.photo_x + geometry.photo_width // 2, geometry.photo_y), "blue")
        assert _is_color(_pixel(output, geometry.photo_x + geometry.photo_width - 1, geometry.photo_y + geometry.photo_height // 2), "cyan")
        assert _is_color(_pixel(output, geometry.photo_x + geometry.photo_width // 2, geometry.photo_y + geometry.photo_height - 1), "green")
        assert _is_color(_pixel(output, geometry.photo_x, geometry.photo_y + geometry.photo_height // 2), "yellow")
        assert _near_color(_pixel(output, geometry.photo_x, geometry.photo_y), (255, 0, 255))
        assert _near_color(_pixel(output, geometry.photo_x + geometry.photo_width - 1, geometry.photo_y), (255, 128, 0))
        assert _near_color(_pixel(output, geometry.photo_x, geometry.photo_y + geometry.photo_height - 1), (0, 0, 0))
        assert _near_color(_pixel(output, geometry.photo_x + geometry.photo_width - 1, geometry.photo_y + geometry.photo_height - 1), (128, 0, 255))


@pytest.mark.parametrize(
    ("source_size", "crop_axis"),
    [((1200, 900), "vertical"), ((1600, 400), "horizontal")],
)
def test_landscape_render_is_lossless_center_crop_in_exact_photo_frame(tmp_path, source_size, crop_axis):
    if shutil.which("magick") is None:
        pytest.skip("ImageMagick 'magick' executable is unavailable")

    source = tmp_path / "source.png"
    output = tmp_path / "output.png"
    _center_crop_marker_fixture(source, *source_size, axis=crop_axis)
    geometry = geometry_for(*source_size)
    run_render(build_magick_command(source, output, geometry, ("", ""), "Helvetica"))

    identify = subprocess.run(
        ["magick", "identify", "-format", "%wx%h", str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert identify.stdout == "1800x1200"
    bounds = subprocess.run(
        [
            "magick", str(output), "-fuzz", "0%", "-fill", "black", "-opaque", "white",
            "-trim", "-format", "%wx%h%O", "info:",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert bounds.stdout == "1640x960+80+0"

    for point in ((79, 0), (1720, 0), (79, 959), (1720, 959), (80, 960), (1719, 960)):
        assert _is_white(_pixel(output, *point))
    for point in ((80, 0), (1719, 0), (80, 959), (1719, 959)):
        assert not _is_white(_pixel(output, *point))

    if crop_axis == "vertical":
        assert _near_color(_pixel(output, 900, 40), (230, 20, 20))
        assert _near_color(_pixel(output, 900, 900), (20, 220, 20))
        assert _near_color(_pixel(output, 900, 0), (64, 64, 64))
        assert _near_color(_pixel(output, 900, 959), (64, 64, 64))
    else:
        assert _near_color(_pixel(output, 220, 480), (230, 20, 20))
        assert _near_color(_pixel(output, 1540, 480), (20, 220, 20))
        assert _near_color(_pixel(output, 80, 480), (64, 64, 64))
        assert _near_color(_pixel(output, 1719, 480), (64, 64, 64))


def test_live_magick_command_accepts_leading_hyphen_input_and_output_paths(tmp_path):
    if shutil.which("magick") is None:
        pytest.skip("ImageMagick 'magick' executable is unavailable")

    ordinary_source = tmp_path / "input.png"
    source = tmp_path / "-input.png"
    output = tmp_path / "-output.jpg"
    _edge_marker_fixture(ordinary_source, 40, 20)
    ordinary_source.rename(source)

    run_render(
        build_magick_command(Path(source.name), Path(output.name), geometry_for(40, 20), ("100% %[EXIF:Make]", ""), _default_font()),
        cwd=tmp_path,
    )

    assert output.is_file()


def test_auto_orient_probe_selects_portrait_canvas_and_preserves_primary_frame_edges(tmp_path):
    if shutil.which("magick") is None:
        pytest.skip("ImageMagick 'magick' executable is unavailable")
    exiftool = shutil.which("exiftool")
    if exiftool is None:
        pytest.skip("ExifTool executable is unavailable")

    raw_source = tmp_path / "raw.jpg"
    source = tmp_path / "oriented.jpg"
    output = tmp_path / "output.jpg"
    _edge_marker_fixture(raw_source, 80, 40)
    subprocess.run(["magick", str(raw_source), str(source)], check=True)
    subprocess.run([exiftool, "-overwrite_original", "-EXIF:Orientation#=6", str(source)], check=True)
    orientation = subprocess.run(
        [exiftool, "-n", "-s3", "-EXIF:Orientation", str(source)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert orientation.stdout.strip() == "6"

    width, height = probe_oriented_dimensions(source)
    geometry = geometry_for(width, height)
    run_render(build_magick_command(source, output, geometry, ("", ""), "Helvetica"))

    identify = subprocess.run(["magick", "identify", "-format", "%wx%h", str(output)], capture_output=True, text=True, check=True)
    assert (width, height) == (40, 80)
    assert identify.stdout == "1200x1800"
    assert _is_color(_pixel(output, geometry.photo_x + geometry.photo_width // 2, geometry.photo_y), "yellow")


def test_literal_percent_property_caption_draws_visible_text_instead_of_expanding(tmp_path):
    if shutil.which("magick") is None:
        pytest.skip("ImageMagick 'magick' executable is unavailable")

    source = tmp_path / "source.png"
    output = tmp_path / "output.jpg"
    _edge_marker_fixture(source, 40, 20)
    geometry = geometry_for(40, 20)
    run_render(build_magick_command(source, output, geometry, ("%[EXIF:NeverThere]", ""), _default_font()))

    ink = subprocess.run(
        ["magick", str(output), "-crop", f"{geometry.canvas_width}x{geometry.canvas_height - geometry.caption_top}+0+{geometry.caption_top}", "-threshold", "90%", "-format", "%[fx:mean]", "info:"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert float(ink.stdout) < 0.999


def test_cli_default_font_renders_distinct_chinese_glyphs(tmp_path):
    if shutil.which("magick") is None:
        pytest.skip("ImageMagick 'magick' executable is unavailable")

    source = tmp_path / "source.png"
    _edge_marker_fixture(source, 40, 20)
    geometry = geometry_for(40, 20)
    font = _default_font()
    outputs = {}
    for name, glyph in {"blank": "", "tofu": "□", "zhong": "中", "wen": "文", "xing": "星", "qi": "期"}.items():
        output = tmp_path / f"{name}.jpg"
        run_render(build_magick_command(source, output, geometry, ("", glyph), font))
        outputs[name] = output

    for name in ("zhong", "wen", "xing", "qi"):
        ink = subprocess.run(
            ["magick", str(outputs[name]), "-crop", "1800x240+0+960", "-threshold", "90%", "-format", "%[fx:mean]", "info:"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert float(ink.stdout) < 1
        for control in ("blank", "tofu"):
            comparison = subprocess.run(
                ["magick", "compare", "-metric", "AE", str(outputs[name]), str(outputs[control]), "null:"],
                capture_output=True,
                text=True,
            )
            assert comparison.returncode == 1
            assert float(comparison.stderr.split()[0]) > 0

    tofu_ink = subprocess.run(
        ["magick", str(outputs["tofu"]), "-crop", "1800x240+0+960", "-threshold", "90%", "-format", "%[fx:mean]", "info:"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert float(tofu_ink.stdout) < 1
    tofu_comparison = subprocess.run(
        ["magick", "compare", "-metric", "AE", str(outputs["tofu"]), str(outputs["blank"]), "null:"],
        capture_output=True,
        text=True,
    )
    assert tofu_comparison.returncode == 1
    assert float(tofu_comparison.stderr.split()[0]) > 0

    for left, right in (("zhong", "wen"), ("zhong", "xing"), ("zhong", "qi"), ("wen", "xing"), ("wen", "qi"), ("xing", "qi")):
        comparison = subprocess.run(
            ["magick", "compare", "-metric", "AE", str(outputs[left]), str(outputs[right]), "null:"],
            capture_output=True,
            text=True,
        )
        assert comparison.returncode == 1
        assert float(comparison.stderr.split()[0]) > 0


def test_multiframe_source_renders_only_the_first_frame(tmp_path):
    if shutil.which("magick") is None:
        pytest.skip("ImageMagick 'magick' executable is unavailable")

    source = tmp_path / "frames.gif"
    output = tmp_path / "output.jpg"
    subprocess.run(["magick", "-size", "40x20", "xc:red", "-size", "40x20", "xc:blue", "-loop", "0", str(source)], check=True)

    width, height = probe_oriented_dimensions(source)
    geometry = geometry_for(width, height)
    run_render(build_magick_command(source, output, geometry, ("", ""), "Helvetica"))

    frames = subprocess.run(["magick", "identify", "-format", "%n", str(output)], capture_output=True, text=True, check=True)
    assert frames.stdout == "1"
    assert _is_color(_pixel(output, geometry.photo_x + geometry.photo_width // 2, geometry.photo_y + geometry.photo_height // 2), "red")


def _edge_marker_fixture(path, width, height):
    subprocess.run(
        [
            "magick", "-size", f"{width}x{height}", "xc:red",
            "-fill", "blue", "-draw", f"rectangle 0,0 {width - 1},3",
            "-fill", "cyan", "-draw", f"rectangle {width - 4},0 {width - 1},{height - 1}",
            "-fill", "#00ff00", "-draw", f"rectangle 0,{height - 4} {width - 1},{height - 1}",
            "-fill", "yellow", "-draw", f"rectangle 0,0 3,{height - 1}",
            "-fill", "#ff00ff", "-draw", "rectangle 0,0 3,3",
            "-fill", "#ff8000", "-draw", f"rectangle {width - 4},0 {width - 1},3",
            "-fill", "black", "-draw", f"rectangle 0,{height - 4} 3,{height - 1}",
            "-fill", "#8000ff", "-draw", f"rectangle {width - 4},{height - 4} {width - 1},{height - 1}",
            str(path),
        ],
        check=True,
    )


def _center_crop_marker_fixture(path, width, height, *, axis):
    background = "#404040"
    commands = ["magick", "-size", f"{width}x{height}", f"xc:{background}"]
    if axis == "vertical":
        commands.extend([
            "-fill", "#1414e6", "-draw", f"rectangle 0,0 {width - 1},80",
            "-fill", "#e61414", "-draw", f"rectangle 0,120 {width - 1},160",
            "-fill", "#14dc14", "-draw", f"rectangle 0,740 {width - 1},780",
            "-fill", "#14e6e6", "-draw", f"rectangle 0,820 {width - 1},{height - 1}",
        ])
    else:
        commands.extend([
            "-fill", "#1414e6", "-draw", f"rectangle 0,0 400,{height - 1}",
            "-fill", "#e61414", "-draw", f"rectangle 500,0 550,{height - 1}",
            "-fill", "#14dc14", "-draw", f"rectangle 1050,0 1100,{height - 1}",
            "-fill", "#14e6e6", "-draw", f"rectangle 1200,0 {width - 1},{height - 1}",
        ])
    subprocess.run([*commands, str(path)], check=True)


def _pixel(path, x, y):
    result = subprocess.run(
        ["magick", str(path), "-format", f"%[pixel:p{{{x},{y}}}]", "info:"],
        capture_output=True,
        text=True,
        check=True,
    )
    return tuple(int(float(channel)) for channel in re.findall(r"\d+(?:\.\d+)?", result.stdout)[:3])


def _is_white(pixel):
    return min(pixel) >= 245


def _is_color(pixel, color):
    red, green, blue = pixel
    expected = {
        "blue": (False, False, True),
        "cyan": (False, True, True),
        "green": (False, True, False),
        "yellow": (True, True, False),
        "red": (True, False, False),
    }[color]
    channels = (red >= 220, green >= 220, blue >= 220)
    dark = (red <= 35, green <= 35, blue <= 35)
    return all((channels[index] if wanted else dark[index]) for index, wanted in enumerate(expected))


def _near_color(pixel, expected):
    return all(abs(actual - wanted) <= 35 for actual, wanted in zip(pixel, expected))
