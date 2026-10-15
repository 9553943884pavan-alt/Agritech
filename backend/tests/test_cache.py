from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from backend.cache import TTLCache
from backend.data_sources import _soilgrids_cache_key


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_cache_expires_entries_after_the_configured_ttl():
    clock = FakeClock()
    cache = TTLCache(max_entries=4, clock=clock)
    calls = []

    assert cache.get_or_load("weather", lambda: calls.append(1) or "first", 10) == "first"
    assert cache.get_or_load("weather", lambda: calls.append(2) or "second", 10) == "first"
    clock.advance(10)
    assert cache.get_or_load("weather", lambda: calls.append(3) or "second", 10) == "second"
    assert calls == [1, 3]


def test_expired_values_remain_available_for_an_explicit_stale_fallback():
    clock = FakeClock()
    cache = TTLCache(max_entries=4, clock=clock)
    cache.get_or_load("weather", lambda: "recorded", 5)
    clock.advance(6)

    assert cache.get_stale("weather") == "recorded"


def test_soilgrids_cache_key_rounds_coordinates_to_four_decimal_places():
    assert _soilgrids_cache_key(16.24001, 80.64001) == _soilgrids_cache_key(
        16.24004,
        80.64004,
    )


def test_concurrent_cache_misses_share_one_loader_call():
    cache = TTLCache(max_entries=4)
    loader_started = Event()
    release_loader = Event()
    calls = 0
    calls_lock = Lock()

    def loader():
        nonlocal calls
        with calls_lock:
            calls += 1
        loader_started.set()
        assert release_loader.wait(timeout=2)
        return "shared"

    with ThreadPoolExecutor(max_workers=6) as executor:
        first = executor.submit(cache.get_or_load, "same", loader, 30)
        assert loader_started.wait(timeout=2)
        rest = [
            executor.submit(cache.get_or_load, "same", loader, 30)
            for _ in range(5)
        ]
        release_loader.set()
        assert [future.result(timeout=2) for future in [first, *rest]] == [
            "shared"
        ] * 6

    assert calls == 1


def test_fallback_uses_only_a_short_negative_ttl():
    clock = FakeClock()
    cache = TTLCache(max_entries=4, clock=clock)
    calls = []
    load_fallback = lambda: calls.append("fallback") or {"fallback": True}

    is_success = lambda value: not value.get("fallback", False)
    assert cache.get_or_load(
        "soil",
        load_fallback,
        3600,
        is_success=is_success,
        negative_ttl_seconds=5,
    ) == {"fallback": True}
    assert cache.get_or_load(
        "soil",
        load_fallback,
        3600,
        is_success=is_success,
        negative_ttl_seconds=5,
    ) == {"fallback": True}
    clock.advance(5)
    cache.get_or_load(
        "soil",
        load_fallback,
        3600,
        is_success=is_success,
        negative_ttl_seconds=5,
    )
    assert calls == ["fallback", "fallback"]


def test_cache_does_not_retain_failed_loads():
    cache = TTLCache(max_entries=4)
    calls = []

    def failing_loader():
        calls.append("failed")
        raise RuntimeError("source unavailable")

    for _ in range(2):
        try:
            cache.get_or_load("failed", failing_loader, 3600)
        except RuntimeError as exc:
            assert str(exc) == "source unavailable"
        else:
            raise AssertionError("The loader exception should be raised.")
    assert calls == ["failed", "failed"]


def test_cache_size_is_bounded_and_least_recently_used_entry_is_evicted():
    cache = TTLCache(max_entries=2)
    calls = []

    def value(key):
        return lambda: calls.append(key) or key

    assert cache.get_or_load("a", value("a"), 30) == "a"
    assert cache.get_or_load("b", value("b"), 30) == "b"
    assert cache.get_or_load("a", value("a"), 30) == "a"
    assert cache.get_or_load("c", value("c"), 30) == "c"
    assert cache.get_or_load("b", value("b"), 30) == "b"
    assert calls == ["a", "b", "c", "b"]
