from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Mapping
from unicodedata import category, normalize

from photo_caption_print.models import PhotoMetadata

WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_FULLWIDTH_DATE_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")
_OVERRIDE_COLUMNS = ("filename", "captured_at", "location", "device")


@dataclass(frozen=True)
class MetadataOverride:
    """Manual metadata values for one source filename."""

    captured_at: datetime | None = None
    location: str | None = None
    device: str | None = None


def _nonblank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _caption_nonblank(value: str | None) -> str | None:
    """Return a single-line caption-safe value without changing raw metadata."""
    if value is None:
        return None
    sanitized = "".join(
        " " if character.isspace() or category(character).startswith("C") else character
        for character in value
    )
    collapsed = " ".join(sanitized.split())
    return collapsed or None


def _normalized_filename(filename: str) -> str:
    return normalize("NFC", filename).casefold()


def _has_path_components(filename: str) -> bool:
    return Path(filename).name != filename or PureWindowsPath(filename).name != filename


def format_caption(metadata: PhotoMetadata) -> tuple[str, str]:
    """Return the two documentary caption lines for a photo."""
    if metadata.captured_at is None:
        date_line = ""
    else:
        date = metadata.captured_at.strftime("%Y年%m月%d日").translate(_FULLWIDTH_DATE_DIGITS)
        date_line = (
            f"{date} · "
            f"{WEEKDAYS[metadata.captured_at.weekday()]} · {metadata.captured_at:%H:%M}"
        )

    details = [
        value
        for value in (_caption_nonblank(metadata.location), _caption_nonblank(metadata.device))
        if value
    ]
    return date_line, " / ".join(details)


def _parse_override_date(value: str) -> datetime | None:
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def load_overrides(path: str | Path) -> tuple[dict[str, MetadataOverride], list[str]]:
    """Load filename-keyed metadata overrides and row-level validation warnings."""
    overrides: dict[str, MetadataOverride] = {}
    warnings: list[str] = []

    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(_OVERRIDE_COLUMNS):
            return {}, [
                "Override CSV header must be exactly: filename,captured_at,location,device."
            ]

        for row in reader:
            row_number = reader.line_num
            if None in row:
                warnings.append(f"Row {row_number}: too many columns; row skipped.")
                continue

            filename = _nonblank(row["filename"])
            if filename is None:
                warnings.append(f"Row {row_number}: filename is required.")
                continue
            if _has_path_components(filename):
                warnings.append(
                    f"Row {row_number}: filename must not include path components."
                )
                continue
            filename = _normalized_filename(filename)

            captured_value = _nonblank(row["captured_at"])
            captured_at = None
            if captured_value is not None:
                captured_at = _parse_override_date(captured_value)
                if captured_at is None:
                    warnings.append(
                        f"Row {row_number}: invalid captured_at '{captured_value}'."
                    )

            if filename in overrides:
                warnings.append(
                    f"Row {row_number}: duplicate filename '{filename}'; last row wins."
                )
            overrides[filename] = MetadataOverride(
                captured_at=captured_at,
                location=_nonblank(row["location"]),
                device=_nonblank(row["device"]),
            )

    return overrides, warnings


def apply_override(metadata: PhotoMetadata, override: MetadataOverride) -> PhotoMetadata:
    """Apply nonblank manual values without erasing extracted metadata."""
    return replace(
        metadata,
        captured_at=(
            override.captured_at
            if override.captured_at is not None
            else metadata.captured_at
        ),
        location=_nonblank(override.location) or metadata.location,
        device=_nonblank(override.device) or metadata.device,
    )


def lookup_override(
    metadata: PhotoMetadata, overrides: Mapping[str, MetadataOverride]
) -> MetadataOverride | None:
    """Return the override for a photo's normalized source basename, if present."""
    return overrides.get(_normalized_filename(metadata.source.name))
