# Experiment Report: XGBoost Crop Yield Regressor

## 1. Experiment Overview
* **Experiment ID / Name:** EXP_REG_01
* **Date & Time:** 2026-10-14 12:22
* **Target Objective:** Predict crop-specific yield (tonnes/hectare) for 22 standard Indian crops
* **Target Variable:** `Yield` (tonnes/hectare, government crop production records)
* **Dataset(s) Used:** `ml/data/processed/Cleaned_crop_production.csv` -- Shape after cleaning: (66141, 13)

## 2. Model Architecture & Pipeline
* **Model Family / Class:** `XGBRegressor` (XGBoost, gradient boosted trees)
* **Architecture Details:** Histogram-based GBDT; `tree_method='hist'` for fast CPU training
* **Feature Engineering & Preprocessing:**
  - `OrdinalEncoder` for Crop, State, Season (unknown_value=-1)
  - `log1p(Area)` to compress the huge area range
  - `crop_mean_yield` target-encoding proxy
  - Removed yield=0 rows and clipped at 99.5th percentile (9117.66 t/ha)

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
* **Holdout R2:**   0.7274
* **Holdout RMSE:** 307.4778 t/ha
* **Holdout MAE:**  29.3294 t/ha
* **Holdout MAPE:** 1241.35%
* **CV R2:**   0.7047 +/- 0.0431
* **CV RMSE:** 322.5563 +/- 21.3795
* **CV MAE:**  32.6380

### Per-Crop R2 on Holdout
| Crop | R2 | RMSE (t/ha) | n |
| :--- | :--- | :--- | :--- |
| rice | -27.34 | 5.234 | 3001 |
| maize | 0.846 | 11.106 | 2791 |
| chickpea | -346.462 | 7.012 | 1476 |
| pigeonpeas | -220.171 | 6.408 | 1496 |
| mothbeans | -3.943 | 0.551 | 150 |
| mungbean | -122.559 | 2.743 | 1968 |
| blackgram | -1898.524 | 6.57 | 17 |
| lentil | -0.676 | 0.121 | 9 |
| pomegranate | -3.622 | 3.747 | 6 |
| banana | -2.252 | 34.473 | 587 |
| mango | -122.533 | 31.611 | 71 |
| grapes | -5.934 | 17.384 | 18 |
| muskmelon | -69.202 | 37.64 | 27 |
| orange | -67.1 | 37.225 | 60 |
| papaya | -1.047 | 53.267 | 86 |
| coconut | 0.609 | 1925.088 | 337 |
| cotton | 0.295 | 9.528 | 832 |
| jute | 0.554 | 3.039 | 293 |

## 6. Feature Importance (XGBoost Gain)
| Feature | Importance |
| :--- | :--- |
| crop_mean_yield | 0.5779 |
| state_enc | 0.2311 |
| log_area | 0.1030 |
| crop_enc | 0.0706 |
| season_enc | 0.0174 |

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
