"""Small bounded TTL cache with in-flight request de-duplication."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from threading import Lock
import time
from collections.abc import Callable
from typing import Generic, TypeVar

Key = TypeVar("Key")
Value = TypeVar("Value")


class TTLCache(Generic[Key, Value]):
    def __init__(
        self,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive.")
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Key, tuple[float, Value]] = OrderedDict()
        self._in_flight: dict[Key, Future[Value]] = {}
        self._lock = Lock()

    def get_or_load(
        self,
        key: Key,
        loader: Callable[[], Value],
        ttl_seconds: float,
        *,
        is_success: Callable[[Value], bool] = lambda _: True,
        negative_ttl_seconds: float = 0,
    ) -> Value:
        with self._lock:
            now = self._clock()
            cached = self._entries.get(key)
            if cached is not None:
                expires_at, value = cached
                if now < expires_at:
                    self._entries.move_to_end(key)
                    return value

            future = self._in_flight.get(key)
            is_loader = future is None
            if future is None:
                future = Future()
                self._in_flight[key] = future

        if not is_loader:
            return future.result()

        try:
            value = loader()
            ttl = ttl_seconds if is_success(value) else negative_ttl_seconds
        except BaseException as exc:
            with self._lock:
                self._in_flight.pop(key, None)
                future.set_exception(exc)
            raise

        with self._lock:
            if ttl > 0:
                self._entries[key] = (self._clock() + ttl, value)
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            self._in_flight.pop(key, None)
            future.set_result(value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get_stale(self, key: Key) -> Value | None:
        with self._lock:
            cached = self._entries.get(key)
            return cached[1] if cached is not None else None
