from __future__ import annotations

import json
import math
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from photo_caption_print.models import PhotoMetadata

DATE_KEYS = (
    "EXIF:DateTimeOriginal",
    "QuickTime:CreationDate",
    "XMP:DateCreated",
    "IPTC:DateCreated",
    "File:FileModifyDate",
)
MODEL_KEYS = ("EXIF:Model", "QuickTime:Model", "XMP:Model")
DEVICE_NAMES = {"iPhone7,2": "iPhone 6"}
LAT_KEYS = ("EXIF:GPSLatitude", "QuickTime:GPSLatitude")
LON_KEYS = ("EXIF:GPSLongitude", "QuickTime:GPSLongitude")


class MetadataError(RuntimeError):
    """Raised when ExifTool cannot provide usable metadata."""


def _first_value(row: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    return next((row[key] for key in keys if row.get(key) is not None), None)


def _friendly_device_name(value: Any) -> Any:
    return DEVICE_NAMES.get(value, value)


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if len(normalized) >= 10 and normalized[4] == normalized[7] == ":":
        normalized = f"{normalized[:4]}-{normalized[5:7]}-{normalized[8:]}"
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _first_parseable_date(row: Mapping[str, Any]) -> tuple[datetime | None, str | None]:
    for key in DATE_KEYS:
        parsed = _parse_date(row.get(key))
        if parsed is not None:
            return parsed, key
    return None, None


def _gps_pair(row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    for latitude_key, longitude_key in zip(LAT_KEYS, LON_KEYS):
        try:
            latitude = float(row[latitude_key])
            longitude = float(row[longitude_key])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(latitude) and math.isfinite(longitude):
            return latitude, longitude
    return None, None


def metadata_from_exiftool(row: Mapping[str, Any]) -> PhotoMetadata:
    captured_at, _ = _first_parseable_date(row)
    latitude, longitude = _gps_pair(row)
    return PhotoMetadata(
        source=Path(row["SourceFile"]),
        captured_at=captured_at,
        latitude=latitude,
        longitude=longitude,
        device=_friendly_device_name(_first_value(row, MODEL_KEYS)),
    )


def metadata_warning_from_exiftool(row: Mapping[str, Any]) -> str | None:
    _, date_key = _first_parseable_date(row)
    if date_key == "File:FileModifyDate":
        return "Capture date unavailable; using file modification date."
    return None


def run_exiftool(
    paths: Sequence[str | Path], runner: Callable[..., Any] = subprocess.run
) -> list[dict[str, Any]]:
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
    try:
        result = runner(command, capture_output=True, text=True)
    except OSError as error:
        raise MetadataError(f"Unable to run ExifTool: {error}") from error
    if result.returncode != 0:
        raise MetadataError(f"ExifTool failed: {result.stderr}")

    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise MetadataError("ExifTool returned invalid JSON") from error

    if not isinstance(rows, list) or not all(
        isinstance(row, dict)
        and isinstance(row.get("SourceFile"), str)
        and row["SourceFile"].strip()
        for row in rows
    ):
        raise MetadataError("ExifTool returned an unexpected JSON payload")
    return rows
