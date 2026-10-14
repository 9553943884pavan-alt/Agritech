"""Train and time-validate a district-aware historical yield estimator."""

import json
import re
import time
import unicodedata
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]
DATA_PATH = PROJECT_ROOT / "ml" / "data" / "processed" / "Cleaned_crop_production.csv"
MODELS_DIR = SCRIPT_DIR / "models"
REPORTS_DIR = PROJECT_ROOT / "ml" / "reports" / "regressor"
MODEL_PATH = MODELS_DIR / "crop_yield_lookup_v3.json"
RECENT_YEARS = 5
VALIDATION_YEARS = range(2008, 2015)
COCONUT_NUTS_TO_TONNES = 0.00014

KNOWN_CROPS = {
    "rice", "maize", "chickpea", "kidneybeans", "pigeonpeas", "mothbeans",
    "mungbean", "blackgram", "lentil", "pomegranate", "banana", "mango",
    "grapes", "watermelon", "muskmelon", "apple", "orange", "papaya",
    "coconut", "cotton", "jute", "coffee",
}


def normalize_name(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", str(value))
    ascii_value = "".join(
        character for character in ascii_value
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()).strip()


def load_training_data() -> pd.DataFrame:
    required = {
        "State_Name", "District_Name", "Crop_Year", "Season",
        "Area", "Crop", "Yield",
    }
    data = pd.read_csv(DATA_PATH)
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"Yield dataset is missing columns: {sorted(missing)}")

    data["Season"] = data["Season"].astype(str).str.strip()
    data["Crop"] = data["Crop"].astype(str).str.strip().str.casefold()
    data["Yield"] = pd.to_numeric(data["Yield"], errors="coerce")
    data["Area"] = pd.to_numeric(data["Area"], errors="coerce")
    data = data[
        data["Crop"].isin(KNOWN_CROPS)
        & data["Yield"].gt(0)
        & data["Area"].gt(0)
    ].copy()
    data.loc[data["Crop"].eq("coconut"), "Yield"] *= COCONUT_NUTS_TO_TONNES

    data["State_Key"] = data["State_Name"].map(normalize_name)
    data["District_Key"] = data["District_Name"].map(normalize_name)
    data["Season_Key"] = data["Season"].map(normalize_name)
    return data


def trim_training_outliers(data: pd.DataFrame) -> pd.DataFrame:
    upper_bounds = data.groupby("Crop")["Yield"].transform(
        lambda values: values.quantile(0.99)
    )
    return data[data["Yield"].le(upper_bounds)].copy()


def build_indices(data: pd.DataFrame) -> dict[str, dict[tuple[str, ...], dict]]:
    levels = {
        "district": ["Crop", "State_Key", "District_Key", "Season_Key"],
        "state": ["Crop", "State_Key", "Season_Key"],
        "season": ["Crop", "Season_Key"],
        "crop": ["Crop"],
    }
    indices = {}
    for level, columns in levels.items():
        aggregates = data.groupby(columns)["Yield"].agg(
            median="median", count="count"
        )
        indices[level] = {
            key if isinstance(key, tuple) else (key,): {
                "median": float(values["median"]),
                "count": int(values["count"]),
            }
            for key, values in aggregates.iterrows()
        }
    return indices


def predict_from_indices(
    indices: dict[str, dict[tuple[str, ...], dict]],
    crop: str,
    state: str,
    district: str,
    season: str,
    district_aware: bool,
) -> float | None:
    crop_key = normalize_name(crop)
    state_key = normalize_name(state)
    district_key = normalize_name(district)
    season_key = normalize_name(season)
    keys = []
    if district_aware:
        keys.append(("district", (crop_key, state_key, district_key, season_key)))
    keys.extend(
        [
            ("state", (crop_key, state_key, season_key)),
            ("season", (crop_key, season_key)),
            ("crop", (crop_key,)),
        ]
    )
    for level, key in keys:
        result = indices[level].get(key)
        if result is not None:
            return result["median"]
    return None


def evaluate(data: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float], dict[str, float]]:
    predictions = []
    for year in VALIDATION_YEARS:
        training = data[data["Crop_Year"] < year]
        test = data[data["Crop_Year"].eq(year)]
        if training.empty or test.empty:
            continue

        full_history = build_indices(trim_training_outliers(training))
        recent_training = training[
            training["Crop_Year"] >= year - RECENT_YEARS
        ]
        recent_history = build_indices(trim_training_outliers(recent_training))

        for row in test.itertuples(index=False):
            truth = float(row.Yield)
            baseline = predict_from_indices(
                full_history, row.Crop, row.State_Name, row.District_Name,
                row.Season, district_aware=False,
            )
            improved = predict_from_indices(
                recent_history, row.Crop, row.State_Name, row.District_Name,
                row.Season, district_aware=True,
            )
            predictions.append(
                {
                    "year": int(row.Crop_Year),
                    "crop": row.Crop,
                    "actual": truth,
                    "baseline": baseline,
                    "improved": improved,
                }
            )
    results = pd.DataFrame(predictions).dropna(
        subset=["baseline", "improved"]
    )
    if results.empty:
        raise ValueError("No rows were available for time-ordered yield validation.")
    return results, score(results, "baseline"), score(results, "improved")


def score(results: pd.DataFrame, prediction_column: str) -> dict[str, float]:
    actual = results["actual"].to_numpy()
    predicted = results[prediction_column].to_numpy()
    return {
        "mae": float(mean_absolute_error(actual, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
        "r2": float(r2_score(actual, predicted)),
        "wape_percent": float(
            np.abs(actual - predicted).sum() / np.maximum(actual.sum(), 1e-9) * 100
        ),
    }


def find_latest_complete_year(data: pd.DataFrame) -> int:
    annual_counts = data.groupby("Crop_Year").size().sort_index()
    latest = int(annual_counts.index[-1])
    if len(annual_counts) > 5:
        prior_counts = annual_counts.loc[annual_counts.index < latest].tail(5)
        if len(prior_counts) and annual_counts.loc[latest] < 0.5 * prior_counts.median():
            latest -= 1
    return latest


def build_artifact(data: pd.DataFrame, latest_year: int) -> dict:
    recent = data[
        data["Crop_Year"].between(latest_year - RECENT_YEARS + 1, latest_year)
    ]
    if recent.empty:
        raise ValueError("No recent records are available to build the yield lookup.")
    fit_data = trim_training_outliers(recent)
    artifact = {}
    for crop, crop_data in fit_data.groupby("Crop"):
        crop_entry = {
            "global_median": round(float(crop_data["Yield"].median()), 4),
            "n": int(len(crop_data)),
            "by_season": {},
            "by_state_season": {},
            "by_district_season": {},
        }
        for season_key, group in crop_data.groupby("Season_Key"):
            crop_entry["by_season"][season_key] = {
                "median": round(float(group["Yield"].median()), 4),
                "n": int(len(group)),
            }
        for (state_key, season_key), group in crop_data.groupby(
            ["State_Key", "Season_Key"]
        ):
            crop_entry["by_state_season"][f"{state_key}|{season_key}"] = {
                "median": round(float(group["Yield"].median()), 4),
                "n": int(len(group)),
            }
        for (state_key, district_key, season_key), group in crop_data.groupby(
            ["State_Key", "District_Key", "Season_Key"]
        ):
            crop_entry["by_district_season"][
                f"{state_key}|{district_key}|{season_key}"
            ] = {
                "median": round(float(group["Yield"].median()), 4),
                "n": int(len(group)),
            }
        artifact[crop] = crop_entry
    return artifact


def write_report(
    results: pd.DataFrame,
    baseline: dict[str, float],
    improved: dict[str, float],
    artifact: dict,
    first_year: int,
    latest_year: int,
    complete_year: int,
) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    mae_gain = (
        (baseline["mae"] - improved["mae"]) / baseline["mae"] * 100
        if baseline["mae"]
        else 0.0
    )
    crop_rows = []
    for crop, group in results.groupby("crop"):
        crop_rows.append(
            f"| {crop} | {len(group)} | "
            f"{mean_absolute_error(group['actual'], group['improved']):.3f} |"
        )

    fig, axes = plt.subplots(1, 2, figsize=(11, 5))
    for label, column, color in (
        ("Existing state lookup", "baseline", "#f9a825"),
        ("District-aware v3", "improved", "#1976d2"),
    ):
        axes[0].scatter(
            np.log1p(results["actual"]),
            np.log1p(results[column]),
            s=8,
            alpha=0.25,
            label=label,
            color=color,
        )
    limit = float(np.log1p(results["actual"].quantile(0.99)))
    axes[0].plot([0, limit], [0, limit], "r--", linewidth=1)
    axes[0].set_xlim(0, limit)
    axes[0].set_ylim(0, limit)
    axes[0].set_xlabel("log(1 + actual yield)")
    axes[0].set_ylabel("log(1 + predicted yield)")
    axes[0].set_title("Out-of-time validation")
    axes[0].legend()

    per_crop = results.assign(
        absolute_error=(results["actual"] - results["improved"]).abs()
    ).groupby("crop")["absolute_error"].mean().sort_values()
    per_crop.plot.barh(ax=axes[1], color="#1976d2")
    axes[1].set_xlabel("MAE (tonnes/hectare)")
    axes[1].set_title("District-aware v3 MAE by crop")
    fig.tight_layout()
    fig.savefig(REPORTS_DIR / "EXP_REG_03_yield_validation.png", dpi=140)
    plt.close(fig)

    report = f"""# Experiment Report: District-Aware Crop Yield Estimator

## 1. Objective and Data
- **Experiment:** EXP_REG_03
- **Target:** Historical crop yield in tonnes per hectare.
- **Source:** `ml/data/processed/Cleaned_crop_production.csv`.
- **Targeted crops:** {len(KNOWN_CROPS)} classifier classes; {len(artifact)} have usable yield records.
- **Validation:** Expanding, year-ordered test years {first_year}-{int(results['year'].max())}; each test year is predicted only from earlier years.
- **Training cutoff:** Raw data ends in {latest_year}; the incomplete terminal year was excluded, so the latest complete year is {complete_year}.
- **Features used at runtime:** Crop, state, district, and planting season. Soil and weather are not used because the historical yield table has no matching district-level soil/weather labels.

## 2. Changes from the Previous Lookup
- Added district × crop × season medians, using the resolved district already returned by geocoding.
- Retained state × crop × season, crop × season, and crop-wide fallbacks for unseen or unmatched districts.
- Fit medians from the most recent {RECENT_YEARS} complete years instead of pooling the full history.
- Excluded the top 1% of each crop's training yields, with cutoffs computed only on each training fold. Median estimates reduce sensitivity to remaining outliers.
- Converted coconut nut counts to tonnes of copra equivalent using the existing project assumption.

## 3. Out-of-Time Results
All validation rows are scored, including extreme values in the source. R²/RMSE are therefore sensitive to source outliers; MAE and WAPE are also reported.

| Metric | Existing state-only lookup | District-aware v3 |
|:---|---:|---:|
| Rows scored | {len(results)} | {len(results)} |
| MAE (t/ha) | {baseline['mae']:.4f} | {improved['mae']:.4f} |
| RMSE (t/ha) | {baseline['rmse']:.4f} | {improved['rmse']:.4f} |
| R² | {baseline['r2']:.4f} | {improved['r2']:.4f} |
| WAPE (%) | {baseline['wape_percent']:.2f} | {improved['wape_percent']:.2f} |

MAE change vs existing lookup: **{mae_gain:+.2f}%**.

### District-aware v3 MAE by crop
| Crop | Validation rows | MAE (t/ha) |
|:---|---:|---:|
{chr(10).join(crop_rows)}

## 4. Artifacts
- Lookup: `ml/experiments/regressor/models/crop_yield_lookup_v3.json`
- Validation plot: `ml/reports/regressor/EXP_REG_03_yield_validation.png`
- Training and evaluation script: `ml/experiments/regressor/train_yield_regressor_v3.py`

## 5. Limitations
- This is a robust, location-aware historical estimator, not a causal farm-level yield model.
- The source covers historical district crop statistics only through {complete_year}; results can be stale and local yield variation is not modeled.
- District spelling differences fall back to the state estimate; crops without source rows return no estimate rather than a fabricated default.
- Soil/weather-linked yield modeling requires a reliable yield dataset with compatible soil, weather, crop, and location measurements.
"""
    (REPORTS_DIR / "EXP_REG_03_District_Aware_Yield.md").write_text(
        report, encoding="utf-8"
    )


def main() -> None:
    started = time.perf_counter()
    data = load_training_data()
    results, baseline, improved = evaluate(data)

    max_year = int(data["Crop_Year"].max())
    complete_year = find_latest_complete_year(data)
    final_data = data[data["Crop_Year"].le(complete_year)]
    artifact = build_artifact(final_data, complete_year)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    write_report(
        results,
        baseline,
        improved,
        artifact,
        min(VALIDATION_YEARS),
        max_year,
        complete_year,
    )

    print(f"[RESULT] Existing state-only MAE: {baseline['mae']:.4f} t/ha")
    print(f"[RESULT] District-aware v3 MAE: {improved['mae']:.4f} t/ha")
    print(f"[RESULT] District-aware v3 RMSE: {improved['rmse']:.4f} t/ha")
    print(f"[RESULT] District-aware v3 R2: {improved['r2']:.4f}")
    print(f"[INFO] Training through latest complete year {complete_year} (raw max {max_year}).")
    print(f"[INFO] Model saved to {MODEL_PATH}")
    print(f"[INFO] Completed in {time.perf_counter() - started:.2f}s")


if __name__ == "__main__":
    main()
