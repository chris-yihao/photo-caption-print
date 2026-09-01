"""6 by 4 inch print geometry and ImageMagick rendering helpers.

Landscape photos use a narrow-margin center crop with 28/20 px caption type,
never falling below 18/15 px.  Portrait type is scaled to 42/30 px (minimum
27/22 px) to remain equally readable on the longer 6-inch edge.
``effective_ppi`` records the source resolution at the rendered print size; it
is intentionally informative, not a resize limit.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import subprocess
from typing import Callable, Sequence


MeasurementRunner = Callable[[str, str, int], int]
DEFAULT_SRGB_PROFILE = Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc")
_EXIF_ORIENTATIONS = {
    "1": "TopLeft", "2": "TopRight", "3": "BottomRight", "4": "BottomLeft",
    "5": "LeftTop", "6": "RightTop", "7": "RightBottom", "8": "LeftBottom",
}


class RenderError(RuntimeError):
    """ImageMagick could not create a rendered print file."""


@dataclass(frozen=True)
class PrintGeometry:
    """All pixel positions for one print, derived from its source dimensions."""

    source_width: int
    source_height: int
    canvas_width: int
    canvas_height: int
    photo_area_x: int
    photo_area_y: int
    photo_area_width: int
    photo_area_height: int
    photo_width: int
    photo_height: int
    photo_x: int
    photo_y: int
    caption_top: int
    primary_y: int
    secondary_y: int
    primary_font_size: int
    secondary_font_size: int
    primary_min_font_size: int
    secondary_min_font_size: int
    scale: float
    effective_ppi: float
    source_crop: tuple[int, int] | None = None


@dataclass(frozen=True)
class FittedCaptions:
    """Caption text suitable for a particular geometry, plus any render warning."""

    primary: str
    secondary: str
    primary_font_size: int
    secondary_font_size: int
    warning: str = ""


def geometry_for(source_width: int, source_height: int) -> PrintGeometry:
    """Return a layout for positive, display-oriented source dimensions."""
    if (
        isinstance(source_width, bool)
        or isinstance(source_height, bool)
        or not isinstance(source_width, int)
        or not isinstance(source_height, int)
        or source_width <= 0
        or source_height <= 0
    ):
        raise ValueError("source dimensions must be positive integers")

    square_like = abs(source_width - source_height) / max(source_width, source_height) <= 0.02
    portrait = not square_like and source_height > source_width
    landscape = not square_like and source_width > source_height
    canvas_width, canvas_height = (1200, 1800) if portrait else (1800, 1200)
    caption_height = 180 if portrait else 120
    caption_top = canvas_height - caption_height

    if landscape:
        area_x, area_y, area_width, area_height = 40, 0, 1720, 1080
        photo_width, photo_height = area_width, area_height
        photo_x, photo_y = area_x, area_y
        scale = max(area_width / source_width, area_height / source_height)
        source_crop = (area_width, area_height)
    else:
        inset = 20 if portrait else 30
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
    # Preserve the approved line separation while centering the pair.
    primary_offset = int(caption_height * 0.40) - 5
    secondary_offset = int(caption_height * 0.67) + 5
    baseline_gap = secondary_offset - primary_offset
    caption_center = caption_top + caption_height // 2
    primary_y = caption_center - baseline_gap // 2
    secondary_y = primary_y + baseline_gap

    return PrintGeometry(
        source_width=source_width,
        source_height=source_height,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        photo_area_x=area_x,
        photo_area_y=area_y,
        photo_area_width=area_width,
        photo_area_height=area_height,
        photo_width=photo_width,
        photo_height=photo_height,
        photo_x=photo_x,
        photo_y=photo_y,
        caption_top=caption_top,
        primary_y=primary_y,
        secondary_y=secondary_y,
        primary_font_size=primary_font_size,
        secondary_font_size=secondary_font_size,
        primary_min_font_size=primary_min_font_size,
        secondary_min_font_size=secondary_min_font_size,
        scale=scale,
        effective_ppi=300 / scale,
        source_crop=source_crop,
    )


def _round_half_up(value: float) -> int:
    """Match ImageMagick's pixel rounding for positive resize dimensions."""
    return math.floor(value + 0.5)


def probe_oriented_dimensions(
    source: Path | str,
    runner: Callable[..., object] = subprocess.run,
) -> tuple[int, int]:
    """Return the first frame's display dimensions after ImageMagick auto-orient.

    ``geometry_for`` deliberately accepts these display-oriented dimensions,
    rather than raw file dimensions, so its selected canvas matches rendering.
    """
    command = [
        "magick", "(", "-read", _primary_frame_source(source),
        *_exif_orientation_argument(source), "-auto-orient", ")",
        "-format", "%w %h", "info:",
    ]
    try:
        result = runner(command, check=False, capture_output=True, text=True)
    except OSError as error:
        raise RenderError(str(error)) from error
    if getattr(result, "returncode", 0) != 0:
        detail = getattr(result, "stderr", "") or getattr(result, "stdout", "") or "orientation probe failed"
        raise RenderError(str(detail).strip())
    fields = str(getattr(result, "stdout", "")).split()
    try:
        width, height = int(fields[0]), int(fields[1])
    except (IndexError, ValueError) as error:
        raise RenderError("ImageMagick returned invalid oriented dimensions") from error
    if width <= 0 or height <= 0:
        raise RenderError("ImageMagick returned invalid oriented dimensions")
    return width, height


def measure_text(
    text: str,
    font: str,
    size: int,
    runner: Callable[..., object] = subprocess.run,
) -> int:
    """Measure an inline ImageMagick label with an injectable process runner."""
    command = [
        "magick",
        "-font",
        str(font),
        "-pointsize",
        str(size),
        f"label:{_safe_annotation(text)}",
        "-format",
        "%w",
        "info:",
    ]
    try:
        result = runner(command, check=False, capture_output=True, text=True)
    except OSError as error:
        raise RenderError(str(error)) from error
    if getattr(result, "returncode", 0) != 0:
        detail = getattr(result, "stderr", "") or getattr(result, "stdout", "") or "text measurement failed"
        raise RenderError(str(detail).strip())
    try:
        return int(str(getattr(result, "stdout", "")).strip())
    except ValueError as error:
        raise RenderError("ImageMagick returned an invalid text width") from error


def fit_captions(
    lines: Sequence[str],
    geometry: PrintGeometry,
    font: str,
    measure: MeasurementRunner,
    *,
    max_width: int | None = None,
) -> FittedCaptions:
    """Fit captions while preserving meaningful date and device text where possible.

    The secondary line is normally ``location / device``.  Before changing its
    type size, location detail after the first `` · ``, comma, or slash is
    removed.  Both lines only receive an ellipsis after the documented minimum
    font size cannot fit.
    """
    if len(lines) != 2:
        raise ValueError("captions must contain exactly two lines")
    safe_width = max_width if max_width is not None else geometry.canvas_width - 2 * geometry.photo_area_x
    if safe_width <= 0:
        raise ValueError("max_width must be positive")
    primary, secondary = (line.strip() for line in lines)
    warning = ""

    primary, primary_size, primary_truncated = _fit_one(
        primary, font, geometry.primary_font_size, geometry.primary_min_font_size, safe_width, measure
    )
    secondary = _shorten_location_detail(secondary, font, geometry.secondary_font_size, safe_width, measure)
    secondary, secondary_size, secondary_truncated = _fit_one(
        secondary, font, geometry.secondary_font_size, geometry.secondary_min_font_size, safe_width, measure
    )
    if primary_truncated or secondary_truncated:
        warning = "Caption text was truncated for the print-safe width."
    return FittedCaptions(primary, secondary, primary_size, secondary_size, warning)


def _shorten_location_detail(text: str, font: str, size: int, max_width: int, measure: MeasurementRunner) -> str:
    if not text or measure(text, font, size) <= max_width:
        return text
    if " / " in text:
        location, device = text.split(" / ", 1)
    else:
        location, device = text, ""
    for delimiter in (" · ", ", ", " / "):
        if delimiter in location:
            shortened = location.split(delimiter, 1)[0].strip()
            candidate = f"{shortened} / {device}" if device else shortened
            if candidate:
                return candidate
    return text


def _fit_one(
    text: str,
    font: str,
    initial_size: int,
    minimum_size: int,
    max_width: int,
    measure: MeasurementRunner,
) -> tuple[str, int, bool]:
    if not text:
        return "", initial_size, False
    for size in range(initial_size, minimum_size - 1, -1):
        if measure(text, font, size) <= max_width:
            return text, size, False
    return _ellipsize(text, font, minimum_size, max_width, measure), minimum_size, True


def _ellipsize(text: str, font: str, size: int, max_width: int, measure: MeasurementRunner) -> str:
    ellipsis = "…"
    if measure(ellipsis, font, size) > max_width:
        return ""
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle].rstrip() + ellipsis
        if measure(candidate, font, size) <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + ellipsis


def build_magick_command(
    source: Path | str,
    output: Path | str,
    geometry: PrintGeometry,
    lines: Sequence[str] | FittedCaptions,
    font: str,
    *,
    profile_path: Path | str = DEFAULT_SRGB_PROFILE,
) -> list[str]:
    """Create a safe argv command that renders a print to ``output``.

    Explicit ``-read`` and ``-write`` operands keep leading-hyphen paths out of
    ImageMagick's option parser. Captions are command arguments, never shell
    text, and their ImageMagick meta characters are escaped literally.
    """
    if isinstance(lines, FittedCaptions):
        primary, secondary = lines.primary, lines.secondary
        primary_size, secondary_size = lines.primary_font_size, lines.secondary_font_size
    else:
        if len(lines) != 2:
            raise ValueError("captions must contain exactly two lines")
        primary, secondary = (str(line).strip() for line in lines)
        primary_size, secondary_size = geometry.primary_font_size, geometry.secondary_font_size
    profile = Path(profile_path)
    if not profile.is_file():
        raise ValueError(f"sRGB ICC profile does not exist: {profile}")
    command = [
        "magick",
        "(",
        "-read",
        _primary_frame_source(source),
        *_exif_orientation_argument(source),
        "-auto-orient",
        "-profile",
        str(profile),
        *_photo_transform(geometry),
        ")",
        "(",
        "-size",
        f"{geometry.canvas_width}x{geometry.canvas_height}",
        "xc:white",
        ")",
        "+swap",
        "-gravity",
        "northwest",
        "-geometry",
        f"+{geometry.photo_x}+{geometry.photo_y}",
        "-composite",
        "-font",
        str(font),
        "-gravity",
        "north",
    ]
    if _uses_visible_caption_layer(geometry, primary, secondary):
        command.extend(_caption_layer_arguments(
            geometry, primary, secondary, primary_size, secondary_size, font
        ))
    else:
        single_line_y = geometry.caption_top + (geometry.canvas_height - geometry.caption_top) // 2
        if primary:
            primary_y = single_line_y if not secondary else geometry.primary_y
            command.extend(["-pointsize", str(primary_size), "-fill", "#171717", "-annotate", f"+0+{primary_y}", _safe_annotation(primary)])
        if secondary:
            secondary_y = single_line_y if not primary else geometry.secondary_y
            command.extend(["-pointsize", str(secondary_size), "-fill", "#666666", "-annotate", f"+0+{secondary_y}", _safe_annotation(secondary)])
    command.extend([
        "-units", "PixelsPerInch", "-density", "300", "-colorspace", "sRGB", "-strip", "-profile", str(profile),
        "-quality", "94", "-write", str(output), "null:",
    ])
    return command


def _uses_square_layout(geometry: PrintGeometry) -> bool:
    return (
        geometry.canvas_width == 1800
        and geometry.canvas_height == 1200
        and geometry.source_crop is None
    )


def _uses_visible_caption_layer(
    geometry: PrintGeometry, primary: str, secondary: str
) -> bool:
    portrait = geometry.canvas_width == 1200 and geometry.canvas_height == 1800
    square_two_line = _uses_square_layout(geometry) and bool(primary and secondary)
    return bool(primary or secondary) and (portrait or square_two_line)


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


def _safe_annotation(text: str) -> str:
    """Escape ImageMagick's indirect, property, and backslash expansions."""
    return text.replace("\\", "\\\\").replace("%", "%%").replace("@", r"\@")


def _primary_frame_source(source: Path | str) -> str:
    """Select frame zero while preserving literal brackets in a source filename."""
    filename = str(source).replace("[", r"\[").replace("]", r"\]")
    return f"{filename}[0]"


def _exif_orientation_argument(source: Path | str) -> list[str]:
    """Provide ImageMagick an EXIF orientation when a codec omits it on read."""
    try:
        result = subprocess.run(
            ["exiftool", "-n", "-s3", "-EXIF:Orientation", "--", str(source)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    orientation = _EXIF_ORIENTATIONS.get(str(getattr(result, "stdout", "")).strip())
    return ["-orient", orientation] if getattr(result, "returncode", 1) == 0 and orientation else []


def _resize_spec(geometry: PrintGeometry) -> str:
    """Use one constraining axis so IM yields geometry's half-up dimensions."""
    width_scale = geometry.photo_area_width / geometry.source_width
    height_scale = geometry.photo_area_height / geometry.source_height
    if width_scale <= height_scale:
        return f"{geometry.photo_width}x"
    return f"x{geometry.photo_height}"


def _photo_transform(geometry: PrintGeometry) -> list[str]:
    """Return the source transform for this geometry's photo frame."""
    if geometry.source_crop is not None:
        width, height = geometry.source_crop
        return [
            "-resize", f"{width}x{height}^",
            "-gravity", "center",
            "-extent", f"{width}x{height}",
        ]
    return ["-resize", _resize_spec(geometry)]


def run_render(
    command: Sequence[str],
    runner: Callable[..., object] = subprocess.run,
    *,
    cwd: Path | str | None = None,
) -> object:
    """Run a prebuilt ImageMagick argv list, translating execution errors."""
    try:
        result = runner(list(command), check=False, capture_output=True, text=True, cwd=cwd)
    except OSError as error:
        raise RenderError(str(error)) from error
    returncode = getattr(result, "returncode", 0)
    if returncode != 0:
        detail = getattr(result, "stderr", "") or getattr(result, "stdout", "") or f"exit status {returncode}"
        raise RenderError(str(detail).strip())
    return result
