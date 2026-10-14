"""Discover and check the CEDA Agmarknet API contract, crop mapping, and prices.

Discovery uses GET requests. Price probes use the documented read-only POST only
when explicitly enabled with --allow-readonly-post.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
from calendar import monthrange
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
API_BASE = "https://api.ceda.ashoka.edu.in/v1"
DOCS_URL = "https://api.ceda.ashoka.edu.in/documentation/"
SPEC_CANDIDATES = [
    "https://api.ceda.ashoka.edu.in/documentation/json",
    "https://api.ceda.ashoka.edu.in/openapi.json",
    "https://api.ceda.ashoka.edu.in/v1/openapi.json",
]
REQUEST_TIMEOUT = 20
BETWEEN_REQUESTS_SECONDS = 0.25
MATCH_THRESHOLD = 0.82
STALE_PRICE_WARNING_DAYS = 30

CROP_CANDIDATES: dict[str, list[str]] = {
    "rice": ["Paddy (Dhan) (Common)", "Paddy (Dhan) (Basmati)", "Rice"],
    "maize": ["Maize"],
    "chickpea": ["Bengal Gram (Gram) (Whole)", "Kabuli Chana (Chickpeas-White)"],
    "kidneybeans": ["Rajma", "Other Pulses", "Beans"],
    "pigeonpeas": ["Arhar (Tur/Red Gram) (Whole)", "Red Gram"],
    "mothbeans": ["Moath Dal", "Mash"],
    "mungbean": ["Green Gram (Moong) (Whole)"],
    "blackgram": ["Black Gram (Urad Beans) (Whole)"],
    "lentil": ["Lentil (Masur) (Whole)"],
    "pomegranate": ["Pomegranate"],
    "banana": ["Banana"],
    "mango": ["Mango"],
    "grapes": ["Grapes"],
    "watermelon": ["Water Melon"],
    "muskmelon": ["Karbuja (Musk Melon)"],
    "apple": ["Apple"],
    "orange": ["Orange"],
    "papaya": ["Papaya"],
    "coconut": ["Coconut"],
    "cotton": ["Cotton"],
    "jute": ["Jute"],
    "coffee": ["Coffee"],
}

TARGET_STATES = {
    "Andhra Pradesh": ["andhra pradesh"],
    "Telangana": ["telangana"],
    "Maharashtra": ["maharashtra"],
}


class StopCheck(RuntimeError):
    """Raised when continuing would require guessing or risk repeated auth."""


class ApiClient:
    def __init__(self, token: str | None) -> None:
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.cache: dict[tuple[str, str], requests.Response] = {}
        self.auth_failures = 0

    def get(self, url: str, *, authenticated: bool) -> requests.Response:
        return self._request("GET", url, authenticated=authenticated)

    def post(
        self, url: str, body: dict[str, Any], *, authenticated: bool
    ) -> requests.Response:
        body_key = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return self._request(
            "POST",
            url,
            authenticated=authenticated,
            body=body,
            cache_suffix=body_key,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        authenticated: bool,
        body: dict[str, Any] | None = None,
        cache_suffix: str = "",
    ) -> requests.Response:
        cache_key = (method, f"{url}?{cache_suffix}")
        if cache_key in self.cache:
            return self.cache[cache_key]
        if authenticated and not self.session.headers.get("Authorization"):
            raise StopCheck("No CEDA API key is available.")
        response = None
        for attempt in range(4):
            response = self.session.request(
                method, url, json=body, timeout=REQUEST_TIMEOUT
            )
            if response.status_code != 429 and response.status_code < 500:
                break
            if attempt == 3:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                wait_seconds = float(retry_after) if retry_after else 2**attempt
            except ValueError:
                wait_seconds = 2**attempt
            time.sleep(min(max(wait_seconds, 1), 30))
        if response is None:
            raise StopCheck(f"CEDA {method} request did not return an HTTP response.")
        self.cache[cache_key] = response
        time.sleep(BETWEEN_REQUESTS_SECONDS)
        if authenticated and response.status_code in (401, 403):
            self.auth_failures += 1
            if self.auth_failures >= 2:
                raise StopCheck("Repeated 401/403 responses; stopping API calls.")
        return response


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def find_openapi_spec() -> tuple[dict[str, Any] | None, str | None, str]:
    """Try documented common spec URLs, then the spec embedded in Swagger UI."""
    with requests.Session() as session:
        for url in SPEC_CANDIDATES:
            try:
                response = session.get(url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException:
                continue
            if not response.ok:
                continue
            try:
                document = response.json()
            except ValueError:
                continue
            if isinstance(document, dict) and (
                "openapi" in document or "swagger" in document
            ):
                return document, url, "direct OpenAPI document"

        try:
            page = session.get(DOCS_URL, timeout=REQUEST_TIMEOUT)
            page.raise_for_status()
            html = page.text
            script_match = re.search(
                r'<script[^>]+src=["\']([^"\']*swagger-ui-init\.js)["\']',
                html,
                flags=re.IGNORECASE,
            )
            if not script_match:
                return None, None, "Swagger UI did not reference a discoverable spec"
            script_url = urljoin(DOCS_URL, script_match.group(1))
            script = session.get(script_url, timeout=REQUEST_TIMEOUT)
            script.raise_for_status()
            marker = re.search(r'"swaggerDoc"\s*:\s*', script.text)
            if not marker:
                return None, None, "Swagger UI initialization contained no swaggerDoc"
            document, _ = json.JSONDecoder().raw_decode(
                script.text, marker.end()
            )
            if isinstance(document, dict) and (
                "openapi" in document or "swagger" in document
            ):
                return document, script_url, "OpenAPI document embedded in Swagger UI"
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            return None, None, "Could not retrieve or parse the Swagger UI specification"
    return None, None, "No OpenAPI/Swagger specification found"


def operation_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for item in operation.get("parameters", []):
        schema = item.get("schema", {})
        found.append(
            {
                "name": item.get("name"),
                "in": item.get("in"),
                "required": bool(item.get("required")),
                "type": schema.get("type"),
                "description": item.get("description", ""),
            }
        )
    request_body = operation.get("requestBody", {})
    content = request_body.get("content", {})
    for media_type, body in content.items():
        schema = body.get("schema", {})
        properties = schema.get("properties", {})
        required_properties = set(schema.get("required", []))
        for name, prop in properties.items():
            found.append(
                {
                    "name": name,
                    "in": f"request body ({media_type})",
                    "required": name in required_properties,
                    "type": prop.get("type"),
                    "description": prop.get("description", ""),
                }
            )
    return found


def summarize_spec(spec: dict[str, Any]) -> list[dict[str, Any]]:
    print(
        f"API spec: {spec.get('info', {}).get('title', 'Unknown')} "
        f"{spec.get('info', {}).get('version', '')}".strip()
    )
    print(f"Servers: {', '.join(x.get('url', '') for x in spec.get('servers', []))}")
    schemes = spec.get("components", {}).get("securitySchemes", {})
    if schemes:
        print("Auth schemes:")
        for name, scheme in schemes.items():
            header = (
                "Authorization: Bearer <token>"
                if scheme.get("type") == "http"
                and scheme.get("scheme", "").casefold() == "bearer"
                else "see scheme definition"
            )
            print(
                f"  {name}: type={scheme.get('type')}, "
                f"scheme={scheme.get('scheme')}, "
                f"bearerFormat={scheme.get('bearerFormat')}, header={header}"
            )
    else:
        print("Auth schemes: none documented")

    operations = []
    paths = spec.get("paths", {})
    print("Documented endpoints:")
    for route, methods in paths.items():
        for method, operation in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            params = operation_parameters(operation)
            operation_info = {
                "method": method.upper(),
                "path": route,
                "summary": operation.get("summary", ""),
                "parameters": params,
                "security": operation.get("security", spec.get("security", [])),
            }
            operations.append(operation_info)
            required = [p for p in params if p["required"]]
            optional = [p for p in params if not p["required"]]
            auth = operation_info["security"]
            print(
                f"  {method.upper()} {route} - "
                f"{operation.get('summary', '(no summary)')}"
            )
            print(f"    auth: {auth or 'none documented'}")
            print(f"    required parameters: {required or 'none documented'}")
            print(f"    optional parameters: {optional or 'none documented'}")
            for code, response in operation.get("responses", {}).items():
                if code == "429":
                    print(f"    rate limit: HTTP 429 documented - {response.get('description', '')}")

    documented_429 = any(
        "429" in operation.get("responses", {})
        for methods in paths.values()
        for operation in methods.values()
        if isinstance(operation, dict)
    )
    if documented_429:
        print("Rate limits: 429 is documented; no numeric quota/window is specified.")
    else:
        print("Rate limits: no rate-limit response/policy documented.")
    return operations


def rate_headers(response: requests.Response) -> dict[str, str]:
    return {
        key: value
        for key, value in response.headers.items()
        if any(
            marker in key.casefold()
            for marker in ("rate-limit", "ratelimit", "retry-after")
        )
    }


def status_summary(response: requests.Response) -> dict[str, Any]:
    return {
        "http_status": response.status_code,
        "rate_limit_headers": rate_headers(response),
    }


def get_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise StopCheck(
            f"Expected JSON response, received HTTP {response.status_code}."
        ) from exc
    if not isinstance(payload, dict):
        raise StopCheck("API returned an unexpected non-object JSON response.")
    return payload


def extract_collection(
    payload: dict[str, Any], field_name: str
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Read either the documented shape or the observed CEDA output.data wrapper."""
    direct = payload.get(field_name)
    if isinstance(direct, list):
        return direct, "documented response field"
    output = payload.get("output")
    if isinstance(output, dict):
        data = output.get("data")
        if isinstance(data, list):
            return data, "observed output.data wrapper"
        nested = output.get(field_name)
        if isinstance(nested, list):
            return nested, "output wrapper"
    return None, None


def similarity(candidate: str, api_name: str) -> float:
    left = normalized(candidate)
    right = normalized(api_name)
    if not left or not right:
        return 0.0
    sequence_score = difflib.SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    token_score = (
        2 * len(left_tokens & right_tokens) / (len(left_tokens) + len(right_tokens))
        if left_tokens or right_tokens
        else 0.0
    )
    return max(sequence_score, token_score)


def map_crops(commodities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    api_commodities = []
    for item in commodities:
        if not isinstance(item, dict):
            continue
        commodity_id = item.get("id", item.get("commodity_id"))
        commodity_name = item.get("name", item.get("commodity_name"))
        if isinstance(commodity_id, int) and isinstance(commodity_name, str):
            api_commodities.append(
                {"id": commodity_id, "name": commodity_name}
            )
    mapping = {}
    broad_aliases = {"other pulses", "beans"}
    for crop, aliases in CROP_CANDIDATES.items():
        alias_results = []
        unique_matches: dict[int, dict[str, Any]] = {}
        for alias in aliases:
            scored = sorted(
                (
                    {
                        "commodity_id": item["id"],
                        "name": item["name"],
                        "score": round(similarity(alias, item["name"]), 4),
                    }
                    for item in api_commodities
                ),
                key=lambda item: (-item["score"], item["name"].casefold()),
            )
            matches = [item for item in scored if item["score"] >= MATCH_THRESHOLD]
            best = scored[0] if scored else None
            alias_results.append(
                {
                    "candidate_name": alias,
                    "best_match": best,
                    "matches_considered": matches,
                }
            )
            for item in matches:
                current = unique_matches.get(item["commodity_id"])
                if current is None or item["score"] > current["score"]:
                    unique_matches[item["commodity_id"]] = item

        matches = sorted(
            unique_matches.values(),
            key=lambda item: (-item["score"], item["name"].casefold()),
        )
        broad_match_only = (
            bool(matches)
            and all(normalized(item["name"]) in broad_aliases for item in matches)
        )
        if not matches:
            confidence = "not_found"
            selected = None
        elif len(matches) > 1 or broad_match_only:
            confidence = "ambiguous"
            selected = None
        elif matches[0]["score"] >= 0.95:
            confidence = "high"
            selected = matches[0]
        else:
            confidence = "medium"
            selected = matches[0]
        mapping[crop] = {
            "commodity_id": selected["commodity_id"] if selected else None,
            "matched_name": selected["name"] if selected else None,
            "confidence_flag": confidence,
            "candidates": matches,
            "candidate_evaluations": alias_results,
            "price_fallback": (
                "Resolve the commodity choice, then use a verified cache; otherwise use the labelled mock price."
                if confidence == "ambiguous"
                else "Use a verified cached quote; otherwise use the labelled mock price."
            ),
        }
    return mapping


def match_state_ids(
    geographies: list[dict[str, Any]],
) -> dict[str, int | None]:
    found = {}
    for requested, aliases in TARGET_STATES.items():
        match = next(
            (
                state
                for state in geographies
                if isinstance(state, dict)
                and normalized(
                    str(state.get("state_name", state.get("census_state_name", "")))
                ) in aliases
            ),
            None,
        )
        found[requested] = (
            match.get("state_id", match.get("census_state_id"))
            if match
            else None
        )
    return found


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def price_row_metrics(
    response: requests.Response, *, as_of_date: date
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        **status_summary(response),
        "row_count": None,
        "date_range": None,
        "latest_date": None,
        "latest_data_lag_days": None,
        "freshness_warning": False,
        "null_or_zero_modal_price_count": None,
        "min_modal_price": None,
        "median_modal_price": None,
        "max_modal_price": None,
        "unit_scale_issues": [],
        "issues": [],
    }
    if response.status_code != 200:
        metrics["issues"].append(f"HTTP {response.status_code}")
        return metrics

    try:
        payload = get_json(response)
    except StopCheck as exc:
        metrics["issues"].append(str(exc))
        return metrics
    rows, shape = extract_collection(payload, "data")
    if rows is None:
        metrics["issues"].append("No price data list in the response.")
        return metrics

    metrics["runtime_response_shape"] = shape
    metrics["row_count"] = len(rows)
    if not rows:
        metrics["null_or_zero_modal_price_count"] = 0
        metrics["issues"].append("No price rows returned for this period.")
        return metrics

    dates = sorted(
        str(row["date"])[:10]
        for row in rows
        if isinstance(row, dict) and row.get("date")
    )
    if dates:
        metrics["date_range"] = {"from": dates[0], "to": dates[-1]}
        metrics["latest_date"] = dates[-1]
        try:
            latest_date = date.fromisoformat(dates[-1])
        except ValueError:
            metrics["issues"].append(
                "Latest returned date is invalid; freshness could not be checked."
            )
        else:
            lag_days = (as_of_date - latest_date).days
            metrics["latest_data_lag_days"] = lag_days
            if lag_days > STALE_PRICE_WARNING_DAYS:
                metrics["freshness_warning"] = True
                metrics["issues"].append(
                    f"Latest returned data is {lag_days} days before the requested end date; "
                    f"older than the {STALE_PRICE_WARNING_DAYS}-day warning threshold."
                )

    parsed_prices = []
    null_or_zero_count = 0
    invalid_count = 0
    for row in rows:
        value = row.get("modal_price") if isinstance(row, dict) else None
        if value is None:
            null_or_zero_count += 1
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            invalid_count += 1
            continue
        if price == 0:
            null_or_zero_count += 1
        else:
            parsed_prices.append(price)

    metrics["null_or_zero_modal_price_count"] = null_or_zero_count
    if invalid_count:
        metrics["invalid_modal_price_count"] = invalid_count
        metrics["issues"].append(
            f"{invalid_count} modal prices were not numeric."
        )
    if parsed_prices:
        parsed_prices.sort()
        middle = len(parsed_prices) // 2
        median = (
            parsed_prices[middle]
            if len(parsed_prices) % 2
            else (parsed_prices[middle - 1] + parsed_prices[middle]) / 2
        )
        minimum = parsed_prices[0]
        maximum = parsed_prices[-1]
        metrics["min_modal_price"] = minimum
        metrics["median_modal_price"] = median
        metrics["max_modal_price"] = maximum
        if median < 50 or median > 100_000:
            metrics["unit_scale_issues"].append(
                "Median modal price is outside the broad Rs/quintal sanity band "
                "(50 to 100000); possible unit-scale issue."
            )
        if minimum > 0 and maximum / minimum >= 100:
            metrics["unit_scale_issues"].append(
                "Returned modal prices span at least 100x; inspect units/outliers."
            )
    elif not invalid_count:
        metrics["issues"].append("No nonzero modal prices returned.")
    metrics["issues"].extend(metrics["unit_scale_issues"])
    return metrics


def run_national_prices_only(api_key: str) -> int:
    map_path = DATA_DIR / "ceda_crop_map.json"
    report_path = DATA_DIR / "ceda_check_report.json"
    if not map_path.is_file() or not report_path.is_file():
        print("Cached discovery report or crop map is missing; run discovery first.")
        return 2

    mapping = json.loads(map_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if (
        report.get("auth_check", {}).get("valid_key", {}).get("http_status") != 200
        or report.get("auth_check", {})
        .get("deliberately_wrong_key", {})
        .get("rejected")
        is not True
    ):
        print("A successful-key and rejected-wrong-key check is required first.")
        return 2
    prices_operation = next(
        (
            operation
            for operation in report.get("specification", {}).get("operations", [])
            if operation.get("path") == "/agmarknet/prices"
            and operation.get("method") == "POST"
        ),
        None,
    )
    if prices_operation is None:
        print("Cached OpenAPI spec does not confirm POST /agmarknet/prices; stopping.")
        return 2
    required_fields = {
        parameter["name"]
        for parameter in prices_operation.get("parameters", [])
        if parameter.get("required")
    }
    if not {"commodity_id", "state_id", "from_date", "to_date"}.issubset(
        required_fields
    ):
        print("Cached price operation is missing required documented fields; stopping.")
        return 2
    state_description = next(
        (
            parameter.get("description", "")
            for parameter in prices_operation.get("parameters", [])
            if parameter.get("name") == "state_id"
        ),
        "",
    )
    if "use 0 for all india" not in state_description.casefold():
        print("Cached spec does not confirm state_id=0 for national prices; stopping.")
        return 2

    candidates_by_id: dict[int, list[dict[str, Any]]] = {}
    for crop, details in mapping.items():
        for candidate in details.get("candidates", []):
            commodity_id = candidate.get("commodity_id")
            if isinstance(commodity_id, int):
                candidates_by_id.setdefault(commodity_id, []).append(
                    {"crop": crop, "name": candidate.get("name")}
                )
    if not candidates_by_id:
        print("No mapped commodity IDs are available; no price requests sent.")
        return 2

    date_to = date.today()
    start_year = date_to.year - 1
    date_from = date(
        start_year,
        date_to.month,
        min(date_to.day, monthrange(start_year, date_to.month)[1]),
    )
    report["price_check"] = {
        "method": "POST",
        "requested": True,
        "scope": "national only; state tests deferred by user choice",
        "stale_warning_days": STALE_PRICE_WARNING_DAYS,
        "reason": (
            "National price probes only. Andhra Pradesh, Telangana, and Maharashtra "
            "price probes are deferred because of the documented 40/hour quota."
        ),
        "date_range": {
            "from_date": date_from.isoformat(),
            "to_date": date_to.isoformat(),
        },
        "unique_commodity_ids": len(candidates_by_id),
        "results": [],
    }
    remaining = None
    latest_geography_headers = (
        report.get("geography_check", {}).get("rate_limit_headers", {})
    )
    if latest_geography_headers.get("RateLimit-Remaining") is not None:
        remaining = int(latest_geography_headers["RateLimit-Remaining"])
    report["price_check"]["rate_limit_remaining_at_start"] = remaining
    if remaining is not None and remaining < len(candidates_by_id):
        print(
            f"Only {remaining} requests remain but {len(candidates_by_id)} are needed; "
            "no national price calls were sent."
        )
        return 2

    api_client = ApiClient(api_key)
    metrics_by_id: dict[int, dict[str, Any]] = {}
    print(
        f"Running national-only checks for {len(candidates_by_id)} distinct "
        f"commodity IDs using documented POST /agmarknet/prices."
    )
    try:
        for index, commodity_id in enumerate(sorted(candidates_by_id), start=1):
            if remaining is not None and remaining <= 0:
                print("RateLimit-Remaining reached zero; stopping before the next request.")
                break
            body = {
                "commodity_id": commodity_id,
                "state_id": 0,
                "from_date": date_from.isoformat(),
                "to_date": date_to.isoformat(),
            }
            response = api_client.post(
                f"{API_BASE}/agmarknet/prices",
                body,
                authenticated=True,
            )
            metrics = price_row_metrics(response, as_of_date=date_to)
            metrics["commodity_id"] = commodity_id
            metrics["matched_names_and_crops"] = candidates_by_id[commodity_id]
            metrics_by_id[commodity_id] = metrics
            remaining_header = metrics.get("rate_limit_headers", {}).get(
                "RateLimit-Remaining"
            )
            if remaining_header is not None:
                remaining = int(remaining_header)
            print(
                f"[{index}/{len(candidates_by_id)}] commodity_id={commodity_id} "
                f"HTTP {response.status_code}, rows={metrics.get('row_count')}, "
                f"remaining={remaining if remaining is not None else 'unknown'}"
            )
            if response.status_code in (401, 403) and api_client.auth_failures >= 2:
                break
    except (StopCheck, requests.RequestException) as exc:
        report["price_check"]["stop_reason"] = (
            f"{type(exc).__name__}: API requests stopped safely."
        )
        print(f"STOP: {report['price_check']['stop_reason']}")

    crop_results = []
    for crop, details in mapping.items():
        candidate_results = []
        for candidate in details.get("candidates", []):
            result = metrics_by_id.get(candidate["commodity_id"])
            candidate_results.append(
                {
                    "commodity_id": candidate["commodity_id"],
                    "matched_name": candidate["name"],
                    "match_score": candidate["score"],
                    "price_result": result,
                }
            )
        available_results = [
            result["price_result"]
            for result in candidate_results
            if result["price_result"] is not None
        ]
        if details.get("confidence_flag") == "ambiguous":
            status = "ambiguous_mapping"
        elif available_results and available_results[0]["http_status"] == 200:
            status = (
                "ok"
                if available_results[0]["row_count"]
                else "no_data"
            )
        elif available_results:
            status = f"http_{available_results[0]['http_status']}"
        else:
            status = "not_requested_quota_or_error"
        crop_results.append(
            {
                "crop": crop,
                "commodity_id": details.get("commodity_id"),
                "candidate_ids": [
                    item["commodity_id"] for item in details.get("candidates", [])
                ],
                "stale_price_data": [
                    item["crop"]
                    for item in crop_results
                    if any(
                        candidate["price_result"] is not None
                        and candidate["price_result"].get("freshness_warning", False)
                        for candidate in item["candidate_results"]
                    )
                ],
                "status": status,
                "national": (
                    available_results[0]
                    if len(available_results) == 1
                    else {
                        "http_status": None,
                        "rows": None,
                        "note": "Multiple plausible commodity IDs; inspect candidate_results.",
                    }
                ),
                "candidate_results": candidate_results,
                "Andhra Pradesh": {"status": "deferred_rate_limit"},
                "Telangana": {"status": "deferred_rate_limit"},
                "Maharashtra": {"status": "deferred_rate_limit"},
                "price_fallback": details.get("price_fallback"),
            }
        )
    report["price_check"]["results"] = crop_results
    report["price_check"]["rate_limit_remaining_at_end"] = remaining
    report["summary"] = {
        "fully_working": [
            item["crop"]
            for item in crop_results
            if item["status"] == "ok"
            and mapping[item["crop"]]["confidence_flag"] != "ambiguous"
            and item["national"].get("median_modal_price") is not None
            and not item["national"].get("freshness_warning", False)
        ],
        "sparse_or_missing_data": [
            item["crop"]
            for item in crop_results
            if item["status"] in {"no_data", "not_requested_quota_or_error"}
            or any(
                candidate["price_result"] is None
                or candidate["price_result"].get("http_status") != 200
                or not candidate["price_result"].get("row_count")
                for candidate in item["candidate_results"]
            )
        ],
        "not_found": [
            crop
            for crop, details in mapping.items()
            if details["confidence_flag"] == "not_found"
        ],
        "ambiguous": [
            crop
            for crop, details in mapping.items()
            if details["confidence_flag"] == "ambiguous"
        ],
        "price_tests_blocked_by_method_constraint": False,
        "state_price_tests_deferred": True,
        "recommendation": (
            "The latest returned quotes may be stale; do not treat them as current prices. "
            "For ambiguous mappings, choose the intended commodity explicitly. For stale, "
            "missing, or unavailable prices, use a cache only after verifying its freshness; "
            "otherwise use the labelled mock price."
        ),
    }
    report["assumptions"].append(
        "The national run used state_id=0 and the user-authorized documented read-only POST; "
        "state-specific price requests were deferred by user choice."
    )
    report["assumptions"] = [
        assumption
        for assumption in report["assumptions"]
        if not assumption.startswith("CEDA auth is sent as HTTP")
        and not assumption.startswith(
            "State ID 0 is the national aggregate because the prices request-body schema documents that meaning;"
        )
    ] + [
        "CEDA authentication uses an Authorization Bearer token as declared by the OpenAPI security scheme.",
        "State ID 0 is the national aggregate because the prices request-body schema documents that meaning; this value was used for the national price requests."
    ]
    report["crop_map"] = mapping
    write_report(report)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--national-only",
        action="store_true",
        help="Run national price probes using the saved verified mapping.",
    )
    parser.add_argument(
        "--allow-readonly-post",
        action="store_true",
        help="Explicitly permit the documented read-only POST /agmarknet/prices.",
    )
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("CEDA_API_KEY", "").strip()
    if args.national_only:
        if not args.allow_readonly_post:
            print("Refusing price POST without --allow-readonly-post.")
            return 2
        if not api_key:
            print("CEDA_API_KEY is not set in the environment or .env.")
            return 2
        return run_national_prices_only(api_key)

    masked_key = "********" if api_key else "(not configured)"

    spec, spec_url, spec_method = find_openapi_spec()
    if spec is None:
        print("Could not find the CEDA OpenAPI/Swagger spec; stopping without API-key calls.")
        print("Tried documented OpenAPI candidates and inspected the Swagger UI.")
        return 2

    print(f"Specification source: {spec_url} ({spec_method})")
    operations = summarize_spec(spec)
    route_methods = {
        (operation["path"], operation["method"]) for operation in operations
    }
    if ("/agmarknet/commodities", "GET") not in route_methods:
        print("The discovered spec has no GET commodities endpoint; stopping.")
        return 2

    report: dict[str, Any] = {
        "specification": {
            "url": spec_url,
            "source_method": spec_method,
            "api_title": spec.get("info", {}).get("title"),
            "api_version": spec.get("info", {}).get("version"),
            "operations": operations,
            "rate_limit_policy": (
                "HTTP 429 documented; no numeric quota/window found in the spec."
            ),
        },
        "key_configured": bool(api_key),
        "key_output": masked_key,
        "auth_check": {},
        "state_ids": {},
        "price_check": {
            "method": next(
                (
                    method
                    for path, method in route_methods
                    if path == "/agmarknet/prices"
                ),
                None,
            ),
            "requested": False,
            "reason": "",
            "date_range": {},
            "results": [],
        },
        "assumptions": [
            "CEDA auth is sent as HTTP Bearer auth because the OpenAPI security scheme declares type=http and scheme=bearer.",
            "The user-requested 12-month period means the corresponding calendar date one year before today through today, inclusive.",
            f"A freshness warning is raised for national data older than {STALE_PRICE_WARNING_DAYS} days before the requested end date; this is a conservative heuristic because the API spec documents no freshness SLA.",
            "State ID 0 is the national aggregate because the prices request-body schema documents that meaning; this API method was not called.",
            "The user states price units should be Rs per quintal; the OpenAPI response schema itself does not document a unit.",
            "Candidate-match scores are string-similarity indicators, not verified agronomic equivalence; multiple IDs are left unselected.",
        ],
        "summary": {},
    }
    api_client = ApiClient(api_key or None)
    commodity_list: list[dict[str, Any]] = []
    geography_list: list[dict[str, Any]] = []
    mapping = {
        crop: {
            "commodity_id": None,
            "matched_name": None,
            "confidence_flag": "not_checked",
            "candidates": [],
            "candidate_evaluations": [],
            "price_fallback": (
                "Use a verified cached quote; otherwise use the labelled mock price."
            ),
        }
        for crop in CROP_CANDIDATES
    }

    try:
        if not api_key:
            raise StopCheck("CEDA_API_KEY is not set in the environment or .env.")

        commodities_url = f"{API_BASE}/agmarknet/commodities"
        valid_response = api_client.get(commodities_url, authenticated=True)
        report["auth_check"]["valid_key"] = status_summary(valid_response)
        print(
            f"Auth check with configured key (masked {masked_key}): "
            f"HTTP {valid_response.status_code}; "
            f"rate-limit headers={rate_headers(valid_response) or 'none returned'}"
        )
        if valid_response.status_code in (401, 403):
            raise StopCheck("The configured key was rejected by the commodities endpoint.")
        if not valid_response.ok:
            raise StopCheck(
                f"Commodities request failed with HTTP {valid_response.status_code}."
            )

        invalid_client = ApiClient("invalid-ceda-token-for-auth-validation")
        invalid_response = invalid_client.get(
            commodities_url, authenticated=True
        )
        invalid_rejected = invalid_response.status_code in (401, 403)
        report["auth_check"]["deliberately_wrong_key"] = {
            **status_summary(invalid_response),
            "rejected": invalid_rejected,
        }
        print(
            f"Auth check with deliberately wrong key: "
            f"HTTP {invalid_response.status_code}; "
            f"rejected={invalid_rejected}; "
            f"rate-limit headers={rate_headers(invalid_response) or 'none returned'}"
        )
        if not invalid_rejected:
            raise StopCheck(
                "The deliberately wrong key was not rejected; key validation is unconfirmed."
            )

        commodity_payload = get_json(valid_response)
        raw_commodities, commodity_shape = extract_collection(
            commodity_payload, "commodities"
        )
        if not isinstance(raw_commodities, list):
            raise StopCheck("Commodities response has no documented 'commodities' list.")
        commodity_list = raw_commodities
        mapping = map_crops(commodity_list)
        save_json(DATA_DIR / "ceda_crop_map.json", mapping)
        report["runtime_response_shapes"] = {
            "commodities": commodity_shape,
        }
        if commodity_shape != "documented response field":
            report["runtime_schema_differences"] = [
                "CEDA returned commodities under output.data with commodity_id/commodity_name, "
                "rather than the documented top-level commodities list with id/name."
            ]
            print(
                "Runtime response note: commodities were wrapped in output.data "
                "with commodity_id/commodity_name; mapped using observed fields."
            )

        print(f"Mapped commodity list has {len(commodity_list)} rows.")
        print("Crop candidate matching (all supplied candidate names are listed):")
        for crop, details in mapping.items():
            if details["candidates"]:
                candidates_text = "; ".join(
                    f"{item['name']} [id={item['commodity_id']}, "
                    f"score={item['score']:.2f}]"
                    for item in details["candidates"]
                )
            else:
                candidates_text = "NOT FOUND"
            considered_text = "; ".join(
                f"{item['candidate_name']} => "
                + (
                    ", ".join(
                        f"{match['name']} (ID {match['commodity_id']}, "
                        f"{match['score']:.2f})"
                        for match in item["matches_considered"]
                    )
                    or (
                        "no match at threshold"
                        + (
                            f" (best: {item['best_match']['name']} "
                            f"{item['best_match']['score']:.2f})"
                            if item["best_match"]
                            else ""
                        )
                    )
                )
                for item in details["candidate_evaluations"]
            )
            print(
                f"  {crop}: confidence={details['confidence_flag']}; "
                f"selected={details['matched_name'] or 'none'}; "
                f"candidate matches={considered_text or candidates_text}"
            )

        if ("/agmarknet/geographies", "GET") in route_methods:
            geography_response = api_client.get(
                f"{API_BASE}/agmarknet/geographies", authenticated=True
            )
            report["geography_check"] = status_summary(geography_response)
            print(
                f"Geographies endpoint: HTTP {geography_response.status_code}; "
                f"rate-limit headers={rate_headers(geography_response) or 'none returned'}"
            )
            if geography_response.status_code in (401, 403):
                raise StopCheck("Geographies endpoint returned an auth failure.")
            if geography_response.ok:
                geo_payload = get_json(geography_response)
                raw_geographies, geography_shape = extract_collection(
                    geo_payload, "geographies"
                )
                if raw_geographies is None:
                    report["geography_check"]["error"] = (
                        "Response did not contain the documented geographies list."
                    )
                else:
                    geography_list = raw_geographies
                    report["runtime_response_shapes"]["geographies"] = geography_shape
                    if geography_shape != "documented response field":
                        report.setdefault("runtime_schema_differences", []).append(
                            "CEDA returned geographies in a wrapped response rather than "
                            "the documented top-level geographies list."
                        )
                    report["state_ids"] = match_state_ids(geography_list)
            else:
                report["geography_check"]["error"] = (
                    "State IDs could not be resolved from the documented GET request. "
                    "No undocumented parameter was guessed."
                )

        price_method = report["price_check"]["method"]
        if price_method != "GET":
            report["price_check"]["reason"] = (
                f"The OpenAPI spec documents /agmarknet/prices as {price_method or 'unavailable'}, "
                "but this task permits read-only GET requests only. No price request was sent."
            )
        elif not geography_list:
            report["price_check"]["reason"] = (
                "The GET prices endpoint is documented, but state IDs could not be resolved."
            )
        else:
            report["price_check"]["reason"] = (
                "Price-check implementation is not enabled for an undocumented GET shape."
            )

    except StopCheck as exc:
        report["stop_reason"] = str(exc)
        print(f"STOP: {exc}")
        save_json(DATA_DIR / "ceda_crop_map.json", mapping)
    except requests.RequestException as exc:
        report["stop_reason"] = (
            f"Network request failed ({type(exc).__name__}); no credential value was logged."
        )
        print(f"STOP: {report['stop_reason']}")
        save_json(DATA_DIR / "ceda_crop_map.json", mapping)

    date_to = date.today()
    start_year = date_to.year - 1
    start_day = min(date_to.day, monthrange(start_year, date_to.month)[1])
    date_from = date(start_year, date_to.month, start_day)
    report["price_check"]["date_range"] = {
        "from_date": date_from.isoformat(),
        "to_date": date_to.isoformat(),
    }
    report["price_check"]["results"] = [
        {
            "crop": crop,
            "commodity_id": details.get("commodity_id"),
            "candidate_ids": [
                item["commodity_id"] for item in details.get("candidates", [])
            ],
            "status": "not_tested_get_only",
            "national": {"http_status": None, "rows": None},
            "Andhra Pradesh": {"http_status": None, "rows": None},
            "Telangana": {"http_status": None, "rows": None},
            "Maharashtra": {"http_status": None, "rows": None},
            "issue": report["price_check"]["reason"],
        }
        for crop, details in mapping.items()
    ]
    report["crop_map"] = mapping
    report["summary"] = {
        "fully_working": [],
        "sparse_or_missing_data": [],
        "not_found": [
            crop
            for crop, details in mapping.items()
            if details["confidence_flag"] == "not_found"
        ],
        "ambiguous": [
            crop
            for crop, details in mapping.items()
            if details["confidence_flag"] == "ambiguous"
        ],
        "price_tests_blocked_by_method_constraint": True,
        "recommendation": (
            "Price availability cannot be evaluated under the GET-only constraint: "
            "the published spec documents /agmarknet/prices as POST. If a price request "
            "is later permitted, use only the documented read-only JSON query body."
        ),
    }
    write_report(report)
    return 0 if not report.get("stop_reason") else 1


def write_report(report: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_json(DATA_DIR / "ceda_check_report.json", report)
    mapping = report.get("crop_map", {})
    state_ids = report.get("state_ids", {})
    price_reason = report.get("price_check", {}).get("reason", "Not tested.")
    price_results = {
        item["crop"]: item
        for item in report.get("price_check", {}).get("results", [])
        if isinstance(item, dict) and item.get("crop")
    }
    prices_requested = report.get("price_check", {}).get("requested", False)
    all_price_metrics = [
        candidate.get("price_result")
        for crop_result in report.get("price_check", {}).get("results", [])
        for candidate in crop_result.get("candidate_results", [])
        if candidate.get("price_result") is not None
    ]
    rate_headers = (
        all_price_metrics[0].get("rate_limit_headers", {})
        if all_price_metrics
        else {}
    )
    rate_limit_note = ""
    if rate_headers:
        rate_limit_note = (
            f"- Observed price-call rate headers: "
            f"`RateLimit-Policy={rate_headers.get('RateLimit-Policy', 'not returned')}`, "
            f"remaining {report.get('price_check', {}).get('rate_limit_remaining_at_start', 'unknown')} "
            f"at start and {report.get('price_check', {}).get('rate_limit_remaining_at_end', 'unknown')} at end."
        )

    def display_price(value: Any) -> str:
        if isinstance(value, (int, float)):
            return f"{value:.2f}"
        return str(value) if value is not None else "—"

    if prices_requested:
        price_method_note = (
            "National price data was requested with the documented read-only POST. "
            "State price checks were deferred."
        )
    else:
        price_method_note = (
            "No price query was sent: the spec documents /agmarknet/prices as POST, "
            "and no explicit permission was supplied to use it."
        )
    lines = [
        "# CEDA Agri Market API Check",
        "",
        f"- Specification: `{report.get('specification', {}).get('url', 'unavailable')}`",
        f"- Auth with configured key: `{report.get('auth_check', {}).get('valid_key', {}).get('http_status', 'not checked')}`",
        f"- Deliberately wrong key: HTTP `{report.get('auth_check', {}).get('deliberately_wrong_key', {}).get('http_status', 'not checked')}` "
        f"(rejected: `{report.get('auth_check', {}).get('deliberately_wrong_key', {}).get('rejected', 'not checked')}`).",
        f"- State IDs: `{json.dumps(state_ids, ensure_ascii=True)}`",
        f"- Price tests: {'requested' if prices_requested else 'not run'}. {price_reason}",
        (
            f"- Requested price window: `{report.get('price_check', {}).get('date_range', {}).get('from_date', 'unknown')}` "
            f"through `{report.get('price_check', {}).get('date_range', {}).get('to_date', 'unknown')}`."
            if prices_requested
            else ""
        ),
        rate_limit_note,
        "",
        price_method_note,
        "",
        "| Crop | Commodity ID | Match / candidates | Confidence | National OK | AP OK | TG OK | MH OK | Rows | Latest date | Median modal price (unit unverified) | Issues |",
        "|---|---:|---|---|---|---|---|---|---:|---|---:|---|",
    ]
    for crop, details in mapping.items():
        candidates = details.get("candidates", [])
        candidate_text = ", ".join(
            f"{item.get('name')} (ID {item.get('commodity_id')})"
            for item in candidates
        ) or "NOT FOUND"
        commodity_id = details.get("commodity_id")
        issues = []
        if details.get("confidence_flag") == "ambiguous":
            issues.append("ambiguous; user decision needed")
        if details.get("confidence_flag") == "not_found":
            issues.append("commodity not found")
        if details.get("confidence_flag") == "not_checked":
            issues.append("mapping not checked")
        result = price_results.get(crop, {})
        national = result.get("national", {})
        candidates_results = result.get("candidate_results", [])
        candidate_metrics = [
            (candidate, candidate.get("price_result"))
            for candidate in candidates_results
        ]
        if details.get("confidence_flag") == "ambiguous":
            national_cell = "AMBIGUOUS; inspect candidate IDs"
            candidate_issues = [
                f"ID {candidate.get('commodity_id')}: {issue}"
                for candidate, metric in candidate_metrics
                if metric is not None
                for issue in metric.get("issues", [])
            ]
            issues.extend(candidate_issues)
        elif not prices_requested:
            national_cell = "NOT TESTED"
            if not issues:
                issues.append("price requests not run")
        elif not national or national.get("http_status") is None:
            national_cell = "NOT TESTED"
            issues.append("quota exhausted or request stopped before this crop")
        else:
            status_code = national.get("http_status")
            row_count = national.get("row_count", national.get("rows"))
            national_cell = (
                "YES"
                if status_code == 200 and row_count
                else "NO DATA"
                if status_code == 200
                else f"HTTP {status_code}"
            )
            issues.extend(national.get("issues", []))
        if details.get("confidence_flag") == "ambiguous" and not prices_requested:
            national_cell = "NOT TESTED (ambiguous)"
        if result and result.get("status") == "not_requested_quota_or_error":
            issues.append("national request not completed")
        latest = national.get("latest_date", "—")
        median = national.get("median_modal_price", "—")
        rows = national.get("row_count", national.get("rows", "—"))
        if details.get("confidence_flag") == "ambiguous":
            rows = "; ".join(
                f"{candidate.get('commodity_id')}: "
                f"{metric.get('row_count', 'not tested') if metric else 'not tested'}"
                for candidate, metric in candidate_metrics
            ) or "not tested"
            latest = "; ".join(
                f"{candidate.get('commodity_id')}: "
                f"{metric.get('latest_date') or 'no rows'}"
                for candidate, metric in candidate_metrics
                if metric is not None
            ) or "not tested"
            median = "; ".join(
                f"{candidate.get('commodity_id')}: "
                f"{display_price(metric.get('median_modal_price')) if metric.get('median_modal_price') is not None else 'no price'}"
                for candidate, metric in candidate_metrics
                if metric is not None
            ) or "not tested"
        else:
            median = display_price(median)
        ap_status = result.get("Andhra Pradesh", {}).get(
            "status", "NOT TESTED" if not prices_requested else "DEFERRED"
        )
        tg_status = result.get("Telangana", {}).get(
            "status", "NOT TESTED" if not prices_requested else "DEFERRED"
        )
        mh_status = result.get("Maharashtra", {}).get(
            "status", "NOT TESTED" if not prices_requested else "DEFERRED"
        )
        id_text = commodity_id if commodity_id is not None else "—"
        lines.append(
            f"| {crop} | {id_text} | {candidate_text} | "
            f"{details.get('confidence_flag', 'not_checked')} | "
            f"{national_cell} | {ap_status} | {tg_status} | {mh_status} | "
            f"{rows} | {latest} | {median} | "
            + ("; ".join(issues) if issues else "—")
            + " |"
        )
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Crops fully working: {len(report.get('summary', {}).get('fully_working', []))}.",
            "- Crops with sparse or missing price data: "
            + (
                ", ".join(
                    report.get("summary", {}).get("sparse_or_missing_data", [])
                )
                or "none identified"
            )
            + ".",
            "- Crops with stale returned prices: "
            + (
                ", ".join(
                    report.get("summary", {}).get("stale_price_data", [])
                )
                or "none identified"
            )
            + f" (warning threshold: {report.get('price_check', {}).get('stale_warning_days', STALE_PRICE_WARNING_DAYS)} days).",
            "- Crops not found: "
            + (
                ", ".join(report.get("summary", {}).get("not_found", []))
                or "none"
            )
            + ".",
            "- Ambiguous mappings requiring a user decision: "
            + (
                ", ".join(report.get("summary", {}).get("ambiguous", []))
                or "none"
            )
            + ".",
            "- Do not treat stale CEDA results as current market prices. Use a cached quote only after verifying its freshness; otherwise use the project’s clearly labelled mock price.",
            "- Per-crop fallback recommendations are recorded in `ceda_crop_map.json` and `ceda_check_report.json`.",
        ]
    )
    assumptions = report.get("assumptions", [])
    if assumptions:
        lines.extend(["", "## Assumptions", ""])
        lines.extend(f"- {assumption}" for assumption in assumptions)
    if report.get("stop_reason"):
        lines.extend(["", f"## Stopped", "", report["stop_reason"]])
    (DATA_DIR / "ceda_check_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    sys.exit(main())
