"""Cached, polite reverse geocoding through the public Nominatim service."""
from __future__ import annotations

import fcntl, inspect, json, math, os, secrets, stat, threading, time, unicodedata
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

DEFAULT_ENDPOINT = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "photo-caption-print/0.1 (+https://github.com/chris-yihao/photo-caption-print)"
_CACHE_VERSION, _MIN_REQUEST_INTERVAL, _SAFE_RETRY_FALLBACK_SECONDS, _DEFAULT_TIMEOUT = 2, 1.0, 60.0, 15.0
_MAX_CACHE_BYTES = 1_000_000  # Bound untrusted cache input before JSON parsing.
_MAX_RESPONSE_BYTES = 1_000_000  # Bound untrusted network input before decoding.
_MAX_JSON_NESTING = 128  # Application limit for untrusted JSON before decoding.
_CITY_KEYS = ("city", "town", "municipality", "county")
_PLACE_KEYS = ("attraction", "tourism", "historic", "amenity", "leisure", "building", "suburb")
_CHINA_PLACE_KEYS = ("road", "attraction", "tourism", "historic", "amenity", "leisure", "building", "hamlet", "suburb")
_DISTRICT_KEYS = ("city_district", "district", "county")
_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True)
class _PinnedCacheParent:
    descriptor: int
    path: Path
    identity: tuple[int, int]

@dataclass(frozen=True)
class GeocodeResult:
    location: str | None
    warning: str = ""
    source: str = "network"

def _address_value(address: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = address.get(key)
        if isinstance(value, str) and (cleaned := value.strip()):
            return cleaned
    return None

def _city_display(value: str | None) -> str | None:
    return value[:-1].strip() if value and value.endswith("市") else value

def _same_location_name(left: str, right: str) -> bool:
    return " ".join(unicodedata.normalize("NFC", left).split()) == " ".join(unicodedata.normalize("NFC", right).split())

def choose_location(payload: object) -> str | None:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("address"), Mapping):
        return None
    address = payload["address"]
    raw_city = _address_value(address, _CITY_KEYS)
    is_china = str(address.get("country_code", "")).casefold() == "cn"
    if is_china:
        region = _address_value(address, ("region",))
        if region and region.endswith("市"):
            raw_city = region
    city = _city_display(raw_city)
    place = _address_value(address, _CHINA_PLACE_KEYS if is_china else _PLACE_KEYS) or _address_value(address, _DISTRICT_KEYS)
    if city and place:
        return city if _same_location_name(place, raw_city or "") or _same_location_name(place, city) else f"{city} · {place}"
    return city or place

class ReverseGeocoder:
    """Non-throwing reverse geocoder with an interprocess-locked JSON cache."""
    def __init__(self, cache_path: str | Path, opener: Callable[..., Any] = urlopen,
                 clock: Callable[[], float] | None = None, sleeper: Callable[[float], None] = time.sleep,
                 endpoint: str = DEFAULT_ENDPOINT, offline: bool = False, timeout: float = _DEFAULT_TIMEOUT,
                 wall_clock: Callable[[], float] | None = None,
                 boundary_hook: Callable[[str, Path], None] | None = None) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        self.cache_path, self.opener, self.clock, self.sleeper = Path(cache_path), opener, clock or time.monotonic, sleeper
        self.wall_clock = wall_clock or (clock if clock is not None else time.time)
        self.endpoint, self.offline, self.timeout = endpoint, offline, float(timeout)
        self.boundary_hook = boundary_hook
        self._cache_parent: _PinnedCacheParent | None = None
        self._last_request_mono: float | None = None
        self._retry_not_before_wall: float | None = None
        self._disk_last_request_wall: float | None = None
        self._entries: dict[str, dict[str, Any]] = {}
        self._cache_warning = self._persistence_warning = ""

    @staticmethod
    def cache_key(latitude: float, longitude: float) -> str:
        coordinates = _validated_coordinates(latitude, longitude)
        if coordinates is None:
            raise ValueError("Coordinates must be finite latitude/longitude values in range")
        return f"{coordinates[0]:.5f},{coordinates[1]:.5f}"

    def reverse(self, latitude: float, longitude: float) -> GeocodeResult:
        coordinates = _validated_coordinates(latitude, longitude)
        if coordinates is None:
            return GeocodeResult(None, "Invalid GPS coordinates; expected latitude/longitude in range.", "invalid")
        key = self.cache_key(*coordinates)
        with self._locked_cache() as locked:
            if not locked:
                return GeocodeResult(None, self._warnings(), "cache")
            entries, disk_last, disk_retry, migrated = self._load_cache()
            self._entries = {**self._entries, **entries}
            self._disk_last_request_wall = disk_last
            if disk_retry is not None:
                self._retry_not_before_wall = max(self._retry_not_before_wall or 0.0, disk_retry)
            if migrated:
                self._persist_current_cache()
            if (cached := self._entries.get(key)) is not None:
                return GeocodeResult(cached["location"], self._warnings(), "cache")
            if self.offline:
                return GeocodeResult(None, _join_warnings(self._warnings(), "Offline mode: location was not found in the geocoding cache."), "offline")
            if self._is_rate_limited():
                return GeocodeResult(None, _join_warnings(self._warnings(), self._rate_limited_message()), "rate_limited")
            self._wait_for_request_slot()
            self._disk_last_request_wall = self.wall_clock()
            payload, warning = self._fetch(*coordinates)
            if warning:
                self._persist_current_cache()
                return GeocodeResult(None, _join_warnings(self._warnings(), warning), "network")
            location = choose_location(payload)
            self._entries[key] = {"payload": payload, "location": location}
            self._persist_current_cache(key)
            return GeocodeResult(location, self._warnings(), "network")

    def _wait_for_request_slot(self) -> None:
        now_mono, now_wall = self.clock(), self.wall_clock()
        local = self._last_request_mono + _MIN_REQUEST_INTERVAL - now_mono if self._last_request_mono is not None else 0.0
        disk = self._disk_last_request_wall + _MIN_REQUEST_INTERVAL - now_wall if self._disk_last_request_wall is not None else 0.0
        if remaining := min(_MIN_REQUEST_INTERVAL, max(0.0, local, disk)):
            self.sleeper(remaining)
        self._last_request_mono = self.clock()

    def _is_rate_limited(self) -> bool:
        return self._retry_not_before_wall is not None and self.wall_clock() < self._retry_not_before_wall

    def _rate_limited_message(self) -> str:
        return f"Geocoding service rate limit is active; retry after {max(0.0, (self._retry_not_before_wall or 0.0) - self.wall_clock()):g} seconds."

    def _fetch(self, latitude: float, longitude: float) -> tuple[dict[str, Any] | None, str]:
        split = urlsplit(self.endpoint)
        pairs = parse_qsl(split.query, keep_blank_values=True)
        pairs.extend((("format", "jsonv2"), ("lat", str(latitude)), ("lon", str(longitude)), ("zoom", "18"), ("addressdetails", "1")))
        request = Request(urlunsplit((split.scheme, split.netloc, split.path, urlencode(pairs), split.fragment)), headers={"User-Agent": USER_AGENT})
        try:
            with self._open(request) as response:
                response_bytes = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(response_bytes) > _MAX_RESPONSE_BYTES:
                    return None, "Geocoding service response was too large; try again later."
                raw = response_bytes.decode("utf-8")
                if not _json_nesting_within_limit(raw):
                    return None, "Geocoding service returned invalid JSON; try again later."
                payload = json.loads(raw)
        except HTTPError as error:
            if error.code == 429:
                return None, self._rate_limit_warning(error)
            return None, f"Geocoding request failed with HTTP {error.code}; try again later."
        except (HTTPException, URLError, OSError) as error:
            return None, f"Geocoding network error: {getattr(error, 'reason', error)}."
        except (RecursionError, UnicodeDecodeError, json.JSONDecodeError):
            return None, "Geocoding service returned invalid JSON; try again later."
        if not isinstance(payload, dict) or ("address" in payload and not isinstance(payload["address"], dict)):
            return None, "Geocoding service returned an invalid payload; try again later."
        return payload, ""

    def _open(self, request: Request) -> Any:
        try:
            signature = inspect.signature(self.opener)
            accepts_timeout = "timeout" in signature.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
        except (TypeError, ValueError):
            accepts_timeout = True
        return self.opener(request, timeout=self.timeout) if accepts_timeout else self.opener(request)

    def _rate_limit_warning(self, error: HTTPError) -> str:
        deadline, delta = self._retry_deadline(error.headers.get("Retry-After") if error.headers else None)
        self._retry_not_before_wall = max(self._retry_not_before_wall or 0.0, deadline)
        return "Geocoding service rate-limited this request; wait at least one minute before trying again." if delta is None else f"Geocoding service rate-limited this request; retry after {delta:g} seconds."

    def _retry_deadline(self, value: object) -> tuple[float, float | None]:
        now = self.wall_clock()
        try:
            seconds = float(int(value)) if value is not None else None
        except (TypeError, ValueError, OverflowError):
            seconds = None
        if seconds is not None and seconds >= 0 and math.isfinite(seconds):
            return now + seconds, seconds
        if isinstance(value, str):
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                deadline = parsed.timestamp()
                if math.isfinite(deadline) and deadline >= now:
                    return deadline, deadline - now
            except (TypeError, ValueError, IndexError, OverflowError):
                pass
        return now + _SAFE_RETRY_FALLBACK_SECONDS, None

    @contextmanager
    def _locked_cache(self) -> Iterator[bool]:
        with _thread_lock(str(self.cache_path.absolute())):
            parent = None
            descriptor = -1
            try:
                parent = _open_pinned_cache_parent(self.cache_path.parent)
            except TypeError:
                # Test doubles and older Python shims may not expose dir_fd;
                # retain the historical locking path for those environments.
                with self._legacy_locked_cache() as legacy:
                    yield legacy
                return
            try:
                self._cache_parent = parent
                lock_name = f".{self.cache_path.name}.lock"
                warning = _unsafe_target_warning_at(parent.descriptor, lock_name)
                if warning:
                    self._cache_warning = "Geocoding cache lock is not a regular file and was left untouched."
                    self._cache_parent = None
                    os.close(parent.descriptor)
                    parent = None
                    yield False; return
                flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(lock_name, flags, 0o600, dir_fd=parent.descriptor)
            except OSError as error:
                self._cache_warning = f"Geocoding cache lock is unavailable ({error}); cache was left untouched."
                self._cache_parent = None
                if parent is not None:
                    try: os.close(parent.descriptor)
                    except OSError: pass
                    parent = None
                yield False; return
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                except OSError as error:
                    self._cache_warning = f"Geocoding cache lock is unavailable ({error}); cache was left untouched."
                    yield False; return
                yield True
            finally:
                cleanup_warnings = []
                if descriptor >= 0:
                    try: fcntl.flock(descriptor, fcntl.LOCK_UN)
                    except OSError as error: cleanup_warnings.append(f"Geocoding cache lock could not be released ({error}).")
                    try: os.close(descriptor)
                    except OSError as error: cleanup_warnings.append(f"Geocoding cache lock could not be closed ({error}).")
                if cleanup_warnings:
                    self._cache_warning = _join_warnings(self._cache_warning, *cleanup_warnings)
                self._cache_parent = None
                if parent is not None:
                    try: os.close(parent.descriptor)
                    except OSError as error: self._cache_warning = _join_warnings(self._cache_warning, f"Geocoding cache parent could not be closed ({error}).")

    @contextmanager
    def _legacy_locked_cache(self) -> Iterator[bool]:
        lock_path = self.cache_path.with_name(f".{self.cache_path.name}.lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as error:
            self._cache_warning = f"Geocoding cache lock is unavailable ({error}); cache was left untouched."
            yield False
            return
        try:
            yield True
        finally:
            try: fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError: pass
            try: os.close(descriptor)
            except OSError: pass

    def _load_cache(self) -> tuple[dict[str, dict[str, Any]], float | None, float | None, bool]:
        descriptor = -1
        opened_identity: tuple[int, int] | None = None
        expected_identity = None
        baseline_captured = False
        try:
            # Keep the historical absolute-path open here: O_NOFOLLOW and the
            # descriptor identity check make the read independent of later
            # pathname swaps.  All writes use the pinned parent descriptor.
            if self._cache_parent is not None and not _cache_parent_visible(self._cache_parent):
                raise OSError("cache parent changed before cache open")
            if self._cache_parent is not None:
                baseline_captured = True
                expected_identity = _cache_entry_identity_at(self._cache_parent.descriptor, self.cache_path.name)
            if self.boundary_hook is not None:
                self.boundary_hook("before-cache-load", self.cache_path)
            descriptor = os.open(self.cache_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            status = os.fstat(descriptor)
            actual_identity = (status.st_dev, status.st_ino, stat.S_IFMT(status.st_mode), status.st_nlink)
            if baseline_captured and expected_identity != actual_identity:
                raise OSError("cache file changed before it could be read")
            opened_identity = status.st_dev, status.st_ino
            if (not stat.S_ISREG(status.st_mode) or status.st_nlink != 1
                    or status.st_size <= 0 or status.st_size > _MAX_CACHE_BYTES):
                raise ValueError("cache exceeds size or safety limits")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                content = handle.read(_MAX_CACHE_BYTES + 1)
            if len(content) > _MAX_CACHE_BYTES:
                raise ValueError("cache exceeds size limit")
            raw = content.decode("utf-8")
            if not _json_nesting_within_limit(raw):
                raise ValueError("cache nesting exceeds limit")
            data = json.loads(raw)
            version = data.get("version") if isinstance(data, dict) else None
            if (not isinstance(data, dict) or isinstance(version, bool)
                    or version not in (1, _CACHE_VERSION) or not isinstance(data.get("entries"), dict)):
                raise ValueError("wrong schema")
            entries = data["entries"]
            if not all(_valid_cache_entry(key, value) for key, value in entries.items()):
                raise ValueError("wrong schema")
            last, retry = _finite_number_or_none(data.get("last_request_wall")), _finite_number_or_none(data.get("retry_not_before_wall"))
            if (data.get("last_request_wall") is not None and last is None) or (data.get("retry_not_before_wall") is not None and retry is None):
                raise ValueError("wrong schema")
            migrated = version == 1
            if migrated:
                entries = {
                    key: {**entry, "location": choose_location(entry["payload"])}
                    for key, entry in entries.items()
                }
            return entries, last, retry, migrated
        except FileNotFoundError:
            if expected_identity is not None:
                self._cache_warning = "Geocoding cache changed before it could be read and was left untouched."
            return {}, None, None, False
        except (OSError, RecursionError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            self._cache_warning = _unsafe_target_warning(self.cache_path) or "Geocoding cache was invalid and has been ignored."
            self._quarantine_cache(opened_identity)
            return {}, None, None, False
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _quarantine_cache(self, expected_identity: tuple[int, int] | None) -> None:
        parent = self._cache_parent
        if expected_identity is None or parent is None:
            return
        try:
            current = os.stat(self.cache_path.name, dir_fd=parent.descriptor, follow_symlinks=False)
        except OSError:
            return
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                or (current.st_dev, current.st_ino) != expected_identity):
            return
        try:
            os.replace(self.cache_path.name, f"{self.cache_path.name}.corrupt-{time.time_ns()}",
                       src_dir_fd=parent.descriptor, dst_dir_fd=parent.descriptor)
        except OSError: self._persistence_warning = "Geocoding cache could not be safely written; this result remains in memory."

    def _persist_current_cache(self, preserve_key: str | None = None) -> None:
        if self.boundary_hook is not None:
            self.boundary_hook("before-cache-persist", self.cache_path)
        self._persistence_warning = self._write_cache(preserve_key)

    def _write_cache(self, preserve_key: str | None = None) -> str:
        parent = self._cache_parent
        if parent is None:
            return self._write_cache_legacy(preserve_key)
        if parent is None or _unsafe_target_warning_at(parent.descriptor, self.cache_path.name):
            return "Geocoding cache could not be safely written; this result remains in memory."
        serialized = _bounded_cache_json(self._entries, self._disk_last_request_wall, self._retry_not_before_wall, preserve_key)
        if serialized is None:
            return "Geocoding cache entry is too large to persist; this result remains in memory."
        temp_name: str | None = None
        descriptor = -1
        try:
            descriptor, temp_name = _new_cache_temp(parent.descriptor, self.cache_path.name)
            _write_all(descriptor, serialized.encode("utf-8"))
            os.fsync(descriptor)
            os.close(descriptor); descriptor = -1
            _replace_cache_entry(parent, temp_name, self.cache_path.name)
            temp_name = None
            if warning := _fsync_directory_descriptor(parent.descriptor):
                return warning
        except OSError as error:
            if descriptor >= 0:
                try: os.close(descriptor)
                except OSError: pass
            if temp_name is not None:
                try: os.unlink(temp_name, dir_fd=parent.descriptor)
                except OSError: pass
            return f"Geocoding cache could not be written ({error}); this result remains in memory."
        return ""

    def _write_cache_legacy(self, preserve_key: str | None = None) -> str:
        if _unsafe_target_warning(self.cache_path):
            return "Geocoding cache could not be safely written; this result remains in memory."
        serialized = _bounded_cache_json(self._entries, self._disk_last_request_wall, self._retry_not_before_wall, preserve_key)
        if serialized is None:
            return "Geocoding cache entry is too large to persist; this result remains in memory."
        temp_path: Path | None = None
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=self.cache_path.parent,
                                    prefix=f".{self.cache_path.name}.", suffix=".tmp", delete=False) as temporary:
                temp_path = Path(temporary.name)
                temporary.write(serialized)
                temporary.flush(); os.fsync(temporary.fileno())
            os.replace(temp_path, self.cache_path)
            if warning := _fsync_directory(self.cache_path.parent):
                return warning
        except OSError as error:
            if temp_path is not None:
                try: temp_path.unlink(missing_ok=True)
                except OSError: pass
            return f"Geocoding cache could not be written ({error}); this result remains in memory."
        return ""

    def _warnings(self) -> str: return _join_warnings(self._cache_warning, self._persistence_warning)

def _thread_lock(key: str) -> threading.RLock:
    with _LOCKS_GUARD: return _THREAD_LOCKS.setdefault(key, threading.RLock())

def _json_nesting_within_limit(raw: str, max_depth: int = _MAX_JSON_NESTING) -> bool:
    depth, in_string, escaped = 0, False, False
    for character in raw:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > max_depth:
                return False
        elif character in "]}":
            depth -= 1
    return True

def _unsafe_target_warning(path: Path) -> str | None:
    try: mode = os.lstat(path).st_mode
    except FileNotFoundError: return None
    except OSError: return "Geocoding cache path could not be inspected and was left untouched."
    return None if stat.S_ISREG(mode) else "Geocoding cache path is not a regular file and was left untouched."


def _unsafe_target_warning_at(parent_descriptor: int, name: str) -> str | None:
    try:
        status = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        return "Geocoding cache path could not be inspected and was left untouched."
    return None if stat.S_ISREG(status.st_mode) else "Geocoding cache path is not a regular file and was left untouched."


def _cache_entry_identity_at(parent_descriptor: int, name: str) -> tuple[int, int, int, int] | None:
    try:
        status = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return status.st_dev, status.st_ino, stat.S_IFMT(status.st_mode), status.st_nlink


def _open_pinned_cache_parent(path: Path, *, create: bool = True) -> _PinnedCacheParent:
    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor = os.open(absolute.anchor, _DIRECTORY_FLAGS)
    current = Path(absolute.anchor)
    try:
        for component in absolute.parts[1:]:
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o755, dir_fd=descriptor)
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor, current = child, current / component
        status = os.fstat(descriptor)
        if not stat.S_ISDIR(status.st_mode):
            raise OSError("cache parent is not a directory")
        result = _PinnedCacheParent(descriptor, current, (status.st_dev, status.st_ino))
        descriptor = -1
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _cache_parent_visible(expected: _PinnedCacheParent) -> bool:
    actual = None
    try:
        actual = _open_pinned_cache_parent(expected.path, create=False)
        return actual.identity == expected.identity
    except OSError:
        return False
    finally:
        if actual is not None:
            os.close(actual.descriptor)


def _new_cache_temp(parent_descriptor: int, cache_name: str) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(100):
        name = f".{cache_name}.{secrets.token_hex(8)}.tmp"
        try:
            return os.open(name, flags, 0o600, dir_fd=parent_descriptor), name
        except FileExistsError:
            continue
    raise FileExistsError("unable to allocate cache temporary")


def _replace_cache_entry(parent: _PinnedCacheParent, temporary_name: str, cache_name: str) -> None:
    replace = os.replace
    try:
        parameters = inspect.signature(replace).parameters
        accepts_dir_fds = "src_dir_fd" in parameters or any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    except (TypeError, ValueError):
        accepts_dir_fds = True
    if accepts_dir_fds:
        replace(temporary_name, cache_name, src_dir_fd=parent.descriptor, dst_dir_fd=parent.descriptor)
    else:
        replace(parent.path / temporary_name, parent.path / cache_name)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("cache write made no progress")
        offset += written

def _finite_number_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None

def _valid_cache_entry(key: object, entry: object) -> bool:
    return isinstance(key, str) and isinstance(entry, dict) and isinstance(entry.get("payload"), dict) and "location" in entry and (isinstance(entry["location"], str) or entry["location"] is None)

def _bounded_cache_json(entries: Mapping[str, dict[str, Any]], last_request_wall: float | None,
                        retry_not_before_wall: float | None, preserve_key: str | None) -> str | None:
    bounded_entries = dict(entries)
    while True:
        serialized = json.dumps(
            {"version": _CACHE_VERSION, "entries": bounded_entries, "last_request_wall": last_request_wall, "retry_not_before_wall": retry_not_before_wall},
            ensure_ascii=False, separators=(",", ":"),
        )
        if len(serialized.encode("utf-8")) <= _MAX_CACHE_BYTES:
            return serialized
        eviction_key = next((key for key in bounded_entries if key != preserve_key), None)
        if eviction_key is None:
            return None
        del bounded_entries[eviction_key]

def _validated_coordinates(latitude: object, longitude: object) -> tuple[float, float] | None:
    if isinstance(latitude, bool) or isinstance(longitude, bool): return None
    try: lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError): return None
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180: return None
    return (0.0 if lat == 0 else lat), (0.0 if lon == 0 else lon)

def _fsync_directory(path: Path) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    except OSError as error:
        return f"Geocoding cache was written but its parent directory could not be fsynced ({error})."
    return ""


def _fsync_directory_descriptor(descriptor: int) -> str:
    try:
        os.fsync(descriptor)
    except OSError as error:
        return f"Geocoding cache was written but its parent directory could not be fsynced ({error})."
    return ""

def _join_warnings(*warnings: str) -> str: return " ".join(warning for warning in warnings if warning)
