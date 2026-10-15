"""Deterministic localhost benchmark for the crop optimization API."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, timedelta
import json
import math
from numbers import Real
import os
from pathlib import Path
import socket
import statistics
import sys
import threading
import time
from typing import Any, Iterator
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

SCRIPT_STARTED = time.perf_counter()
FIXTURE_DELAY_SECONDS = 0.0
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ["TIMING"] = "1"


class RecordedResponse:
    status = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> RecordedResponse:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class _ImmediateFuture:
    def __init__(self, function: Any, *args: Any, **kwargs: Any) -> None:
        self._value = None
        self._error = None
        try:
            self._value = function(*args, **kwargs)
        except BaseException as exc:
            self._error = exc

    def result(self) -> Any:
        if self._error is not None:
            raise self._error
        return self._value


class _NoCache:
    def get_or_load(self, _key: Any, loader: Any, *_args: Any, **_kwargs: Any) -> Any:
        return loader()

    def get_stale(self, _key: Any) -> None:
        return None

    def clear(self) -> None:
        return None


PLACES = {
    "Nizamabad": {
        "name": "Nizamabad",
        "admin1": "Telangana",
        "admin2": "Nizamabad",
        "latitude": 18.67,
        "longitude": 78.09,
    },
    "Pune": {
        "name": "Pune",
        "admin1": "Maharashtra",
        "admin2": "Pune",
        "latitude": 18.52,
        "longitude": 73.86,
    },
    "Ludhiana": {
        "name": "Ludhiana",
        "admin1": "Punjab",
        "admin2": "Ludhiana",
        "latitude": 30.90,
        "longitude": 75.85,
    },
    "Guntur": {
        "name": "Guntur",
        "admin1": "Andhra Pradesh",
        "admin2": "Guntur",
        "latitude": 16.31,
        "longitude": 80.44,
    },
    "Indore": {
        "name": "Indore",
        "admin1": "Madhya Pradesh",
        "admin2": "Indore",
        "latitude": 22.72,
        "longitude": 75.86,
    },
}
WEATHER = {
    "hourly": {
        "temperature_2m": [28.0, 30.0, 29.0],
        "relative_humidity_2m": [60.0, 80.0, 70.0],
    },
    "daily": {"precipitation_sum": [4.0, 6.0, 5.0]},
}


def _recorded_urlopen(request: Any, timeout: float | None = None) -> RecordedResponse:
    url = request if isinstance(request, str) else request.full_url
    parsed = urlparse(url)
    if parsed.hostname == "geocoding-api.open-meteo.com":
        time.sleep(FIXTURE_DELAY_SECONDS)
        location = parse_qs(parsed.query).get("name", [""])[0]
        place = PLACES.get(location)
        return RecordedResponse({
            "results": [{**place, "country_code": "IN"}] if place else [],
        })
    if parsed.hostname == "archive-api.open-meteo.com":
        time.sleep(FIXTURE_DELAY_SECONDS)
        return RecordedResponse(WEATHER)
    if parsed.hostname and "soilhealth" in parsed.hostname:
        time.sleep(FIXTURE_DELAY_SECONDS)
        raise URLError("recorded offline fixture: Soil Health Card unavailable")
    raise AssertionError(f"Unexpected outbound URL in offline benchmark: {url}")


def _recorded_soilgrid(*_: Any, **__: Any) -> Any:
    import requests

    time.sleep(FIXTURE_DELAY_SECONDS)
    raise requests.ConnectionError("recorded offline fixture: SoilGrids unavailable")


@contextmanager
def _mock_external_services(main: Any, data_sources: Any) -> Iterator[None]:
    from unittest.mock import patch

    with (
        patch.object(main.urllib.request, "urlopen", side_effect=_recorded_urlopen),
        patch.object(data_sources, "urlopen", side_effect=_recorded_urlopen),
        patch.object(data_sources.requests, "get", side_effect=_recorded_soilgrid),
        patch.object(
            main,
            "_generate_ai_reasoning",
            return_value={"status": "not_configured", "error_code": "missing_api_key"},
        ),
    ):
        yield


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    upper_weight = position - lower
    return ordered[lower] * (1 - upper_weight) + ordered[upper] * upper_weight


def _server_timing(header: str) -> dict[str, float]:
    stages = {}
    for item in header.split(","):
        name, *attributes = item.strip().split(";")
        duration = next(
            (
                attribute.removeprefix("dur=")
                for attribute in attributes
                if attribute.startswith("dur=")
            ),
            None,
        )
        if duration is not None:
            stages[name] = float(duration)
    return stages


def _responses_match(actual: Any, expected: Any, tolerance: float = 1e-9) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, Real) and isinstance(expected, Real):
        return abs(float(actual) - float(expected)) <= tolerance
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _responses_match(actual[key], expected[key], tolerance)
            for key in actual
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _responses_match(left, right, tolerance)
            for left, right in zip(actual, expected)
        )
    return actual == expected


def _start_server(app: Any) -> tuple[Any, threading.Thread, int]:
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="error",
        access_log=False,
    ))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.perf_counter() + 30
    while not server.started and thread.is_alive() and time.perf_counter() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        raise RuntimeError("The local benchmark server failed to start.")
    return server, thread, port


def run_benchmark(
    request_count: int,
    result_path: Path,
    golden_path: Path | None,
    rf_n_jobs: int | None,
    compare_golden_path: Path | None,
    simulated_provider_delay_ms: float,
    baseline_mode: bool,
) -> dict[str, Any]:
    import requests
    from backend import data_sources, main
    from backend.timing import STARTUP_TIMINGS

    global FIXTURE_DELAY_SECONDS
    FIXTURE_DELAY_SECONDS = simulated_provider_delay_ms / 1000
    original_n_jobs = main._clf.n_jobs if main._clf is not None else None
    original_warmup = main._warm_up_runtime
    original_submit = main._submit_io
    original_request_cache = main._request_data_cache
    original_source_cache = data_sources._cache
    if rf_n_jobs is not None and main._clf is not None:
        main._clf.n_jobs = rf_n_jobs
    if baseline_mode:
        if main._clf is not None:
            main._clf.n_jobs = -1
        main._warm_up_runtime = lambda: None
        main._submit_io = lambda function, *args, **kwargs: _ImmediateFuture(
            function,
            *args,
            **kwargs,
        )
        main._request_data_cache = _NoCache()
        data_sources._cache = _NoCache()
    inputs = [
        {
            "location": location,
            "budget": budget,
            "land_acres": land,
        }
        for location, budget, land in (
            ("Nizamabad", 125000, 5),
            ("Pune", 250000, 10),
            ("Ludhiana", 180000, 7.5),
            ("Guntur", 90000, 4),
            ("Indore", 300000, 12),
        )
    ]
    server = None
    captured: dict[str, dict[str, Any]] = {}
    latencies: list[float] = []
    stage_samples: dict[str, list[float]] = {}
    cold_first_stages: dict[str, float] = {}
    cold_start_ms = None
    with _mock_external_services(main, data_sources):
        expected_by_location = {}
        if compare_golden_path is not None:
            expected = json.loads(compare_golden_path.read_text(encoding="utf-8"))
            expected_by_location = {
                item["request"]["location"]: item["response"]
                for item in expected
            }
        server, thread, port = _start_server(main.app)
        try:
            for index in range(request_count):
                payload = inputs[index % len(inputs)]
                started = time.perf_counter()
                response = requests.post(
                    f"http://127.0.0.1:{port}/api/optimize",
                    json=payload,
                    timeout=60,
                )
                duration_ms = (time.perf_counter() - started) * 1000
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Offline benchmark request failed ({response.status_code}): "
                        f"{response.text[:500]}"
                    )
                if cold_start_ms is None:
                    cold_start_ms = (time.perf_counter() - SCRIPT_STARTED) * 1000
                body = response.json()
                expected_body = expected_by_location.get(payload["location"])
                if (
                    expected_body is not None
                    and not _responses_match(body, expected_body)
                ):
                    raise AssertionError(
                        f"Full response changed for {payload['location']}."
                    )
                latencies.append(duration_ms)
                for name, duration in _server_timing(
                    response.headers.get("Server-Timing", "")
                ).items():
                    stage_samples.setdefault(name, []).append(duration)
                    if index == 0:
                        cold_first_stages[name] = duration
                if payload["location"] not in captured:
                    captured[payload["location"]] = {
                        "request": payload,
                        "response": body,
                    }
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("The local benchmark server did not shut down.")
            main._warm_up_runtime = original_warmup
            main._submit_io = original_submit
            main._request_data_cache = original_request_cache
            data_sources._cache = original_source_cache
            if main._clf is not None and original_n_jobs is not None:
                main._clf.n_jobs = original_n_jobs

    stage_report = {
        name: {
            "mean_ms": round(statistics.mean(values), 3),
            "p95_ms": round(_percentile(values, 0.95), 3),
            "max_ms": round(max(values), 3),
        }
        for name, values in sorted(stage_samples.items())
    }
    result = {
        "mode": "localhost Uvicorn; external providers mocked with recorded fixtures",
        "simulated_provider_delay_ms": simulated_provider_delay_ms,
        "baseline_mode": baseline_mode,
        "request_count": request_count,
        "rf_n_jobs": (
            -1 if baseline_mode
            else rf_n_jobs if rf_n_jobs is not None
            else original_n_jobs
        ),
        "golden_response_match": (
            "within_1e-9_numeric_tolerance"
            if compare_golden_path is not None else "not_checked"
        ),
        "input_cycle": list(PLACES),
        "latency_ms": {
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
            "cold_first_request": round(latencies[0], 3),
            "warm_p50_excluding_first": round(
                _percentile(latencies[1:], 0.50), 3
            ) if len(latencies) > 1 else None,
            "warm_p95_excluding_first": round(
                _percentile(latencies[1:], 0.95), 3
            ) if len(latencies) > 1 else None,
            "cold_start_process_to_first_success": (
                round(cold_start_ms, 3)
                if cold_start_ms is not None
                else None
            ),
        },
        "request_latencies_ms": [round(value, 3) for value in latencies],
        "startup_stage_ms": {
            name: round(duration, 3)
            for name, duration in STARTUP_TIMINGS.items()
        },
        "stages": stage_report,
        "cold_first_request_stages": cold_first_stages,
        "frontend": {
            "measurement": (
                "Build chunk sizes are measured by `npm run build`; browser render and "
                "submit-to-chart times require a browser session."
            ),
        },
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if golden_path is not None:
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(
            json.dumps(list(captured.values()), indent=2) + "\n",
            encoding="utf-8",
        )
    return result


def main_cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument(
        "--rf-n-jobs",
        type=int,
        default=None,
        help="Temporarily benchmark the loaded forest with this n_jobs value.",
    )
    parser.add_argument(
        "--simulated-provider-delay-ms",
        type=float,
        default=0,
        help=(
            "Add the same local delay to each recorded outbound provider call. "
            "This is a simulation, not observed provider latency."
        ),
    )
    parser.add_argument(
        "--baseline-mode",
        action="store_true",
        help=(
            "Disable warmup, geocoding/weather caches, and parallel provider "
            "lookups to reproduce the unoptimized request path."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "bench_baseline.json",
    )
    parser.add_argument(
        "--capture-golden",
        action="store_true",
        help="Save full response fixtures for the five benchmark inputs.",
    )
    parser.add_argument(
        "--golden-output",
        type=Path,
        default=PROJECT_ROOT / "data" / "bench_golden.json",
    )
    parser.add_argument(
        "--compare-golden",
        type=Path,
        default=None,
        help="Fail if any response differs from a saved golden fixture.",
    )
    args = parser.parse_args()
    if args.requests < 1:
        parser.error("--requests must be positive")
    golden_path = (
        args.golden_output
        if args.capture_golden
        else None
    )
    result = run_benchmark(
        args.requests,
        args.output,
        golden_path,
        args.rf_n_jobs,
        args.compare_golden,
        args.simulated_provider_delay_ms,
        args.baseline_mode,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main_cli()
