"""Opt-in regression coverage for the user-provided Desktop photo fixture.

The fixture remains outside the repository and is never copied, edited, or
otherwise modified by this metadata-only test.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path

import pytest

from photo_caption_print.captions import WEEKDAYS, format_caption
from photo_caption_print.metadata import metadata_from_exiftool, run_exiftool


if not os.environ.get("PHOTO_CAPTION_PRINT_DESKTOP_FIXTURE"):
    pytest.skip(
        "Desktop fixture integration test is opt-in; set PHOTO_CAPTION_PRINT_DESKTOP_FIXTURE to the fixture path.",
        allow_module_level=True,
    )
FIXTURE = Path(os.environ["PHOTO_CAPTION_PRINT_DESKTOP_FIXTURE"])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_desktop_fixture_metadata_and_caption_are_read_only():
    assert FIXTURE.is_file(), f"Desktop fixture is unavailable: {FIXTURE}"
    before = _sha256(FIXTURE)

    try:
        rows = run_exiftool([FIXTURE])
        assert len(rows) == 1
        metadata = metadata_from_exiftool(rows[0])
        primary, secondary = format_caption(metadata)

        assert primary
        assert any(weekday in primary for weekday in WEEKDAYS)
        assert metadata.captured_at is not None
        assert metadata.latitude is not None and math.isfinite(metadata.latitude) and -90 <= metadata.latitude <= 90
        assert metadata.longitude is not None and math.isfinite(metadata.longitude) and -180 <= metadata.longitude <= 180
        assert isinstance(metadata.device, str) and metadata.device.strip()
        assert metadata.device in secondary
    finally:
        assert _sha256(FIXTURE) == before
