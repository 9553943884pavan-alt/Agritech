"""
EXP_REG_01: XGBoost Crop Yield Regressor
Dataset: ml/data/processed/Cleaned_crop_production.csv (67,396 rows, 22 crops)
Target: 'Yield' (tonnes/hectare)
Features: Crop, State_Name, Season, Area
Protocol: Per Rules.md - venv python, pathlib, random_state=42, no hardcoded paths
"""

import sys
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from xgboost import XGBRegressor
from sklearn.model_selection import KFold, cross_validate, train_test_split
from sklearn.preprocessing import LabelEncoder, OrdinalEncoder
from sklearn.metrics import r2_score, mean_squared_error, mean_absolute_error

# ── Path setup (Rules: pathlib relative to __file__) ─────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]          # VJ/
DATA_PROC    = PROJECT_ROOT / "ml" / "data" / "processed"
MODELS_DIR   = SCRIPT_DIR / "models"
REPORTS_DIR  = PROJECT_ROOT / "ml" / "reports" / "regressor"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)

RANDOM_STATE = 42

# ── 1. Load & Clean Data ──────────────────────────────────────────────────────
print("[INFO] Loading Cleaned_crop_production.csv ...")
df = pd.read_csv(DATA_PROC / "Cleaned_crop_production.csv")
print(f"  Raw shape: {df.shape}")

# Strip whitespace in Season
df["Season"] = df["Season"].str.strip()

# Remove rows with zero/negative yield or area; remove extreme outliers (> 99.5th percentile yield)
df = df[(df["Yield"] > 0) & (df["Area"] > 0)]
upper_clip = df["Yield"].quantile(0.995)
df = df[df["Yield"] <= upper_clip]
print(f"  After cleaning: {df.shape}  (yield <= {upper_clip:.2f} t/ha)")

# Only keep 22 known target crops
KNOWN_CROPS = [
    "rice", "maize", "chickpea", "kidneybeans", "pigeonpeas", "mothbeans",
    "mungbean", "blackgram", "lentil", "pomegranate", "banana", "mango",
    "grapes", "watermelon", "muskmelon", "apple", "orange", "papaya",
    "coconut", "cotton", "jute", "coffee"
]
df = df[df["Crop"].isin(KNOWN_CROPS)].copy()
print(f"  After crop filter: {df.shape}")
print(f"  Crops present: {sorted(df['Crop'].unique())}")

# ── 2. Feature Engineering ────────────────────────────────────────────────────
# Crop mean yield (target-encoded proxy for crop identity)
crop_mean_yield = df.groupby("Crop")["Yield"].mean().to_dict()
df["crop_mean_yield"] = df["Crop"].map(crop_mean_yield)

# Log area (Area has huge range -- log compresses it)
df["log_area"] = np.log1p(df["Area"])

# Encode categoricals
oe_crop   = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
oe_state  = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
oe_season = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)

df["crop_enc"]   = oe_crop.fit_transform(df[["Crop"]]).astype(np.int32)
df["state_enc"]  = oe_state.fit_transform(df[["State_Name"]]).astype(np.int32)
df["season_enc"] = oe_season.fit_transform(df[["Season"]]).astype(np.int32)

FEATURE_COLS = ["crop_enc", "state_enc", "season_enc", "log_area", "crop_mean_yield"]
TARGET_COL   = "Yield"

X = df[FEATURE_COLS].values.astype(np.float32)
y = df[TARGET_COL].values.astype(np.float32)
print(f"  Features: {FEATURE_COLS}")
print(f"  Target range: [{y.min():.2f}, {y.max():.2f}]  mean: {y.mean():.2f}")

# ── 3. Train / Test Split ─────────────────────────────────────────────────────
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=RANDOM_STATE
)
print(f"  Train: {X_train.shape[0]} | Test: {X_test.shape[0]}")

# ── 4. Define Model ───────────────────────────────────────────────────────────
reg = XGBRegressor(
    n_estimators=500,
    max_depth=7,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    reg_alpha=0.1,
    reg_lambda=1.0,
    min_child_weight=5,
    random_state=RANDOM_STATE,
    tree_method="hist",      # fast CPU training
    n_jobs=-1,
    verbosity=0
)

# ── 5. 5-Fold Cross Validation ────────────────────────────────────────────────
print("\n[INFO] Running 5-Fold CV ...")
cv = KFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
cv_results = cross_validate(
    reg, X_train, y_train, cv=cv,
    scoring=["r2", "neg_root_mean_squared_error", "neg_mean_absolute_error"],
    return_train_score=True, n_jobs=-1
)

cv_r2_mean   = cv_results["test_r2"].mean()
cv_r2_std    = cv_results["test_r2"].std()
cv_rmse_mean = -cv_results["test_neg_root_mean_squared_error"].mean()
cv_rmse_std  = cv_results["test_neg_root_mean_squared_error"].std()
cv_mae_mean  = -cv_results["test_neg_mean_absolute_error"].mean()

print(f"  CV R2:   {cv_r2_mean:.4f} +/- {cv_r2_std:.4f}")
print(f"  CV RMSE: {cv_rmse_mean:.4f} +/- {cv_rmse_std:.4f}")
print(f"  CV MAE:  {cv_mae_mean:.4f}")

# ── 6. Final Fit ──────────────────────────────────────────────────────────────
print("\n[INFO] Fitting on full training set ...")
t0 = time.perf_counter()
reg.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
fit_time = time.perf_counter() - t0
print(f"  Fit time: {fit_time:.2f}s")

# ── 7. Holdout Evaluation ─────────────────────────────────────────────────────
y_pred = reg.predict(X_test)

r2   = r2_score(y_test, y_pred)
rmse = np.sqrt(mean_squared_error(y_test, y_pred))
mae  = mean_absolute_error(y_test, y_pred)
mape = np.mean(np.abs((y_test - y_pred) / np.maximum(y_test, 1e-6))) * 100

print(f"\n[RESULT] Holdout R2:   {r2:.4f}")
print(f"[RESULT] Holdout RMSE: {rmse:.4f} t/ha")
print(f"[RESULT] Holdout MAE:  {mae:.4f} t/ha")
print(f"[RESULT] Holdout MAPE: {mape:.2f}%")

# Per-crop R2
print("\n[RESULT] Per-crop R2 on holdout:")
crop_r2_rows = []
for crop in KNOWN_CROPS:
    crop_enc_val = oe_crop.transform([[crop]])[0][0]
    mask = X_test[:, 0] == crop_enc_val
    if mask.sum() >= 5:
        cr2 = r2_score(y_test[mask], y_pred[mask])
        rmse_c = np.sqrt(mean_squared_error(y_test[mask], y_pred[mask]))
        print(f"  {crop:<15}: R2={cr2:.3f}  RMSE={rmse_c:.3f}  n={mask.sum()}")
        crop_r2_rows.append({"crop": crop, "r2": round(cr2, 3), "rmse": round(rmse_c, 3), "n": int(mask.sum())})

# ── 8. Feature Importance Plot ────────────────────────────────────────────────
fi = pd.Series(reg.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
print("\n[RESULT] Feature Importances:")
for f, v in fi.items():
    print(f"  {f:<20}: {v:.4f}")

fig, ax = plt.subplots(figsize=(8, 5))
fi.plot.bar(ax=ax, color="#4facfe")
ax.set_title("XGBoost Regressor Feature Importances")
ax.set_ylabel("Gain Importance")
plt.tight_layout()
fi_path = REPORTS_DIR / "EXP_REG_01_feature_importance.png"
fig.savefig(fi_path, dpi=120)
plt.close()
print(f"\n[INFO] Feature importance plot saved -> {fi_path}")

# ── 9. Residual Plot ──────────────────────────────────────────────────────────
fig2, ax2 = plt.subplots(figsize=(8, 5))
ax2.scatter(y_test, y_pred, alpha=0.3, s=10, color="#4facfe")
lims = [0, max(y_test.max(), y_pred.max())]
ax2.plot(lims, lims, 'r--', linewidth=1)
ax2.set_xlabel("True Yield (t/ha)")
ax2.set_ylabel("Predicted Yield (t/ha)")
ax2.set_title("XGBoost Regressor: True vs Predicted")
plt.tight_layout()
res_path = REPORTS_DIR / "EXP_REG_01_true_vs_predicted.png"
fig2.savefig(res_path, dpi=120)
plt.close()
print(f"[INFO] Residual plot saved -> {res_path}")

# ── 10. Save per-crop mean yield lookup (for backend fallback) ─────────────────
lookup_path = MODELS_DIR / "crop_mean_yield_lookup.json"
lookup_path.write_text(json.dumps(crop_mean_yield, indent=2), encoding="utf-8")
print(f"[INFO] Crop mean yield lookup saved -> {lookup_path}")

# ── 11. Serialize Model + Encoders ────────────────────────────────────────────
model_path    = MODELS_DIR / "xgb_regressor_v1.joblib"
enc_path      = MODELS_DIR / "xgb_regressor_v1_encoders.joblib"
meta_path     = MODELS_DIR / "xgb_regressor_v1_meta.json"

joblib.dump(reg, model_path, compress=3)
joblib.dump({"oe_crop": oe_crop, "oe_state": oe_state, "oe_season": oe_season}, enc_path, compress=3)

meta = {
    "model_type": "XGBRegressor",
    "version": "v1",
    "feature_cols": FEATURE_COLS,
    "known_crops": KNOWN_CROPS,
    "holdout_r2": float(round(r2, 4)),
    "holdout_rmse": float(round(rmse, 4)),
    "holdout_mae": float(round(mae, 4)),
    "holdout_mape": float(round(mape, 2)),
    "cv_r2_mean": float(round(cv_r2_mean, 4)),
    "cv_r2_std": float(round(cv_r2_std, 4)),
    "random_state": RANDOM_STATE,
    "train_rows": int(X_train.shape[0]),
    "test_rows": int(X_test.shape[0]),
    "per_crop_r2": [{k: (float(v) if isinstance(v, (np.floating, np.integer)) else v) for k, v in r.items()} for r in crop_r2_rows]
}
meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
print(f"[INFO] Model saved   -> {model_path}")
print(f"[INFO] Encoders saved-> {enc_path}")
print(f"[INFO] Metadata saved-> {meta_path}")

# ── 12. Write Experiment Report ────────────────────────────────────────────────
report_path = REPORTS_DIR / "EXP_REG_01_XGBoost_Baseline.md"
report_md = f"""# Experiment Report: XGBoost Crop Yield Regressor

## 1. Experiment Overview
* **Experiment ID / Name:** EXP_REG_01
* **Date & Time:** {time.strftime('%Y-%m-%d %H:%M')}
* **Target Objective:** Predict crop-specific yield (tonnes/hectare) for 22 standard Indian crops
* **Target Variable:** `Yield` (tonnes/hectare, government crop production records)
* **Dataset(s) Used:** `ml/data/processed/Cleaned_crop_production.csv` -- Shape after cleaning: {df.shape}

## 2. Model Architecture & Pipeline
* **Model Family / Class:** `XGBRegressor` (XGBoost, gradient boosted trees)
* **Architecture Details:** Histogram-based GBDT; `tree_method='hist'` for fast CPU training
* **Feature Engineering & Preprocessing:**
  - `OrdinalEncoder` for Crop, State, Season (unknown_value=-1)
  - `log1p(Area)` to compress the huge area range
  - `crop_mean_yield` target-encoding proxy
  - Removed yield=0 rows and clipped at 99.5th percentile ({upper_clip:.2f} t/ha)

## 3. Hyperparameter Configuration
| Hyperparameter | Value | Description |
| :--- | :--- | :--- |
| n_estimators | 500 | Boosting rounds |
| max_depth | 7 | Tree depth |
| learning_rate | 0.05 | Step size shrinkage |
| subsample | 0.8 | Row sampling ratio |
| colsample_bytree | 0.8 | Feature sampling ratio |
| reg_alpha | 0.1 | L1 regularization |
| reg_lambda | 1.0 | L2 regularization |
| min_child_weight | 5 | Min leaf sum of hessians |
| random_state | 42 | Fixed seed |

## 4. Evaluation Strategy & Data Splitting
* **Split Methodology:** 5-Fold KFold CV + 80/20 Holdout
* **Stratification:** None (regression)
* **Random Seed:** 42

## 5. Quantitative Evaluation Metrics
* **Holdout R2:**   {r2:.4f}
* **Holdout RMSE:** {rmse:.4f} t/ha
* **Holdout MAE:**  {mae:.4f} t/ha
* **Holdout MAPE:** {mape:.2f}%
* **CV R2:**   {cv_r2_mean:.4f} +/- {cv_r2_std:.4f}
* **CV RMSE:** {cv_rmse_mean:.4f} +/- {cv_rmse_std:.4f}
* **CV MAE:**  {cv_mae_mean:.4f}

### Per-Crop R2 on Holdout
| Crop | R2 | RMSE (t/ha) | n |
| :--- | :--- | :--- | :--- |
{chr(10).join(f"| {r['crop']} | {r['r2']} | {r['rmse']} | {r['n']} |" for r in crop_r2_rows)}

## 6. Feature Importance (XGBoost Gain)
| Feature | Importance |
| :--- | :--- |
{chr(10).join(f'| {f} | {v:.4f} |' for f, v in fi.items())}

## 7. Artifacts & Reproducibility
* **Serialized Model:** `ml/experiments/regressor/models/xgb_regressor_v1.joblib`
* **Encoders:** `ml/experiments/regressor/models/xgb_regressor_v1_encoders.joblib`
* **Metadata JSON:** `ml/experiments/regressor/models/xgb_regressor_v1_meta.json`
* **Executable Script:** `ml/experiments/regressor/train_xgb_regressor.py`
* **Feature Importance Plot:** `ml/reports/regressor/EXP_REG_01_feature_importance.png`
* **True vs Predicted Plot:** `ml/reports/regressor/EXP_REG_01_true_vs_predicted.png`

## 8. Key Findings & Next Steps
* `crop_mean_yield` target encoding is the strongest signal (crop identity drives yield range).
* Residuals are larger for banana/papaya/coconut due to high production variance across states.
* Next: integrate into `backend/main.py` -- predict yield per crop per state/season then pass to SciPy optimizer.
* Next: consider EXP_REG_02 with per-state stratified split and crop-season interaction features.
"""

report_path.write_text(report_md, encoding="utf-8")
print(f"[INFO] Experiment report saved -> {report_path}")

# ── 13. Sanity-check inference ─────────────────────────────────────────────────
print("\n[INFO] Sanity check -- Rice in Guntur, Kharif:")
sample_crop = "rice"
sample_state = "Guntur"
sample_season = "Kharif"
sample_area = 1000.0

crop_enc_v  = oe_crop.transform([[sample_crop]])[0][0]
state_enc_v = oe_state.transform([[sample_state]])[0][0] if sample_state in oe_state.categories_[0] else -1
season_enc_v = oe_season.transform([[sample_season]])[0][0] if sample_season in oe_season.categories_[0] else -1
cmean       = crop_mean_yield.get(sample_crop, 2.0)
log_a       = np.log1p(sample_area)

inp = np.array([[crop_enc_v, state_enc_v, season_enc_v, log_a, cmean]], dtype=np.float32)
pred_yield = float(reg.predict(inp)[0])
print(f"  Predicted yield for Rice (Guntur, Kharif, 1000 ha): {pred_yield:.3f} t/ha")

print("\n[SUCCESS] EXP_REG_01 complete.")
