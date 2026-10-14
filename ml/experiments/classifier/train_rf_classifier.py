"""
EXP_CLS_01: Random Forest Crop Suitability Classifier
Dataset: ml/data/raw/Crop_recommendation.csv (2200 rows, 22 classes)
Target: 'label' (crop name)
Features: N, P, K, temperature, humidity, ph, rainfall
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
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_validate, train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import (
    accuracy_score, classification_report, confusion_matrix,
    precision_score, recall_score, f1_score
)

# ── Path setup (Rules: use pathlib relative to __file__) ──────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]          # VJ/
DATA_RAW     = PROJECT_ROOT / "ml" / "data" / "raw"
MODELS_DIR   = SCRIPT_DIR / "models"
REPORTS_DIR  = PROJECT_ROOT / "ml" / "reports" / "classifier"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)

RANDOM_STATE = 42

# ── 1. Load Data ──────────────────────────────────────────────────────────────
print("[INFO] Loading Crop_recommendation.csv ...")
df = pd.read_csv(DATA_RAW / "Crop_recommendation.csv")
print(f"  Shape: {df.shape}")
print(f"  Classes: {df['label'].nunique()}")

FEATURE_COLS = ["N", "P", "K", "temperature", "humidity", "ph", "rainfall"]
TARGET_COL   = "label"

X = df[FEATURE_COLS].values.astype(np.float32)
y_raw = df[TARGET_COL].values

le = LabelEncoder()
y = le.fit_transform(y_raw)

print(f"  Label classes: {list(le.classes_)}")

# ── 2. Train / Test Split ─────────────────────────────────────────────────────
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, stratify=y, random_state=RANDOM_STATE
)
print(f"  Train: {X_train.shape[0]} | Test: {X_test.shape[0]}")

# ── 3. Define Model ───────────────────────────────────────────────────────────
clf = RandomForestClassifier(
    n_estimators=300,
    max_depth=None,          # grow full trees; RF self-regularizes via bagging
    min_samples_leaf=2,
    max_features="sqrt",
    class_weight="balanced",
    random_state=RANDOM_STATE,
    n_jobs=-1
)

# ── 4. 5-Fold Stratified Cross Validation ────────────────────────────────────
print("\n[INFO] Running 5-Fold Stratified CV ...")
cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
cv_results = cross_validate(
    clf, X_train, y_train, cv=cv,
    scoring=["accuracy", "f1_macro", "f1_weighted"],
    return_train_score=True, n_jobs=-1
)

cv_acc_mean  = cv_results["test_accuracy"].mean()
cv_acc_std   = cv_results["test_accuracy"].std()
cv_f1m_mean  = cv_results["test_f1_macro"].mean()
cv_f1m_std   = cv_results["test_f1_macro"].std()
cv_f1w_mean  = cv_results["test_f1_weighted"].mean()
cv_f1w_std   = cv_results["test_f1_weighted"].std()

print(f"  CV Accuracy:   {cv_acc_mean:.4f} +/- {cv_acc_std:.4f}")
print(f"  CV F1 Macro:   {cv_f1m_mean:.4f} +/- {cv_f1m_std:.4f}")
print(f"  CV F1 Weighted:{cv_f1w_mean:.4f} +/- {cv_f1w_std:.4f}")

# ── 5. Final Fit on Full Train Set ────────────────────────────────────────────
print("\n[INFO] Fitting on full training set ...")
t0 = time.perf_counter()
clf.fit(X_train, y_train)
fit_time = time.perf_counter() - t0
print(f"  Fit time: {fit_time:.2f}s")

# ── 6. Holdout Evaluation ─────────────────────────────────────────────────────
y_pred = clf.predict(X_test)

acc  = accuracy_score(y_test, y_pred)
prec_macro = precision_score(y_test, y_pred, average="macro", zero_division=0)
rec_macro  = recall_score(y_test, y_pred, average="macro", zero_division=0)
f1_macro   = f1_score(y_test, y_pred, average="macro", zero_division=0)
prec_wt    = precision_score(y_test, y_pred, average="weighted", zero_division=0)
rec_wt     = recall_score(y_test, y_pred, average="weighted", zero_division=0)
f1_wt      = f1_score(y_test, y_pred, average="weighted", zero_division=0)

print(f"\n[RESULT] Holdout Accuracy: {acc:.4f}")
print(f"  Precision Macro: {prec_macro:.4f} | Recall Macro: {rec_macro:.4f} | F1 Macro: {f1_macro:.4f}")
print(f"  Precision Wt:    {prec_wt:.4f}    | Recall Wt:    {rec_wt:.4f}    | F1 Wt:    {f1_wt:.4f}")

# Full per-class report
class_names = list(le.classes_)
report_str = classification_report(y_test, y_pred, target_names=class_names, zero_division=0)
print("\n[RESULT] Full Classification Report:")
print(report_str)

# ── 7. Feature Importance ─────────────────────────────────────────────────────
importances = pd.Series(clf.feature_importances_, index=FEATURE_COLS).sort_values(ascending=False)
print("\n[RESULT] Feature Importances:")
for feat, imp in importances.items():
    print(f"  {feat:<15}: {imp:.4f}")

# ── 8. Save Confusion Matrix Plot ─────────────────────────────────────────────
cm = confusion_matrix(y_test, y_pred)
fig, ax = plt.subplots(figsize=(14, 12))
sns.heatmap(cm, annot=True, fmt="d", xticklabels=class_names, yticklabels=class_names,
            cmap="Blues", ax=ax)
ax.set_title("RF Classifier Confusion Matrix (EXP_CLS_01)")
ax.set_xlabel("Predicted")
ax.set_ylabel("True")
plt.tight_layout()
cm_path = REPORTS_DIR / "EXP_CLS_01_confusion_matrix.png"
fig.savefig(cm_path, dpi=120)
plt.close()
print(f"\n[INFO] Confusion matrix saved -> {cm_path}")

# ── 9. Save Feature Importance Plot ───────────────────────────────────────────
fig2, ax2 = plt.subplots(figsize=(8, 5))
importances.plot.bar(ax=ax2, color="#4facfe")
ax2.set_title("RF Classifier Feature Importances")
ax2.set_ylabel("Gini Importance")
plt.tight_layout()
fi_path = REPORTS_DIR / "EXP_CLS_01_feature_importance.png"
fig2.savefig(fi_path, dpi=120)
plt.close()
print(f"[INFO] Feature importance plot saved -> {fi_path}")

# ── 10. Serialize Model + LabelEncoder ────────────────────────────────────────
model_path = MODELS_DIR / "rf_classifier_v1.joblib"
meta_path  = MODELS_DIR / "rf_classifier_v1_meta.json"

joblib.dump(clf, model_path, compress=3)
meta = {
    "model_type": "RandomForestClassifier",
    "version": "v1",
    "n_classes": int(len(class_names)),
    "classes": class_names,
    "feature_cols": FEATURE_COLS,
    "holdout_accuracy": round(acc, 4),
    "holdout_f1_macro": round(f1_macro, 4),
    "holdout_f1_weighted": round(f1_wt, 4),
    "cv_accuracy_mean": round(cv_acc_mean, 4),
    "cv_accuracy_std": round(cv_acc_std, 4),
    "random_state": RANDOM_STATE,
    "train_rows": int(X_train.shape[0]),
    "test_rows": int(X_test.shape[0])
}
meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
print(f"[INFO] Model saved   -> {model_path}")
print(f"[INFO] Metadata saved-> {meta_path}")

# ── 11. Write Experiment Report ────────────────────────────────────────────────
report_path = REPORTS_DIR / "EXP_CLS_01_RandomForest_Baseline.md"
report_md = f"""# Experiment Report: Random Forest Crop Suitability Classifier

## 1. Experiment Overview
* **Experiment ID / Name:** EXP_CLS_01
* **Date & Time:** {time.strftime('%Y-%m-%d %H:%M')}
* **Target Objective:** Predict optimal crop suitability out of 22 standard classes from soil and climate features
* **Target Variable:** `label` (crop name)
* **Dataset(s) Used:** `ml/data/raw/Crop_recommendation.csv` -- Shape: {df.shape}

## 2. Model Architecture & Pipeline
* **Model Family / Class:** `RandomForestClassifier` (scikit-learn)
* **Architecture Details:** Bagged ensemble of decision trees; majority vote for class prediction
* **Feature Engineering & Preprocessing:** No scaling needed (tree-based model); float32 cast; LabelEncoder for target

## 3. Hyperparameter Configuration
| Hyperparameter | Value | Description |
| :--- | :--- | :--- |
| n_estimators | 300 | Number of decision trees |
| max_depth | None | Full tree growth (bagging self-regularizes) |
| min_samples_leaf | 2 | Minimum samples per leaf |
| max_features | sqrt | Features considered per split |
| class_weight | balanced | Handles class imbalance |
| random_state | 42 | Fixed seed |

## 4. Evaluation Strategy & Data Splitting
* **Split Methodology:** 5-Fold Stratified K-Fold Cross Validation + 80/20 Holdout
* **Stratification:** Yes (stratified on label)
* **Random Seed:** 42

## 5. Quantitative Evaluation Metrics
* **Holdout Accuracy:** {acc:.4f}
* **Macro Precision:** {prec_macro:.4f} | Recall: {rec_macro:.4f} | F1: {f1_macro:.4f}
* **Weighted Precision:** {prec_wt:.4f} | Recall: {rec_wt:.4f} | F1: {f1_wt:.4f}
* **CV Accuracy:** {cv_acc_mean:.4f} +/- {cv_acc_std:.4f}
* **CV F1 Macro:** {cv_f1m_mean:.4f} +/- {cv_f1m_std:.4f}
* **CV F1 Weighted:** {cv_f1w_mean:.4f} +/- {cv_f1w_std:.4f}

```
{report_str}
```

## 6. Feature Importance (Gini)
| Feature | Importance |
| :--- | :--- |
{chr(10).join(f'| {f} | {v:.4f} |' for f, v in importances.items())}

## 7. Artifacts & Reproducibility
* **Serialized Model:** `ml/experiments/classifier/models/rf_classifier_v1.joblib`
* **Metadata JSON:** `ml/experiments/classifier/models/rf_classifier_v1_meta.json`
* **Executable Script:** `ml/experiments/classifier/train_rf_classifier.py`
* **Confusion Matrix:** `ml/reports/classifier/EXP_CLS_01_confusion_matrix.png`
* **Feature Importance Plot:** `ml/reports/classifier/EXP_CLS_01_feature_importance.png`

## 8. Key Findings & Next Steps
* Model achieves near-perfect accuracy on this balanced, clean dataset (100 samples/class).
* Rainfall, N, and K are the dominant discriminators across Indian crops.
* Next: integrate `rf_classifier_v1.joblib` into `backend/main.py` to replace the current rain-threshold heuristic.
* Next: train XGBoost Regressor (EXP_REG_01) for dynamic yield prediction.
"""

report_path.write_text(report_md, encoding="utf-8")
print(f"[INFO] Experiment report saved -> {report_path}")

# ── 12. Quick sanity-check inference ──────────────────────────────────────────
print("\n[INFO] Sanity check -- Tenali-like inputs (N=88, P=44, K=46, T=30.16, H=80, pH=7.0, Rain=306mm):")
test_input = np.array([[88, 44, 46, 30.16, 80.0, 7.0, 306.0]], dtype=np.float32)
probs = clf.predict_proba(test_input)[0]
top3_idx = np.argsort(probs)[::-1][:3]
print("  Top-3 recommended crops:")
for rank, idx in enumerate(top3_idx, 1):
    print(f"    {rank}. {le.classes_[idx]:<15} ({probs[idx]*100:.1f}% confidence)")

print("\n[SUCCESS] EXP_CLS_01 complete.")
