"""Build the offline CEDA historical modal-price cache.

No API requests are made unless this script is run without --dry-run.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time as time_module
from typing import Any

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.data_sources import RICE_COMMODITY_ID

DATA_DIR = PROJECT_ROOT / "data"
CROP_MAP_PATH = DATA_DIR / "ceda_crop_map.final.json"
CACHE_PATH = DATA_DIR / "ceda_price_cache.csv"
META_PATH = DATA_DIR / "ceda_price_cache.meta.json"
PROGRESS_PATH = DATA_DIR / "ceda_cache_progress.json"
API_URL = "https://api.ceda.ashoka.edu.in/v1/agmarknet/prices"
WINDOW_FROM = date(2024, 11, 1)
WINDOW_TO = date(2025, 10, 30)
MAX_CALLS_PER_HOUR = 35
RATE_LIMIT_RESERVE = 3
STALE_DAYS = 45
SCRIPT_VERSION = "1.0.0"
REQUEST_TIMEOUT = 30
BETWEEN_CALLS_SECONDS = 0.25
PRICE_FIELDS = (
    "crop",
    "commodity_id",
    "matched_name",
    "proxy",
    "median_modal_rs_per_quintal",
    "mean_modal",
    "min_modal",
    "max_modal",
    "n_rows",
    "window_from",
    "window_to",
    "latest_date",
    "sparse",
    "incomplete",
    "as_of",
    "source",
    "fetched_at",
)

# Broad sanity bands are warnings only. They are not treated as unit conversion rules.
PRICE_SANITY_RANGES = {
    "cereal": (300.0, 100_000.0),
    "pulse": (300.0, 100_000.0),
    "fruit": (100.0, 200_000.0),
    "fiber": (300.0, 100_000.0),
    "beverage": (500.0, 200_000.0),
}
CROP_PRICE_GROUPS = {
    "maize": "cereal",
    "rice": "cereal",
    "mungbean": "pulse",
    "blackgram": "pulse",
    "lentil": "pulse",
    "chickpea": "pulse",
    "pigeonpeas": "pulse",
    "mothbeans": "pulse",
    "banana": "fruit",
    "mango": "fruit",
    "grapes": "fruit",
    "watermelon": "fruit",
    "muskmelon": "fruit",
    "apple": "fruit",
    "orange": "fruit",
    "papaya": "fruit",
    "pomegranate": "fruit",
    "cotton": "fiber",
    "jute": "fiber",
    "coconut": "fruit",
    "coffee": "beverage",
}


class CacheBuildError(RuntimeError):
    """Raised when an API response or local cache cannot be safely processed."""


class AuthenticationError(CacheBuildError):
    """Raised when CEDA rejects the configured credential."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_crop_map(path: Path = CROP_MAP_PATH) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CacheBuildError(f"Could not read crop map {path.name}: {exc}") from exc
    if not isinstance(payload, dict) or len(payload) != 22:
        raise CacheBuildError("Final CEDA crop map must contain exactly 22 crops.")
    for crop, item in payload.items():
        if (
            not isinstance(item, dict)
            or item.get("crop") != crop
            or not isinstance(item.get("proxy"), bool)
            or (
                item.get("commodity_id") is not None
                and (not isinstance(item["commodity_id"], int) or item["commodity_id"] <= 0)
            )
        ):
            raise CacheBuildError(f"Invalid CEDA mapping entry for {crop!r}.")
    return payload


def parse_price_rows(payload: Any, *, crop: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CacheBuildError(f"{crop}: response must be a JSON object.")
    output = payload.get("output")
    if not isinstance(output, dict) or "data" not in output:
        raise CacheBuildError(
            f"{crop}: expected CEDA's verified output.data response wrapper."
        )
    rows = output["data"]
    if not isinstance(rows, list):
        raise CacheBuildError(f"{crop}: output.data must be a list.")

    valid_prices: list[float] = []
    valid_dates: list[date] = []
    dropped_null_or_zero = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CacheBuildError(f"{crop}: output.data[{index}] must be an object.")
        if "date" not in row or "modal_price" not in row:
            raise CacheBuildError(
                f"{crop}: output.data[{index}] is missing required date/modal_price fields."
            )
        try:
            observed_date = date.fromisoformat(str(row["date"])[:10])
        except (TypeError, ValueError) as exc:
            raise CacheBuildError(
                f"{crop}: output.data[{index}].date is not YYYY-MM-DD."
            ) from exc
        raw_price = row["modal_price"]
        if raw_price is None or raw_price == "":
            dropped_null_or_zero += 1
            continue
        try:
            price = float(str(raw_price).replace(",", "").strip())
        except (TypeError, ValueError) as exc:
            raise CacheBuildError(
                f"{crop}: output.data[{index}].modal_price is not numeric."
            ) from exc
        if not math.isfinite(price):
            raise CacheBuildError(
                f"{crop}: output.data[{index}].modal_price is not finite."
            )
        if price == 0:
            dropped_null_or_zero += 1
            continue
        if price < 0:
            raise CacheBuildError(
                f"{crop}: output.data[{index}].modal_price must not be negative."
            )
        valid_prices.append(price)
        valid_dates.append(observed_date)

    if not valid_prices:
        raise CacheBuildError(f"{crop}: no nonzero modal prices returned.")

    latest_date = max(valid_dates)
    return {
        "median_modal": statistics.median(valid_prices),
        "mean_modal": statistics.fmean(valid_prices),
        "min_modal": min(valid_prices),
        "max_modal": max(valid_prices),
        "n_rows": len(valid_prices),
        "dropped_null_or_zero": dropped_null_or_zero,
        "latest_date": latest_date.isoformat(),
        "observed_from": min(valid_dates).isoformat(),
        "observed_to": max(valid_dates).isoformat(),
    }


def _parse_reset_delay(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    current = now or _utc_now()
    try:
        number = float(value)
    except ValueError:
        try:
            reset_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=timezone.utc)
        return max(0.0, (reset_at - current).total_seconds())
    if number > current.timestamp():
        return max(0.0, number - current.timestamp())
    return max(0.0, number)


def _load_progress() -> dict[str, Any]:
    try:
        payload = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"call_timestamps": [], "completed": {}, "run_log": []}
    except (OSError, json.JSONDecodeError) as exc:
        raise CacheBuildError(f"Could not load cache progress: {exc}") from exc
    if not isinstance(payload, dict):
        raise CacheBuildError("Cache progress must be a JSON object.")
    payload.setdefault("call_timestamps", [])
    payload.setdefault("completed", {})
    payload.setdefault("run_log", [])
    if not isinstance(payload["call_timestamps"], list) or not isinstance(
        payload["completed"], dict
    ):
        raise CacheBuildError("Cache progress has an invalid structure.")
    return payload


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _read_cache_rows() -> dict[str, dict[str, str]]:
    if not CACHE_PATH.exists():
        return {}
    try:
        with CACHE_PATH.open("r", encoding="utf-8", newline="") as cache_file:
            return {
                row["crop"]: row
                for row in csv.DictReader(cache_file)
                if row.get("crop")
            }
    except (OSError, KeyError, csv.Error) as exc:
        raise CacheBuildError(f"Could not read existing cache CSV: {exc}") from exc


def _write_cache_rows(rows_by_crop: dict[str, dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("w", encoding="utf-8", newline="") as cache_file:
        writer = csv.DictWriter(cache_file, fieldnames=PRICE_FIELDS)
        writer.writeheader()
        for crop in sorted(rows_by_crop):
            writer.writerow(rows_by_crop[crop])


def _active_call_times(progress: dict[str, Any], now: datetime) -> list[datetime]:
    cutoff = now - timedelta(hours=1)
    active = []
    for raw_value in progress["call_timestamps"]:
        try:
            parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed > cutoff:
            active.append(parsed)
    progress["call_timestamps"] = [_iso_utc(item) for item in active]
    return active


def _wait_for_quota(
    progress: dict[str, Any],
    *,
    remote_remaining: int | None,
    reset_header: str | None,
    sleep=time_module.sleep,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    current = now or _utc_now()
    wait_seconds: float | None = None
    reason = None
    active = _active_call_times(progress, current)
    if len(active) >= MAX_CALLS_PER_HOUR:
        oldest_allowed = min(active) + timedelta(hours=1, seconds=3)
        wait_seconds = max(0.0, (oldest_allowed - current).total_seconds())
        reason = "local rolling-hour budget"
    if remote_remaining is not None and remote_remaining <= RATE_LIMIT_RESERVE:
        reset_wait = _parse_reset_delay(reset_header, current)
        if reset_wait is None:
            return False, "CEDA quota is at or below the reserve and no valid reset time was provided."
        wait_seconds = max(wait_seconds or 0.0, reset_wait + 3)
        reason = "CEDA RateLimit-Remaining reserve"
    if wait_seconds is None:
        return True, None
    if wait_seconds <= 0:
        return True, None
    sleep(wait_seconds)
    return True, f"resumed after {reason}"


def _api_post(
    session: requests.Session,
    token: str,
    commodity_id: int,
    *,
    progress: dict[str, Any],
) -> tuple[requests.Response, dict[str, str]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    body = {
        "commodity_id": commodity_id,
        "state_id": 0,
        "from_date": WINDOW_FROM.isoformat(),
        "to_date": WINDOW_TO.isoformat(),
    }
    last_error: requests.RequestException | None = None
    for attempt in range(3):
        now = _utc_now()
        progress["call_timestamps"].append(_iso_utc(now))
        _save_json(PROGRESS_PATH, progress)
        try:
            response = session.post(
                API_URL,
                json=body,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 2:
                break
            time_module.sleep(2**attempt)
            continue
        response_headers = {
            key: value
            for key, value in response.headers.items()
            if key.casefold().startswith("ratelimit-")
        }
        if response.status_code in (401, 403):
            raise AuthenticationError(
                f"CEDA rejected the configured key (HTTP {response.status_code}); "
                "verify CEDA_API_KEY and rerun."
            )
        if response.status_code == 429:
            _save_json(PROGRESS_PATH, progress)
            allowed, wait_message = _wait_for_quota(
                progress,
                remote_remaining=0,
                reset_header=response.headers.get("RateLimit-Reset"),
            )
            if not allowed:
                raise CacheBuildError(wait_message or "CEDA rate limit reached.")
            continue
        if 500 <= response.status_code <= 599:
            if attempt == 2:
                raise CacheBuildError(
                    f"CEDA price request failed after 3 attempts (HTTP {response.status_code})."
                )
            time_module.sleep(2**attempt)
            continue
        if response.status_code != 200:
            raise CacheBuildError(
                f"CEDA price request failed (HTTP {response.status_code})."
            )
        return response, response_headers
    raise CacheBuildError(
        "CEDA request failed after 3 attempts."
        if last_error is None
        else f"CEDA request failed after 3 attempts ({type(last_error).__name__})."
    )


def _warn_sanity(crop: str, metrics: dict[str, Any]) -> list[str]:
    warnings = []
    median_value = metrics["median_modal"]
    group = CROP_PRICE_GROUPS.get(crop)
    if group:
        lower, upper = PRICE_SANITY_RANGES[group]
        if median_value < lower or median_value > upper:
            warnings.append(
                f"Median Rs/quintal value {median_value:.2f} is outside the "
                f"broad {group} sanity range {lower:.0f}-{upper:.0f}."
            )
    if metrics["min_modal"] > metrics["max_modal"]:
        warnings.append("Minimum modal price exceeds maximum modal price.")
    return warnings


def build(args: argparse.Namespace) -> int:
    crop_map = load_crop_map()
    available = {
        crop: {
            **details,
            "commodity_id": (
                RICE_COMMODITY_ID if crop == "rice" else details["commodity_id"]
            ),
        }
        for crop, details in crop_map.items()
        if details["commodity_id"] is not None
    }
    selected = set(args.only.split(",")) if args.only else set(available)
    unknown = selected - available.keys()
    if unknown:
        raise CacheBuildError(
            f"Unknown or unmapped crop names in --only: {', '.join(sorted(unknown))}."
        )
    ordered_crops = [crop for crop in available if crop in selected]
    if args.max_calls is not None and args.max_calls < 1:
        raise CacheBuildError("--max-calls must be at least 1.")
    limit = args.max_calls if args.max_calls is not None else len(ordered_crops)
    if args.dry_run:
        planned = ordered_crops[:limit]
        print(
            f"DRY RUN: {len(planned)} planned CEDA calls; "
            f"{len(ordered_crops) - len(planned)} deferred by --max-calls."
        )
        print(
            f"Window: {WINDOW_FROM.isoformat()} through {WINDOW_TO.isoformat()}, "
            "national state_id=0."
        )
        for crop in planned:
            item = available[crop]
            print(
                f"{crop}: commodity_id={item['commodity_id']}; "
                f"matched_name={item['matched_name']}; proxy={item['proxy']}"
            )
        return 0

    token = os.environ.get("CEDA_API_KEY", "").strip()
    if not token:
        raise CacheBuildError(
            "CEDA_API_KEY is missing; set it in the environment or project .env."
        )
    progress = _load_progress()
    if args.force:
        for crop in ordered_crops:
            progress["completed"].pop(crop, None)
    rows_by_crop: dict[str, dict[str, Any]] = _read_cache_rows()
    completed_this_window = set()
    for crop in ordered_crops:
        cached_result = progress["completed"].get(crop)
        if (
            not args.force
            and isinstance(cached_result, dict)
            and cached_result.get("window_from") == WINDOW_FROM.isoformat()
            and cached_result.get("window_to") == WINDOW_TO.isoformat()
            and isinstance(cached_result.get("csv_row"), dict)
        ):
            rows_by_crop[crop] = cached_result["csv_row"]
            completed_this_window.add(crop)

    todo = [crop for crop in ordered_crops if crop not in completed_this_window]
    if not todo:
        print("All requested crops already have results for this window; no calls made.")
        _write_cache_rows(rows_by_crop)
        return 0

    completed_this_run = 0
    remote_remaining: int | None = None
    reset_header: str | None = None
    last_rate_headers: dict[str, str] = {}
    stop_message: str | None = None
    with requests.Session() as session:
        for crop in todo:
            if completed_this_run >= limit:
                break
            allowed, wait_message = _wait_for_quota(
                progress,
                remote_remaining=remote_remaining,
                reset_header=reset_header,
            )
            if not allowed:
                stop_message = wait_message
                break
            if wait_message:
                print(f"Rate limit: {wait_message}.")
            details = available[crop]
            try:
                response, last_rate_headers = _api_post(
                    session,
                    token,
                    details["commodity_id"],
                    progress=progress,
                )
            except CacheBuildError as exc:
                stop_message = str(exc)
                progress["run_log"].append(
                    {"crop": crop, "status": "stopped", "message": stop_message}
                )
                _save_json(PROGRESS_PATH, progress)
                if isinstance(exc, AuthenticationError):
                    raise
                if "rate limit" in stop_message.casefold() or "quota" in stop_message.casefold():
                    break
                raise
            try:
                payload = response.json()
            except ValueError as exc:
                raise CacheBuildError(
                    f"{crop}: CEDA returned invalid JSON."
                ) from exc
            metrics = parse_price_rows(payload, crop=crop)
            latest = date.fromisoformat(metrics["latest_date"])
            fetched_at = _iso_utc()
            csv_row = {
                "crop": crop,
                "commodity_id": details["commodity_id"],
                "matched_name": details["matched_name"],
                "proxy": str(details["proxy"]).lower(),
                "median_modal_rs_per_quintal": round(metrics["median_modal"], 4),
                "mean_modal": round(metrics["mean_modal"], 4),
                "min_modal": round(metrics["min_modal"], 4),
                "max_modal": round(metrics["max_modal"], 4),
                "n_rows": metrics["n_rows"],
                "window_from": WINDOW_FROM.isoformat(),
                "window_to": WINDOW_TO.isoformat(),
                "latest_date": metrics["latest_date"],
                "sparse": str(metrics["n_rows"] < 60).lower(),
                "incomplete": str((WINDOW_TO - latest).days > STALE_DAYS).lower(),
                "as_of": metrics["latest_date"],
                "source": "CEDA Agmarknet",
                "fetched_at": fetched_at,
            }
            warnings = _warn_sanity(crop, metrics)
            entry = {
                "window_from": WINDOW_FROM.isoformat(),
                "window_to": WINDOW_TO.isoformat(),
                "csv_row": csv_row,
                "dropped_null_or_zero": metrics["dropped_null_or_zero"],
                "warnings": warnings,
                "rate_limit_headers": last_rate_headers,
            }
            progress["completed"][crop] = entry
            progress["run_log"].append(
                {
                    "crop": crop,
                    "status": "complete",
                    "n_rows": metrics["n_rows"],
                    "dropped_null_or_zero": metrics["dropped_null_or_zero"],
                    "latest_date": metrics["latest_date"],
                    "warnings": warnings,
                    "fetched_at": fetched_at,
                }
            )
            rows_by_crop[crop] = csv_row
            _write_cache_rows(rows_by_crop)
            _save_json(PROGRESS_PATH, progress)
            completed_this_run += 1
            remote_remaining = (
                int(last_rate_headers["RateLimit-Remaining"])
                if last_rate_headers.get("RateLimit-Remaining", "").isdigit()
                else None
            )
            reset_header = last_rate_headers.get("RateLimit-Reset")
            print(
                f"Completed {crop}: rows={metrics['n_rows']}; "
                f"dropped_null_or_zero={metrics['dropped_null_or_zero']}; "
                f"latest={metrics['latest_date']}."
            )
            for warning in warnings:
                print(f"WARNING {crop}: {warning}")
            time_module.sleep(BETWEEN_CALLS_SECONDS)

    run_metadata = {
        "script_version": SCRIPT_VERSION,
        "window": {
            "from_date": WINDOW_FROM.isoformat(),
            "to_date": WINDOW_TO.isoformat(),
        },
        "source": "CEDA Agmarknet",
        "state_id": 0,
        "api_call_budget_per_rolling_hour": MAX_CALLS_PER_HOUR,
        "last_rate_limit_headers": last_rate_headers,
        "run_log": progress["run_log"],
        "updated_at": _iso_utc(),
    }
    _save_json(META_PATH, run_metadata)
    remaining_crops = [
        crop for crop in ordered_crops
        if crop not in completed_this_window
        and crop not in {
            name for name, item in progress["completed"].items()
            if isinstance(item, dict)
            and item.get("window_from") == WINDOW_FROM.isoformat()
            and item.get("window_to") == WINDOW_TO.isoformat()
            and isinstance(item.get("csv_row"), dict)
        }
    ]
    if stop_message or remaining_crops:
        reset_delay = _parse_reset_delay(reset_header)
        resume_at = (
            (_utc_now() + timedelta(seconds=reset_delay)).astimezone().strftime("%H:%M %Z")
            if reset_delay is not None
            else "after the rate limit resets"
        )
        print(
            f"{len(ordered_crops) - len(remaining_crops)} crops done, "
            f"{len(remaining_crops)} remaining, rerun {resume_at}."
        )
        if stop_message:
            print(f"Stopped cleanly: {stop_message}")
    else:
        print(f"Completed all {len(ordered_crops)} requested crops.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Refetch requested crops.")
    parser.add_argument(
        "--only",
        help="Comma-separated crop names to fetch.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned calls without contacting CEDA.",
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        help="Limit the number of crops fetched in this run.",
    )
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    try:
        return build(args)
    except CacheBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
