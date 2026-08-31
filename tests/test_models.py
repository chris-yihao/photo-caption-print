from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path

import pytest

from photo_caption_print import __version__
from photo_caption_print.models import PhotoMetadata, ProcessResult


def test_photo_metadata_uses_none_defaults():
    metadata = PhotoMetadata(source=Path("edited.jpg"))

    assert metadata.source == Path("edited.jpg")
    assert metadata.captured_at is None
    assert metadata.latitude is None
    assert metadata.longitude is None
    assert metadata.device is None
    assert metadata.location is None


def test_photo_metadata_stores_metadata_values_and_is_frozen():
    captured_at = datetime(2026, 8, 26, 12, 30)
    metadata = PhotoMetadata(
        source=Path("edited.jpg"),
        captured_at=captured_at,
        latitude=31.23,
        longitude=121.47,
        device="Camera",
        location="Shanghai",
    )

    assert metadata.captured_at == captured_at
    assert metadata.latitude == 31.23
    assert metadata.longitude == 121.47
    assert metadata.device == "Camera"
    assert metadata.location == "Shanghai"

    with pytest.raises(FrozenInstanceError):
        metadata.device = "Other"


def test_process_result_defaults_optional_output_and_messages():
    result = ProcessResult(source=Path("edited.jpg"), output=None, status="skipped")

    assert result.source == Path("edited.jpg")
    assert result.output is None
    assert result.status == "skipped"
    assert result.warning == ""
    assert result.error == ""


def test_process_result_stores_output_and_messages_and_is_frozen():
    result = ProcessResult(
        source=Path("edited.jpg"),
        output=Path("printed.jpg"),
        status="success",
        warning="minor issue",
        error="",
    )

    assert result.output == Path("printed.jpg")
    assert result.warning == "minor issue"
    assert result.error == ""

    with pytest.raises(FrozenInstanceError):
        result.status = "failed"


def test_package_exports_version():
    assert __version__ == "0.1.0"
