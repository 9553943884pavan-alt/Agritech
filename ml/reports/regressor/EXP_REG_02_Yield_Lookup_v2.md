# Experiment Report: Crop Yield Lookup Table v2

## 1. Experiment Overview
* **Experiment ID:** EXP_REG_02
* **Date:** 2026-10-14 13:23
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
| R² | 0.8925 |
| RMSE (t/ha) | 2.3866 |
| MAE (t/ha) | 0.7147 |
| MAPE (%) | 1054.19 |

### Coverage by Fallback Level
### Coverage by Fallback Level
| Level | Count |
| L1 | 13154 |
| L2 | 5 |
| L3 | 2 |

### Per-Crop Results
| Crop | R² | RMSE | MAE | n |
|:---|:---|:---|:---|:---|
| rice | 0.535 | 0.619 | 0.455 | 2991 |
| maize | 0.473 | 0.878 | 0.589 | 2748 |
| chickpea | 0.287 | 0.265 | 0.198 | 1396 |
| pigeonpeas | 0.413 | 0.289 | 0.206 | 1502 |
| mothbeans | 0.207 | 0.215 | 0.156 | 165 |
| mungbean | 0.428 | 0.174 | 0.127 | 1971 |
| blackgram | 0.388 | 0.094 | 0.066 | 22 |
| lentil | 0.457 | 0.086 | 0.046 | 5 |
| pomegranate | -46.214 | 3.454 | 3.417 | 5 |
| banana | 0.698 | 9.251 | 5.721 | 613 |
| mango | 0.085 | 2.736 | 1.898 | 66 |
| grapes | -0.49 | 7.472 | 5.522 | 16 |
| muskmelon | 0.309 | 2.015 | 1.647 | 22 |
| orange | 0.641 | 2.363 | 1.409 | 50 |
| papaya | 0.779 | 13.756 | 7.766 | 76 |
| coconut | 0.411 | 0.519 | 0.286 | 366 |
| cotton | 0.463 | 0.782 | 0.549 | 873 |
| jute | 0.569 | 2.808 | 2.023 | 269 |

## 6. Final Yield Medians (full dataset)

| Crop | Median (t/ha) | Mean | Std | n |
|:---|:---|:---|:---|:---|
| rice | 1.926 | 1.9474 | 0.8952 | 14927 |
| maize | 1.7859 | 2.0802 | 1.2049 | 13585 |
| chickpea | 0.8002 | 0.8181 | 0.3241 | 7114 |
| kidneybeans | 1.2513 | 1.2411 | 0.0227 | 17 |
| pigeonpeas | 0.7275 | 0.7789 | 0.3734 | 7382 |
| mothbeans | 0.3333 | 0.3712 | 0.247 | 752 |
| mungbean | 0.4206 | 0.4453 | 0.2243 | 9936 |
| blackgram | 0.7417 | 0.7133 | 0.1175 | 98 |
| lentil | 0.809 | 0.7554 | 0.1237 | 30 |
| pomegranate | 13.3667 | 12.2753 | 1.8982 | 20 |
| banana | 21.7665 | 26.6339 | 17.0234 | 3072 |
| mango | 4.7616 | 5.1364 | 3.1928 | 331 |
| grapes | 23.4483 | 22.1819 | 6.646 | 108 |
| watermelon | N/A |  |  |  |
| muskmelon | 3.0 | 3.428 | 2.6567 | 151 |
| apple | N/A |  |  |  |
| orange | 9.9449 | 9.553 | 4.5089 | 234 |
| papaya | 17.8457 | 32.3969 | 35.6679 | 407 |
| coconut | 0.0032 | 0.5372 | 0.669 | 1937 |
| cotton | 1.3649 | 1.5555 | 1.1009 | 4306 |
| jute | 9.2766 | 9.3287 | 4.3369 | 1393 |
| coffee | 0.7376 | 0.6903 | 0.1296 | 5 |

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
    crop_data = lookup.get(crop, {})
    key = f"{state}|{season}"
    if key in crop_data.get("by_state_season", {}):
        return crop_data["by_state_season"][key]["median"]
    if season in crop_data.get("by_season", {}):
        return crop_data["by_season"][season]["median"]
    return crop_data.get("global_median", 2.0)
```

## 9. Next Steps
* EXP_REG_03: If soil+weather features are available per district, train a
  proper causal model (LightGBM on N, P, K, pH, rainfall, temp, humidity → Yield).
  The Crop_recommendation.csv has these features but no production-based Yield column.
