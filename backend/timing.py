"""Opt-in request-stage and outbound HTTP timing."""

from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from typing import Any

logger = logging.getLogger(__name__)
STARTUP_TIMINGS: dict[str, float] = {}
_request_stages: ContextVar[dict[str, float] | None] = ContextVar(
    "request_stages", default=None
)
_request_deadline: ContextVar[float | None] = ContextVar(
    "request_deadline", default=None
)


def timing_enabled() -> bool:
    return os.getenv("TIMING", "").strip() == "1"


def remaining_timeout(max_seconds: float) -> float:
    deadline = _request_deadline.get()
    if deadline is None:
        return max_seconds
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("The overall request time budget was exhausted.")
    return min(max_seconds, remaining)


def requests_timeout(max_total_seconds: float, connect_seconds: float = 2) -> tuple[float, float]:
    remaining = remaining_timeout(max_total_seconds)
    connect = min(connect_seconds, remaining / 2)
    return connect, remaining - connect


def timed_call(
    stage: str,
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    if not timing_enabled():
        return function(*args, **kwargs)

    started = time.perf_counter()
    try:
        return function(*args, **kwargs)
    finally:
        stages = _request_stages.get()
        if stages is not None:
            stages[stage] = stages.get(stage, 0.0) + (
                time.perf_counter() - started
            ) * 1000


@contextmanager
def outbound_http_call(
    service: str,
    *,
    fallback_on_error: bool = False,
) -> Iterator[dict[str, Any]]:
    call: dict[str, Any] = {
        "service": service,
        "status": None,
        "timed_out": False,
        "fallback": False,
    }
    if not timing_enabled():
        yield call
        return

    started = time.perf_counter()
    try:
        yield call
    except BaseException as exc:
        raw_status = getattr(exc, "code", None)
        if raw_status is None:
            response = getattr(exc, "response", None)
            raw_status = getattr(response, "status_code", None)
        call["status"] = (
            raw_status
            if isinstance(raw_status, (int, float, str, type(None)))
            else None
        )
        call["timed_out"] = isinstance(exc, TimeoutError) or (
            exc.__class__.__name__ in {"Timeout", "ConnectTimeout", "ReadTimeout"}
        )
        call["fallback"] = fallback_on_error
        raise
    finally:
        call["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if not isinstance(call["status"], (int, float, str, type(None))):
            call["status"] = None
        logger.info("outbound_http %s", json.dumps(call, sort_keys=True))


def record_startup_timing(stage: str, started: float) -> None:
    if timing_enabled():
        duration_ms = (time.perf_counter() - started) * 1000
        STARTUP_TIMINGS[stage] = duration_ms
        logger.info("startup_timing %s", json.dumps({
            "stage": stage,
            "duration_ms": round(duration_ms, 3),
        }, sort_keys=True))


async def timing_middleware(request: Any, call_next: Callable[..., Any]) -> Any:
    if not timing_enabled():
        return await call_next(request)

    stages: dict[str, float] = {}
    token = _request_stages.set(stages)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        stages["total"] = (time.perf_counter() - started) * 1000
        response.headers["Server-Timing"] = ", ".join(
            f"{name};dur={duration:.3f}"
            for name, duration in stages.items()
        )
        logger.info("request_timing %s", json.dumps(stages, sort_keys=True))
        return response
    finally:
        _request_stages.reset(token)


async def request_budget_middleware(
    request: Any,
    call_next: Callable[..., Any],
) -> Any:
    token = _request_deadline.set(time.monotonic() + 25.0)
    try:
        return await call_next(request)
    finally:
        _request_deadline.reset(token)
