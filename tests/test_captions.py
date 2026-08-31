from datetime import datetime
from pathlib import Path

import pytest

from photo_caption_print.captions import (
    WEEKDAYS,
    MetadataOverride,
    apply_override,
    format_caption,
    lookup_override,
    load_overrides,
)
from photo_caption_print.models import PhotoMetadata


def make_metadata(**values):
    return PhotoMetadata(source=Path("photo.jpg"), **values)


def test_weekdays_are_chinese_monday_through_sunday():
    assert WEEKDAYS == (
        "星期一",
        "星期二",
        "星期三",
        "星期四",
        "星期五",
        "星期六",
        "星期日",
    )


def test_format_caption_formats_complete_metadata_exactly():
    caption = format_caption(
        make_metadata(
            captured_at=datetime(2018, 5, 1, 14, 30),
            location="上海 · 外滩",
            device="iPhone 8",
        )
    )

    assert caption == ("2018年05月01日 · 星期二 · 14:30", "上海 · 外滩 / iPhone 8")


def test_monday_date_formats_with_monday():
    metadata = make_metadata(captured_at=datetime(2030, 1, 7, 8, 9, 10))

    assert format_caption(metadata)[0] == "2030年01月07日 · 星期一 · 08:09"


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (make_metadata(), ("", "")),
        (make_metadata(captured_at=datetime(2024, 1, 1, 9, 5)), ("2024年01月01日 · 星期一 · 09:05", "")),
        (make_metadata(location="上海"), ("", "上海")),
        (make_metadata(device="iPhone"), ("", "iPhone")),
        (make_metadata(captured_at=datetime(2024, 1, 1, 9, 5), location="上海"), ("2024年01月01日 · 星期一 · 09:05", "上海")),
        (make_metadata(captured_at=datetime(2024, 1, 1, 9, 5), device="iPhone"), ("2024年01月01日 · 星期一 · 09:05", "iPhone")),
        (make_metadata(location="上海", device="iPhone"), ("", "上海 / iPhone")),
        (make_metadata(location="  ", device="  "), ("", "")),
    ],
)
def test_format_caption_omits_missing_fields_without_separators(metadata, expected):
    assert format_caption(metadata) == expected


def test_format_caption_sanitizes_controls_only_in_the_rendered_secondary_line():
    metadata = make_metadata(
        location="上海, 外滩\n夜景\t\x00\u200b",
        device="iPhone\t8\x00\u2060",
    )

    caption = format_caption(metadata)

    assert caption == ("", "上海, 外滩 夜景 / iPhone 8")
    assert metadata.location == "上海, 外滩\n夜景\t\x00\u200b"
    assert metadata.device == "iPhone\t8\x00\u2060"


def test_load_overrides_reads_trimmed_valid_values_and_apply_override(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text(
        "filename,captured_at,location,device\n"
        " processed-photo.jpg , 2018-05-01 14:30 , 上海 · 外滩 , iPhone 8 \n",
        encoding="utf-8",
    )

    overrides, warnings = load_overrides(path)

    assert warnings == []
    assert overrides == {
        "processed-photo.jpg": MetadataOverride(
            captured_at=datetime(2018, 5, 1, 14, 30),
            location="上海 · 外滩",
            device="iPhone 8",
        )
    }
    applied = apply_override(
        make_metadata(captured_at=datetime(2020, 1, 1), location="旧地点", device="旧设备"),
        overrides["processed-photo.jpg"],
    )
    assert applied.captured_at == datetime(2018, 5, 1, 14, 30)
    assert applied.location == "上海 · 外滩"
    assert applied.device == "iPhone 8"


def test_blank_override_cells_preserve_extracted_metadata(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text(
        "filename,captured_at,location,device\nphoto.jpg,  , , \n", encoding="utf-8"
    )

    overrides, warnings = load_overrides(path)
    applied = apply_override(
        make_metadata(captured_at=datetime(2020, 1, 1, 10, 30), location="上海", device="Camera"),
        overrides["photo.jpg"],
    )

    assert warnings == []
    assert applied.captured_at == datetime(2020, 1, 1, 10, 30)
    assert applied.location == "上海"
    assert applied.device == "Camera"


def test_apply_override_ignores_whitespace_only_direct_values():
    applied = apply_override(
        make_metadata(location="上海", device="Camera"),
        MetadataOverride(location="  ", device="\t"),
    )

    assert applied.location == "上海"
    assert applied.device == "Camera"


@pytest.mark.parametrize("date_value", ["2018-05-01T14:30:00+08:00", "2018-05-01 14:30"])
def test_load_overrides_accepts_iso8601_and_minute_precision_dates(tmp_path, date_value):
    path = tmp_path / "overrides.csv"
    path.write_text(
        f"filename,captured_at,location,device\nphoto.jpg,{date_value},,\n", encoding="utf-8"
    )

    overrides, warnings = load_overrides(path)

    assert warnings == []
    assert overrides["photo.jpg"].captured_at == datetime.fromisoformat(date_value)


def test_load_overrides_warns_and_skips_invalid_date_value(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text(
        "filename,captured_at,location,device\nphoto.jpg,not-a-date,上海,\n", encoding="utf-8"
    )

    overrides, warnings = load_overrides(path)

    assert overrides["photo.jpg"] == MetadataOverride(location="上海")
    assert warnings == ["Row 2: invalid captured_at 'not-a-date'."]


def test_load_overrides_warns_for_malformed_header(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text("filename,location\nphoto.jpg,上海\n", encoding="utf-8")

    overrides, warnings = load_overrides(path)

    assert overrides == {}
    assert warnings == [
        "Override CSV header must be exactly: filename,captured_at,location,device."
    ]


def test_load_overrides_accepts_utf8_bom_header(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text(
        "\ufefffilename,captured_at,location,device\nphoto.jpg,,上海,Camera\n",
        encoding="utf-8",
    )

    overrides, warnings = load_overrides(path)

    assert warnings == []
    assert overrides == {"photo.jpg": MetadataOverride(location="上海", device="Camera")}


def test_load_overrides_warns_and_skips_unquoted_surplus_columns(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text(
        "filename,captured_at,location,device\nphoto.jpg,,上海,Camera,extra\n",
        encoding="utf-8",
    )

    overrides, warnings = load_overrides(path)

    assert overrides == {}
    assert warnings == ["Row 2: too many columns; row skipped."]


def test_load_overrides_accepts_quoted_commas_and_embedded_newlines(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text(
        "filename,captured_at,location,device\n"
        'photo.jpg,,"上海, 外滩\n夜景","iPhone, 8"\n',
        encoding="utf-8",
    )

    overrides, warnings = load_overrides(path)

    assert warnings == []
    assert overrides == {
        "photo.jpg": MetadataOverride(location="上海, 外滩\n夜景", device="iPhone, 8")
    }


def test_load_overrides_warns_and_skips_blank_filename(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text(
        "filename,captured_at,location,device\n  ,2018-05-01 14:30,上海,iPhone\n",
        encoding="utf-8",
    )

    overrides, warnings = load_overrides(path)

    assert overrides == {}
    assert warnings == ["Row 2: filename is required."]


def test_load_overrides_uses_last_duplicate_and_warns(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text(
        "filename,captured_at,location,device\n"
        "photo.jpg,,上海,\n"
        "photo.jpg,,北京,Camera\n",
        encoding="utf-8",
    )

    overrides, warnings = load_overrides(path)

    assert overrides["photo.jpg"] == MetadataOverride(location="北京", device="Camera")
    assert warnings == ["Row 3: duplicate filename 'photo.jpg'; last row wins."]


@pytest.mark.parametrize("filename", ["folder/photo.jpg", "folder\\photo.jpg"])
def test_load_overrides_rejects_filenames_with_path_components(tmp_path, filename):
    path = tmp_path / "overrides.csv"
    path.write_text(
        f"filename,captured_at,location,device\n{filename},,上海,Camera\n",
        encoding="utf-8",
    )

    overrides, warnings = load_overrides(path)

    assert overrides == {}
    assert warnings == ["Row 2: filename must not include path components."]


def test_lookup_override_uses_nfc_casefolded_source_basename(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text(
        "filename,captured_at,location,device\nCafé.JPG,,上海,Camera\n",
        encoding="utf-8",
    )

    overrides, warnings = load_overrides(path)
    matched = lookup_override(
        PhotoMetadata(source=Path("nested/CAFÉ.jpg")), overrides
    )

    assert warnings == []
    assert set(overrides) == {"café.jpg"}
    assert matched == MetadataOverride(location="上海", device="Camera")
