import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from photo_caption_print.metadata import (
    MetadataError,
    metadata_from_exiftool,
    metadata_warning_from_exiftool,
    run_exiftool,
)


def test_metadata_from_exiftool_reads_complete_exif_values():
    metadata = metadata_from_exiftool(
        {
            "SourceFile": "IMG_0001.HEIC",
            "EXIF:DateTimeOriginal": "2021:10:16 16:42:03",
            "EXIF:GPSLatitude": 30.2431,
            "EXIF:GPSLongitude": 120.1502,
            "EXIF:Model": "iPhone 13 Pro",
        }
    )

    assert metadata.source == Path("IMG_0001.HEIC")
    assert metadata.captured_at == datetime(2021, 10, 16, 16, 42, 3)
    assert metadata.latitude == 30.2431
    assert metadata.longitude == 120.1502
    assert metadata.device == "iPhone 13 Pro"


@pytest.mark.parametrize(
    ("raw_model", "expected"),
    [
        ("iPhone7,2", "iPhone 6"),
        ("iPhone 13 Pro", "iPhone 13 Pro"),
        ("iPhone99,9", "iPhone99,9"),
        ("Test Camera", "Test Camera"),
    ],
)
def test_metadata_normalizes_only_confirmed_device_identifiers(raw_model, expected):
    metadata = metadata_from_exiftool(
        {"SourceFile": "photo.jpg", "EXIF:Model": raw_model}
    )

    assert metadata.device == expected


def test_synthetic_exif_fields_produce_complete_metadata():
    metadata = metadata_from_exiftool(
        {
            "SourceFile": "synthetic-photo.jpeg",
            "EXIF:DateTimeOriginal": "2030:01:07 08:09:10",
            "EXIF:GPSLatitude": 30.2431,
            "EXIF:GPSLongitude": 120.1502,
            "EXIF:Model": "Test Camera",
        }
    )

    assert metadata.captured_at == datetime(2030, 1, 7, 8, 9, 10)
    assert (metadata.latitude, metadata.longitude) == (
        30.2431,
        120.1502,
    )
    assert metadata.device == "Test Camera"


def test_metadata_from_exiftool_returns_none_for_missing_optional_tags():
    metadata = metadata_from_exiftool({"SourceFile": "edited.jpg"})

    assert metadata.source == Path("edited.jpg")
    assert metadata.captured_at is None
    assert metadata.latitude is None
    assert metadata.longitude is None
    assert metadata.device is None


def test_metadata_from_exiftool_parses_iso_date_with_offset():
    metadata = metadata_from_exiftool(
        {
            "SourceFile": "clip.mov",
            "QuickTime:CreationDate": "2021-10-16T16:42:03+08:00",
        }
    )

    assert metadata.captured_at == datetime.fromisoformat("2021-10-16T16:42:03+08:00")


def test_metadata_from_exiftool_uses_first_available_value_in_each_precedence_list():
    metadata = metadata_from_exiftool(
        {
            "SourceFile": "photo.jpg",
            "EXIF:DateTimeOriginal": "2021:10:16 16:42:03",
            "QuickTime:CreationDate": "2022-01-01T00:00:00+00:00",
            "XMP:DateCreated": "2023-01-01T00:00:00+00:00",
            "IPTC:DateCreated": "2024-01-01T00:00:00+00:00",
            "File:FileModifyDate": "2025-01-01T00:00:00+00:00",
            "EXIF:Model": "EXIF camera",
            "QuickTime:Model": "QuickTime camera",
            "XMP:Model": "XMP camera",
            "EXIF:GPSLatitude": 30.0,
            "QuickTime:GPSLatitude": 31.0,
            "EXIF:GPSLongitude": 120.0,
            "QuickTime:GPSLongitude": 121.0,
        }
    )

    assert metadata.captured_at == datetime(2021, 10, 16, 16, 42, 3)
    assert metadata.device == "EXIF camera"
    assert metadata.latitude == 30.0
    assert metadata.longitude == 120.0


def test_metadata_from_exiftool_uses_complete_quicktime_gps_pair_when_exif_pair_is_partial():
    metadata = metadata_from_exiftool(
        {
            "SourceFile": "clip.mov",
            "EXIF:GPSLatitude": 30.0,
            "QuickTime:GPSLatitude": 31.0,
            "QuickTime:GPSLongitude": 121.0,
        }
    )

    assert metadata.latitude == 31.0
    assert metadata.longitude == 121.0


def test_metadata_from_exiftool_returns_no_gps_when_neither_source_has_a_complete_pair():
    metadata = metadata_from_exiftool(
        {
            "SourceFile": "clip.mov",
            "EXIF:GPSLatitude": 30.0,
            "QuickTime:GPSLongitude": 121.0,
        }
    )

    assert metadata.latitude is None
    assert metadata.longitude is None


def test_metadata_from_exiftool_returns_none_for_malformed_date():
    metadata = metadata_from_exiftool(
        {"SourceFile": "photo.jpg", "EXIF:DateTimeOriginal": "not a date"}
    )

    assert metadata.captured_at is None


def test_metadata_from_exiftool_uses_lower_priority_parseable_date_and_warns_for_file_fallback():
    row = {
        "SourceFile": "photo.jpg",
        "EXIF:DateTimeOriginal": "not a date",
        "File:FileModifyDate": "2024:02:03 04:05:06",
    }

    assert metadata_from_exiftool(row).captured_at == datetime(2024, 2, 3, 4, 5, 6)
    assert metadata_warning_from_exiftool(row) == "Capture date unavailable; using file modification date."


def test_file_modification_date_fallback_exposes_warning_signal():
    row = {"SourceFile": "photo.jpg", "File:FileModifyDate": "2024:02:03 04:05:06"}

    assert metadata_from_exiftool(row).captured_at == datetime(2024, 2, 3, 4, 5, 6)
    assert metadata_warning_from_exiftool(row) == "Capture date unavailable; using file modification date."


def test_file_modification_date_does_not_warn_when_capture_date_wins_precedence():
    row = {
        "SourceFile": "photo.jpg",
        "EXIF:DateTimeOriginal": "2024:02:03 04:05:06",
        "File:FileModifyDate": "2024:02:03 04:05:06",
    }

    assert metadata_warning_from_exiftool(row) is None


def test_run_exiftool_runs_one_non_mutating_json_batch_command():
    calls = []

    def runner(command, *, capture_output, text):
        calls.append((command, capture_output, text))
        return SimpleNamespace(returncode=0, stdout='[{"SourceFile": "one.jpg"}]', stderr="")

    rows = run_exiftool([Path("one.jpg"), Path("-all=")], runner=runner)

    assert rows == [{"SourceFile": "one.jpg"}]
    assert calls == [
        (
            [
                "exiftool",
                "-json",
                "-G",
                "-n",
                "-api",
                "QuickTimeUTC=1",
                "--",
                "one.jpg",
                "-all=",
            ],
            True,
            True,
        )
    ]


def test_run_exiftool_raises_typed_error_with_stderr_on_failure():
    def runner(command, *, capture_output, text):
        return SimpleNamespace(returncode=1, stdout="", stderr="unreadable image")

    with pytest.raises(MetadataError, match="unreadable image"):
        run_exiftool(["broken.jpg"], runner=runner)


def test_run_exiftool_wraps_runner_oserror_in_metadata_error():
    def runner(command, *, capture_output, text):
        raise FileNotFoundError("exiftool not installed")

    with pytest.raises(MetadataError, match="exiftool not installed"):
        run_exiftool(["photo.jpg"], runner=runner)


def test_run_exiftool_rejects_invalid_json():
    def runner(command, *, capture_output, text):
        return SimpleNamespace(returncode=0, stdout="not json", stderr="")

    with pytest.raises(MetadataError, match="invalid JSON"):
        run_exiftool(["photo.jpg"], runner=runner)


@pytest.mark.parametrize("row", [{}, {"SourceFile": ""}, {"SourceFile": 42}])
def test_run_exiftool_rejects_rows_without_a_nonempty_string_source_file(row):
    def runner(command, *, capture_output, text):
        return SimpleNamespace(returncode=0, stdout=json.dumps([row]), stderr="")

    with pytest.raises(MetadataError, match="unexpected JSON payload"):
        run_exiftool(["photo.jpg"], runner=runner)
