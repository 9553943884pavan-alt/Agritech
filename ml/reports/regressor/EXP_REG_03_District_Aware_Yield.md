# Experiment Report: District-Aware Crop Yield Estimator

## 1. Objective and Data
- **Experiment:** EXP_REG_03
- **Target:** Historical crop yield in tonnes per hectare.
- **Source:** `ml/data/processed/Cleaned_crop_production.csv`.
- **Targeted crops:** 22 classifier classes; 19 have usable yield records.
- **Validation:** Expanding, year-ordered test years 2008-2014; each test year is predicted only from earlier years.
- **Training cutoff:** Raw data ends in 2015; the incomplete terminal year was excluded, so the latest complete year is 2014.
- **Features used at runtime:** Crop, state, district, and planting season. Soil and weather are not used because the historical yield table has no matching district-level soil/weather labels.

## 2. Changes from the Previous Lookup
- Added district × crop × season medians, using the resolved district already returned by geocoding.
- Retained state × crop × season, crop × season, and crop-wide fallbacks for unseen or unmatched districts.
- Fit medians from the most recent 5 complete years instead of pooling the full history.
- Excluded the top 1% of each crop's training yields, with cutoffs computed only on each training fold. Median estimates reduce sensitivity to remaining outliers.
- Converted coconut nut counts to tonnes of copra equivalent using the existing project assumption.

## 3. Out-of-Time Results
All validation rows are scored, including extreme values in the source. R²/RMSE are therefore sensitive to source outliers; MAE and WAPE are also reported.

| Metric | Existing state-only lookup | District-aware v3 |
|:---|---:|---:|
| Rows scored | 26283 | 26283 |
| MAE (t/ha) | 0.9076 | 0.7469 |
| RMSE (t/ha) | 3.6066 | 3.3571 |
| R² | 0.8108 | 0.8361 |
| WAPE (%) | 27.60 | 22.71 |

MAE change vs existing lookup: **+17.71%**.

### District-aware v3 MAE by crop
| Crop | Validation rows | MAE (t/ha) |
|:---|---:|---:|
| banana | 1215 | 6.193 |
| blackgram | 41 | 0.095 |
| chickpea | 2659 | 0.223 |
| coconut | 735 | 0.360 |
| cotton | 1649 | 0.565 |
| grapes | 8 | 3.318 |
| jute | 545 | 1.908 |
| lentil | 22 | 0.090 |
| maize | 5681 | 0.604 |
| mango | 84 | 2.680 |
| mothbeans | 180 | 0.199 |
| mungbean | 4474 | 0.130 |
| muskmelon | 2 | 0.065 |
| orange | 78 | 0.686 |
| papaya | 139 | 15.148 |
| pigeonpeas | 2916 | 0.202 |
| pomegranate | 15 | 2.701 |
| rice | 5840 | 0.373 |

## 4. Artifacts
- Lookup: `ml/experiments/regressor/models/crop_yield_lookup_v3.json`
- Validation plot: `ml/reports/regressor/EXP_REG_03_yield_validation.png`
- Training and evaluation script: `ml/experiments/regressor/train_yield_regressor_v3.py`

## 5. Limitations
- This is a robust, location-aware historical estimator, not a causal farm-level yield model.
- The source covers historical district crop statistics only through 2014; results can be stale and local yield variation is not modeled.
- District spelling differences fall back to the state estimate; crops without source rows return no estimate rather than a fabricated default.
- Soil/weather-linked yield modeling requires a reliable yield dataset with compatible soil, weather, crop, and location measurements.
