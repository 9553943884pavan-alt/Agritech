"""Public Soil Health Card, Mandi API, and CEDA price integrations."""

from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date
import json
import math
import re
import time
import unicodedata
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import requests

SOIL_GRAPHQL_URL = "https://soilhealth4.dac.gov.in/"
SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
MANDI_MAPPING_PATH = (
    Path(__file__).resolve().parents[1] / "universal_data" / "mandi_api_mapping.json"
)
MOCK_PRICES_PATH = Path(__file__).resolve().parents[1] / "universal_data" / "crop_prices.json"
MOCK_SOIL_PATH = Path(__file__).resolve().parents[1] / "universal_data" / "district_soil_data.json"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CEDA_CROP_MAP_PATH = DATA_DIR / "ceda_crop_map.final.json"
CEDA_PRICE_CACHE_PATH = DATA_DIR / "ceda_price_cache.csv"

# The yield source identifies the crop only as "rice" and does not distinguish
# paddy from milled rice. Keep the requested paddy default; switch to 3 (Rice)
# only after confirming the yield-data basis and unit consistency.
RICE_COMMODITY_ID = 2

# TGK's feed response has no unit metadata. Do not enable TGK prices until its
# owner documents that modal_price is Rs/quintal; the feed returned 11 exact
# commodity-name matches, but none has a verified price unit.
TGK_ELIGIBLE_CROPS = frozenset()

_CACHE_SECONDS = 3600
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = Lock()
_mandi_request_lock = Lock()
_mandi_rate_limit_lock = Lock()
_mandi_retry_after = 0.0
_ceda_price_cache: dict[str, dict[str, Any]] = {}
_ceda_price_cache_loaded = False


class DataSourceError(RuntimeError):
    """Raised when a public data source cannot provide a valid response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def normalize_name(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    ascii_value = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(char for char in ascii_value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()).strip()


def normalize_district(value: str) -> str:
    return normalize_name(value).removesuffix(" district")


def _get_mandi_json(endpoint: str, params: dict[str, str]) -> dict[str, Any]:
    global _mandi_retry_after
    with _mandi_request_lock:
        with _mandi_rate_limit_lock:
            if time.monotonic() < _mandi_retry_after:
                raise DataSourceError(
                    "Mandi API rate limit reached; waiting before retrying.",
                    status_code=429,
                )
        try:
            response = requests.get(
                endpoint,
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "AgriTech Crop Optimizer/1.0",
                },
                timeout=4,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 429:
                with _mandi_rate_limit_lock:
                    _mandi_retry_after = time.monotonic() + 60
            raise DataSourceError(
                f"Could not retrieve Mandi API data: {exc}",
                status_code=status_code,
            ) from exc
        except (requests.RequestException, ValueError) as exc:
            raise DataSourceError(f"Could not retrieve Mandi API data: {exc}") from exc

    if (
        not isinstance(payload, dict)
        or payload.get("success") is not True
        or not isinstance(payload.get("data"), list)
    ):
        raise DataSourceError("Mandi API returned an invalid response.")
    return payload


def _post_json(url: str, payload: dict[str, Any], origin: str) -> Any:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AgriTech Crop Optimizer/1.0",
            "Origin": origin,
            "Referer": f"{origin}/",
        },
        method="POST",
    )
    return _read_json(request, timeout_seconds=5)


def _read_json(request: Request, timeout_seconds: float = 20) -> Any:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise DataSourceError(f"Could not retrieve data from {request.full_url}: {exc}") from exc


def _cached(
    key: str,
    loader: Any,
    ttl_seconds: int = _CACHE_SECONDS,
) -> Any:
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < ttl_seconds:
            return cached[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def _soil_graphql(query: str, variables: dict[str, Any] | None = None) -> Any:
    response = _post_json(
        SOIL_GRAPHQL_URL,
        {"query": query, "variables": variables or {}},
        "https://soilhealth.dac.gov.in",
    )
    if "errors" in response:
        raise DataSourceError(f"Soil Health Card GraphQL error: {response['errors']}")
    if "data" not in response:
        raise DataSourceError("Soil Health Card returned no data.")
    return response["data"]


def get_soil_health_summary(state_name: str, district_name: str) -> dict[str, Any] | None:
    states = _cached("soil-states", lambda: _soil_graphql("query { getState }")["getState"])
    state_key = normalize_name(state_name)
    state = next(
        (item for item in states if normalize_name(item.get("name", "")) == state_key),
        None,
    )
    if state is None:
        return None

    districts = _cached(
        f"soil-districts:{state['_id']}",
        lambda: _soil_graphql(
            "query Districts($state: ID) { getdistrictAndSubdistrictBystate(state: $state) }",
            {"state": state["_id"]},
        )["getdistrictAndSubdistrictBystate"],
    )
    district_key = normalize_district(district_name)
    district = next(
        (
            item
            for item in districts
            if normalize_district(item.get("name", "")) == district_key
        ),
        None,
    )
    if district is None:
        return None

    records = _cached(
        f"soil-summary:{state['_id']}:{district['_id']}",
        lambda: _soil_graphql(
            "query Nutrients($state: ID, $district: ID) { "
            "getNutrientDashboardForPortal(state: $state, district: $district) }",
            {"state": state["_id"], "district": district["_id"]},
        )["getNutrientDashboardForPortal"],
    )
    if not records:
        return None

    latest_cycle = max((record.get("cycle", "") for record in records), default="")
    current_records = [record for record in records if record.get("cycle", "") == latest_cycle]
    category_counts: dict[str, dict[str, int]] = {}
    for nutrient in ("n", "p", "k", "pH"):
        counts: dict[str, int] = {}
        for record in current_records:
            for category, count in record.get("results", {}).get(nutrient, {}).items():
                counts[category] = counts.get(category, 0) + int(count)
        if counts:
            category_counts[nutrient] = counts

    return {
        "source": "Soil Health Card Portal",
        "state": state.get("name", state_name),
        "district": district.get("name", district_name),
        "cycle": latest_cycle or None,
        "category_counts": category_counts,
        "note": "District/block sample counts by category; these are not exact numeric soil-test values.",
    }


def _get_soilgrids_values(latitude: float, longitude: float) -> dict[str, Any]:
    try:
        response = requests.get(
            SOILGRIDS_URL,
            params=[
                ("lon", longitude),
                ("lat", latitude),
                ("property", "nitrogen"),
                ("property", "phh2o"),
                ("depth", "0-5cm"),
                ("value", "mean"),
            ],
            headers={"Accept": "application/json"},
            timeout=4,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise DataSourceError(f"Could not retrieve SoilGrids estimates: {exc}") from exc
    if not isinstance(payload, dict):
        raise DataSourceError("SoilGrids returned an invalid response.")

    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return {}
    layers = properties.get("layers", [])
    if not isinstance(layers, list):
        return {}
    values: dict[str, Any] = {}
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        depths = layer.get("depths", [])
        if not depths or not isinstance(depths[0], dict):
            continue
        layer_values = depths[0].get("values")
        if not isinstance(layer_values, dict):
            continue
        raw_value = layer_values.get("mean")
        if raw_value is None:
            continue
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric_value):
            continue
        layer_name = layer.get("name")
        if layer_name == "phh2o":
            ph = numeric_value / 10
            if 0 < ph <= 14:
                values["ph"] = ph
        elif layer_name == "nitrogen":
            unit_measure = layer.get("unit_measure")
            if not isinstance(unit_measure, dict):
                continue
            conversion_factor = unit_measure.get("d_factor", 100)
            try:
                values["nitrogen_g_per_kg"] = numeric_value / float(conversion_factor)
            except (TypeError, ValueError, ZeroDivisionError):
                continue
    return values


def get_soil_inputs(
    latitude: float,
    longitude: float,
    location_names: list[str],
) -> tuple[dict[str, float], dict[str, dict[str, str]], dict[str, Any]]:
    try:
        def load_soilgrids_result() -> dict[str, Any]:
            try:
                return {"values": _get_soilgrids_values(latitude, longitude)}
            except DataSourceError as exc:
                return {"values": {}, "error": str(exc)}

        soilgrids_result = _cached(
            f"soilgrids:{latitude:.4f}:{longitude:.4f}",
            load_soilgrids_result,
            ttl_seconds=90,
        )
        api_values = soilgrids_result["values"]
        soilgrids_error = soilgrids_result.get("error")
    except (KeyError, TypeError) as exc:
        raise DataSourceError(f"SoilGrids cache returned an invalid result: {exc}") from exc

    try:
        with MOCK_SOIL_PATH.open("r", encoding="utf-8") as soil_file:
            mock_soil = json.load(soil_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise DataSourceError(f"Could not load prototype mock soil values: {exc}") from exc
    if not isinstance(mock_soil, dict):
        raise DataSourceError("Prototype mock soil data must be a JSON object.")

    mock_values = None
    matched_location = None
    for location_name in location_names:
        key = normalize_district(location_name)
        if key in mock_soil and isinstance(mock_soil[key], dict):
            mock_values = mock_soil[key]
            matched_location = key
            break
    if mock_values is None:
        fallback = mock_soil.get("default")
        if not isinstance(fallback, dict):
            raise DataSourceError(
                "No location-specific prototype soil values or default fallback are configured."
            )
        mock_values = fallback
        matched_location = "default"

    soil_values: dict[str, float] = {}
    provenance: dict[str, dict[str, str]] = {}
    for feature in ("nitrogen", "phosphorus", "potassium", "ph"):
        # SoilGrids reports total nitrogen in g/kg, which is not compatible
        # with the crop classifier's available-nutrient feature scale.
        api_value = api_values.get(feature) if feature == "ph" else None
        if api_value is not None:
            soil_values[feature] = float(api_value)
            provenance[feature] = {
                "source": "SoilGrids model estimate",
                "status": "api_estimate",
                "depth": "0-5 cm",
                "note": "Modelled map-cell estimate; not a farm soil test.",
            }
            continue

        try:
            mock_value = float(mock_values[feature])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataSourceError(
                f"Mock soil values for {matched_location} lack valid {feature}."
            ) from exc
        if not math.isfinite(mock_value):
            raise DataSourceError(
                f"Mock soil values for {matched_location} contain invalid {feature}."
            )
        soil_values[feature] = mock_value
        provenance[feature] = {
            "source": "Prototype static mock JSON",
            "status": "mock",
            "location_key": matched_location,
            "note": "Illustrative fallback value; not measured soil data.",
        }

    return soil_values, provenance, {
        "source": "ISRIC SoilGrids",
        "api_values": api_values,
        "api_error": soilgrids_error,
        "mock_location_key": matched_location,
        "note": (
            "SoilGrids provides modelled pH estimates when available. Its total nitrogen "
            "estimate in g/kg is not compatible with the classifier's N feature, and the API "
            "does not provide its P or K features. Static mock values fill missing or "
            "incompatible features."
        ),
    }


def _load_price_mappings() -> tuple[dict[str, str], dict[str, float], list[str], str]:
    try:
        with MANDI_MAPPING_PATH.open("r", encoding="utf-8") as mapping_file:
            mapping = json.load(mapping_file)
        with MOCK_PRICES_PATH.open("r", encoding="utf-8") as prices_file:
            mock_prices = json.load(prices_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise DataSourceError(f"Could not load crop price mappings: {exc}") from exc

    if not isinstance(mapping, dict) or not isinstance(mock_prices, dict):
        raise DataSourceError("Mandi commodity mappings or prototype mock prices are invalid.")
    commodities = mapping.get("crop_commodities")
    supported_states = mapping.get("supported_states")
    base_url = mapping.get("base_url")
    if (
        not isinstance(commodities, dict)
        or not isinstance(supported_states, list)
        or not all(isinstance(state, str) for state in supported_states)
        or not isinstance(base_url, str)
    ):
        raise DataSourceError("Mandi API configuration is invalid.")
    return commodities, mock_prices, supported_states, base_url.rstrip("/")


def _load_ceda_crop_map() -> dict[str, dict[str, Any]]:
    try:
        with CEDA_CROP_MAP_PATH.open("r", encoding="utf-8") as map_file:
            mapping = json.load(map_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise DataSourceError(f"Could not load the finalized CEDA crop map: {exc}") from exc
    if not isinstance(mapping, dict):
        raise DataSourceError("The finalized CEDA crop map must be a JSON object.")
    return mapping


def _load_ceda_price_cache() -> dict[str, dict[str, Any]]:
    global _ceda_price_cache, _ceda_price_cache_loaded
    if _ceda_price_cache_loaded:
        return _ceda_price_cache
    rows: dict[str, dict[str, Any]] = {}
    if CEDA_PRICE_CACHE_PATH.exists():
        try:
            with CEDA_PRICE_CACHE_PATH.open(
                "r", encoding="utf-8", newline=""
            ) as cache_file:
                for row in csv.DictReader(cache_file):
                    crop = row.get("crop")
                    raw_price = row.get("median_modal_rs_per_quintal")
                    price = _valid_price(raw_price)
                    if not crop or price is None:
                        continue
                    rows[crop] = {
                        **row,
                        "median_modal_rs_per_quintal": price,
                        "commodity_id": (
                            int(row["commodity_id"])
                            if row.get("commodity_id", "").isdigit()
                            else None
                        ),
                        "proxy": row.get("proxy", "").casefold() == "true",
                        "sparse": row.get("sparse", "").casefold() == "true",
                        "incomplete": row.get("incomplete", "").casefold() == "true",
                    }
        except (OSError, csv.Error, ValueError) as exc:
            raise DataSourceError(f"Could not load CEDA historical price cache: {exc}") from exc
    _ceda_price_cache = rows
    _ceda_price_cache_loaded = True
    return _ceda_price_cache


def initialize_price_cache() -> None:
    """Load the offline price cache once during application startup."""
    _load_ceda_price_cache()


def _valid_price(value: Any) -> float | None:
    try:
        price = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def _valid_arrival_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _is_stale(arrival_date: date) -> bool:
    return (date.today() - arrival_date).days > 14


def _ceda_label(as_of: str | None) -> str:
    parsed_date = _valid_arrival_date(as_of)
    month_label = parsed_date.strftime("%b %Y") if parsed_date else "unknown date"
    return f"Historical mandi price, {month_label} (Agmarknet via CEDA)"


def _price_detail(
    *,
    status: str,
    price: float,
    source: str,
    as_of: str | None,
    proxy: bool,
    warning: str | None,
    label: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "status": status,
        "price_per_quintal": round(price, 2),
        "price_rs_per_quintal": round(price, 2),
        "source": source,
        "as_of": as_of,
        "proxy": proxy,
        "is_simulated": source == "mock" or proxy,
        "label": label,
        "warning": warning,
        **extra,
    }


def _get_mandi_result(
    base_url: str,
    endpoint_name: str,
    params: dict[str, str],
) -> dict[str, Any]:
    cache_key = f"mandi:{endpoint_name}:{json.dumps(params, sort_keys=True)}"
    result = _cached(
        cache_key,
        lambda: _load_mandi_result(
            f"{base_url}/v1/{endpoint_name}",
            params,
        ),
        ttl_seconds=60,
    )
    if "error" in result:
        raise DataSourceError(result["error"], result.get("status_code"))
    return result


def _load_mandi_result(endpoint: str, params: dict[str, str]) -> dict[str, Any]:
    try:
        return _get_mandi_json(endpoint, params)
    except DataSourceError as exc:
        return {"error": str(exc), "status_code": exc.status_code}


def _district_price(
    records: list[Any],
    state: str,
    district: str,
    commodity: str,
) -> tuple[float, dict[str, Any]] | None:
    matching_records = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if normalize_name(record.get("state")) != normalize_name(state):
            continue
        if normalize_name(record.get("commodity")) != normalize_name(commodity):
            continue
        if normalize_district(str(record.get("district", ""))) != normalize_district(district):
            continue
        price = _valid_price(record.get("modal_price"))
        arrival_date = _valid_arrival_date(record.get("arrival_date"))
        if price is not None and arrival_date is not None:
            matching_records.append((record, price, arrival_date))

    if not matching_records:
        return None

    latest_date = max(item[2] for item in matching_records)
    latest_records = [item for item in matching_records if item[2] == latest_date]
    market_names = sorted(
        {
            str(record.get("market", "")).strip()
            for record, _, _ in latest_records
            if str(record.get("market", "")).strip()
        }
    )
    average_price = sum(price for _, price, _ in latest_records) / len(latest_records)
    return average_price, {
        "status": "live",
        "source": "Mandi API",
        "scope": "district_aggregate",
        "district": district,
        "markets": market_names,
        "market_count": len(market_names),
        "arrival_date": latest_date.isoformat(),
        "is_stale": _is_stale(latest_date),
    }


def _state_average_price(
    records: list[Any],
) -> tuple[float, dict[str, Any]] | None:
    valid_records = []
    for record in records:
        if not isinstance(record, dict):
            continue
        price = _valid_price(record.get("avg_modal_price"))
        arrival_date = _valid_arrival_date(record.get("arrival_date"))
        if price is not None and arrival_date is not None:
            valid_records.append((record, price, arrival_date))
    if not valid_records:
        return None

    latest_date = max(item[2] for item in valid_records)
    latest_records = [item for item in valid_records if item[2] == latest_date]
    latest_price = sum(price for _, price, _ in latest_records) / len(latest_records)
    data_points = sum(
        int(record.get("data_points", 0) or 0)
        for record, _, _ in latest_records
        if str(record.get("data_points", 0)).isdigit()
    )
    return latest_price, {
        "status": "live",
        "source": "Mandi API state average",
        "scope": "state_average",
        "arrival_date": latest_date.isoformat(),
        "data_points": data_points,
        "is_stale": _is_stale(latest_date),
    }


def get_crop_prices(
    crops: list[str],
    state: str | None = None,
    district: str | None = None,
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    crop_commodities, mock_prices, supported_states, base_url = _load_price_mappings()
    ceda_map = _load_ceda_crop_map()
    ceda_cache = _load_ceda_price_cache()
    prices: dict[str, float] = {}
    details: dict[str, dict[str, Any]] = {}
    supported_state = next(
        (
            configured_state
            for configured_state in supported_states
            if state and normalize_name(configured_state) == normalize_name(state)
        ),
        None,
    )

    def get_live_price(
        crop: str,
    ) -> tuple[tuple[float, dict[str, Any]] | None, str | None]:
        commodity = crop_commodities.get(crop)
        if (
            crop not in TGK_ELIGIBLE_CROPS
            or not isinstance(commodity, str)
            or not supported_state
        ):
            return None, None
        try:
            current_result = _get_mandi_result(
                base_url,
                "prices",
                {"state": supported_state, "commodity": commodity},
            )
            current_price = _district_price(
                current_result["data"],
                supported_state,
                district or "",
                commodity,
            )
            if current_price is not None:
                if not current_price[1].get("is_stale", False):
                    return current_price, None
                source_error = "TGK quote was stale."
            else:
                source_error = None
        except DataSourceError as exc:
            source_error = str(exc)
        try:
            history_result = _get_mandi_result(
                base_url,
                "prices/history",
                {"state": supported_state, "commodity": commodity},
            )
            state_average = _state_average_price(history_result["data"])
            if state_average is not None:
                if not state_average[1].get("is_stale", False):
                    return state_average, None
                source_error = "TGK state-average quote was stale."
        except DataSourceError as exc:
            source_error = str(exc)
        return None, source_error

    eligible_crops = [crop for crop in crops if crop in TGK_ELIGIBLE_CROPS]
    if eligible_crops and supported_state:
        with ThreadPoolExecutor(max_workers=min(3, len(eligible_crops))) as executor:
            live_results = dict(zip(eligible_crops, executor.map(get_live_price, eligible_crops)))
    else:
        live_results = {}

    for crop in crops:
        live_result, source_error = live_results.get(crop, (None, None))
        if live_result is not None:
            live_price, detail = live_result
            prices[crop] = live_price
            details[crop] = _price_detail(
                status="live",
                price=live_price,
                source="tgk_live",
                as_of=detail.get("arrival_date"),
                proxy=False,
                warning=None,
                label="Live (TGK)",
                **{
                    key: value
                    for key, value in detail.items()
                    if key not in {"status", "source"}
                },
            ) | {
                "commodity": crop_commodities[crop],
            }
            continue

        crop_map_entry = ceda_map.get(crop, {})
        ceda_row = ceda_cache.get(crop)
        expected_commodity_id = (
            RICE_COMMODITY_ID
            if crop == "rice"
            else crop_map_entry.get("commodity_id")
        )
        if (
            ceda_row
            and expected_commodity_id is not None
            and ceda_row.get("commodity_id") == expected_commodity_id
        ):
            cached_price = _valid_price(
                ceda_row.get("median_modal_rs_per_quintal")
            )
            proxy = bool(crop_map_entry.get("proxy", False))
            is_sparse = bool(ceda_row.get("sparse"))
            is_incomplete = bool(ceda_row.get("incomplete"))
            if cached_price is not None and (not is_sparse and not is_incomplete):
                proxy_warning = (
                    "Moth bean dal price used as a proxy."
                    if proxy
                    else None
                )
                prices[crop] = cached_price
                details[crop] = _price_detail(
                    status="cached",
                    price=cached_price,
                    source="ceda_cached",
                    as_of=ceda_row.get("as_of") or ceda_row.get("latest_date"),
                    proxy=proxy,
                    warning=proxy_warning,
                    label=_ceda_label(
                        ceda_row.get("as_of") or ceda_row.get("latest_date")
                    ),
                    commodity_id=expected_commodity_id,
                    matched_name=crop_map_entry.get("matched_name"),
                    source_name=ceda_row.get("source", "CEDA Agmarknet"),
                )
                continue

        raw_mock_price = mock_prices.get(crop)
        try:
            mock_price = float(raw_mock_price)
        except (TypeError, ValueError):
            details[crop] = {
                "status": "unavailable",
                "reason": "No Mandi API quote or prototype mock price is configured.",
            }
            continue
        if not math.isfinite(mock_price) or mock_price <= 0:
            details[crop] = {
                "status": "unavailable",
                "reason": "The configured prototype mock price is invalid.",
            }
            continue

        prices[crop] = mock_price
        if crop_map_entry.get("commodity_id") is None and crop == "kidneybeans":
            reason = "No verified CEDA commodity mapping exists; using a labelled mock price."
        elif not supported_state:
            reason = (
                f"The Mandi API does not cover {state or 'the unresolved state'}; "
                f"supported states: {', '.join(supported_states)}."
            )
        elif crop not in TGK_ELIGIBLE_CROPS and isinstance(
            crop_commodities.get(crop), str
        ):
            reason = (
                "TGK price units are not documented; using the next verified source."
            )
        elif source_error:
            reason = f"Mandi API lookup failed: {source_error}"
        elif ceda_row and (
            ceda_row.get("sparse") or ceda_row.get("incomplete")
        ):
            reason = (
                "The CEDA cache entry is sparse or incomplete; no eligible TGK quote "
                "or usable cache was available."
            )
        else:
            reason = (
                "No fresh TGK quote or usable CEDA cache was available."
            )
        proxy = bool(crop_map_entry.get("proxy", False))
        proxy_warning = "Moth bean dal price used as a proxy." if proxy else None
        prices[crop] = mock_price
        details[crop] = _price_detail(
            status="mock",
            price=mock_price,
            source="mock",
            as_of=None,
            proxy=proxy,
            warning=proxy_warning,
            label="Mock (simulated)",
            source_name="Prototype mock price",
            scope="prototype_fallback",
            reason=reason,
        )

    return prices, details
