from pathlib import Path
from types import SimpleNamespace

import pytest

from photo_caption_print.layout import (
    RenderError,
    build_magick_command,
    fit_captions,
    geometry_for,
    measure_text,
    probe_oriented_dimensions,
    run_render,
)


def _profile(tmp_path):
    path = tmp_path / "sRGB Profile.icc"
    path.write_bytes(b"test profile")
    return path


def test_landscape_crop_scale_uses_width_for_vertical_crop():
    geometry = geometry_for(4032, 3024)

    assert geometry.scale == pytest.approx(1640 / 4032)
    assert geometry.effective_ppi == pytest.approx(4032 / (1640 / 300))


def test_landscape_crop_scale_uses_height_for_panorama():
    geometry = geometry_for(4000, 1000)

    assert geometry.scale == pytest.approx(960 / 1000)
    assert geometry.effective_ppi == pytest.approx(312.5)


def test_portrait_geometry_uses_proportional_safe_inset_and_print_fonts():
    geometry = geometry_for(3024, 4032)

    assert (geometry.canvas_width, geometry.canvas_height) == (1200, 1800)
    assert geometry.caption_top == 1440
    assert (geometry.photo_area_x, geometry.photo_area_y) == (40, 40)
    assert (geometry.photo_area_width, geometry.photo_area_height) == (1120, 1360)
    assert (geometry.primary_font_size, geometry.secondary_font_size) == (42, 30)
    assert (geometry.primary_min_font_size, geometry.secondary_min_font_size) == (27, 22)
    assert geometry.caption_top < geometry.primary_y < geometry.secondary_y < 1800


def test_square_geometry_is_landscape_and_never_crops():
    geometry = geometry_for(3000, 3000)

    assert (geometry.canvas_width, geometry.canvas_height) == (1800, 1200)
    assert geometry.source_crop is None
    assert geometry.photo_width == geometry.photo_height == 840


def test_landscape_geometry_uses_narrow_margin_center_crop_and_smaller_type():
    geometry = geometry_for(3264, 2448)

    assert (geometry.canvas_width, geometry.canvas_height) == (1800, 1200)
    assert (geometry.caption_top, geometry.photo_x, geometry.photo_y) == (960, 80, 0)
    assert (geometry.photo_width, geometry.photo_height) == (1640, 960)
    assert geometry.source_crop == (1640, 960)
    assert (geometry.primary_font_size, geometry.secondary_font_size) == (28, 20)
    assert (geometry.primary_min_font_size, geometry.secondary_min_font_size) == (18, 15)


def test_square_and_portrait_remain_uncropped_with_reduced_type():
    square = geometry_for(3000, 3000)
    portrait = geometry_for(3024, 4032)

    assert square.source_crop is None
    assert portrait.source_crop is None
    assert (square.primary_font_size, square.secondary_font_size) == (28, 20)
    assert (portrait.primary_font_size, portrait.secondary_font_size) == (42, 30)


@pytest.mark.parametrize("width,height", [(0, 1), (1, 0), (-2, 4), (1.0, 2), (True, 2)])
def test_geometry_requires_positive_integer_source_dimensions(width, height):
    with pytest.raises(ValueError, match="positive integers"):
        geometry_for(width, height)


def test_command_has_safe_source_and_print_metadata_settings(tmp_path):
    geometry = geometry_for(4032, 3024)
    command = build_magick_command(
        Path("-source.jpg"),
        tmp_path / "-output.jpg",
        geometry,
        ("2018年05月01日 · 星期二 · 14:30", "上海 · 外滩 / iPhone 8"),
        "Helvetica", profile_path=_profile(tmp_path),
    )

    assert command[:4] == ["magick", "(", "-read", "-source.jpg[0]"]
    assert "--" not in command
    assert command.index("+swap") < command.index("-composite")
    assert command[command.index("-resize") + 1] == "1640x960^"
    assert "-crop" not in command
    assert command.index("-resize") < command.index("-composite") < command.index("-annotate")
    assert command[command.index("-units") : command.index("-units") + 11] == [
        "-units", "PixelsPerInch", "-density", "300", "-colorspace", "sRGB", "-strip",
        "-profile", str(tmp_path / "sRGB Profile.icc"), "-quality", "94",
    ]
    assert command[-3:] == ["-write", str(tmp_path / "-output.jpg"), "null:"]


def test_command_converts_source_profile_before_resize_and_tags_destination_before_strip(tmp_path):
    profile = _profile(tmp_path)
    command = build_magick_command(
        Path("source.jpg"), tmp_path / "output.jpg", geometry_for(4032, 3024), ("", ""), "Helvetica",
        profile_path=profile,
    )

    source_group_open = command.index("(")
    source_group_close = command.index(")", source_group_open)
    source_group = command[source_group_open : source_group_close + 1]
    assert source_group == [
        "(", "-read", "source.jpg[0]", "-auto-orient", "-profile", str(profile),
        "-resize", "1640x960^", "-gravity", "center", "-extent", "1640x960", ")",
    ]
    profile_positions = [index for index, value in enumerate(command) if value == "-profile"]
    assert command.index("-composite") < command.index("-strip") < profile_positions[1] < command.index("-quality")


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


@pytest.mark.parametrize(
    ("source_width", "source_height", "expected_photo", "expected_resize"),
    [
        (1001, 1000, (1640, 960), "1640x960^"),
        (1000, 1001, (1120, 1121), "1120x"),
    ],
)
def test_geometry_uses_half_up_rounding_and_one_axis_im_resize(tmp_path, source_width, source_height, expected_photo, expected_resize):
    geometry = geometry_for(source_width, source_height)
    command = build_magick_command(
        Path("source.tif"), tmp_path / "output.jpg", geometry, ("", ""), "Helvetica", profile_path=_profile(tmp_path)
    )

    assert (geometry.photo_width, geometry.photo_height) == expected_photo
    assert command[command.index("-resize") + 1] == expected_resize


def test_build_command_reads_only_the_primary_source_frame(tmp_path):
    command = build_magick_command(
        Path("-animation.gif"), tmp_path / "output.jpg", geometry_for(40, 20), ("", ""), "Helvetica", profile_path=_profile(tmp_path)
    )

    assert command[command.index("-read") + 1] == "-animation.gif[0]"


def test_probe_oriented_dimensions_reads_and_auto_orients_only_the_primary_frame():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="40 80", stderr="")

    assert probe_oriented_dimensions(Path("-photo.heic"), runner) == (40, 80)
    command, kwargs = calls[0]
    assert command == ["magick", "(", "-read", "-photo.heic[0]", "-auto-orient", ")", "-format", "%w %h", "info:"]
    assert kwargs == {"check": False, "capture_output": True, "text": True}


def test_probe_oriented_dimensions_raises_typed_error_for_bad_tool_output():
    def runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="not dimensions", stderr="")

    with pytest.raises(RenderError, match="invalid oriented dimensions"):
        probe_oriented_dimensions(Path("photo.jpg"), runner)


def test_command_skips_blank_annotations_and_escapes_indirect_text(tmp_path):
    geometry = geometry_for(4032, 3024)
    command = build_magick_command(
        Path("photo.jpg"),
        tmp_path / "output.jpg",
        geometry,
        ("", "@/tmp/secret -fill red"),
        "Helvetica", profile_path=_profile(tmp_path),
    )

    assert command.count("-annotate") == 1
    text = command[command.index("-annotate") + 2]
    assert text.startswith(r"\@")
    assert text != "@/tmp/secret -fill red"
    assert "-fill red" in text


def test_command_escapes_percent_property_expansions_backslashes_and_at_signs(tmp_path):
    geometry = geometry_for(4032, 3024)
    command = build_magick_command(
        Path("photo.jpg"),
        tmp_path / "output.jpg",
        geometry,
        (r"%[EXIF:Make] 50% @foo \\bar", ""),
        "Helvetica", profile_path=_profile(tmp_path),
    )

    assert command[command.index("-annotate") + 2] == r"%%[EXIF:Make] 50%% \@foo \\\\bar"


def test_command_requires_an_existing_srgb_profile(tmp_path):
    with pytest.raises(ValueError, match="sRGB ICC profile"):
        build_magick_command(
            Path("photo.jpg"), tmp_path / "output.jpg", geometry_for(1, 1), ("", ""), "Helvetica",
            profile_path=tmp_path / "missing.icc",
        )


def test_single_caption_line_is_centered_in_the_reserved_caption_zone(tmp_path):
    geometry = geometry_for(4032, 3024)
    command = build_magick_command(
        Path("photo.jpg"), tmp_path / "output.jpg", geometry, ("", "Shanghai / iPhone"), "Helvetica", profile_path=_profile(tmp_path)
    )

    assert command[command.index("-annotate") + 1] == "+0+1080"


def test_fit_captions_shortens_location_detail_before_reducing_font_size():
    geometry = geometry_for(4032, 3024)
    widths = {"上海市 · 黄浦区 · 外滩十八号 / iPhone 8": 900, "上海市 / iPhone 8": 300}
    seen_sizes = []

    def measure(text, _font, size):
        seen_sizes.append(size)
        return widths.get(text, 100)

    fitted = fit_captions(
        ("2024年01月01日 · 星期一 · 09:05", "上海市 · 黄浦区 · 外滩十八号 / iPhone 8"),
        geometry,
        "Helvetica",
        measure,
        max_width=400,
    )

    assert fitted.secondary == "上海市 / iPhone 8"
    assert fitted.secondary_font_size == geometry.secondary_font_size
    assert seen_sizes == [geometry.primary_font_size, geometry.secondary_font_size, geometry.secondary_font_size]
    assert fitted.warning == ""


def test_fit_captions_drops_location_detail_without_device_before_reducing_font_size():
    geometry = geometry_for(4032, 3024)

    def measure(text, _font, _size):
        return {"上海市 · 黄浦区 · 外滩十八号": 900, "上海市": 200}.get(text, 100)

    fitted = fit_captions(("", "上海市 · 黄浦区 · 外滩十八号"), geometry, "Helvetica", measure, max_width=400)

    assert fitted.secondary == "上海市"
    assert fitted.secondary_font_size == geometry.secondary_font_size


def test_fit_captions_reduces_to_minimum_then_ellipsizes_with_warning():
    geometry = geometry_for(4032, 3024)

    def measure(text, _font, size):
        return len(text) * size

    fitted = fit_captions(
        ("very long date and time line", "Location / Very Long Device Name"),
        geometry,
        "Helvetica",
        measure,
        max_width=100,
    )

    assert fitted.primary_font_size == geometry.primary_min_font_size
    assert fitted.secondary_font_size == geometry.secondary_min_font_size
    assert fitted.primary.endswith("…")
    assert fitted.secondary.endswith("…")
    assert "truncated" in fitted.warning


def test_measure_text_uses_safe_inline_label_and_injectable_runner():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="123", stderr="")

    assert measure_text("@/tmp/secret", "Helvetica", 24, runner) == 123
    command, kwargs = calls[0]
    assert command[:6] == ["magick", "-font", "Helvetica", "-pointsize", "24", r"label:\@/tmp/secret"]
    assert command[-3:] == ["-format", "%w", "info:"]
    assert kwargs == {"check": False, "capture_output": True, "text": True}


def test_run_render_raises_typed_error_for_runner_failure_and_os_error():
    command = ["magick", "input.jpg", "output.jpg"]

    def failure_runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stderr="bad image", stdout="")

    with pytest.raises(RenderError, match="bad image"):
        run_render(command, failure_runner)

    def missing_runner(*_args, **_kwargs):
        raise OSError("not found")

    with pytest.raises(RenderError, match="not found"):
        run_render(command, missing_runner)


def test_run_render_forwards_cwd_for_relative_image_paths(tmp_path):
    observed = {}

    def runner(_command, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    run_render(["magick", "-read", "-input.jpg"], runner, cwd=tmp_path)

    assert observed["cwd"] == tmp_path
