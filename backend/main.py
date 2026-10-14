"""
backend/main.py
FastAPI entry point for the AgriTech Optimization API.

ML Pipeline (as per README.md Slide 7):
  1. Geocoding + 90-day weather (Open-Meteo)
  2. Exact numeric soil-test readings (the public SHC map exposes aggregates only)
  3. Random Forest Classifier -> Top-3 suitable crops
  4. Yield Lookup v3 -> Recent district×crop×season median with fallbacks
     [XGBoost v1 retired: Yield=Production/Area circular leakage, no causal features]
  5. District/state Mandi API prices + labelled prototype mock prices + optimization

Protocol: Rules.md - pathlib, no hardcoded paths, ASCII-safe output, random_state=42
"""

import json
import statistics
import urllib.request
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import joblib
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .data_sources import (
    DataSourceError,
    get_crop_prices,
    get_soil_inputs,
    get_soil_health_summary,
    normalize_name,
)
from .optimizer.lp_solver import optimize_crop_allocation

app = FastAPI(title="AgriTech Optimization API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SoilTestValues(BaseModel):
    nitrogen: float = Field(ge=0, le=300)
    phosphorus: float = Field(ge=0, le=300)
    potassium: float = Field(ge=0, le=500)
    ph: float = Field(gt=0, le=14)


class OptimizeRequest(BaseModel):
    location: str = Field(min_length=2)
    budget: float = Field(gt=0)
    land_acres: float = Field(gt=0)
    soil_test: SoilTestValues | None = None


# ── Resolve paths dynamically (Rules: pathlib relative to __file__) ───────────
BACKEND_DIR       = Path(__file__).resolve().parent
PROJECT_ROOT      = BACKEND_DIR.parent

# ML model artifacts
CLS_DIR = PROJECT_ROOT / "ml" / "experiments" / "classifier" / "models"
REG_DIR = PROJECT_ROOT / "ml" / "experiments" / "regressor"  / "models"

# ── Load ML models once at startup ────────────────────────────────────────────
_clf          = None
_clf_meta     = None
_reg_meta     = None       # kept for logging/metadata only
_yield_lookup = None       # v3 recent district-aware historical yield lookup

def _load_models():
    global _clf, _clf_meta, _reg_meta, _yield_lookup

    clf_path  = CLS_DIR / "rf_classifier_v1.joblib"
    cmeta_path = CLS_DIR / "rf_classifier_v1_meta.json"
    reg_path  = REG_DIR / "xgb_regressor_v1.joblib"          # kept for reference, not used
    rmeta_path = REG_DIR / "xgb_regressor_v1_meta.json"
    ylookup_path = REG_DIR / "crop_yield_lookup_v3.json"

    if clf_path.exists():
        _clf = joblib.load(clf_path)
        if cmeta_path.exists():
            with open(cmeta_path, "r", encoding="utf-8") as f:
                _clf_meta = json.load(f)
        print("[INFO] RF Classifier loaded successfully.")
    else:
        print("[WARN] RF Classifier model not found.")

    if reg_path.exists():
        # v2: do not load the XGBoost model - lookup table is the sole inference path
        if rmeta_path.exists():
            with open(rmeta_path, "r", encoding="utf-8") as f:
                _reg_meta = json.load(f)
        print("[INFO] XGBoost artifacts found (not loaded -- v3 yield lookup active).")
    else:
        print("[INFO] XGBoost artifacts are not required; the yield lookup is used if available.")

    if ylookup_path.exists():
        with open(ylookup_path, "r", encoding="utf-8") as f:
            _yield_lookup = json.load(f)
        print("[INFO] crop_yield_lookup_v3.json loaded successfully.")

_load_models()

# ── Classifier helper ─────────────────────────────────────────────────────────
def _predict_top_crops(n: float, p: float, k: float,
                        temperature: float, humidity: float,
                        ph: float, rainfall: float,
                        top_k: int = 3) -> list[str]:
    """
    Returns top_k most suitable crop names using the trained RF Classifier.
    """
    if _clf is None or _clf_meta is None:
        raise HTTPException(
            status_code=503,
            detail="The trained crop classifier is unavailable; no crop plan was generated.",
        )
    inp = np.array([[n, p, k, temperature, humidity, ph, rainfall]], dtype=np.float32)
    probs = _clf.predict_proba(inp)[0]
    classes = _clf_meta["classes"]
    top_idx = np.argsort(probs)[::-1][:top_k]
    return [classes[i] for i in top_idx]


# ── Yield estimator (v3 — district-aware historical lookup) ──────────────────
# EXP_REG_01 (XGBoost) was retired: Yield = Production/Area is a mathematical
# identity in the source data. Area/Production as features = circular leakage.
# State + Season + log(Area) have |r| < 0.1 with Yield → no causal signal.
# EXP_REG_03 uses recent crop×district×season medians with state, season,
# and crop-wide fallbacks. Validation is time-ordered to avoid future leakage.

def _predict_yields(
    crops: list[str],
    state: str,
    season: str = "Kharif",
    district: str | None = None,
) -> dict[str, float | None]:
    """
    Return historical yield estimates (t/ha) at district level when available.
    Fallback order: district×season → state×season → crop×season → crop median.
    """
    yields = {}
    state_key = normalize_name(state)
    district_key = normalize_name(district)
    season_key = normalize_name(season)

    for crop in crops:
        if _yield_lookup and crop in _yield_lookup:
            crop_data = _yield_lookup[crop]
            district_key_value = f"{state_key}|{district_key}|{season_key}"
            state_key_value = f"{state_key}|{season_key}"
            district_yield = (
                crop_data.get("by_district_season", {})
                .get(district_key_value, {})
                .get("median")
                if district_key
                else None
            )
            state_yield = (
                crop_data.get("by_state_season", {})
                .get(state_key_value, {})
                .get("median")
            )
            season_yield = crop_data.get("by_season", {}).get(
                season_key, {}
            ).get("median")
            yields[crop] = next(
                (
                    value
                    for value in (
                        district_yield,
                        state_yield,
                        season_yield,
                        crop_data.get("global_median"),
                    )
                    if value is not None
                ),
                None,
            )
        else:
            yields[crop] = None

    return yields


# ── Main API endpoint ─────────────────────────────────────────────────────────
@app.post("/api/optimize")
def optimize_farm(req: OptimizeRequest):
    # 1. Resolve the farmer's location to a district and state.
    geo_url = (
        f"https://geocoding-api.open-meteo.com/v1/search"
        f"?name={urllib.parse.quote(req.location)}&count=5&countryCode=IN"
    )
    try:
        with urllib.request.urlopen(geo_url, timeout=15) as response:
            geo_resp = json.loads(response.read().decode())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Location lookup failed; no crop plan was generated: {exc}",
        ) from exc

    results = geo_resp.get("results", [])
    if not results:
        raise HTTPException(status_code=422, detail="Location was not found in India.")
    place = next(
        (
            item
            for item in results
            if item.get("country_code", "").upper() == "IN"
            and item.get("admin1")
            and item.get("admin2")
        ),
        None,
    )
    if place is None:
        raise HTTPException(
            status_code=422,
            detail="Could not resolve this location to an Indian district and state.",
        )

    lat = place["latitude"]
    lon = place["longitude"]
    state_label = place["admin1"]
    district_label = place["admin2"]
    resolved_parts = [place.get("name"), district_label, state_label]
    resolved_location = ", ".join(
        part for index, part in enumerate(resolved_parts) if part and part not in resolved_parts[:index]
    )

    # Keep the public Soil Health Card summary for display, but obtain classifier
    # values from compatible SoilGrids estimates or explicitly labelled mock data.
    soil_summary = None
    soil_source_error = None
    try:
        soil_summary = get_soil_health_summary(state_label, district_label)
    except DataSourceError as exc:
        soil_source_error = str(exc)

    if req.soil_test is not None:
        soil_data = req.soil_test.model_dump()
        soil_data_provenance = {
            feature: {
                "source": "Farmer-provided soil test",
                "status": "user_measurement",
                "note": "Value supplied by the user; confirm against the original test report.",
            }
            for feature in soil_data
        }
        soil_source_details = {
            "source": "Farmer-provided soil test",
            "soilgrids": None,
            "note": "User readings take precedence over mapped and prototype values.",
        }
    else:
        try:
            soil_data, soil_data_provenance, soil_source_details = get_soil_inputs(
                latitude=lat,
                longitude=lon,
                location_names=[
                    place.get("name", ""),
                    place.get("admin3", ""),
                    district_label,
                ],
            )
        except DataSourceError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Soil inputs and their configured mock fallback are unavailable: {exc}",
            ) from exc

    # 2. Fetch actual historical weather inputs.
    end_date   = date.today() - timedelta(days=5)
    start_date = end_date - timedelta(days=90)
    archive_url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}"
        f"&start_date={start_date}&end_date={end_date}"
        f"&hourly=temperature_2m,relative_humidity_2m"
        f"&daily=precipitation_sum"
        f"&timezone=auto"
    )
    try:
        with urllib.request.urlopen(archive_url, timeout=20) as response:
            w_resp = json.loads(response.read().decode())
            temps = [
                value
                for value in w_resp["hourly"]["temperature_2m"]
                if value is not None
            ]
            precips = [
                value
                for value in w_resp["daily"]["precipitation_sum"]
                if value is not None
            ]
            humidities = [
                value
                for value in w_resp["hourly"]["relative_humidity_2m"]
                if value is not None
            ]
            if not temps or not precips or not humidities:
                raise ValueError("Historical weather response was missing required measurements.")
            temp = round(statistics.mean(temps), 2)
            rain = round(sum(precips), 2)
            humidity = round(statistics.mean(humidities), 2)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Historical weather lookup failed; no crop plan was generated: {exc}",
        ) from exc

    # 3. Classify crops from exact values in the farmer's soil test.
    n, p, k, ph = (
        soil_data["nitrogen"],
        soil_data["phosphorus"],
        soil_data["potassium"],
        soil_data["ph"],
    )
    candidates = _predict_top_crops(
        n=n, p=p, k=k,
        temperature=temp,
        humidity=humidity,
        ph=ph,
        rainfall=rain,
        top_k=3,
    )
    model_used_classifier = "RandomForest v1"

    # 4. Resolve yield estimates against the geocoded state and current season.
    current_month = date.today().month
    season = "Kharif" if 6 <= current_month <= 11 else "Rabi"
    custom_yields = _predict_yields(
        candidates,
        state=state_label,
        season=season,
        district=district_label,
    )
    if _yield_lookup is None:
        raise HTTPException(
            status_code=503,
            detail="The historical crop-yield lookup is unavailable; no crop plan was generated.",
        )

    # Prefer district-level Mandi reports, then the API's state average.
    try:
        crop_prices, mandi_details = get_crop_prices(
            candidates,
            state=state_label,
            district=district_label,
        )
    except DataSourceError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Crop price configuration could not be loaded: {exc}",
        ) from exc

    optimized_crops = [
        crop
        for crop in candidates
        if crop in crop_prices and custom_yields.get(crop) is not None
    ]
    if not optimized_crops:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "no_crop_prices",
                "message": (
                    "No live or prototype mock crop prices and measured soil-based crop plan "
                    "can be calculated for these recommendations."
                ),
                "recommended_crops": candidates,
                "mandi_prices": mandi_details,
            },
        )

    soil_data_is_simulated = any(
        detail.get("status") == "mock"
        for detail in soil_data_provenance.values()
    )
    price_data_is_simulated = any(
        mandi_details.get(crop, {}).get("status") == "mock"
        or mandi_details.get(crop, {}).get("is_stale", False)
        for crop in optimized_crops
    )
    plan_is_simulated = soil_data_is_simulated or price_data_is_simulated

    # 6. Optimize using available Mandi quotes and clearly labelled mock prices.
    lp_result = optimize_crop_allocation(
        land_acres=req.land_acres,
        budget=req.budget,
        candidate_crops=optimized_crops,
        max_single_crop_ratio=0.65,
        custom_yields={crop: custom_yields[crop] for crop in optimized_crops},
        custom_prices={crop: crop_prices[crop] for crop in optimized_crops},
    )

    return {
        "status": lp_result.status,
        "regional_data": {
            **soil_data,
            "temperature": temp,
            "rainfall":    rain,
            "humidity":    humidity
        },
        "resolved_location":  resolved_location,
        "soil_health_summary": soil_summary,
        "soil_health_source_error": soil_source_error,
        "soil_data_provenance": soil_data_provenance,
        "soil_source_details": soil_source_details,
        "soil_data_is_simulated": soil_data_is_simulated,
        "detected_season":    season,
        "models_used": {
            "classifier": model_used_classifier,
            "regressor": "district-aware-yield-lookup-v3",
            "market_prices": "Mandi API district/state averages with labelled prototype mock prices",
        },
        "top_crops_recommended": candidates,
        "optimized_crops": optimized_crops,
        "mandi_prices": mandi_details,
        "price_data_is_simulated": price_data_is_simulated,
        "plan_is_simulated": plan_is_simulated,
        "profit_estimate_type": "simulated" if plan_is_simulated else "estimated",
        "allocations":              lp_result.allocations,
        "total_estimated_profit":   lp_result.total_estimated_profit,
        "utilized_budget":          lp_result.utilized_budget,
        "allocated_land_acres":     lp_result.allocated_land_acres,
        "message": (
            (
                "SIMULATED PROTOTYPE PLAN: one or more soil or crop-price inputs are mock "
                "or stale; the displayed profit is not a real forecast. "
                if plan_is_simulated
                else "Soil inputs and prices came from their indicated data sources; profit remains an estimate. "
            )
            + f"Used soil inputs and 90-day historical weather for "
            f"{resolved_location}. The classifier recommended {', '.join(candidates)}. "
            f"{lp_result.message}"
        )
    }
