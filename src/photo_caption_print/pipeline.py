"""Safe, deterministic batch orchestration and CSV reporting."""
from __future__ import annotations

import csv
import errno
import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from unicodedata import normalize

from photo_caption_print.captions import apply_override, format_caption, load_overrides, lookup_override
from photo_caption_print.geocode import GeocodeResult
from photo_caption_print.layout import FittedCaptions, build_magick_command, fit_captions, geometry_for, measure_text, probe_oriented_dimensions, run_render
from photo_caption_print.metadata import metadata_from_exiftool, metadata_warning_from_exiftool, run_exiftool
from photo_caption_print.models import PhotoMetadata, ProcessResult

SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".heic", ".png", ".tif"})
REPORT_COLUMNS = ("source", "output", "status", "captured_at", "location", "device", "missing_fields", "effective_ppi", "warning", "error")
MANIFEST_NAME, MANIFEST_VERSION = ".photo-caption-print-manifest.json", 1
MAX_MANIFEST_BYTES, MAX_MANIFEST_ENTRIES = 1024 * 1024, 10_000
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


class ReportError(RuntimeError):
    """The batch completed, but a durable report could not be written."""


@dataclass(frozen=True)
class BatchSummary:
    results: tuple[ProcessResult, ...]
    warnings: tuple[str, ...] = ()

    @property
    def success_count(self) -> int: return sum(item.status == "success" for item in self.results)

    @property
    def warning_count(self) -> int: return sum(item.status == "warning" for item in self.results)

    @property
    def skipped_count(self) -> int: return sum(item.status == "skipped" for item in self.results)

    @property
    def failed_count(self) -> int: return sum(item.status == "failed" for item in self.results)


@dataclass(frozen=True)
class _PinnedDirectory:
    descriptor: int
    path: Path
    identity: tuple[int, int]


@dataclass(frozen=True)
class _RenderTemp:
    descriptor: int
    name: str
    path: Path
    identity: tuple[int, int]


def _default_caption_fitter(lines: Sequence[str], geometry: Any) -> FittedCaptions:
    return fit_captions(lines, geometry, "Helvetica", measure_text)


def _default_renderer(source: Path, output: Path, geometry: Any, captions: Any) -> None:
    run_render(build_magick_command(source, output, geometry, captions, "Helvetica"))


class BatchPipeline:
    """Render a folder without changing sources or overwriting existing outputs."""

    def __init__(self, *, metadata_reader: Callable[[Sequence[Path]], Iterable[Mapping[str, Any]]] = run_exiftool,
                 geocoder: Any | None = None, dimension_probe: Callable[[Path], tuple[int, int]] = probe_oriented_dimensions,
                 caption_formatter: Callable[[PhotoMetadata], tuple[str, str]] = format_caption,
                 caption_fitter: Callable[[Sequence[str], Any], Any] = _default_caption_fitter,
                 renderer: Callable[[Path, Path, Any, Any], None] = _default_renderer,
                 replace: Callable[..., None] = os.replace,
                 boundary_hook: Callable[[str, Path], None] | None = None) -> None:
        self.metadata_reader, self.geocoder, self.dimension_probe = metadata_reader, geocoder, dimension_probe
        self.caption_formatter, self.caption_fitter, self.renderer, self.replace = caption_formatter, caption_fitter, renderer, replace
        self.boundary_hook = boundary_hook

    def process_folder(self, input_dir: str | Path, output_dir: str | Path, report_path: str | Path,
                       overrides_path: str | Path | None = None) -> BatchSummary:
        source_pin = target_pin = report_pin = None
        try:
            source_dir, target_dir, report = self._validate_paths(input_dir, output_dir, report_path)
            source_pin = _open_pinned_directory(source_dir, create=False, label="input directory")
            sources = self._discover(source_pin.path)
            self._validate_report_collision(report, target_dir, sources)
            report_pin = _open_pinned_directory(report.parent, create=True, label="report path parent")
            _validate_report_destination_at(report_pin.descriptor, report.name)
            target_pin = _open_pinned_directory(target_dir, create=True, label="output directory")
            _validate_pinned_relationships(source_pin, target_pin, report_pin, report.name)
            if not all(_visible_directory_matches(pin) for pin in (source_pin, target_pin, report_pin)):
                raise ValueError("validated directory changed before batch processing")
            overrides, override_warnings = self._load_overrides(overrides_path)
            metadata, metadata_warnings = self._read_metadata(sources)
            entries, manifest_warning = _load_manifest(target_pin)
            global_warnings = [*override_warnings, *metadata_warnings, *([manifest_warning] if manifest_warning else [])]
            reserved, details = set(), []
            for source in sources:
                item_metadata, metadata_item_warnings = metadata[source]
                warnings = [*override_warnings, *metadata_item_warnings, *([manifest_warning] if manifest_warning else [])]
                if (override := lookup_override(item_metadata, overrides)) is not None:
                    item_metadata = apply_override(item_metadata, override)
                item_metadata, geocode_warning = self._geocode(item_metadata)
                if geocode_warning: warnings.append(geocode_warning)
                preferred = self._output_for(source, target_dir, entries, reserved)
                result, ppi = self._process_one(source, preferred, item_metadata, warnings, reserved, target_pin)
                reserved.add(preferred)
                if result.output is not None: reserved.add(result.output)
                details.append({"result": result, "metadata": item_metadata, "ppi": ppi})
            output_race_warning = ""
            if self.boundary_hook is not None:
                self.boundary_hook("before-manifest", target_pin.path)
            if not _visible_directory_matches(target_pin):
                output_race_warning = _output_directory_moved_warning()
            elif manifest_error := self._update_manifest(entries, details, target_pin):
                global_warnings.append(manifest_error)
                for detail in details:
                    if detail["result"].output is not None:
                        detail["result"] = _with_warning(detail["result"], manifest_error)
            if not output_race_warning and not _visible_directory_matches(target_pin):
                output_race_warning = _output_directory_moved_warning()
            if output_race_warning:
                global_warnings.append(output_race_warning)
                _mark_output_directory_moved(details, output_race_warning)
            try:
                if self.boundary_hook is not None:
                    self.boundary_hook("before-report", report)
                if not _visible_directory_matches(target_pin):
                    warning = _output_directory_moved_warning()
                    global_warnings.append(warning)
                    _mark_output_directory_moved(details, warning)
                self._write_report(report, details, report_pin)
                if not _visible_directory_matches(target_pin):
                    warning = _output_directory_moved_warning()
                    global_warnings.append(warning)
                    _mark_output_directory_moved(details, warning)
                    self._write_report(report, details, report_pin)
            except Exception as error:
                raise ReportError(f"Unable to write report: {error}") from error
            results = tuple(detail["result"] for detail in details)
            return BatchSummary(results, tuple(global_warnings))
        finally:
            for pin in (target_pin, report_pin, source_pin):
                if pin is not None:
                    os.close(pin.descriptor)

    @staticmethod
    def _validate_paths(input_dir: str | Path, output_dir: str | Path, report_path: str | Path) -> tuple[Path, Path, Path]:
        source_dir, target_dir = _absolute_path(input_dir), _absolute_path(output_dir)
        report_argument = _absolute_path(report_path)
        if not report_argument.name: raise ValueError("report path must name a file")
        report = report_argument.parent / report_argument.name
        if _same_or_nested(source_dir, target_dir) or _same_or_nested(target_dir, source_dir):
            raise ValueError("output directory must not equal or contain the input directory")
        if _same_or_nested(source_dir, report): raise ValueError("report path must not be inside the input directory")
        if _same_or_nested(target_dir, report):
            raise ValueError("report path collides with a generated output or manifest")
        return source_dir, target_dir, report

    @staticmethod
    def _discover(source_dir: Path) -> list[Path]:
        return sorted((path for path in source_dir.iterdir() if not path.name.startswith(".") and not path.is_symlink()
                       and path.is_file() and path.suffix.casefold() in SUPPORTED_EXTENSIONS),
                      key=_discovery_key)

    @staticmethod
    def _validate_report_collision(report: Path, target_dir: Path, sources: Sequence[Path]) -> None:
        if _same_or_nested(report, target_dir):
            raise ValueError("report path collides with a generated output or manifest")
        try: relative = report.relative_to(target_dir)
        except ValueError: return
        artifact = relative.parts[0]
        if artifact == MANIFEST_NAME or _is_manifest_temp_name(artifact) or _is_potential_output_name(artifact, sources) or _is_render_temp_name(artifact, sources):
            raise ValueError("report path collides with a generated output or manifest")

    def _load_overrides(self, path: str | Path | None) -> tuple[dict[str, Any], list[str]]:
        if path is None: return {}, []
        try:
            overrides, warnings = load_overrides(path)
            return overrides, list(warnings)
        except (OSError, UnicodeError, csv.Error) as error:
            return {}, [f"Unable to load overrides: {error}"]

    def _read_metadata(self, sources: Sequence[Path]) -> tuple[dict[Path, tuple[PhotoMetadata, list[str]]], list[str]]:
        if not sources: return {}, []
        try:
            rows = list(self.metadata_reader(sources))
            if not all(isinstance(row, Mapping) and isinstance(row.get("SourceFile"), str) for row in rows):
                raise ValueError("metadata reader returned an invalid row")
            index, collected = _metadata_index(sources), {}
            for row in rows:
                source = _match_metadata_source(row["SourceFile"], index)
                if source is None or source in collected: continue
                parsed = replace(metadata_from_exiftool(row), source=source)
                warning = metadata_warning_from_exiftool(row)
                collected[source] = (parsed, [warning] if warning else [])
            for source in sources: collected.setdefault(source, (PhotoMetadata(source=source), ["Metadata unavailable for this file."]))
            return collected, []
        except Exception as error:
            warning = f"Metadata batch unavailable: {error}"
            return {source: (PhotoMetadata(source=source), [warning]) for source in sources}, [warning]

    def _geocode(self, metadata: PhotoMetadata) -> tuple[PhotoMetadata, str]:
        if self.geocoder is None or _present(metadata.location) or metadata.latitude is None or metadata.longitude is None:
            return metadata, ""
        try:
            response = self.geocoder.reverse(metadata.latitude, metadata.longitude) if hasattr(self.geocoder, "reverse") else self.geocoder(metadata.latitude, metadata.longitude)
        except Exception as error:
            return metadata, f"Geocoding failed: {error}"
        if isinstance(response, GeocodeResult): location, warning = response.location, response.warning
        else: location, warning = getattr(response, "location", None), getattr(response, "warning", "")
        return replace(metadata, location=location or metadata.location), str(warning or "")

    def _output_for(self, source: Path, target_dir: Path, entries: Mapping[str, Mapping[str, str]], reserved: set[Path]) -> Path:
        if (entry := entries.get(_source_identity(source))) is not None:
            candidate = target_dir / entry["output"]
            if _safe_output(candidate, target_dir) and _belongs_to_source_name(candidate, source) and candidate not in reserved:
                return candidate
        stem, number = _safe_stem(source.stem), 1
        while True:
            candidate = target_dir / (f"{stem}-print.jpg" if number == 1 else f"{stem}-print-{number}.jpg")
            if candidate not in reserved: return candidate
            number += 1

    def _process_one(self, source: Path, preferred: Path, metadata: PhotoMetadata, warnings: list[str],
                     reserved: set[Path], target: _PinnedDirectory) -> tuple[ProcessResult, float | None]:
        temporary = None
        render_identity = None
        ppi = None
        try:
            width, height = self.dimension_probe(source)
            geometry = geometry_for(width, height); ppi = geometry.effective_ppi
            if ppi < 240: warnings.append(f"Effective source print resolution is {ppi:.1f} PPI, below 240 PPI.")
            captions = self.caption_fitter(self.caption_formatter(metadata), geometry)
            if isinstance(captions, FittedCaptions) and captions.warning: warnings.append(captions.warning)
            temporary = _new_render_temp(target)
            render_path = temporary.path / "render.jpg"
            expected_before_render = _entry_identity_at(temporary.descriptor, "render.jpg")
            if expected_before_render is not None:
                raise RuntimeError("Render directory was not empty before renderer invocation")
            if self.boundary_hook is not None:
                self.boundary_hook("before-render", temporary.path)
            if _entry_identity_at(temporary.descriptor, "render.jpg") != expected_before_render:
                raise RuntimeError("Render output appeared before renderer invocation")
            if not _visible_render_temp_matches(target, temporary):
                raise RuntimeError("Output directory changed before renderer invocation")
            if self.boundary_hook is not None:
                self.boundary_hook("after-temp-validation", render_path)
            if not _visible_render_temp_matches(target, temporary):
                raise RuntimeError("Output directory changed before renderer invocation")
            try:
                self.renderer(source, render_path, geometry, captions)
            except Exception:
                if render_identity is None:
                    render_identity = _entry_identity_at(temporary.descriptor, "render.jpg")
                raise
            render_identity = _entry_identity_at(temporary.descriptor, "render.jpg")
            if render_identity is None:
                raise RuntimeError("Renderer did not create a render output")
            if self.boundary_hook is not None:
                self.boundary_hook("after-render-capture", render_path)
            if _entry_identity_at(temporary.descriptor, "render.jpg") != render_identity:
                raise RuntimeError("Renderer output changed before it could be published")
            if not _visible_render_temp_matches(target, temporary):
                raise RuntimeError("Output directory changed during renderer invocation")
            render_descriptor = _open_render_result(temporary.descriptor, "render.jpg", render_identity)
            try:
                output = _publish_rendered(render_descriptor, temporary.descriptor, preferred, source, reserved, target)
            finally:
                os.close(render_descriptor)
            if not _visible_directory_matches(target):
                raise RuntimeError("Output directory changed during publication")
            warning = _join_warnings(warnings)
            result = ProcessResult(source, output, "warning" if warning else "success", warning)
        except Exception as error:
            if temporary is not None and render_identity is None:
                render_identity = _entry_identity_at(temporary.descriptor, "render.jpg")
            result = ProcessResult(source, None, "failed", _join_warnings(warnings), str(error))
        finally:
            if temporary is not None:
                cleanup_warning = _cleanup_render_temp(target, temporary, render_identity)
                os.close(temporary.descriptor)
                if cleanup_warning:
                    result = _with_warning(result, cleanup_warning)
        return result, ppi

    def _update_manifest(self, entries: dict[str, dict[str, str]], details: Sequence[dict[str, Any]], target: _PinnedDirectory) -> str:
        active = {_source_identity(detail["result"].source) for detail in details}
        updated = {key: value for key, value in entries.items() if key in active}
        changed = updated != entries
        for detail in details:
            result: ProcessResult = detail["result"]
            if result.output is None: continue
            if (digest := _file_digest_at(target.descriptor, result.output.name, single_link=True)) is None:
                return "Output manifest could not authenticate a generated file."
            updated[_source_identity(result.source)] = {"output": result.output.name, "sha256": digest}; changed = True
        if not changed: return ""
        try: _write_manifest(target, updated, self.replace)
        except (OSError, UnicodeError, TypeError, ValueError) as error: return f"Output manifest could not be written: {error}"
        return ""

    def _write_report(self, path: Path, details: Iterable[Mapping[str, Any]], parent: _PinnedDirectory) -> None:
        if not _visible_directory_matches(parent):
            raise RuntimeError("report parent changed during batch processing")
        try: os.stat(path.name, dir_fd=parent.descriptor, follow_symlinks=False)
        except FileNotFoundError: pass
        except OSError as error: raise RuntimeError(f"report path cannot be inspected: {error}") from error
        temporary_name = None
        try:
            descriptor, temporary_name = _new_file_at(parent.descriptor, f".{path.name}.", ".tmp")
            with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS, extrasaction="ignore"); writer.writeheader()
                for detail in details:
                    result, metadata, ppi = detail["result"], detail["metadata"], detail["ppi"]
                    row = {"source": result.source.resolve(), "output": result.output if result.output else "", "status": result.status,
                           "captured_at": metadata.captured_at.isoformat() if metadata.captured_at else "", "location": metadata.location or "", "device": metadata.device or "",
                           "missing_fields": _missing_fields(metadata), "effective_ppi": f"{ppi:.3f}" if ppi is not None else "", "warning": result.warning, "error": result.error}
                    writer.writerow({key: _csv_safe(value) for key, value in row.items()})
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary_name, path.name, src_dir_fd=parent.descriptor, dst_dir_fd=parent.descriptor)
            temporary_name = None
            os.fsync(parent.descriptor)
            if not _visible_directory_matches(parent):
                raise RuntimeError("report parent changed during report publication")
        finally:
            if temporary_name is not None:
                try: os.unlink(temporary_name, dir_fd=parent.descriptor)
                except OSError: pass


def _absolute_path(path: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _open_pinned_directory(path: str | Path, *, create: bool, label: str) -> _PinnedDirectory:
    argument = Path(path)
    if argument.is_absolute():
        current_path, components = Path(argument.anchor), argument.parts[1:]
        start = argument.anchor
    else:
        current_path, components = Path.cwd(), argument.parts
        start = "."
    try:
        descriptor = os.open(start, _DIRECTORY_FLAGS)
    except OSError as error:
        raise ValueError(f"{label} is unsafe: {error}") from error
    try:
        for component in components:
            if component in ("", "."):
                continue
            next_path = current_path.parent if component == ".." else current_path / component
            try:
                next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise ValueError(f"{label} must exist and be a directory")
                try:
                    os.mkdir(component, 0o755, dir_fd=descriptor)
                    next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
                except OSError as error:
                    raise ValueError(f"{label} is unsafe: {error}") from error
            except OSError as error:
                raise ValueError(f"{label} is unsafe: {error}") from error
            os.close(descriptor)
            descriptor, current_path = next_descriptor, next_path
        status = os.fstat(descriptor)
        if not stat.S_ISDIR(status.st_mode):
            raise ValueError(f"{label} must be a directory")
        pinned = _PinnedDirectory(descriptor, _absolute_path(current_path), (status.st_dev, status.st_ino))
        if not _visible_directory_matches(pinned):
            raise ValueError(f"{label} changed while it was being opened")
        descriptor = -1
        return pinned
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _visible_directory_matches(expected: _PinnedDirectory) -> bool:
    actual = None
    try:
        actual = _open_pinned_directory_unchecked(expected.path)
        return actual.identity == expected.identity
    except (OSError, ValueError):
        return False
    finally:
        if actual is not None:
            os.close(actual.descriptor)


def _open_pinned_directory_unchecked(path: Path) -> _PinnedDirectory:
    current_path, components = Path(path.anchor), path.parts[1:]
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for component in components:
            next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            current_path /= component
        status = os.fstat(descriptor)
        result = _PinnedDirectory(descriptor, current_path, (status.st_dev, status.st_ino))
        descriptor = -1
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_pinned_relationships(source: _PinnedDirectory, target: _PinnedDirectory,
                                   report_parent: _PinnedDirectory, report_name: str) -> None:
    report = report_parent.path / report_name
    if _same_or_nested(source.path, target.path) or _same_or_nested(target.path, source.path):
        raise ValueError("output directory must not equal or contain the input directory")
    if _same_or_nested(source.path, report):
        raise ValueError("report path must not be inside the input directory")
    if report == target.path or _same_or_nested(target.path, report):
        raise ValueError("report path collides with a generated output or manifest")
    if source.identity == target.identity:
        raise ValueError("output directory must not equal the input directory")


def _new_render_temp(output: _PinnedDirectory) -> _RenderTemp:
    for _ in range(100):
        name = f".photo-caption-print-render-{secrets.token_hex(12)}"
        try:
            os.mkdir(name, 0o700, dir_fd=output.descriptor)
        except FileExistsError:
            continue
        descriptor = -1
        opened_identity = None
        try:
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=output.descriptor)
            status = os.fstat(descriptor)
            opened_identity = status.st_dev, status.st_ino
            visible = os.stat(name, dir_fd=output.descriptor, follow_symlinks=False)
            identity = opened_identity
            if (not stat.S_ISDIR(status.st_mode) or stat.S_IMODE(status.st_mode) != 0o700
                    or identity != (visible.st_dev, visible.st_ino)):
                raise RuntimeError("Unable to create a private render directory")
            result = _RenderTemp(descriptor, name, output.path / name, identity)
            descriptor = -1
            return result
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            if opened_identity is not None:
                try:
                    current = os.stat(name, dir_fd=output.descriptor, follow_symlinks=False)
                    if stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == opened_identity:
                        os.rmdir(name, dir_fd=output.descriptor)
                except OSError:
                    pass
            raise
    raise FileExistsError("unable to allocate a private render directory")


def _visible_render_temp_matches(output: _PinnedDirectory, temporary: _RenderTemp) -> bool:
    if not _visible_directory_matches(output):
        return False
    actual = None
    try:
        actual = _open_pinned_directory_unchecked(temporary.path)
        status = os.fstat(actual.descriptor)
        return actual.identity == temporary.identity and stat.S_IMODE(status.st_mode) == 0o700
    except (OSError, ValueError):
        return False
    finally:
        if actual is not None:
            os.close(actual.descriptor)


def _entry_identity_at(directory_descriptor: int, name: str) -> tuple[int, int, int] | None:
    try: status = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except OSError: return None
    return status.st_dev, status.st_ino, stat.S_IFMT(status.st_mode)


def _open_render_result(directory_descriptor: int, name: str,
                        expected_identity: tuple[int, int, int] | None = None) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try: descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as error:
        raise RuntimeError("Renderer did not create a safe output file") from error
    status = os.fstat(descriptor)
    visible = _entry_identity_at(directory_descriptor, name)
    identity = (status.st_dev, status.st_ino, stat.S_IFMT(status.st_mode))
    if (not stat.S_ISREG(status.st_mode) or status.st_size <= 0 or status.st_nlink != 1
            or visible != identity or (expected_identity is not None and identity != expected_identity)):
        os.close(descriptor)
        raise RuntimeError("Renderer did not create a non-empty regular output file in its private directory")
    os.fsync(descriptor)
    return descriptor


def _cleanup_render_temp(output: _PinnedDirectory, temporary: _RenderTemp,
                         render_identity: tuple[int, int, int] | None) -> str:
    warnings = []
    try:
        os.fchmod(temporary.descriptor, 0o700)
    except OSError as error:
        warnings.append(f"Render cleanup could not restore the private directory ({error}).")
    if render_identity is None:
        candidate = _entry_identity_at(temporary.descriptor, "render.jpg")
        if candidate is not None and candidate[2] == stat.S_IFREG:
            descriptor = -1
            try:
                descriptor = os.open("render.jpg", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                     dir_fd=temporary.descriptor)
                opened = os.fstat(descriptor)
                if candidate == (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode)):
                    render_identity = candidate
            except OSError:
                pass
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
    if render_identity is not None:
        current = _entry_identity_at(temporary.descriptor, "render.jpg")
        if current == render_identity:
            try: os.unlink("render.jpg", dir_fd=temporary.descriptor)
            except OSError as error: warnings.append(f"Render cleanup could not remove its verified file ({error}).")
        elif current is not None:
            warnings.append("Render cleanup left a changed render filename untouched.")
    try:
        current_dir = os.stat(temporary.name, dir_fd=output.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        warnings.append("Render cleanup left a renamed private directory untouched.")
        return _join_warnings(warnings)
    except OSError as error:
        warnings.append(f"Render cleanup could not inspect its private directory ({error}).")
        return _join_warnings(warnings)
    if (current_dir.st_dev, current_dir.st_ino) != temporary.identity or not stat.S_ISDIR(current_dir.st_mode):
        warnings.append("Render cleanup left a replacement at the private directory name untouched.")
        return _join_warnings(warnings)
    try:
        os.rmdir(temporary.name, dir_fd=output.descriptor)
    except OSError as error:
        if error.errno in (errno.ENOTEMPTY, errno.EEXIST):
            warnings.append("Render cleanup left a non-empty private directory untouched.")
        else:
            warnings.append(f"Render cleanup could not remove its private directory ({error}).")
    return _join_warnings(warnings)


def _validate_report_destination_at(parent_descriptor: int, name: str) -> None:
    try: status = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError: return
    except OSError as error: raise ValueError(f"report path cannot be inspected: {error}") from error
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise ValueError("report path must be absent or an existing regular file")


def _new_file_at(parent_descriptor: int, prefix: str, suffix: str) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(100):
        name = f"{prefix}{secrets.token_hex(8)}{suffix}"
        try: return os.open(name, flags, 0o600, dir_fd=parent_descriptor), name
        except FileExistsError: continue
    raise FileExistsError("unable to allocate an atomic report temporary")


def _publish_rendered(render_descriptor: int, temporary_descriptor: int, preferred: Path, source: Path,
                      reserved: set[Path], output: _PinnedDirectory) -> Path:
    digest = _file_digest_descriptor(render_descriptor, single_link=True)
    if digest is None: raise RuntimeError("Rendered output could not be authenticated")
    expected = os.fstat(render_descriptor)
    candidate = preferred
    while True:
        if candidate in reserved:
            candidate = _next_output(candidate, source)
            continue
        existing_descriptor = None
        try:
            existing_descriptor = os.open(candidate.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                          | getattr(os, "O_NONBLOCK", 0), dir_fd=output.descriptor)
        except FileNotFoundError:
            try:
                os.link("render.jpg", candidate.name, src_dir_fd=temporary_descriptor,
                        dst_dir_fd=output.descriptor, follow_symlinks=False)
            except FileExistsError:
                continue
            published_descriptor = os.open(candidate.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=output.descriptor)
            try:
                published = os.fstat(published_descriptor)
                if (not stat.S_ISREG(published.st_mode)
                        or (published.st_dev, published.st_ino) != (expected.st_dev, expected.st_ino)):
                    raise RuntimeError("Published output changed before it could be synchronized")
                os.fsync(published_descriptor)
            finally:
                os.close(published_descriptor)
            os.fsync(output.descriptor)
            return candidate
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.ENXIO):
                candidate = _next_output(candidate, source)
                continue
            raise RuntimeError(f"Unable to inspect output destination: {error}") from error
        else:
            try:
                status = os.fstat(existing_descriptor)
                if (stat.S_ISREG(status.st_mode) and status.st_size > 0 and status.st_nlink == 1
                        and _file_digest_descriptor(existing_descriptor, single_link=True) == digest):
                    os.fsync(existing_descriptor)
                    os.fsync(output.descriptor)
                    return candidate
            finally:
                os.close(existing_descriptor)
        candidate = _next_output(candidate, source)


def _next_output(current: Path, source: Path) -> Path:
    base = f"{_safe_stem(source.stem)}-print"
    suffix = current.stem
    number = 2 if suffix == base else int(suffix[len(base) + 1:]) + 1
    return current.with_name(f"{base}-{number}.jpg")


def _load_manifest(target: _PinnedDirectory) -> tuple[dict[str, dict[str, str]], str]:
    try:
        raw = _read_bounded_regular_at(target.descriptor, MANIFEST_NAME, MAX_MANIFEST_BYTES)
    except FileNotFoundError:
        return {}, ""
    except (OSError, ValueError):
        return {}, "Output manifest was invalid and has been ignored."
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION or not isinstance(data.get("entries"), dict): raise ValueError("wrong schema")
        if len(data["entries"]) > MAX_MANIFEST_ENTRIES: raise ValueError("too many entries")
        entries = {}
        for key, entry in data["entries"].items():
            if not isinstance(key, str) or not _valid_manifest_entry(entry): raise ValueError("wrong schema")
            entries[key] = {"output": entry["output"], "sha256": entry["sha256"]}
        return entries, ""
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError): return {}, "Output manifest was invalid and has been ignored."


def _valid_manifest_entry(entry: object) -> bool:
    if not isinstance(entry, Mapping): return False
    output, digest = entry.get("output"), entry.get("sha256")
    return (isinstance(output, str) and len(output.encode("utf-8")) <= 255 and isinstance(digest, str)
            and len(digest) == 64 and all(char in "0123456789abcdef" for char in digest))


def _write_manifest(target: _PinnedDirectory, entries: Mapping[str, Mapping[str, str]], replace: Callable[..., None]) -> None:
    if len(entries) > MAX_MANIFEST_ENTRIES: raise ValueError("manifest has too many entries")
    payload = json.dumps({"version": MANIFEST_VERSION, "entries": entries}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES: raise ValueError("manifest is too large")
    temporary_name = None
    try:
        descriptor, temporary_name = _new_file_at(target.descriptor, f".{MANIFEST_NAME}.", ".tmp")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        replace(temporary_name, MANIFEST_NAME, src_dir_fd=target.descriptor, dst_dir_fd=target.descriptor)
        temporary_name = None
        os.fsync(target.descriptor)
    finally:
        if temporary_name is not None:
            try: os.unlink(temporary_name, dir_fd=target.descriptor)
            except OSError: pass


def _file_digest_descriptor(descriptor: int, *, single_link: bool = False) -> str | None:
    digest = hashlib.sha256()
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_size <= 0 or (single_link and status.st_nlink != 1): return None
        os.lseek(descriptor, 0, os.SEEK_SET)
        for block in iter(lambda: os.read(descriptor, 1024 * 1024), b""): digest.update(block)
    except OSError: return None
    return digest.hexdigest()


def _file_digest_at(directory_descriptor: int, name: str, *, single_link: bool = False) -> str | None:
    try: descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_descriptor)
    except OSError: return None
    try:
        return _file_digest_descriptor(descriptor, single_link=single_link)
    finally:
        os.close(descriptor)


def _read_bounded_regular_at(directory_descriptor: int, name: str, limit: int) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_descriptor)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1 or status.st_size <= 0 or status.st_size > limit:
            raise ValueError("unsafe or oversized file")
        content = os.read(descriptor, limit + 1)
        if len(content) > limit: raise ValueError("oversized file")
        return content
    finally:
        os.close(descriptor)


def _with_warning(result: ProcessResult, warning: str) -> ProcessResult:
    return replace(result, status="warning" if result.status != "failed" else "failed", warning=_join_warnings((result.warning, warning)))


def _output_directory_moved_warning() -> str:
    return ("Output directory moved or was replaced after rendering; generated files remain in the pinned "
            "directory and require manual recovery.")


def _mark_output_directory_moved(details: list[dict[str, Any]], warning: str) -> None:
    for detail in details:
        result: ProcessResult = detail["result"]
        if result.output is None:
            continue
        detail["result"] = replace(result, output=None, status="failed",
                                    warning=_join_warnings((result.warning, warning)),
                                    error=warning)


def _present(value: object) -> bool: return isinstance(value, str) and bool(value.strip())


def _csv_safe(value: object) -> str:
    text, index = str(value), 0
    while index < len(text) and (text[index].isspace() or ord(text[index]) < 32): index += 1
    return f"'{text}" if index < len(text) and text[index] in "=+-@" else text


def _missing_fields(metadata: PhotoMetadata) -> str:
    return ";".join(name for name, missing in (("date", metadata.captured_at is None), ("location", not _present(metadata.location)), ("device", not _present(metadata.device))) if missing)


def _join_warnings(warnings: Iterable[str]) -> str: return " ".join(item for item in warnings if item)
def _key(value: str) -> str: return normalize("NFC", value).casefold()
def _source_identity(source: Path) -> str: return str(source.resolve())


def _discovery_key(path: Path) -> tuple[str, str, int, int]:
    status = path.stat()
    return (_key(path.name), path.name, status.st_dev, status.st_ino)


def _metadata_index(sources: Sequence[Path]) -> dict[str, Path | None]:
    index = {}
    for source in sources:
        exact = f"path:{source.resolve()}"
        index[exact] = source if exact not in index else None
        basename = f"name:{_key(source.name)}"
        index[basename] = source if basename not in index else None
    return index


def _match_metadata_source(value: str, index: Mapping[str, Path | None]) -> Path | None:
    candidate = Path(value)
    for key in (f"path:{candidate.resolve()}", f"name:{_key(candidate.name)}"):
        if (matched := index.get(key)) is not None: return matched
    return None


def _same_or_nested(parent: Path, child: Path) -> bool: return parent == child or parent in child.parents
def _safe_stem(value: str) -> str: return value.replace("/", "_").replace("\\", "_").replace("..", "_").strip(". ") or "photo"
def _safe_output(path: Path, target_dir: Path) -> bool: return path.parent == target_dir and path.suffix.casefold() == ".jpg" and path.name == Path(path.name).name


def _is_potential_output_name(name: str, sources: Sequence[Path]) -> bool:
    return any(_belongs_to_source_name(Path(name), source) for source in sources)


def _is_render_temp_name(name: str, sources: Sequence[Path]) -> bool:
    if not name.startswith(".") or not name.endswith(".jpg"): return False
    output_stem, separator, unique = name[1:].partition(".tmp-")
    return bool(separator and unique) and _is_potential_output_name(f"{output_stem}.jpg", sources)


def _is_manifest_temp_name(name: str) -> bool:
    return name.startswith(f".{MANIFEST_NAME}.") and name.endswith(".tmp")


def _belongs_to_source_name(output: Path, source: Path) -> bool:
    base, suffix = f"{_safe_stem(source.stem)}-print", output.stem
    if suffix == base: return True
    if not suffix.startswith(f"{base}-"): return False
    digits = suffix[len(base) + 1:]
    significant = digits.lstrip("0") or "0"
    return digits.isascii() and digits.isdigit() and (len(significant) > 1 or significant >= "2")


def _unlink_quietly(path: Path) -> None:
    try: path.unlink(missing_ok=True)
    except OSError: pass
