"""
backend/main.py
FastAPI entry point for the AgriTech Optimization API.

ML Pipeline (as per README.md Slide 7):
  1. Geocoding + 90-day weather (Open-Meteo)
  2. Exact numeric soil-test readings (the public SHC map exposes aggregates only)
  3. Random Forest Classifier -> Top-3 suitable crops
  4. Yield Lookup v3 -> Recent district×crop×season median with fallbacks
     [XGBoost v1 retired: Yield=Production/Area circular leakage, no causal features]
  5. Verified TGK quotes, cached CEDA history, and labelled mock prices + optimization

Protocol: Rules.md - pathlib, no hardcoded paths, ASCII-safe output, random_state=42
"""

import json
import logging
import os
import statistics
import urllib.error
import urllib.request
import urllib.parse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import joblib
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .data_sources import (
    DataSourceError,
    get_crop_prices,
    get_soil_inputs,
    get_soil_health_summary,
    initialize_price_cache,
    normalize_name,
)
from .optimizer.lp_solver import optimize_crop_allocation

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    initialize_price_cache()
    yield


app = FastAPI(title="AgriTech Optimization API", lifespan=lifespan)

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
load_dotenv(PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)

# ML model artifacts
CLS_DIR = PROJECT_ROOT / "ml" / "experiments" / "classifier" / "models"
REG_DIR = PROJECT_ROOT / "ml" / "experiments" / "regressor"  / "models"

# ── Load ML models once at startup ────────────────────────────────────────────
_clf          = None
_clf_meta     = None
_reg_meta     = None       # kept for logging/metadata only
_yield_lookup = None       # v3 recent district-aware historical yield lookup
_shap_explainer = None


def _generate_ai_reasoning(
    *,
    farmer_inputs: dict[str, Any],
    classifier_reasoning: dict[str, Any],
    optimizer_reasoning: dict[str, Any],
    optimization_result: dict[str, Any],
    recommended_crops: list[str],
    optimized_crops: list[str],
    crop_economics: dict[str, Any],
) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {"status": "not_configured", "error_code": "missing_api_key"}

    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()
    evidence = {
        "farmer_inputs": farmer_inputs,
        "classifier": classifier_reasoning,
        "recommended_crops": recommended_crops,
        "crops_used_by_optimizer": optimized_crops,
        "crops_excluded_from_optimizer": [
            crop for crop in recommended_crops if crop not in optimized_crops
        ],
        "crop_economics": crop_economics,
        "optimizer": optimizer_reasoning,
        "optimization_result": optimization_result,
    }
    system_prompt = (
        "You explain a crop recommendation and an optimization plan to a farmer and judges. "
        "Use only the supplied JSON evidence. Do not invent agronomic facts, data, causes, "
        "market trends, or guarantees. Explain that classifier SHAP values describe model "
        "attribution, not causation, and model scores are not calibrated success probabilities. "
        "Explain that the optimizer maximizes estimated net profit only among crops with usable "
        "yield and price inputs, subject to the reported land, budget, and crop limits. "
        "If a crop was excluded, state that data was missing rather than implying it was inferior. "
        "If optimizer status is fallback, say the allocation is not an LP optimum. "
        "Return only a JSON object with these fields: classifier_summary (string), "
        "classifier_reasons (array of {crop, reason}, one for every recommended crop), "
        "optimizer_summary (string), allocation_reasons (array of {crop, reason}, one for every "
        "allocation). Keep each reason concise and evidence-grounded."
    )
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{
            "role": "user",
            "parts": [{"text": json.dumps(evidence, allow_nan=False)}],
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1400,
            "responseMimeType": "application/json",
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=body,
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            provider_response = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.warning("Gemini reasoning request failed with HTTP status %s.", exc.code)
        return {
            "status": "error",
            "error_code": f"provider_http_{exc.code}",
        }
    except (urllib.error.URLError, TimeoutError):
        logger.warning("Gemini reasoning request could not reach the provider.")
        return {"status": "error", "error_code": "provider_unavailable"}
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Gemini reasoning provider returned a malformed response.")
        return {"status": "error", "error_code": "invalid_provider_response"}

    try:
        content = provider_response["candidates"][0]["content"]["parts"][0]["text"]
        generated = json.loads(content)
        classifier_reasons = _validate_reason_items(
            generated["classifier_reasons"], recommended_crops
        )
        allocation_names = [
            allocation["crop"]
            for allocation in optimization_result.get("allocations", [])
        ]
        allocation_reasons = _validate_reason_items(
            generated["allocation_reasons"], allocation_names
        )
        classifier_summary = generated["classifier_summary"]
        optimizer_summary = generated["optimizer_summary"]
        if (
            not isinstance(classifier_summary, str)
            or not classifier_summary.strip()
            or not isinstance(optimizer_summary, str)
            or not optimizer_summary.strip()
        ):
            raise ValueError("Summaries must be non-empty strings.")
        if len(classifier_summary) > 800 or len(optimizer_summary) > 800:
            raise ValueError("Summaries exceed the supported length.")
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        logger.warning("Gemini reasoning provider returned content outside the expected schema.")
        return {"status": "error", "error_code": "invalid_reasoning_schema"}

    return {
        "status": "ready",
        "model": model,
        "classifier_summary": classifier_summary.strip(),
        "classifier_reasons": classifier_reasons,
        "optimizer_summary": optimizer_summary.strip(),
        "allocation_reasons": allocation_reasons,
    }


def _validate_reason_items(
    items: Any,
    expected_crops: list[str],
) -> list[dict[str, str]]:
    if not isinstance(items, list):
        raise ValueError("Reason items must be a list.")
    expected_by_key = {crop.casefold(): crop for crop in expected_crops}
    reasons: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Reason item must be an object.")
        crop = item.get("crop")
        reason = item.get("reason")
        canonical_crop = (
            expected_by_key.get(crop.strip().casefold())
            if isinstance(crop, str)
            else None
        )
        if (
            canonical_crop is None
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 500
            or canonical_crop in reasons
        ):
            raise ValueError("Reason item does not match the expected crop list.")
        reasons[canonical_crop] = reason.strip()
    if set(reasons) != set(expected_crops):
        raise ValueError("Reason items do not cover every expected crop.")
    return [{"crop": crop, "reason": reasons[crop]} for crop in expected_crops]

def _load_models():
    global _clf, _clf_meta, _reg_meta, _yield_lookup, _shap_explainer
    _shap_explainer = None

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
def _predict_crop_recommendations(
    n: float,
    p: float,
    k: float,
    temperature: float,
    humidity: float,
    ph: float,
    rainfall: float,
    top_k: int = 3,
) -> tuple[list[str], dict[str, Any]]:
    """Return ranked crops and SHAP evidence from the Random Forest."""
    global _shap_explainer
    if _clf is None or _clf_meta is None:
        raise HTTPException(
            status_code=503,
            detail="The trained crop classifier is unavailable; no crop plan was generated.",
        )
    feature_values = {
        "N": n,
        "P": p,
        "K": k,
        "temperature": temperature,
        "humidity": humidity,
        "ph": ph,
        "rainfall": rainfall,
    }
    feature_cols = _clf_meta["feature_cols"]
    if len(feature_cols) != len(feature_values) or set(feature_cols) != set(feature_values):
        raise HTTPException(
            status_code=503,
            detail="The crop classifier feature metadata is incompatible; no crop plan was generated.",
        )
    classes = _clf_meta["classes"]
    expected_class_ids = np.arange(len(classes))
    if not np.array_equal(_clf.classes_, expected_class_ids) or not _clf.estimators_:
        raise HTTPException(
            status_code=503,
            detail="The crop classifier class metadata is incompatible; no crop plan was generated.",
        )
    inp = np.array(
        [[feature_values[feature] for feature in feature_cols]],
        dtype=np.float32,
    )
    probs = _clf.predict_proba(inp)[0]
    positive_class_indices = np.flatnonzero(probs > 0)
    top_idx = positive_class_indices[
        np.argsort(probs[positive_class_indices])[::-1][:top_k]
    ]
    if _shap_explainer is None:
        import shap

        _shap_explainer = shap.TreeExplainer(_clf)
    raw_values = _shap_explainer.shap_values(inp, check_additivity=True)
    if isinstance(raw_values, list):
        shap_values = np.stack([np.asarray(values)[0] for values in raw_values], axis=-1)
    else:
        raw_array = np.asarray(raw_values)
        if raw_array.shape == (1, len(feature_cols), len(classes)):
            shap_values = raw_array[0]
        elif raw_array.shape == (len(classes), 1, len(feature_cols)):
            shap_values = raw_array[:, 0, :].T
        else:
            raise HTTPException(
                status_code=503,
                detail="The classifier SHAP output shape is incompatible; no crop plan was generated.",
            )
    expected_values = np.asarray(_shap_explainer.expected_value).reshape(-1)
    if shap_values.shape != (len(feature_cols), len(classes)) or len(expected_values) != len(classes):
        raise HTTPException(
            status_code=503,
            detail="The classifier SHAP metadata is incompatible; no crop plan was generated.",
        )

    crops = []
    for rank, class_index in enumerate(top_idx, start=1):
        contributions = [
            {
                "feature": feature,
                "value": feature_values[feature],
                "shap_value": float(shap_values[i, class_index]),
            }
            for i, feature in enumerate(feature_cols)
        ]
        baseline_score = float(expected_values[class_index])
        reconstructed_score = baseline_score + sum(
            item["shap_value"] for item in contributions
        )
        if not np.isclose(reconstructed_score, probs[class_index], atol=1e-5, rtol=1e-4):
            raise HTTPException(
                status_code=503,
                detail="Classifier SHAP values do not match the reported model score.",
            )
        crops.append({
            "crop": classes[class_index],
            "rank": rank,
            "score": float(probs[class_index]),
            "baseline_score": baseline_score,
            "feature_contributions": contributions,
        })

    return [classes[i] for i in top_idx], {
        "method": "random_forest_shap",
        "crops": crops,
    }

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
    candidates, classifier_reasoning = _predict_crop_recommendations(
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
        mandi_details.get(crop, {}).get("is_simulated", False)
        or mandi_details.get(crop, {}).get("status") == "mock"
        or mandi_details.get(crop, {}).get("is_stale", False)
        for crop in optimized_crops
    )
    plan_is_simulated = soil_data_is_simulated or price_data_is_simulated

    # 6. Optimize using resolved market prices and clearly labelled mock fallbacks.
    lp_result = optimize_crop_allocation(
        land_acres=req.land_acres,
        budget=req.budget,
        candidate_crops=optimized_crops,
        max_single_crop_ratio=0.65,
        custom_yields={crop: custom_yields[crop] for crop in optimized_crops},
        custom_prices={crop: crop_prices[crop] for crop in optimized_crops},
    )
    optimizer_reasoning = getattr(lp_result, "reasoning", None) or {}
    optimization_result = {
        "status": lp_result.status,
        "total_estimated_profit": lp_result.total_estimated_profit,
        "utilized_budget": lp_result.utilized_budget,
        "allocated_land_acres": lp_result.allocated_land_acres,
        "allocations": lp_result.allocations,
    }
    crop_economics = {
        crop: {
            "yield_estimate_tonnes_per_hectare": custom_yields.get(crop),
            "price_rs_per_quintal": crop_prices.get(crop),
            "price_source": mandi_details.get(crop, {}).get("source"),
            "price_status": mandi_details.get(crop, {}).get("status"),
            "price_proxy": mandi_details.get(crop, {}).get("proxy", False),
            "price_simulated": mandi_details.get(crop, {}).get("is_simulated", False),
            "eligible_for_optimizer": crop in optimized_crops,
            "exclusion_reason": (
                None if crop in optimized_crops
                else "missing_yield" if custom_yields.get(crop) is None
                else "missing_price" if crop not in crop_prices
                else "not_eligible"
            ),
        }
        for crop in candidates
    }
    ai_reasoning = _generate_ai_reasoning(
        farmer_inputs={
            "soil": {"N": n, "P": p, "K": k, "pH": ph},
            "weather": {
                "average_temperature_c": temp,
                "average_humidity_percent": humidity,
                "rainfall_total_mm": rain,
            },
            "season": season,
            "land_acres": req.land_acres,
            "budget_rs": req.budget,
        },
        classifier_reasoning=classifier_reasoning,
        optimizer_reasoning=optimizer_reasoning,
        optimization_result=optimization_result,
        recommended_crops=candidates,
        optimized_crops=optimized_crops,
        crop_economics=crop_economics,
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
            "market_prices": (
                "TGK valid live quotes, CEDA cached historical prices, then labelled mock prices"
            ),
        },
        "top_crops_recommended": candidates,
        "classifier_reasoning": classifier_reasoning,
        "optimized_crops": optimized_crops,
        "optimizer_reasoning": optimizer_reasoning,
        "ai_reasoning": ai_reasoning,
        "mandi_prices": mandi_details,
        "price_metadata": {
            crop: {
                field: mandi_details[crop].get(field)
                for field in (
                    "source",
                    "as_of",
                    "label",
                    "proxy",
                    "is_simulated",
                    "warning",
                )
            }
            for crop in candidates
            if crop in mandi_details
        },
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
