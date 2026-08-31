from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from photo_caption_print.geocode import (
    ReverseGeocoder,
    _CACHE_VERSION,
    _json_nesting_within_limit,
    choose_location,
)


class FakeResponse:
    def __init__(self, payload: object):
        self.payload = payload

    def read(self, size: int = -1) -> bytes:
        return json.dumps(self.payload).encode("utf-8")[:size if size >= 0 else None]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_choose_location_prefers_a_specific_place():
    assert choose_location(
        {
            "address": {
                "city": "杭州市",
                "attraction": "西湖风景区",
                "amenity": "楼外楼",
            }
        }
    ) == "杭州 · 西湖风景区"


def test_choose_location_uses_district_when_no_specific_place_exists():
    assert choose_location(
        {"address": {"city": "杭州市", "city_district": "西湖区"}}
    ) == "杭州 · 西湖区"


def test_choose_location_handles_a_plausible_nominatim_city_and_district_payload():
    assert choose_location(
        {
            "place_id": 123456,
            "licence": "Data © OpenStreetMap contributors",
            "osm_type": "relation",
            "osm_id": 1234,
            "lat": "12.3456789",
            "lon": "98.7654321",
            "display_name": "合川区, 重庆市, 中国",
            "address": {
                "city": "重庆市",
                "city_district": "合川区",
                "province": "重庆市",
                "country": "中国",
                "country_code": "cn",
            },
        }
    ) == "重庆 · 合川区"


def test_choose_location_prefers_fictional_chinese_municipality_and_road():
    assert choose_location(
        {
            "address": {
                "country_code": "cn",
                "road": "星河大道",
                "hamlet": "云栖社区",
                "suburb": "朝阳街道",
                "city": "海城区",
                "district": "新城区",
                "region": "海州市",
            }
        }
    ) == "海州 · 星河大道"


def test_choose_location_keeps_landmark_priority_outside_china():
    assert choose_location(
        {
            "address": {
                "country_code": "us",
                "city": "Example City",
                "road": "Bridge Road",
                "attraction": "Example Museum",
            }
        }
    ) == "Example City · Example Museum"


def test_choose_location_strips_city_suffix_but_preserves_place_name():
    assert choose_location(
        {"address": {"city": "苏州市", "tourism": "同里古镇市"}}
    ) == "苏州 · 同里古镇市"


def test_choose_location_suppresses_duplicate_city_and_place():
    assert choose_location(
        {"address": {"city": "杭州市", "suburb": "杭州"}}
    ) == "杭州"


def test_choose_location_normalizes_city_for_duplicate_detection_but_not_place_display():
    assert choose_location(
        {"address": {"city": "杭州市", "suburb": "杭州市"}}
    ) == "杭州"


def test_choose_location_does_not_normalize_a_nonduplicate_place_name():
    assert choose_location(
        {"address": {"city": "古镇", "tourism": "古镇市"}}
    ) == "古镇 · 古镇市"


@pytest.mark.parametrize("payload", [None, {}, {"address": []}, {"address": {}}])
def test_choose_location_rejects_invalid_or_empty_payload(payload):
    assert choose_location(payload) is None


def test_cache_key_uses_exact_five_decimal_rounding(tmp_path: Path):
    geocoder = ReverseGeocoder(tmp_path / "cache.json", opener=lambda request: FakeResponse({}))
    assert geocoder.cache_key(30.2431, 120.1502) == "30.24310,120.15020"


def test_cache_hit_avoids_second_network_request(tmp_path: Path):
    calls = []

    def opener(request):
        calls.append(request)
        return FakeResponse({"address": {"city": "杭州市", "tourism": "西湖"}})

    geocoder = ReverseGeocoder(tmp_path / "cache.json", opener=opener)
    first = geocoder.reverse(30.2431, 120.1502)
    second = geocoder.reverse(30.2431, 120.1502)

    assert first.location == second.location == "杭州 · 西湖"
    assert first.source == "network"
    assert second.source == "cache"
    assert len(calls) == 1


def test_uncached_requests_are_rate_limited(tmp_path: Path):
    now = [10.0]
    sleeps = []

    def opener(request):
        return FakeResponse({"address": {"city": "杭州市"}})

    geocoder = ReverseGeocoder(
        tmp_path / "cache.json", opener=opener, clock=lambda: now[0], sleeper=sleeps.append
    )
    geocoder.reverse(30.0, 120.0)
    now[0] = 10.25
    geocoder.reverse(30.1, 120.1)

    assert sleeps == [pytest.approx(0.75)]


def test_retry_after_rate_limit_delays_the_next_uncached_request(tmp_path: Path):
    now = [10.0]
    sleeps = []
    responses = [
        HTTPError("url", 429, "slow down", {"Retry-After": "12"}, None),
        FakeResponse({"address": {"city": "杭州市"}}),
    ]

    def opener(request):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    geocoder = ReverseGeocoder(
        tmp_path / "cache.json", opener=opener, clock=lambda: now[0], sleeper=sleeps.append
    )
    geocoder.reverse(30.0, 120.0)
    blocked = geocoder.reverse(30.1, 120.1)

    assert blocked.source == "rate_limited"
    assert sleeps == []
    assert len(responses) == 1


@pytest.mark.parametrize("retry_after", [None, "tomorrow", "-2", "120"])
def test_invalid_or_excessive_retry_after_uses_safe_fallback(tmp_path: Path, retry_after):
    now = [1_735_689_600.0]
    sleeps = []
    requests = []
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    responses = [
        HTTPError("url", 429, "slow down", headers, None),
        FakeResponse({"address": {"city": "杭州市"}}),
    ]

    def opener(request):
        requests.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    geocoder = ReverseGeocoder(
        tmp_path / "cache.json", opener=opener, clock=lambda: now[0], sleeper=sleeps.append
    )
    geocoder.reverse(30.0, 120.0)
    blocked = geocoder.reverse(30.1, 120.1)

    assert blocked.source == "rate_limited"
    assert sleeps == []
    assert len(requests) == 1
    if retry_after == "120":
        assert "120" in blocked.warning


def test_huge_numeric_retry_after_falls_back_without_raising(tmp_path: Path):
    now = [1_735_689_600.0]
    sleeps = []
    responses = [
        HTTPError("url", 429, "slow down", {"Retry-After": "9" * 10_000}, None),
        FakeResponse({"address": {"city": "杭州市"}}),
    ]

    def opener(request):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    geocoder = ReverseGeocoder(
        tmp_path / "cache.json", opener=opener, clock=lambda: now[0], sleeper=sleeps.append
    )
    assert geocoder.reverse(30, 120).location is None
    assert geocoder.reverse(30.1, 120.1).source == "rate_limited"
    assert sleeps == []


def test_http_date_retry_after_delays_the_next_request_with_injected_clock(tmp_path: Path):
    now = [1_735_689_600.0]
    sleeps = []
    requests = []
    retry_at = datetime.fromtimestamp(now[0] + 25, tz=timezone.utc)
    responses = [
        HTTPError(
            "url",
            429,
            "slow down",
            {"Retry-After": retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT")},
            None,
        ),
        FakeResponse({"address": {"city": "杭州市"}}),
    ]

    def opener(request):
        requests.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    geocoder = ReverseGeocoder(
        tmp_path / "cache.json", opener=opener, clock=lambda: now[0], sleeper=sleeps.append
    )
    geocoder.reverse(30.0, 120.0)
    blocked = geocoder.reverse(30.1, 120.1)

    assert blocked.source == "rate_limited"
    assert sleeps == []
    assert len(requests) == 1


def test_request_uses_nominatim_query_and_identifying_user_agent(tmp_path: Path):
    requests = []

    def opener(request):
        requests.append(request)
        return FakeResponse({"address": {"city": "杭州市"}})

    ReverseGeocoder(tmp_path / "cache.json", opener=opener).reverse(30.2431, 120.1502)

    request = requests[0]
    query = parse_qs(urlparse(request.full_url).query)
    assert query == {
        "format": ["jsonv2"],
        "lat": ["30.2431"],
        "lon": ["120.1502"],
        "zoom": ["18"],
        "addressdetails": ["1"],
    }
    assert request.get_header("User-agent") == (
        "photo-caption-print/0.1 (+https://github.com/chris-yihao/photo-caption-print)"
    )


def test_request_uses_the_configured_endpoint(tmp_path: Path):
    requests = []

    def opener(request):
        requests.append(request)
        return FakeResponse({"address": {"city": "杭州市"}})

    ReverseGeocoder(
        tmp_path / "cache.json", opener=opener, endpoint="https://example.test/geocode"
    ).reverse(30, 120)

    assert requests[0].full_url.startswith("https://example.test/geocode?")


def test_offline_mode_reads_cache_but_never_uses_network(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": _CACHE_VERSION,
                "entries": {
                    "30.00000,120.00000": {
                        "payload": {"address": {"city": "杭州市"}},
                        "location": "杭州",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    def opener(request):
        raise AssertionError("offline mode must not open the network")

    geocoder = ReverseGeocoder(cache_path, opener=opener, offline=True)
    assert geocoder.reverse(30.0, 120.0).source == "cache"
    result = geocoder.reverse(31.0, 121.0)
    assert result.location is None
    assert result.source == "offline"
    assert "offline" in result.warning.lower()


@pytest.mark.parametrize("lat, lon", [(91, 0), (-91, 0), (0, 181), (0, -181), (float("nan"), 0)])
def test_reverse_rejects_invalid_coordinates_without_network(tmp_path: Path, lat, lon):
    geocoder = ReverseGeocoder(
        tmp_path / "cache.json", opener=lambda request: pytest.fail("unexpected network")
    )
    result = geocoder.reverse(lat, lon)
    assert result.location is None
    assert result.source == "invalid"
    assert "coordinates" in result.warning.lower()


def test_corrupt_cache_is_quarantined_without_crashing(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("not json", encoding="utf-8")
    geocoder = ReverseGeocoder(cache_path, opener=lambda request: FakeResponse({"address": {}}))

    result = geocoder.reverse(30, 120)

    assert "cache" in result.warning.lower()
    assert list(tmp_path.glob("cache.json.corrupt-*"))


def test_wrong_schema_cache_is_quarantined_without_crashing(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps(["not", "a", "cache"]), encoding="utf-8")

    result = ReverseGeocoder(
        cache_path, opener=lambda request: FakeResponse({"address": {}})
    ).reverse(30, 120)

    assert result.location is None
    assert "cache" in result.warning.lower()
    assert list(tmp_path.glob("cache.json.corrupt-*"))


def test_v1_cache_migrates_derived_locations_without_network_or_quarantine(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "30.00000,120.00000": {
                        "payload": {"address": {"city": "杭州市", "tourism": "西湖"}},
                        "location": "stale location",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    def opener(request):
        raise AssertionError("v1 migration must not use the network")

    result = ReverseGeocoder(cache_path, opener=opener, offline=True).reverse(30.0, 120.0)

    assert result.location == "杭州 · 西湖"
    assert result.source == "cache"
    migrated = json.loads(cache_path.read_text(encoding="utf-8"))
    assert migrated["version"] == _CACHE_VERSION
    assert migrated["entries"]["30.00000,120.00000"]["location"] == "杭州 · 西湖"
    assert not list(tmp_path.glob("cache.json.corrupt-*"))


@pytest.mark.parametrize(
    "opener, expected",
    [
        (lambda request: (_ for _ in ()).throw(OSError("network down")), "network down"),
        (lambda request: (_ for _ in ()).throw(URLError("DNS unavailable")), "DNS unavailable"),
        (
            lambda request: (_ for _ in ()).throw(HTTPError("url", 500, "bad gateway", {}, None)),
            "HTTP 500",
        ),
        (
            lambda request: (_ for _ in ()).throw(HTTPError("url", 429, "slow down", {"Retry-After": "12"}, None)),
            "retry after 12",
        ),
        (lambda request: _RawResponse(b"not json"), "invalid JSON"),
        (lambda request: FakeResponse(["not", "an", "object"]), "invalid payload"),
    ],
)
def test_network_failures_return_actionable_warning(tmp_path: Path, opener, expected):
    result = ReverseGeocoder(tmp_path / "cache.json", opener=opener).reverse(30, 120)
    assert result.location is None
    assert result.source == "network"
    assert expected.lower() in result.warning.lower()


class _RawResponse:
    def __init__(self, data: bytes):
        self.data = data

    def read(self, size: int = -1) -> bytes:
        return self.data[:size if size >= 0 else None]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_atomic_cache_write_failure_keeps_in_memory_result(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    replacements = []

    def fail_replace(source, target, **kwargs):
        replacements.append((source, target, kwargs))
        raise OSError("disk full")

    monkeypatch.setattr(geocode.os, "replace", fail_replace)
    calls = []

    def opener(request):
        calls.append(request)
        return FakeResponse({"address": {"city": "杭州市"}})

    geocoder = ReverseGeocoder(tmp_path / "cache.json", opener=opener)
    result = geocoder.reverse(30, 120)
    cached = geocoder.reverse(30, 120)

    assert "cache" in result.warning.lower()
    assert cached.source == "cache"
    assert len(calls) == 1
    source, target, kwargs = replacements[-1]
    assert source.startswith(".cache.json.")
    assert target == "cache.json"
    assert kwargs["src_dir_fd"] == kwargs["dst_dir_fd"]


def test_negative_result_is_cached(tmp_path: Path):
    calls = []

    def opener(request):
        calls.append(request)
        return FakeResponse({"address": {}})

    geocoder = ReverseGeocoder(tmp_path / "cache.json", opener=opener)
    assert geocoder.reverse(30, 120).location is None
    result = geocoder.reverse(30, 120)

    assert result.location is None
    assert result.source == "cache"
    assert len(calls) == 1


def test_cache_reloads_persisted_utf8_and_negative_results_in_new_instance(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    responses = [
        FakeResponse({"address": {"city": "杭州市", "tourism": "西湖风景区"}}),
        FakeResponse({"address": {}}),
    ]

    def opener(request):
        return responses.pop(0)

    writer = ReverseGeocoder(cache_path, opener=opener)
    assert writer.reverse(30, 120).location == "杭州 · 西湖风景区"
    assert writer.reverse(31, 121).location is None
    assert "杭州" in cache_path.read_text(encoding="utf-8")

    reader = ReverseGeocoder(
        cache_path, opener=lambda request: pytest.fail("persisted cache must avoid network")
    )
    assert reader.reverse(30, 120).location == "杭州 · 西湖风景区"
    assert reader.reverse(31, 121).location is None


def test_successful_cache_write_replaces_a_sibling_temporary_file(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    cache_path = tmp_path / "cache.json"
    original_replace = geocode.os.replace
    replacements = []
    fsynced_descriptors = []

    def record_replace(source, target, **kwargs):
        replacements.append((source, target, kwargs))
        original_replace(source, target, **kwargs)

    original_fsync = geocode.os.fsync

    def record_fsync(descriptor):
        fsynced_descriptors.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(geocode.os, "replace", record_replace)
    monkeypatch.setattr(geocode.os, "fsync", record_fsync)
    ReverseGeocoder(
        cache_path, opener=lambda request: FakeResponse({"address": {"city": "杭州市"}})
    ).reverse(30, 120)

    source, target, kwargs = replacements[-1]
    assert source.startswith(".cache.json.")
    assert source.endswith(".tmp")
    assert target == cache_path.name
    assert kwargs["src_dir_fd"] == kwargs["dst_dir_fd"]
    assert fsynced_descriptors[-1] == kwargs["src_dir_fd"]
    assert cache_path.exists()


def test_truncated_cache_entry_is_quarantined_without_crashing(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {"version": _CACHE_VERSION, "entries": {"30.00000,120.00000": {"payload": {}}}}
        ),
        encoding="utf-8",
    )

    result = ReverseGeocoder(cache_path, offline=True).reverse(30, 120)

    assert result.location is None
    assert "cache" in result.warning.lower()
    assert list(tmp_path.glob("cache.json.corrupt-*"))


def test_deeply_nested_cache_is_quarantined_without_recursion_error(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    nested_payload = "[" * 2_000 + "null" + "]" * 2_000
    cache_path.write_text(
        '{"version": ' + str(_CACHE_VERSION) + ', "entries": {"30.00000,120.00000": '
        '{"payload": ' + nested_payload + ', "location": null}}}',
        encoding="utf-8",
    )

    result = ReverseGeocoder(cache_path, offline=True).reverse(30, 120)

    assert result.source == "offline"
    assert "cache" in result.warning.lower()
    assert list(tmp_path.glob("cache.json.corrupt-*"))


def test_extreme_disk_request_time_is_clamped_to_one_second(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps({"version": _CACHE_VERSION, "entries": {}, "last_request_wall": 1e100}),
        encoding="utf-8",
    )
    sleeps = []
    calls = []

    def opener(request):
        calls.append(request)
        return FakeResponse({"address": {"city": "杭州市"}})

    ReverseGeocoder(
        cache_path, opener=opener, clock=lambda: 10.0, wall_clock=lambda: 10.0,
        sleeper=sleeps.append,
    ).reverse(30, 120)

    assert len(calls) == 1
    assert sleeps == [pytest.approx(1.0)]


def test_directory_cache_path_is_left_untouched(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    cache_path.mkdir()

    result = ReverseGeocoder(cache_path, offline=True).reverse(30, 120)

    assert result.location is None
    assert "regular file" in result.warning.lower()
    assert cache_path.is_dir()
    assert not list(tmp_path.glob("cache.json.corrupt-*"))


def test_symlink_cache_path_is_left_untouched(tmp_path: Path):
    target = tmp_path / "user-data.json"
    target.write_text("keep me", encoding="utf-8")
    cache_path = tmp_path / "cache.json"
    cache_path.symlink_to(target)

    result = ReverseGeocoder(cache_path, offline=True).reverse(30, 120)

    assert "regular file" in result.warning.lower()
    assert cache_path.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep me"


def test_opener_receives_a_finite_configurable_timeout(tmp_path: Path):
    received = []

    def opener(request, *, timeout):
        received.append(timeout)
        return FakeResponse({"address": {"city": "杭州市"}})

    result = ReverseGeocoder(tmp_path / "cache.json", opener=opener, timeout=4.5).reverse(30, 120)

    assert result.location == "杭州"
    assert received == [4.5]


def test_endpoint_query_is_preserved_and_extended_safely(tmp_path: Path):
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        return FakeResponse({"address": {"city": "杭州市"}})

    ReverseGeocoder(
        tmp_path / "cache.json", opener=opener, endpoint="https://example.test/reverse?token=abc"
    ).reverse(30, 120)

    assert parse_qs(urlparse(requests[0].full_url).query)["token"] == ["abc"]
    assert requests[0].full_url.count("?") == 1


def test_cache_write_warning_persists_on_memory_hits_until_a_write_succeeds(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    cache_path = tmp_path / "cache.json"
    original_replace = geocode.os.replace
    monkeypatch.setattr(
        geocode.os,
        "replace",
        lambda source, target, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    geocoder = ReverseGeocoder(
        cache_path, opener=lambda request: FakeResponse({"address": {"city": "杭州市"}})
    )
    first = geocoder.reverse(30, 120)
    cached = geocoder.reverse(30, 120)
    monkeypatch.setattr(geocode.os, "replace", original_replace)
    persisted = geocoder.reverse(31, 121)

    assert "cache" in first.warning.lower()
    assert "cache" in cached.warning.lower()
    assert persisted.warning == ""


def test_atomic_cache_write_reports_a_pinned_parent_directory_fsync_failure(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    cache_path = tmp_path / "cache.json"
    original_replace = geocode.os.replace
    original_fsync = geocode.os.fsync
    replacement_descriptors = []

    def record_replace(source, target, **kwargs):
        replacement_descriptors.append(kwargs)
        original_replace(source, target, **kwargs)

    def fail_parent_directory_fsync(descriptor):
        if replacement_descriptors and descriptor == replacement_descriptors[-1]["src_dir_fd"]:
            raise OSError("directory sync unavailable")
        original_fsync(descriptor)

    monkeypatch.setattr(geocode.os, "replace", record_replace)
    monkeypatch.setattr(geocode.os, "fsync", fail_parent_directory_fsync)
    result = ReverseGeocoder(
        cache_path, opener=lambda request: FakeResponse({"address": {"city": "杭州市"}})
    ).reverse(30, 120)

    assert result.location == "杭州"
    assert "fsync" in result.warning.lower()
    assert replacement_descriptors[-1]["src_dir_fd"] == replacement_descriptors[-1]["dst_dir_fd"]
    assert cache_path.exists()


def test_fsync_directory_descriptor_returns_warning_for_a_failing_descriptor(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    descriptor = 123
    fsynced = []

    def fail_fsync(actual_descriptor):
        fsynced.append(actual_descriptor)
        raise OSError("directory sync unavailable")

    monkeypatch.setattr(geocode.os, "fsync", fail_fsync)

    warning = geocode._fsync_directory_descriptor(descriptor)

    assert fsynced == [descriptor]
    assert "fsync" in warning.lower()


def test_lock_acquisition_failure_returns_a_warning_without_network(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    def fail_acquire(descriptor, operation):
        if operation == geocode.fcntl.LOCK_EX:
            raise OSError("lock unavailable")

    monkeypatch.setattr(geocode.fcntl, "flock", fail_acquire)
    result = ReverseGeocoder(
        tmp_path / "cache.json", opener=lambda request: pytest.fail("must not use network without lock")
    ).reverse(30, 120)

    assert result.source == "cache"
    assert "lock" in result.warning.lower()


def test_lock_release_failure_never_escapes_reverse(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    original_flock = geocode.fcntl.flock

    def fail_release(descriptor, operation):
        if operation == geocode.fcntl.LOCK_UN:
            raise OSError("unlock unavailable")
        return original_flock(descriptor, operation)

    monkeypatch.setattr(geocode.fcntl, "flock", fail_release)
    result = ReverseGeocoder(
        tmp_path / "cache.json", opener=lambda request: FakeResponse({"address": {"city": "杭州市"}})
    ).reverse(30, 120)

    assert result.location == "杭州"


def test_truncated_http_response_returns_a_network_warning(tmp_path: Path):
    class TruncatedResponse:
        def read(self, size=-1):
            raise IncompleteRead(b'{"address":', 10)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    result = ReverseGeocoder(
        tmp_path / "cache.json", opener=lambda request: TruncatedResponse()
    ).reverse(30, 120)

    assert result.source == "network"
    assert "network" in result.warning.lower()


def test_deeply_nested_network_json_returns_an_invalid_response_warning(tmp_path: Path):
    nested_payload = "[" * 2_000 + "null" + "]" * 2_000
    result = ReverseGeocoder(
        tmp_path / "cache.json", opener=lambda request: _RawResponse(
            ('{"address": {"extra": ' + nested_payload + "}}").encode("utf-8")
        )
    ).reverse(30, 120)

    assert result.source == "network"
    assert "invalid json" in result.warning.lower()


def test_network_json_over_the_application_nesting_limit_returns_an_invalid_response_warning(tmp_path: Path):
    nested_payload = "[" * 256 + "null" + "]" * 256
    result = ReverseGeocoder(
        tmp_path / "cache.json", opener=lambda request: _RawResponse(
            ('{"address": {"extra": ' + nested_payload + "}}").encode("utf-8")
        )
    ).reverse(30, 120)

    assert result.location is None
    assert result.source == "network"
    assert "invalid json" in result.warning.lower()


def test_network_response_read_is_bounded_and_oversize_is_rejected_before_decode(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    monkeypatch.setattr(geocode, "_MAX_RESPONSE_BYTES", 64)

    class StreamingResponse:
        def __init__(self):
            self.read_sizes = []

        def read(self, size):
            self.read_sizes.append(size)
            return b"{" + b" " * size

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    response = StreamingResponse()
    result = ReverseGeocoder(tmp_path / "cache.json", opener=lambda request: response).reverse(30, 120)

    assert response.read_sizes == [65]
    assert result.location is None
    assert "large" in result.warning.lower()


def test_cache_is_read_from_one_nofollow_descriptor_when_path_is_swapped(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    cache_path = tmp_path / "cache.json"
    moved_cache = tmp_path / "opened-cache.json"
    victim = tmp_path / "valuable.json"
    victim.write_bytes(b"valuable bytes")
    cache_path.write_text(json.dumps({
        "version": _CACHE_VERSION,
        "entries": {"30.00000,120.00000": {"payload": {"address": {"city": "杭州市"}}, "location": "杭州"}},
    }), encoding="utf-8")
    original_open = geocode.os.open
    swapped = []

    def swap_after_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if Path(path) == cache_path and not swapped:
            swapped.append(True)
            cache_path.rename(moved_cache)
            cache_path.symlink_to(victim)
        return descriptor

    monkeypatch.setattr(geocode.os, "open", swap_after_open)
    result = ReverseGeocoder(cache_path, offline=True).reverse(30, 120)

    assert result.location == "杭州"
    assert result.source == "cache"
    assert cache_path.is_symlink()
    assert victim.read_bytes() == b"valuable bytes"


def test_invalid_cache_swap_is_not_quarantined_or_followed(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    cache_path = tmp_path / "cache.json"
    moved_cache = tmp_path / "opened-cache.json"
    victim = tmp_path / "valuable.json"
    cache_path.write_bytes(b"not json")
    victim.write_bytes(b"valuable bytes")
    original_open = geocode.os.open
    swapped = []

    def swap_after_open(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if Path(path) == cache_path and not swapped:
            swapped.append(True)
            cache_path.rename(moved_cache)
            cache_path.symlink_to(victim)
        return descriptor

    monkeypatch.setattr(geocode.os, "open", swap_after_open)
    result = ReverseGeocoder(cache_path, offline=True).reverse(30, 120)

    assert result.source == "offline"
    assert "cache" in result.warning.lower()
    assert cache_path.is_symlink()
    assert victim.read_bytes() == b"valuable bytes"
    assert not list(tmp_path.glob("cache.json.corrupt-*"))


def test_cache_parent_swap_during_persist_never_writes_external_victim(tmp_path: Path):
    cache_root = tmp_path / "cache-root"
    cache_root.mkdir()
    moved_root = tmp_path / "moved-cache-root"
    external_root = tmp_path / "external-root"
    external_root.mkdir()
    victim = external_root / "cache.json"
    victim.write_bytes(b"valuable bytes")
    cache_path = cache_root / "cache.json"

    def swap_parent(stage, path):
        if stage == "before-cache-persist":
            cache_root.rename(moved_root)
            cache_root.symlink_to(external_root, target_is_directory=True)

    result = ReverseGeocoder(
        cache_path,
        opener=lambda request: FakeResponse({"address": {"city": "杭州市"}}),
        boundary_hook=swap_parent,
    ).reverse(30, 120)

    assert result.location == "杭州"
    assert victim.read_bytes() == b"valuable bytes"


def test_cache_file_swap_before_open_is_not_parsed_or_quarantined(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    original_path = tmp_path / "original-cache.json"
    cache_path.write_bytes(b"original invalid cache")

    def swap_before_open(stage, path):
        if stage == "before-cache-load":
            cache_path.rename(original_path)
            cache_path.write_bytes(b"replacement invalid cache")

    result = ReverseGeocoder(cache_path, offline=True, boundary_hook=swap_before_open).reverse(30, 120)

    assert result.source == "offline"
    assert "cache" in result.warning.lower()
    assert original_path.read_bytes() == b"original invalid cache"
    assert cache_path.read_bytes() == b"replacement invalid cache"
    assert not list(tmp_path.glob("cache.json.corrupt-*"))


def test_json_nesting_guard_ignores_structural_characters_inside_escaped_strings():
    raw = '{"address":{"city":"an escaped quote \\" [ { ] } and escaped slash \\\\"}}'

    assert json.loads(raw)
    assert _json_nesting_within_limit(raw)


def test_json_nesting_guard_rejects_depth_over_128():
    raw = "[" * 129 + "null" + "]" * 129

    assert not _json_nesting_within_limit(raw)


def test_json_nesting_guard_leaves_malformed_escapes_for_the_json_decoder():
    raw = '{"address":"unterminated escape: ' + "\\"

    assert _json_nesting_within_limit(raw)
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)


def test_cache_eviction_keeps_the_current_normal_result_readable(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    monkeypatch.setattr(geocode, "_MAX_CACHE_BYTES", 4_000)
    cache_path = tmp_path / "cache.json"

    def opener(request):
        latitude = parse_qs(urlparse(request.full_url).query)["lat"][0]
        return FakeResponse({"address": {"city": "杭州市"}, "display_name": "x" * 2_500 + latitude})

    writer = ReverseGeocoder(
        cache_path, opener=opener, clock=lambda: 0.0, wall_clock=lambda: 0.0, sleeper=lambda _: None,
    )
    for latitude in range(30, 35):
        writer.reverse(latitude, 120)

    assert cache_path.stat().st_size <= geocode._MAX_CACHE_BYTES
    reader = ReverseGeocoder(cache_path, offline=True)
    retained = reader.reverse(34, 120)
    assert retained.source == "cache"
    assert retained.location == "杭州"


def test_oversized_current_entry_stays_in_memory_without_replacing_existing_cache(tmp_path: Path, monkeypatch):
    from photo_caption_print import geocode

    monkeypatch.setattr(geocode, "_MAX_CACHE_BYTES", 300)
    cache_path = tmp_path / "cache.json"
    existing = {
        "version": _CACHE_VERSION,
        "entries": {"30.00000,120.00000": {"payload": {"address": {"city": "杭州市"}}, "location": "杭州"}},
        "last_request_wall": None,
        "retry_not_before_wall": None,
    }
    cache_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    before = cache_path.read_bytes()

    result = ReverseGeocoder(
        cache_path,
        opener=lambda request: FakeResponse({"address": {"city": "苏州市"}, "display_name": "x" * 1_000}),
    ).reverse(31, 121)

    assert result.location == "苏州"
    assert "cache" in result.warning.lower()
    assert cache_path.read_bytes() == before
    assert ReverseGeocoder(cache_path, offline=True).reverse(30, 120).location == "杭州"


def test_concurrent_instances_merge_distinct_cache_entries_and_serialize_requests(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    first_started = threading.Event()
    release_first = threading.Event()
    calls = []
    sleeps = []

    def opener(request, *, timeout):
        latitude = parse_qs(urlparse(request.full_url).query)["lat"][0]
        calls.append(latitude)
        if latitude == "30.0":
            first_started.set()
            assert release_first.wait(timeout=2)
        return FakeResponse({"address": {"city": "杭州市", "tourism": f"地点{latitude}"}})

    common = {"opener": opener, "clock": lambda: 100.0, "wall_clock": lambda: 200.0, "sleeper": sleeps.append}
    one = ReverseGeocoder(cache_path, **common)
    two = ReverseGeocoder(cache_path, **common)
    results = []
    thread_one = threading.Thread(target=lambda: results.append(one.reverse(30, 120)))
    thread_two = threading.Thread(target=lambda: results.append(two.reverse(31, 121)))
    thread_one.start()
    assert first_started.wait(timeout=2)
    thread_two.start()
    release_first.set()
    thread_one.join(timeout=2)
    thread_two.join(timeout=2)

    reader = ReverseGeocoder(cache_path, offline=True)
    assert len(calls) == 2
    assert sleeps == [pytest.approx(1.0)]
    assert reader.reverse(30, 120).location == "杭州 · 地点30.0"
    assert reader.reverse(31, 121).location == "杭州 · 地点31.0"


def test_concurrent_instances_recheck_the_disk_cache_before_duplicate_request(tmp_path: Path):
    cache_path = tmp_path / "cache.json"
    first_started = threading.Event()
    release_first = threading.Event()
    second_called = threading.Event()
    calls = []

    def opener(request, *, timeout):
        calls.append(request)
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(timeout=2)
        else:
            second_called.set()
        return FakeResponse({"address": {"city": "杭州市"}})

    one = ReverseGeocoder(cache_path, opener=opener)
    two = ReverseGeocoder(cache_path, opener=opener)
    thread_one = threading.Thread(target=lambda: one.reverse(30, 120))
    thread_two = threading.Thread(target=lambda: two.reverse(30, 120))
    thread_one.start()
    assert first_started.wait(timeout=2)
    thread_two.start()
    second_called.wait(timeout=0.1)
    release_first.set()
    thread_one.join(timeout=2)
    thread_two.join(timeout=2)

    assert not thread_one.is_alive()
    assert not thread_two.is_alive()
    assert len(calls) == 1
