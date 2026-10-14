"""
EXP_REG_02: Crop Yield Lookup Table (v2)
Replaces: EXP_REG_01 XGBoost Regressor

WHY v2 EXISTS (root-cause of EXP_REG_01 failure):
  - Yield = Production / Area (mathematical identity in the dataset).
    Using Area or Production as features is circular: the model memorises
    the identity, not a real signal.
  - State + Season + log(Area) have near-zero correlation with Yield
    (|r| < 0.1 for almost every crop). There are no agro-climatic features
    (N, P, K, pH, rainfall, temperature) in the production dataset.
  - crop_mean_yield was target-encoded leakage from training data.
  - Coconut Production is in nuts (not tonnes). Yield = nuts/ha ≈ 3500–11000,
    which inflated the overall R² while destroying grain-crop accuracy.

CORRECT APPROACH:
  Yield prediction from (Crop, State, Season) is best answered by the
  historical median — it is the minimum-variance unbiased estimator when
  no causal features are available.  A 3-level fallback hierarchy gives
  coverage for unseen state/season combos:
    L1  crop × state × season  (most specific)
    L2  crop × season          (fallback when state unseen)
    L3  crop only              (final fallback)

COCONUT UNIT FIX:
  Raw Production for coconut is in number of nuts.
  Conversion: 1000 nuts ≈ 0.14 tonnes of copra (70% extraction, 200 g/nut)
  We convert Yield from nuts/ha → tonnes_copra/ha before computing medians.
  This brings coconut into the same unit as all other crops (t/ha).

Protocol: venv python, pathlib relative to __file__, random_state=42, no
hardcoded absolute paths.
"""

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

# ── Path setup ────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]
DATA_PROC    = PROJECT_ROOT / "ml" / "data" / "processed"
MODELS_DIR   = SCRIPT_DIR / "models"
REPORTS_DIR  = PROJECT_ROOT / "ml" / "reports" / "regressor"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)

RANDOM_STATE = 42

KNOWN_CROPS = [
    "rice", "maize", "chickpea", "kidneybeans", "pigeonpeas", "mothbeans",
    "mungbean", "blackgram", "lentil", "pomegranate", "banana", "mango",
    "grapes", "watermelon", "muskmelon", "apple", "orange", "papaya",
    "coconut", "cotton", "jute", "coffee"
]

# Coconut: Production in raw nuts → convert to tonnes of copra equivalent
# 1000 nuts * 200 g/nut * 0.70 extraction = 140 kg = 0.14 t
COCONUT_NUTS_TO_TONNES = 1.0 / (1000.0 / 0.14)   # 0.00014 t/nut

# ── 1. Load & Clean ───────────────────────────────────────────────────────────
print("[INFO] Loading Cleaned_crop_production.csv ...")
df = pd.read_csv(DATA_PROC / "Cleaned_crop_production.csv")
df["Season"] = df["Season"].str.strip()
df = df[(df["Yield"] > 0) & (df["Area"] > 0)]
df = df[df["Crop"].isin(KNOWN_CROPS)].copy()
print(f"  After crop filter: {df.shape}")

# Fix coconut units: nuts/ha → tonnes_copra/ha
coconut_mask = df["Crop"] == "coconut"
df.loc[coconut_mask, "Yield"] = df.loc[coconut_mask, "Yield"] * COCONUT_NUTS_TO_TONNES

print(f"  Coconut yield after unit fix (t_copra/ha): "
      f"median={df.loc[coconut_mask, 'Yield'].median():.3f}  "
      f"mean={df.loc[coconut_mask, 'Yield'].mean():.3f}")

# Remove extreme outliers per crop (clip at 99th percentile per crop)
def clip_per_crop(grp, crop_name):
    upper = grp["Yield"].quantile(0.99)
    result = grp[grp["Yield"] <= upper].copy()
    result["Crop"] = crop_name
    return result

clipped = []
for crop_name, grp in df.groupby("Crop", group_keys=False):
    upper = grp["Yield"].quantile(0.99)
    sub = grp[grp["Yield"] <= upper].copy()
    sub["Crop"] = crop_name
    clipped.append(sub)
df = pd.concat(clipped, ignore_index=True)
print(f"  After per-crop 99th-pct clip: {df.shape}")

# ── 2. Build Lookup Tables ────────────────────────────────────────────────────
print("\n[INFO] Building lookup tables ...")

# L1: crop × state × season
l1 = (df.groupby(["Crop", "State_Name", "Season"])["Yield"]
        .agg(median="median", mean="mean", std="std", n="count")
        .reset_index())

# L2: crop × season
l2 = (df.groupby(["Crop", "Season"])["Yield"]
        .agg(median="median", mean="mean", std="std", n="count")
        .reset_index())

# L3: crop only
l3 = (df.groupby("Crop")["Yield"]
        .agg(median="median", mean="mean", std="std", n="count")
        .reset_index())

print(f"  L1 (crop×state×season): {len(l1)} rows")
print(f"  L2 (crop×season):       {len(l2)} rows")
print(f"  L3 (crop only):         {len(l3)} rows")

# ── 3. Evaluate Lookup Accuracy (hold-out simulation) ─────────────────────────
# Hold out 20% of rows (by index), then predict using L1/L2/L3 fallback
# from the remaining 80%.  This simulates what happens when the backend
# encounters a state/season it hasn't seen.

np.random.seed(RANDOM_STATE)
holdout_idx = df.sample(frac=0.2, random_state=RANDOM_STATE).index
train_df = df.drop(holdout_idx)
test_df  = df.loc[holdout_idx].copy()

# Rebuild lookups on training portion only
tr_l1 = (train_df.groupby(["Crop", "State_Name", "Season"])["Yield"]
           .median().rename("pred").reset_index())
tr_l2 = (train_df.groupby(["Crop", "Season"])["Yield"]
           .median().rename("pred").reset_index())
tr_l3 = (train_df.groupby("Crop")["Yield"]
           .median().rename("pred").reset_index())

test_df = test_df.merge(tr_l1, on=["Crop", "State_Name", "Season"], how="left")
test_df = test_df.rename(columns={"pred": "pred_l1"})
test_df = test_df.merge(tr_l2, on=["Crop", "Season"], how="left")
test_df = test_df.rename(columns={"pred": "pred_l2"})
test_df = test_df.merge(tr_l3, on=["Crop"], how="left")
test_df = test_df.rename(columns={"pred": "pred_l3"})

# Apply fallback hierarchy
test_df["pred"] = (test_df["pred_l1"]
                   .fillna(test_df["pred_l2"])
                   .fillna(test_df["pred_l3"]))

# Which level was used
test_df["level_used"] = "L3"
test_df.loc[test_df["pred_l2"].notna(), "level_used"] = "L2"
test_df.loc[test_df["pred_l1"].notna(), "level_used"] = "L1"

print("\n[INFO] Coverage by fallback level on 20% holdout:")
print(test_df["level_used"].value_counts().to_string())

y_true = test_df["Yield"].values
y_pred = test_df["pred"].values
valid  = ~np.isnan(y_pred)

from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error

r2   = r2_score(y_true[valid], y_pred[valid])
rmse = np.sqrt(mean_squared_error(y_true[valid], y_pred[valid]))
mae  = mean_absolute_error(y_true[valid], y_pred[valid])
mape = np.mean(np.abs((y_true[valid] - y_pred[valid]) / np.maximum(y_true[valid], 1e-6))) * 100

print(f"\n[RESULT] Holdout R2:   {r2:.4f}")
print(f"[RESULT] Holdout RMSE: {rmse:.4f} t/ha")
print(f"[RESULT] Holdout MAE:  {mae:.4f} t/ha")
print(f"[RESULT] Holdout MAPE: {mape:.2f}%")

# Per-crop evaluation
print("\n[RESULT] Per-crop evaluation on holdout:")
per_crop_results = []
for crop in KNOWN_CROPS:
    sub = test_df[test_df["Crop"] == crop]
    sv = sub.dropna(subset=["pred"])
    if len(sv) < 5:
        continue
    yt = sv["Yield"].values
    yp = sv["pred"].values
    cr2  = r2_score(yt, yp)
    crmse = np.sqrt(mean_squared_error(yt, yp))
    cmae  = mean_absolute_error(yt, yp)
    print(f"  {crop:<15}: R2={cr2:+.3f}  RMSE={crmse:.3f}  MAE={cmae:.3f}  n={len(sv)}")
    per_crop_results.append({"crop": crop, "r2": round(cr2, 3),
                              "rmse": round(crmse, 3), "mae": round(cmae, 3),
                              "n": int(len(sv))})

# ── 4. Feature Importance (variance explained by each dimension) ──────────────
# Proxy: how much does knowing state/season vs crop-only reduce prediction error
mae_l3_only = mean_absolute_error(
    test_df.loc[test_df["level_used"] == "L3", "Yield"],
    test_df.loc[test_df["level_used"] == "L3", "pred_l3"]
) if (test_df["level_used"] == "L3").sum() > 0 else float("nan")

print(f"\n[INFO] MAE when only crop is known (L3 fallback): "
      f"{mae_l3_only:.4f} t/ha" if not np.isnan(mae_l3_only) else "[INFO] No L3-only rows in holdout")

# ── 5. Plots ──────────────────────────────────────────────────────────────────
# True vs Predicted scatter
fig, ax = plt.subplots(figsize=(8, 5))
colors = {"L1": "#4facfe", "L2": "#f9a825", "L3": "#ef5350"}
for lvl, grp in test_df.dropna(subset=["pred"]).groupby("level_used"):
    ax.scatter(grp["Yield"], grp["pred"], alpha=0.3, s=8,
               color=colors.get(lvl, "grey"), label=lvl)
lim_max = test_df["Yield"].quantile(0.99)
ax.set_xlim(0, lim_max)
ax.set_ylim(0, lim_max)
ax.plot([0, lim_max], [0, lim_max], "r--", linewidth=1)
ax.set_xlabel("True Yield (t/ha)")
ax.set_ylabel("Predicted Yield (t/ha)")
ax.set_title(f"Lookup Table v2: True vs Predicted  (R²={r2:.3f})")
ax.legend(title="Fallback Level")
plt.tight_layout()
scatter_path = REPORTS_DIR / "EXP_REG_02_true_vs_predicted.png"
fig.savefig(scatter_path, dpi=120)
plt.close()
print(f"\n[INFO] Scatter plot saved -> {scatter_path}")

# Per-crop MAE bar chart
if per_crop_results:
    pcdf = pd.DataFrame(per_crop_results).set_index("crop")
    fig2, ax2 = plt.subplots(figsize=(12, 5))
    pcdf["mae"].plot.bar(ax=ax2, color="#4facfe")
    ax2.set_title("EXP_REG_02 Per-Crop MAE (t/ha)")
    ax2.set_ylabel("MAE (t/ha)")
    ax2.set_xlabel("")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    mae_path = REPORTS_DIR / "EXP_REG_02_per_crop_mae.png"
    fig2.savefig(mae_path, dpi=120)
    plt.close()
    print(f"[INFO] Per-crop MAE plot saved -> {mae_path}")

# ── 6. Serialize Lookup Tables as JSON ────────────────────────────────────────
# Build final lookups using the FULL dataset (not just train split)
# These are what the backend actually uses at inference time.

def build_lookup_json(df_full):
    lookup = {}
    for crop in KNOWN_CROPS:
        sub = df_full[df_full["Crop"] == crop]
        if sub.empty:
            continue
        lookup[crop] = {
            "global_median": round(float(sub["Yield"].median()), 4),
            "global_mean":   round(float(sub["Yield"].mean()), 4),
            "global_std":    round(float(sub["Yield"].std()), 4),
            "n":             int(len(sub)),
            "by_season": {},
            "by_state_season": {}
        }
        # L2: by season
        for season, grp in sub.groupby("Season"):
            lookup[crop]["by_season"][season] = {
                "median": round(float(grp["Yield"].median()), 4),
                "n":      int(len(grp))
            }
        # L1: by state × season
        for (state, season), grp in sub.groupby(["State_Name", "Season"]):
            key = f"{state}|{season}"
            lookup[crop]["by_state_season"][key] = {
                "median": round(float(grp["Yield"].median()), 4),
                "n":      int(len(grp))
            }
    return lookup

print("\n[INFO] Building final lookup from full dataset ...")
full_lookup = build_lookup_json(df)

lookup_path = MODELS_DIR / "crop_yield_lookup_v2.json"
lookup_path.write_text(json.dumps(full_lookup, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"[INFO] Lookup table saved -> {lookup_path}")

# Also save simple crop-only median (backend fallback, replaces v1 lookup)
simple_medians = {crop: full_lookup[crop]["global_median"]
                  for crop in full_lookup}
simple_path = MODELS_DIR / "crop_mean_yield_lookup.json"
simple_path.write_text(json.dumps(simple_medians, indent=2), encoding="utf-8")
print(f"[INFO] Simple median lookup updated -> {simple_path}")

# ── 7. Print lookup summary table ─────────────────────────────────────────────
print("\n[RESULT] Final crop yield medians (t/ha) from full dataset:")
print(f"  {'Crop':<15} {'Median':>8} {'Mean':>8} {'Std':>8} {'n':>6}")
print("  " + "-" * 50)
for crop in KNOWN_CROPS:
    if crop not in full_lookup:
        print(f"  {crop:<15} {'N/A':>8}")
        continue
    v = full_lookup[crop]
    print(f"  {crop:<15} {v['global_median']:>8.3f} {v['global_mean']:>8.3f} {v['global_std']:>8.3f} {v['n']:>6}")

# ── 8. Write Experiment Report ────────────────────────────────────────────────
report_path = REPORTS_DIR / "EXP_REG_02_Yield_Lookup_v2.md"
report_md = f"""# Experiment Report: Crop Yield Lookup Table v2

## 1. Experiment Overview
* **Experiment ID:** EXP_REG_02
* **Date:** {time.strftime('%Y-%m-%d %H:%M')}
* **Replaces:** EXP_REG_01 (XGBoost Regressor)
* **Objective:** Produce accurate crop yield estimates (t/ha) for 22 crops for the SciPy optimizer

## 2. Why EXP_REG_01 Failed (Root Cause Analysis)

| Issue | Detail |
|:---|:---|
| Circular leakage | `Yield = Production / Area` exactly. Using `Area` as a feature is a tautology |
| No causal features | State + Season + log(Area) have |r| < 0.1 with Yield for most crops |
| Coconut unit mismatch | Production in nuts → Yield = 3500–11000 nuts/ha; inflated global R² while destroying grain R² |
| Target-encoding leakage | `crop_mean_yield` computed on full dataset, not train-fold only |
| Wrong model class | ML adds variance but zero signal when no causal features are available |

## 3. Correct Approach: Hierarchical Median Lookup

When no causal agro-climatic features are available, the conditional median
is the minimum-variance unbiased estimator for yield.  Three-level fallback:

```
L1  crop × state × season   → most specific
L2  crop × season           → fallback if state unseen
L3  crop only               → final fallback
```

## 4. Coconut Unit Fix
Raw government data: Production = number of nuts (e.g. 65,100,000 nuts for 18,168 ha).
Conversion: `Yield_tonnes = Yield_nuts × (0.14 / 1000)` (200 g/nut × 70% copra extraction).

## 5. Evaluation on 20% Holdout (trained on 80%)

| Metric | Value |
|:---|:---|
| R² | {r2:.4f} |
| RMSE (t/ha) | {rmse:.4f} |
| MAE (t/ha) | {mae:.4f} |
| MAPE (%) | {mape:.2f} |

### Coverage by Fallback Level
### Coverage by Fallback Level
{chr(10).join(f"| {k} | {v} |" for k, v in [("Level", "Count")] + list(test_df["level_used"].value_counts().items()))}

### Per-Crop Results
| Crop | R² | RMSE | MAE | n |
|:---|:---|:---|:---|:---|
{chr(10).join(f"| {r['crop']} | {r['r2']} | {r['rmse']} | {r['mae']} | {r['n']} |" for r in per_crop_results)}

## 6. Final Yield Medians (full dataset)

| Crop | Median (t/ha) | Mean | Std | n |
|:---|:---|:---|:---|:---|
{chr(10).join(f"| {crop} | {full_lookup[crop]['global_median'] if crop in full_lookup else 'N/A'} | {full_lookup[crop]['global_mean'] if crop in full_lookup else ''} | {full_lookup[crop]['global_std'] if crop in full_lookup else ''} | {full_lookup[crop]['n'] if crop in full_lookup else ''} |" for crop in KNOWN_CROPS)}

## 7. Artifacts
* **Lookup JSON (full):** `ml/experiments/regressor/models/crop_yield_lookup_v2.json`
* **Simple median fallback:** `ml/experiments/regressor/models/crop_mean_yield_lookup.json`
* **True vs Predicted:** `ml/reports/regressor/EXP_REG_02_true_vs_predicted.png`
* **Per-Crop MAE:** `ml/reports/regressor/EXP_REG_02_per_crop_mae.png`
* **Script:** `ml/experiments/regressor/train_yield_lookup_v2.py`

## 8. Backend Integration
In `backend/main.py`, replace XGBoost inference with:
```python
def predict_yield(crop, state, season, lookup):
    crop_data = lookup.get(crop, {{}})
    key = f"{{state}}|{{season}}"
    if key in crop_data.get("by_state_season", {{}}):
        return crop_data["by_state_season"][key]["median"]
    if season in crop_data.get("by_season", {{}}):
        return crop_data["by_season"][season]["median"]
    return crop_data.get("global_median", 2.0)
```

## 9. Next Steps
* EXP_REG_03: If soil+weather features are available per district, train a
  proper causal model (LightGBM on N, P, K, pH, rainfall, temp, humidity → Yield).
  The Crop_recommendation.csv has these features but no production-based Yield column.
"""

report_path.write_text(report_md, encoding="utf-8")
print(f"\n[INFO] Experiment report -> {report_path}")
print("\n[SUCCESS] EXP_REG_02 complete.")
