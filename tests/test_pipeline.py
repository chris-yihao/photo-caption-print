from __future__ import annotations

import csv
import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path

import pytest

from photo_caption_print.geocode import GeocodeResult
from photo_caption_print.layout import FittedCaptions
from photo_caption_print.models import PhotoMetadata
import photo_caption_print.pipeline as pipeline_module
from photo_caption_print.pipeline import BatchPipeline, ReportError


class FakeMetadataReader:
    def __init__(self, rows=(), error: Exception | None = None):
        self.rows, self.error, self.calls = list(rows), error, []

    def __call__(self, paths):
        self.calls.append(list(paths))
        if self.error:
            raise self.error
        return self.rows


class FakeGeocoder:
    def __init__(self, result=GeocodeResult("Hangzhou")):
        self.result, self.calls = result, []

    def reverse(self, latitude, longitude):
        self.calls.append((latitude, longitude))
        return self.result


class FakeRenderer:
    def __init__(self, failing=()):
        self.calls, self.failing = [], set(failing)

    def __call__(self, source, output, geometry, captions):
        self.calls.append((source, output, geometry, captions))
        if source.name in self.failing:
            output.write_bytes(b"partial")
            raise RuntimeError(f"cannot render {source.name}")
        output.write_text(source.name, encoding="utf-8")


def dimensions(source):
    return (4000, 3000) if source.name != "portrait.jpg" else (3000, 4000)


def rows_for(*names):
    return [{"SourceFile": name} for name in names]


def pipeline(reader=None, geocoder=None, renderer=None, **kwargs):
    dimension_probe = kwargs.pop("dimension_probe", dimensions)
    caption_fitter = kwargs.pop("caption_fitter", lambda lines, geometry: lines)
    return BatchPipeline(
        metadata_reader=reader or FakeMetadataReader(),
        geocoder=geocoder,
        dimension_probe=dimension_probe,
        renderer=renderer or FakeRenderer(),
        caption_fitter=caption_fitter,
        **kwargs,
    )


def make_photo(folder, name):
    path = folder / name
    path.write_bytes(b"source")
    return path


def read_report(path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_manifest(output, source, name, content):
    digest = hashlib.sha256(content).hexdigest()
    (output / ".photo-caption-print-manifest.json").write_text(
        json.dumps({"version": 1, "entries": {pipeline_module._source_identity(source): {"output": name, "sha256": digest}}}),
        encoding="utf-8",
    )


def test_discovery_is_non_recursive_sorted_case_insensitively_and_safe(tmp_path):
    source, output, report = tmp_path / "source", tmp_path / "output", tmp_path / "report.csv"
    source.mkdir()
    make_photo(source, "z.TiF")
    make_photo(source, "A.JpG")
    make_photo(source, ".hidden.jpg")
    make_photo(source, "note.txt")
    make_photo(source, "unsupported.TIFF")
    (source / "nested").mkdir()
    make_photo(source / "nested", "child.jpg")
    os.symlink(source / "A.JpG", source / "link.jpg")

    reader, renderer = FakeMetadataReader(rows_for("A.JpG", "z.TiF")), FakeRenderer()
    summary = pipeline(reader, renderer=renderer).process_folder(source, output, report)

    assert [result.source.name for result in summary.results] == ["A.JpG", "z.TiF"]
    assert [path.name for path in reader.calls[0]] == ["A.JpG", "z.TiF"]
    assert not (output / "unsupported-print.jpg").exists()
    assert (source / "note.txt").read_bytes() == b"source"


def test_metadata_is_read_once_and_mapped_by_resolved_path_or_unique_basename(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir()
    first, second = make_photo(source, "one.jpg"), make_photo(source, "two.heic")
    reader = FakeMetadataReader([
        {"SourceFile": str(first.resolve()), "EXIF:Model": "Camera"},
        {"SourceFile": "two.heic", "EXIF:DateTimeOriginal": "2024:01:02 03:04:05"},
    ])

    summary = pipeline(reader).process_folder(source, output, report)

    assert len(reader.calls) == 1
    assert [result.status for result in summary.results] == ["success", "success"]
    rows = read_report(report)
    assert rows[0]["device"] == "Camera"
    assert rows[1]["captured_at"].startswith("2024-01-02T03:04:05")


def test_missing_or_ambiguous_metadata_rows_become_warnings_not_batch_errors(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir()
    make_photo(source, "same.jpg")
    make_photo(source, "nested.JPG")
    reader = FakeMetadataReader([{"SourceFile": "unknown.jpg"}])

    summary = pipeline(reader).process_folder(source, output, report)

    assert all(result.status == "warning" for result in summary.results)
    assert all("Metadata unavailable" in result.warning for result in summary.results)


def test_metadata_batch_error_and_bad_override_are_reported_without_stopping(tmp_path):
    source, output, report, overrides = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv", tmp_path / "overrides.csv"
    source.mkdir()
    make_photo(source, "a.jpg")
    make_photo(source, "b.jpg")
    overrides.write_text("filename,wrong\na.jpg,x\n", encoding="utf-8")

    summary = pipeline(FakeMetadataReader(error=RuntimeError("ExifTool unavailable"))).process_folder(source, output, report, overrides)

    assert summary.warning_count == 2
    assert any("ExifTool unavailable" in warning for warning in summary.warnings)
    assert any("Override CSV header" in warning for warning in summary.warnings)
    assert all("ExifTool unavailable" in row["warning"] for row in read_report(report))
    assert all("Override CSV header" in row["warning"] for row in read_report(report))


def test_metadata_iteration_and_schema_errors_fall_back_for_every_photo(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg"); make_photo(source, "b.jpg")
    def broken_rows(paths):
        yield {"SourceFile": "a.jpg"}
        raise RuntimeError("iteration failure")

    summary = pipeline(broken_rows).process_folder(source, output, report)
    assert all("Metadata batch unavailable" in result.warning for result in summary.results)

    summary = pipeline(lambda paths: [{"SourceFile": 42}]).process_folder(source, output, report)
    assert all("Metadata batch unavailable" in result.warning for result in summary.results)


def test_override_and_geocode_rules_use_normalized_filename_and_never_geocode_existing_location(tmp_path):
    source, output, report, overrides = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv", tmp_path / "overrides.csv"
    source.mkdir()
    make_photo(source, "CAFÉ.jpg")
    make_photo(source, "gps.jpg")
    overrides.write_text(
        "filename,captured_at,location,device\nCafé.JPG,2020-01-02T03:04:05,Manual,Override Camera\n",
        encoding="utf-8",
    )
    reader = FakeMetadataReader([
        {"SourceFile": "CAFÉ.jpg", "EXIF:GPSLatitude": 1, "EXIF:GPSLongitude": 2},
        {"SourceFile": "gps.jpg", "EXIF:GPSLatitude": 3, "EXIF:GPSLongitude": 4},
    ])
    geocoder = FakeGeocoder()

    pipeline(reader, geocoder).process_folder(source, output, report, overrides)

    assert geocoder.calls == [(3.0, 4.0)]
    rows = read_report(report)
    assert rows[0]["location"] == "Manual"
    assert rows[0]["device"] == "Override Camera"
    assert rows[1]["location"] == "Hangzhou"


def test_gps_metadata_flows_through_geocoding_to_documentary_captions(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir()
    make_photo(source, "photo.jpeg")
    reader = FakeMetadataReader([
        {
            "SourceFile": "photo.jpeg",
            "EXIF:DateTimeOriginal": "2030:01:07 08:09:10",
            "EXIF:GPSLatitude": 35.12345,
            "EXIF:GPSLongitude": 139.54321,
            "EXIF:Model": "Test Camera",
        }
    ])
    geocoder = FakeGeocoder(GeocodeResult("重庆 · 合川区"))
    renderer = FakeRenderer()

    summary = pipeline(reader, geocoder, renderer).process_folder(source, output, report)

    assert geocoder.calls == [(35.12345, 139.54321)]
    assert renderer.calls[0][3] == (
        "2030年01月07日 · 星期一 · 08:09",
        "重庆 · 合川区 / Test Camera",
    )
    assert read_report(report)[0]["location"] == "重庆 · 合川区"
    assert summary.failed_count == 0


def test_geocode_failure_keeps_date_and_device_captions_and_leaves_report_location_empty(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir()
    make_photo(source, "photo.jpeg")
    reader = FakeMetadataReader([
        {
            "SourceFile": "photo.jpeg",
            "EXIF:DateTimeOriginal": "2030:01:07 08:09:10",
            "EXIF:GPSLatitude": 35.12345,
            "EXIF:GPSLongitude": 139.54321,
            "EXIF:Model": "Test Camera",
        }
    ])
    geocoder = FakeGeocoder(GeocodeResult(None, "Offline cache miss", "offline"))
    renderer = FakeRenderer()

    summary = pipeline(reader, geocoder, renderer).process_folder(source, output, report)

    assert summary.results[0].warning == "Offline cache miss"
    assert summary.warning_count == 1
    row = read_report(report)[0]
    assert row["location"] == ""
    assert row["warning"] == "Offline cache miss"
    assert row["status"] == "warning"
    assert renderer.calls[0][3] == (
        "2030年01月07日 · 星期一 · 08:09",
        "Test Camera",
    )


def test_control_characters_are_sanitized_for_rendering_but_preserved_in_report(tmp_path):
    source, output, report, overrides = (
        tmp_path / "in",
        tmp_path / "out",
        tmp_path / "report.csv",
        tmp_path / "overrides.csv",
    )
    source.mkdir()
    make_photo(source, "a.jpg")
    raw_location = "上海, 外滩\n夜景\t\x00\u200b"
    raw_device = "iPhone\t8\x00\u2060"
    overrides.write_text(
        "filename,captured_at,location,device\n"
        f'a.jpg,,"{raw_location}","{raw_device}"\n',
        encoding="utf-8",
    )
    renderer = FakeRenderer()

    pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer).process_folder(
        source, output, report, overrides
    )

    assert renderer.calls[0][3] == ("", "上海, 外滩 夜景 / iPhone 8")
    assert read_report(report)[0]["location"] == raw_location
    assert read_report(report)[0]["device"] == raw_device


def test_geocode_warning_and_oriented_dimensions_flow_to_result_and_low_ppi_warning(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir()
    make_photo(source, "portrait.jpg")
    reader = FakeMetadataReader([{"SourceFile": "portrait.jpg", "EXIF:GPSLatitude": 1, "EXIF:GPSLongitude": 2}])
    geocoder = FakeGeocoder(GeocodeResult(None, "Offline cache miss", "offline"))
    renderer = FakeRenderer()

    summary = pipeline(reader, geocoder, renderer, dimension_probe=lambda source: (80, 120)).process_folder(source, output, report)

    assert summary.results[0].status == "warning"
    assert "Offline cache miss" in summary.results[0].warning
    assert "below 240" in summary.results[0].warning
    assert renderer.calls[0][2].canvas_width == 1200
    assert renderer.calls[0][2].canvas_height == 1800
    assert float(read_report(report)[0]["effective_ppi"]) < 240


def test_renderer_is_atomic_and_failure_is_isolated_with_temp_cleanup(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir()
    make_photo(source, "a.jpg")
    make_photo(source, "b.jpg")
    renderer = FakeRenderer(failing={"a.jpg"})

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg", "b.jpg")), renderer=renderer).process_folder(source, output, report)

    assert [result.status for result in summary.results] == ["failed", "success"]
    assert not (output / "a-print.jpg").exists()
    assert (output / "b-print.jpg").read_text(encoding="utf-8") == "b.jpg"
    assert not list(output.glob(".*.tmp-*.jpg"))
    assert len(read_report(report)) == 2


def test_collision_existing_output_and_rerun_use_advisory_manifest_preferences(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); output.mkdir()
    make_photo(source, "a.jpg"); make_photo(source, "a.heic")
    unrelated = output / "a-print.jpg"
    unrelated.write_text("do not overwrite", encoding="utf-8")
    first_renderer = FakeRenderer()

    pipeline(FakeMetadataReader(rows_for("a.jpg", "a.heic")), renderer=first_renderer).process_folder(source, output, report)

    assert unrelated.read_text(encoding="utf-8") == "do not overwrite"
    assert (output / "a-print-2.jpg").exists()
    assert (output / "a-print-3.jpg").exists()
    second_renderer = FakeRenderer()
    pipeline(FakeMetadataReader(rows_for("a.jpg", "a.heic")), renderer=second_renderer).process_folder(source, output, report)
    assert [call[1].name for call in second_renderer.calls] == ["render.jpg", "render.jpg"]
    assert all(call[1].parent.parent == output for call in second_renderer.calls)
    assert unrelated.read_text(encoding="utf-8") == "do not overwrite"


def test_report_has_exact_safe_utf8_sig_rows_and_empty_folder_is_header_only(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "reports" / "report.csv"
    source.mkdir()
    make_photo(source, "formula.jpg")
    reader = FakeMetadataReader([{"SourceFile": "formula.jpg", "EXIF:Model": "=HYPERLINK(\"bad\")", "EXIF:DateTimeOriginal": "2024:01:01 00:00:00"}])

    summary = pipeline(reader).process_folder(source, output, report)

    assert report.read_bytes().startswith(b"\xef\xbb\xbf")
    assert list(read_report(report)[0]) == ["source", "output", "status", "captured_at", "location", "device", "missing_fields", "effective_ppi", "warning", "error"]
    assert read_report(report)[0]["device"] == "'=HYPERLINK(\"bad\")"
    assert summary.success_count == 1

    empty = tmp_path / "empty"; empty.mkdir()
    empty_report = tmp_path / "empty.csv"
    empty_summary = pipeline().process_folder(empty, tmp_path / "empty-out", empty_report)
    assert empty_summary.results == ()
    assert empty_summary.success_count == empty_summary.warning_count == empty_summary.failed_count == empty_summary.skipped_count == 0
    assert len(empty_report.read_text(encoding="utf-8-sig").splitlines()) == 1


def test_invalid_source_output_relationship_and_report_failure_are_safe(tmp_path):
    source = tmp_path / "in"; source.mkdir(); make_photo(source, "a.jpg")
    with pytest.raises(ValueError, match="must not"):
        pipeline().process_folder(source, source / "output", tmp_path / "report.csv")
    with pytest.raises(ValueError, match="input directory"):
        pipeline().process_folder(tmp_path / "missing", tmp_path / "out", tmp_path / "report.csv")
    with pytest.raises(ValueError, match="report path"):
        pipeline().process_folder(source, tmp_path / "out", source / "report.csv")

    report_parent_file = tmp_path / "not-a-directory"; report_parent_file.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="report path"):
        pipeline().process_folder(source, tmp_path / "out", report_parent_file / "report.csv")
    assert not (tmp_path / "out").exists()


def test_report_cannot_claim_an_unrelated_existing_output_as_owned(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); output.mkdir(); make_photo(source, "a.jpg")
    unrelated = output / "holiday.jpg"; unrelated.write_text("keep", encoding="utf-8")
    report.write_text(
        "source,output,status,captured_at,location,device,missing_fields,effective_ppi,warning,error\n"
        f"{(source / 'a.jpg').resolve()},{unrelated.resolve()},success,,,,,,,,\n",
        encoding="utf-8",
    )

    pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)

    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert (output / "a-print.jpg").exists()


def test_fitted_caption_warning_is_reported_and_marks_photo_warning(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")
    fitter = lambda lines, geometry: FittedCaptions(*lines, 34, 24, "Caption text was truncated.")

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg")), caption_fitter=fitter).process_folder(source, output, report)

    assert summary.results[0].status == "warning"
    assert "truncated" in summary.results[0].warning
    assert "truncated" in read_report(report)[0]["warning"]


@pytest.mark.parametrize("renderer", [lambda source, output, geometry, captions: None, lambda source, output, geometry, captions: output.symlink_to(source)])
def test_renderer_must_create_nonempty_regular_file_and_leaves_no_temp(tmp_path, renderer):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer).process_folder(source, output, report)

    assert summary.results[0].status == "failed"
    assert summary.results[0].output is None
    assert not list(output.glob(".*.tmp-*.jpg"))
    assert not (output / "a-print.jpg").exists()


def test_renderer_uses_private_directory_and_rejects_created_symlink(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    victim = tmp_path / "victim.jpg"
    source.mkdir(); make_photo(source, "a.jpg"); victim.write_bytes(b"victim bytes")
    observed = {}

    def symlink_renderer(source_path, temporary, geometry, captions):
        observed["temporary"] = temporary
        observed["mode"] = stat.S_IMODE(temporary.parent.stat().st_mode)
        observed["preexisting"] = temporary.exists() or temporary.is_symlink()
        temporary.symlink_to(victim)

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=symlink_renderer).process_folder(source, output, report)

    assert summary.results[0].status == "failed"
    assert observed == {
        "temporary": observed["temporary"],
        "mode": 0o700,
        "preexisting": False,
    }
    assert observed["temporary"].parent.parent == output
    assert victim.read_bytes() == b"victim bytes"
    assert not observed["temporary"].parent.exists()
    assert not list(output.glob(".photo-caption-print-render-*"))


def test_renderer_created_hardlink_is_rejected_without_publishing_victim(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    victim = tmp_path / "victim.jpg"
    source.mkdir(); make_photo(source, "a.jpg"); victim.write_bytes(b"victim bytes")

    def hardlink_renderer(source_path, temporary, geometry, captions):
        os.link(victim, temporary)

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=hardlink_renderer).process_folder(source, output, report)

    assert summary.results[0].status == "failed"
    assert victim.read_bytes() == b"victim bytes"
    assert not (output / "a-print.jpg").exists()
    assert not list(output.glob(".photo-caption-print-render-*"))


def test_render_temp_cleanup_repairs_renderer_permission_tampering(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")
    observed = {}

    def permission_renderer(source_path, temporary, geometry, captions):
        temporary.write_bytes(b"partial")
        observed["directory"] = temporary.parent
        nested = temporary.parent / "nested"; nested.mkdir()
        (nested / "extra").write_bytes(b"extra")
        nested.chmod(0)
        temporary.parent.chmod(0)
        raise RuntimeError("render failed after permission change")

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=permission_renderer).process_folder(source, output, report)

    assert summary.results[0].status == "failed"
    assert observed["directory"].exists()
    (observed["directory"] / "nested").chmod(0o700)
    assert (observed["directory"] / "nested" / "extra").read_bytes() == b"extra"
    assert "cleanup" in summary.results[0].warning.lower()
    (observed["directory"] / "nested" / "extra").unlink()
    (observed["directory"] / "nested").rmdir()
    observed["directory"].rmdir()


def test_manifest_preserves_rerun_preference_after_failure(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")
    first = FakeRenderer()
    pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=first).process_folder(source, output, report)
    owned = output / "a-print.jpg"
    assert owned.exists()

    failed = pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=FakeRenderer(failing={"a.jpg"})).process_folder(source, output, report)
    assert failed.results[0].status == "failed"
    assert owned.exists()

    third = pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)
    assert third.results[0].output == owned
    assert not (output / "a-print-2.jpg").exists()


def test_tampered_manifest_owned_output_and_forged_report_are_never_reused(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")
    pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)
    owned = output / "a-print.jpg"; owned.write_text("tampered", encoding="utf-8")
    report.write_text(
        "source,output,status,captured_at,location,device,missing_fields,effective_ppi,warning,error\n"
        f"{(source / 'a.jpg').resolve()},{owned.resolve()},success,,,,,,,,\n",
        encoding="utf-8",
    )

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)

    assert owned.read_text(encoding="utf-8") == "tampered"
    assert summary.results[0].output == output / "a-print-2.jpg"


def test_forged_manifest_can_never_authorize_overwrite(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); output.mkdir(); photo = make_photo(source, "a.jpg")
    occupied = output / "a-print.jpg"; occupied.write_bytes(b"valuable unrelated bytes")
    write_manifest(output, photo, occupied.name, occupied.read_bytes())

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)

    assert occupied.read_bytes() == b"valuable unrelated bytes"
    assert summary.results[0].output == output / "a-print-2.jpg"
    assert (output / "a-print-2.jpg").read_bytes() == b"a.jpg"


def test_forged_manifest_name_cannot_escape_output_namespace(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); output.mkdir(); photo = make_photo(source, "a.jpg")
    victim = tmp_path / "victim.jpg"; victim.write_bytes(b"victim bytes")
    write_manifest(output, photo, "../victim.jpg", victim.read_bytes())

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)

    assert victim.read_bytes() == b"victim bytes"
    assert summary.results[0].output == output / "a-print.jpg"


def test_oversized_manifest_is_bounded_and_ignored(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); output.mkdir(); make_photo(source, "a.jpg")
    (output / ".photo-caption-print-manifest.json").write_bytes(b" " * (1024 * 1024 + 1))

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)

    assert summary.results[0].status == "warning"
    assert "manifest" in summary.results[0].warning.lower()
    assert summary.results[0].output == output / "a-print.jpg"


def test_identical_orphan_output_is_reused_without_manifest(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); output.mkdir(); make_photo(source, "a.jpg")
    orphan = output / "a-print.jpg"; orphan.write_bytes(b"a.jpg")

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)

    assert summary.results[0].output == orphan
    assert not (output / "a-print-2.jpg").exists()


def test_publish_race_never_overwrites_racing_target(tmp_path, monkeypatch):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")
    real_link = os.link
    raced = []

    def racing_link(source_path, destination, *args, **kwargs):
        destination = output / Path(destination)
        if not raced:
            raced.append(destination)
            descriptor = os.open(Path(destination).name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                 0o600, dir_fd=kwargs["dst_dir_fd"])
            try: os.write(descriptor, b"racing writer")
            finally: os.close(descriptor)
        return real_link(source_path, destination, *args, **kwargs)

    monkeypatch.setattr(pipeline_module.os, "link", racing_link)
    summary = pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)

    assert raced == [output / "a-print.jpg"]
    assert (output / "a-print.jpg").read_bytes() == b"racing writer"
    assert summary.results[0].output == output / "a-print-2.jpg"
    assert (output / "a-print-2.jpg").read_bytes() == b"a.jpg"


@pytest.mark.parametrize("occupied_kind", ["symlink", "hardlink"])
def test_unsafe_or_hardlinked_existing_output_never_changes_victim(tmp_path, occupied_kind):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); output.mkdir(); make_photo(source, "a.jpg")
    victim = tmp_path / "victim.jpg"; victim.write_bytes(b"victim bytes")
    occupied = output / "a-print.jpg"
    if occupied_kind == "symlink": occupied.symlink_to(victim)
    else: os.link(victim, occupied)

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)

    assert victim.read_bytes() == b"victim bytes"
    assert summary.results[0].output == output / "a-print-2.jpg"
    assert (output / "a-print-2.jpg").read_bytes() == b"a.jpg"


def test_manifest_write_failure_leaves_recoverable_published_output(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")

    def fail_manifest_replace(source_path, destination):
        if Path(destination).name == ".photo-caption-print-manifest.json":
            raise OSError("simulated crash before manifest publication")
        os.replace(source_path, destination)

    first = pipeline(FakeMetadataReader(rows_for("a.jpg")), replace=fail_manifest_replace).process_folder(source, output, report)
    published = output / "a-print.jpg"
    assert published.read_bytes() == b"a.jpg"
    assert first.results[0].output == published
    assert first.results[0].status == "warning"
    assert "manifest" in first.results[0].warning.lower()
    assert not (output / ".photo-caption-print-manifest.json").exists()

    recovered = pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)
    assert recovered.results[0].output == published
    assert not (output / "a-print-2.jpg").exists()


def test_exact_case_distinct_source_identities_remain_stable(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); upper = make_photo(source, "A.jpg")
    lower = make_photo(source, "a.jpg")
    if upper.samefile(lower):
        pytest.skip("filesystem folds filename case")

    first = pipeline(FakeMetadataReader(rows_for("A.jpg", "a.jpg"))).process_folder(source, output, report)
    first_outputs = [result.output for result in first.results]
    manifest = json.loads((output / ".photo-caption-print-manifest.json").read_text(encoding="utf-8"))
    second = pipeline(FakeMetadataReader(rows_for("A.jpg", "a.jpg"))).process_folder(source, output, report)

    assert set(manifest["entries"]) == {str(upper.resolve()), str(lower.resolve())}
    assert [result.output for result in second.results] == first_outputs


def test_exact_unicode_distinct_source_identities_remain_stable(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); composed = make_photo(source, "caf\N{LATIN SMALL LETTER E WITH ACUTE}.jpg")
    decomposed = source / "cafe\N{COMBINING ACUTE ACCENT}.jpg"
    try: decomposed.write_bytes(b"source")
    except OSError: pytest.skip("filesystem rejects normalization-distinct filenames")
    try:
        if composed.samefile(decomposed): pytest.skip("filesystem folds Unicode normalization")
    except OSError:
        pytest.skip("filesystem does not preserve normalization-distinct filenames")

    first = pipeline(FakeMetadataReader(rows_for(composed.name, decomposed.name))).process_folder(source, output, report)
    first_outputs = [result.output for result in first.results]
    manifest = json.loads((output / ".photo-caption-print-manifest.json").read_text(encoding="utf-8"))
    second = pipeline(FakeMetadataReader(rows_for(composed.name, decomposed.name))).process_folder(source, output, report)

    assert set(manifest["entries"]) == {str(composed.resolve()), str(decomposed.resolve())}
    assert [result.output for result in second.results] == first_outputs


def test_report_failures_are_typed_atomic_and_clean_temps(tmp_path, monkeypatch):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")
    report.write_bytes(b"previous report bytes\n")
    reader = FakeMetadataReader([{"SourceFile": "a.jpg", "EXIF:Model": "\ud800"}])
    with pytest.raises(ReportError):
        pipeline(reader).process_folder(source, output, report)
    assert report.read_bytes() == b"previous report bytes\n"
    assert not list(report.parent.glob(f".{report.name}.*.tmp"))

    real_replace = os.replace
    def fail_report_replace(source_path, destination, *args, **kwargs):
        if Path(destination).name == report.name:
            raise RuntimeError("injected report replace failure")
        real_replace(source_path, destination, *args, **kwargs)

    monkeypatch.setattr(pipeline_module.os, "replace", fail_report_replace)
    with pytest.raises(ReportError) as raised:
        pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert report.read_bytes() == b"previous report bytes\n"
    assert not list(report.parent.glob(f".{report.name}.*.tmp"))


def test_report_final_symlink_is_rejected_before_batch_mutation(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    victim = tmp_path / "victim.csv"
    source.mkdir(); make_photo(source, "a.jpg"); victim.write_bytes(b"victim bytes\n")
    report.symlink_to(victim)
    renderer = FakeRenderer()

    with pytest.raises(ValueError, match="report path"):
        pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer).process_folder(source, output, report)

    assert report.is_symlink()
    assert victim.read_bytes() == b"victim bytes\n"
    assert renderer.calls == []
    assert not output.exists()


def test_report_nonregular_and_hardlinked_destinations_are_rejected_before_rendering(tmp_path):
    source, output = tmp_path / "in", tmp_path / "out"
    source.mkdir(); make_photo(source, "a.jpg")
    directory_report = tmp_path / "directory-report.csv"; directory_report.mkdir()
    renderer = FakeRenderer()

    with pytest.raises(ValueError, match="report path"):
        pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer).process_folder(source, output, directory_report)
    assert renderer.calls == []
    assert not output.exists()

    victim, report = tmp_path / "victim.csv", tmp_path / "hardlink-report.csv"
    victim.write_bytes(b"keep these bytes\n"); os.link(victim, report)
    renderer = FakeRenderer()

    with pytest.raises(ValueError, match="report path"):
        pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer).process_folder(source, output, report)

    assert victim.read_bytes() == b"keep these bytes\n"
    assert report.read_bytes() == b"keep these bytes\n"
    assert renderer.calls == []


def test_report_symlink_swap_during_batch_replaces_link_not_victim(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    victim = tmp_path / "victim.csv"
    source.mkdir(); make_photo(source, "a.jpg")
    report.write_bytes(b"old report\n"); victim.write_bytes(b"victim bytes\n")

    def swapping_renderer(source_path, temporary, geometry, captions):
        report.unlink(); report.symlink_to(victim)
        temporary.write_bytes(b"a.jpg")

    pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=swapping_renderer).process_folder(source, output, report)

    assert not report.is_symlink()
    assert report.read_bytes().startswith(b"\xef\xbb\xbf")
    assert victim.read_bytes() == b"victim bytes\n"


def test_report_parent_symlink_swap_cannot_escape_pinned_directory(tmp_path):
    source, output = tmp_path / "in", tmp_path / "out"
    report_parent, moved_parent = tmp_path / "reports", tmp_path / "moved-reports"
    victim_parent = tmp_path / "victim-dir"
    source.mkdir(); report_parent.mkdir(); victim_parent.mkdir(); make_photo(source, "a.jpg")
    report = report_parent / "report.csv"
    victim = victim_parent / "report.csv"; victim.write_bytes(b"victim bytes\n")

    def swapping_renderer(source_path, temporary, geometry, captions):
        report_parent.rename(moved_parent)
        report_parent.symlink_to(victim_parent, target_is_directory=True)
        temporary.write_bytes(b"a.jpg")

    with pytest.raises(ReportError):
        pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=swapping_renderer).process_folder(source, output, report)

    assert victim.read_bytes() == b"victim bytes\n"
    assert not (moved_parent / "report.csv").exists()


def test_output_directory_swap_before_temp_creation_never_writes_replacement(tmp_path, monkeypatch):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    moved_output = tmp_path / "moved-output"
    source.mkdir(); make_photo(source, "a.jpg")
    renderer = FakeRenderer()
    original_new_render_temp = pipeline_module._new_render_temp

    def swap_then_create(pinned_output):
        output.rename(moved_output)
        output.mkdir()
        (output / "valuable.marker").write_bytes(b"valuable bytes")
        return original_new_render_temp(pinned_output)

    monkeypatch.setattr(pipeline_module, "_new_render_temp", swap_then_create)
    summary = pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer).process_folder(source, output, report)

    assert summary.results[0].status == "failed"
    assert renderer.calls == []
    assert (output / "valuable.marker").read_bytes() == b"valuable bytes"
    assert not (output / "a-print.jpg").exists()
    assert not (output / pipeline_module.MANIFEST_NAME).exists()


def test_renderer_rejects_render_file_that_existed_before_renderer_boundary(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")

    def renderer(source_path, render_path, geometry, captions):
        render_path.write_bytes(b"rendered")

    def create_before_renderer(stage, temporary):
        if stage == "before-render":
            (temporary / "render.jpg").write_bytes(b"attacker file")

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer,
                       boundary_hook=create_before_renderer).process_folder(source, output, report)

    assert summary.results[0].status == "failed"
    assert not (output / "a-print.jpg").exists()


def test_renderer_output_swap_after_capture_is_not_published(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")
    moved = tmp_path / "moved.jpg"

    def renderer(source_path, render_path, geometry, captions):
        render_path.write_bytes(b"rendered")

    def swap_after_capture(stage, render_path):
        if stage == "after-render-capture":
            render_path.rename(moved)
            render_path.write_bytes(b"attacker replacement")

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer,
                       boundary_hook=swap_after_capture).process_folder(source, output, report)

    assert summary.results[0].status == "failed"
    assert not (output / "a-print.jpg").exists()
    assert moved.read_bytes() == b"rendered"


def test_output_path_swap_after_renderer_validation_never_writes_external_file(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    external = tmp_path / "external"
    moved = tmp_path / "moved-out"
    source.mkdir(); make_photo(source, "a.jpg"); external.mkdir()
    victim = external / "render.jpg"

    def renderer(source_path, render_path, geometry, captions):
        render_path.write_bytes(b"must not escape")

    def swap_after_validation(stage, render_path):
        if stage == "after-temp-validation":
            output.rename(moved)
            output.symlink_to(external, target_is_directory=True)

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer,
                       boundary_hook=swap_after_validation).process_folder(source, output, report)

    assert summary.results[0].status == "failed"
    assert not victim.exists()


def test_output_directory_move_before_manifest_does_not_claim_visible_success(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    external = tmp_path / "external"
    moved = tmp_path / "moved-out"
    source.mkdir(); make_photo(source, "a.jpg"); external.mkdir()
    external_marker = external / "valuable.marker"
    external_marker.write_bytes(b"keep")

    def swap_before_manifest(stage, path):
        if stage == "before-manifest":
            output.rename(moved)
            output.symlink_to(external, target_is_directory=True)

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg")),
                       boundary_hook=swap_before_manifest).process_folder(source, output, report)

    assert summary.results[0].status == "failed"
    assert summary.results[0].output is None
    assert "output directory" in summary.results[0].error.lower()
    assert (moved / "a-print.jpg").read_bytes() == b"a.jpg"
    assert external_marker.read_bytes() == b"keep"
    assert read_report(report)[0]["output"] == ""


@pytest.mark.parametrize("report_builder", [
    lambda output: output / "report.csv",
    lambda output: output / "." / "report.csv",
    lambda output: output / "nested" / ".." / "report.csv",
])
def test_report_inside_nonexistent_output_is_rejected_before_output_creation(tmp_path, report_builder):
    source = tmp_path / "in"; source.mkdir(); make_photo(source, "a.jpg")
    output = tmp_path / "new-output"
    report = report_builder(output)

    with pytest.raises(ValueError, match="report path collides"):
        pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)

    assert not output.exists()


def test_cleanup_never_deletes_replacement_at_private_temp_name(tmp_path):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")
    observed = {}

    def replacing_renderer(source_path, temporary, geometry, captions):
        temporary.write_bytes(b"rendered")
        original = temporary.parent.with_name(f"{temporary.parent.name}-renamed")
        temporary.parent.rename(original)
        temporary.parent.mkdir(mode=0o700)
        marker = temporary.parent / "valuable.marker"
        marker.write_bytes(b"valuable bytes")
        observed.update(original=original, replacement=temporary.parent, marker=marker)

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=replacing_renderer).process_folder(source, output, report)

    assert summary.results[0].status == "failed"
    assert observed["marker"].read_bytes() == b"valuable bytes"
    assert observed["replacement"].is_dir()
    assert "cleanup" in summary.results[0].warning.lower()


def test_report_ancestor_swap_leaves_external_report_byte_identical(tmp_path):
    source, output = tmp_path / "in", tmp_path / "out"
    report_root, moved_root = tmp_path / "reports", tmp_path / "moved-reports"
    external_root = tmp_path / "external"
    report_parent = report_root / "year" / "month"
    external_parent = external_root / "year" / "month"
    source.mkdir(); report_parent.mkdir(parents=True); external_parent.mkdir(parents=True)
    make_photo(source, "a.jpg")
    report = report_parent / "report.csv"
    external_report = external_parent / "report.csv"
    external_report.write_bytes(b"valuable external report\n")

    def swapping_renderer(source_path, temporary, geometry, captions):
        report_root.rename(moved_root)
        report_root.symlink_to(external_root, target_is_directory=True)
        temporary.write_bytes(b"rendered")

    with pytest.raises(ReportError):
        pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=swapping_renderer).process_folder(source, output, report)

    assert external_report.read_bytes() == b"valuable external report\n"
    assert not (moved_root / "year" / "month" / "report.csv").exists()


@pytest.mark.parametrize("kind", ["output", "report"])
def test_symlinked_directory_component_is_rejected_before_rendering(tmp_path, kind):
    source = tmp_path / "in"; source.mkdir(); make_photo(source, "a.jpg")
    real_parent = tmp_path / "real"; real_parent.mkdir()
    alias = tmp_path / "alias"; alias.symlink_to(real_parent, target_is_directory=True)
    output = alias / "out" if kind == "output" else tmp_path / "out"
    report = alias / "reports" / "report.csv" if kind == "report" else tmp_path / "report.csv"
    renderer = FakeRenderer()

    with pytest.raises(ValueError):
        pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer).process_folder(source, output, report)

    assert renderer.calls == []
    assert list(real_parent.iterdir()) == []


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", " ", "\t", "\r", "\n", "\x1f"])
def test_report_neutralizes_formula_after_whitespace_or_control_prefix(tmp_path, prefix):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg")
    dangerous = f"{prefix}=formula"
    reader = FakeMetadataReader([{"SourceFile": "a.jpg", "EXIF:Model": dangerous, "EXIF:DateTimeOriginal": "2024:01:02 03:04:05"}])

    pipeline(reader).process_folder(source, output, report)

    assert read_report(report)[0]["device"] == f"'{dangerous}"


@pytest.mark.parametrize("stage", ["dimensions", "format", "fit", "renderer", "publish"])
def test_stage_failures_are_isolated_and_keep_calculated_ppi_in_report(tmp_path, stage, monkeypatch):
    source, output, report = tmp_path / "in", tmp_path / "out", tmp_path / "report.csv"
    source.mkdir(); make_photo(source, "a.jpg"); make_photo(source, "b.jpg")
    def bad_dimensions(path):
        if stage == "dimensions" and path.name == "a.jpg": raise RuntimeError("dimension failure")
        return dimensions(path)
    def bad_formatter(metadata):
        if stage == "format" and metadata.source.name == "a.jpg": raise RuntimeError("format failure")
        return (metadata.source.name, "")
    def bad_fitter(lines, geometry):
        if stage == "fit" and lines == ("a.jpg", ""): raise RuntimeError("fit failure")
        return lines
    renderer = FakeRenderer(failing={"a.jpg"}) if stage == "renderer" else FakeRenderer()
    real_link = os.link
    def bad_link(source_path, destination, *args, **kwargs):
        if stage == "publish" and Path(destination).name == "a-print.jpg": raise OSError("publish failure")
        return real_link(source_path, destination, *args, **kwargs)
    monkeypatch.setattr(pipeline_module.os, "link", bad_link)

    summary = pipeline(FakeMetadataReader(rows_for("a.jpg", "b.jpg")), renderer=renderer, dimension_probe=bad_dimensions, caption_formatter=bad_formatter, caption_fitter=bad_fitter).process_folder(source, output, report)

    assert summary.results[0].status == "failed"
    assert summary.results[1].status == "success"
    row = read_report(report)[0]
    assert row["output"] == ""
    assert row["error"]
    assert (row["effective_ppi"] == "") is (stage == "dimensions")
    if stage != "dimensions": assert float(row["effective_ppi"]) > 0


def test_missing_fields_and_all_path_validation_happen_before_mutation(tmp_path):
    source = tmp_path / "in"; source.mkdir(); make_photo(source, "a.jpg")
    output, report = tmp_path / "out", tmp_path / "report.csv"
    summary = pipeline(FakeMetadataReader(rows_for("a.jpg"))).process_folder(source, output, report)
    assert read_report(report)[0]["missing_fields"] == "date;location;device"

    variants = [source, source.parent, source / "nested", source / "report.csv", output / "a-print.jpg"]
    for index, bad_output_or_report in enumerate(variants):
        target = tmp_path / f"other-{index}"
        if index < 3:
            with pytest.raises(ValueError): pipeline().process_folder(source, bad_output_or_report, target / "report.csv")
        else:
            actual_output = output if index == 4 else target
            with pytest.raises(ValueError): pipeline().process_folder(source, actual_output, bad_output_or_report)
        assert not target.exists()

    linked = tmp_path / "linked"; linked.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError): pipeline().process_folder(source, linked / "inside", tmp_path / "linked-report.csv")


@pytest.mark.parametrize("report_name", ["a-print.jpg", "a-print.jpg/report.csv", ".photo-caption-print-manifest.json/report.csv", ".a-print.tmp-reserved.jpg"])
def test_report_artifact_namespace_validation_precedes_all_output_mutation(tmp_path, report_name):
    source, output = tmp_path / "in", tmp_path / "out"
    source.mkdir(); make_photo(source, "a.jpg")
    renderer = FakeRenderer()

    with pytest.raises(ValueError, match="report path collides"):
        pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer).process_folder(source, output, output / report_name)

    assert renderer.calls == []
    assert not output.exists()


def test_report_artifact_validation_resolves_output_symlinks_before_mutation(tmp_path):
    source, output, alias = tmp_path / "in", tmp_path / "out", tmp_path / "out-alias"
    source.mkdir(); output.mkdir(); make_photo(source, "a.jpg")
    alias.symlink_to(output, target_is_directory=True)
    renderer = FakeRenderer()

    with pytest.raises(ValueError, match="report path collides"):
        pipeline(FakeMetadataReader(rows_for("a.jpg")), renderer=renderer).process_folder(source, alias, alias / "a-print.jpg" / "report.csv")

    assert renderer.calls == []
    assert list(output.iterdir()) == []
