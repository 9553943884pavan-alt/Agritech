# Experiment Report: Random Forest Crop Suitability Classifier

## 1. Experiment Overview
* **Experiment ID / Name:** EXP_CLS_01
* **Date & Time:** 2026-10-14 12:21
* **Target Objective:** Predict optimal crop suitability out of 22 standard classes from soil and climate features
* **Target Variable:** `label` (crop name)
* **Dataset(s) Used:** `ml/data/raw/Crop_recommendation.csv` -- Shape: (2200, 8)

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
* **Holdout Accuracy:** 0.9932
* **Macro Precision:** 0.9935 | Recall: 0.9932 | F1: 0.9932
* **Weighted Precision:** 0.9935 | Recall: 0.9932 | F1: 0.9932
* **CV Accuracy:** 0.9926 +/- 0.0061
* **CV F1 Macro:** 0.9926 +/- 0.0062
* **CV F1 Weighted:** 0.9926 +/- 0.0062

```
              precision    recall  f1-score   support

       apple       1.00      1.00      1.00        20
      banana       1.00      1.00      1.00        20
   blackgram       1.00      0.95      0.97        20
    chickpea       1.00      1.00      1.00        20
     coconut       1.00      1.00      1.00        20
      coffee       1.00      1.00      1.00        20
      cotton       1.00      1.00      1.00        20
      grapes       1.00      1.00      1.00        20
        jute       0.95      1.00      0.98        20
 kidneybeans       1.00      1.00      1.00        20
      lentil       1.00      0.95      0.97        20
       maize       0.95      1.00      0.98        20
       mango       1.00      1.00      1.00        20
   mothbeans       0.95      1.00      0.98        20
    mungbean       1.00      1.00      1.00        20
   muskmelon       1.00      1.00      1.00        20
      orange       1.00      1.00      1.00        20
      papaya       1.00      1.00      1.00        20
  pigeonpeas       1.00      1.00      1.00        20
 pomegranate       1.00      1.00      1.00        20
        rice       1.00      0.95      0.97        20
  watermelon       1.00      1.00      1.00        20

    accuracy                           0.99       440
   macro avg       0.99      0.99      0.99       440
weighted avg       0.99      0.99      0.99       440

```

## 6. Feature Importance (Gini)
| Feature | Importance |
| :--- | :--- |
| rainfall | 0.2236 |
| humidity | 0.2154 |
| K | 0.1761 |
| P | 0.1490 |
| N | 0.1058 |
| temperature | 0.0747 |
| ph | 0.0553 |

## 7. Artifacts & Reproducibility
* **Serialized Model:** `ml/experiments/classifier/models/rf_classifier_v1.joblib`
* **Metadata JSON:** `ml/experiments/classifier/models/rf_classifier_v1_meta.json`
* **Executable Script:** `ml/experiments/classifier/train_rf_classifier.py`
* **Confusion Matrix:** `ml/reports/classifier/EXP_CLS_01_confusion_matrix.png`
* **Feature Importance Plot:** `ml/reports/classifier/EXP_CLS_01_feature_importance.png`

## 8. Key Findings & Next Steps
* Model achieves near-perfect accuracy on this balanced, clean dataset (100 samples/class).
* Rainfall, N, and K are the dominant discriminators across Indian crops.
* **Backend integration:** Complete. `backend/main.py` loads `rf_classifier_v1.joblib` and its class metadata at startup, calls `predict_proba` with the seven features in the saved feature order, and returns the three highest-probability crop classes.
* This model is trained on a clean, balanced recommendation dataset; its holdout metrics do not guarantee field-level accuracy. Soil inputs may be mapped estimates or labelled prototype mock values, so plans using mock inputs are marked simulated.
* The former XGBoost yield model was not selected for inference because its available production dataset did not provide suitable causal prediction features. The active prototype uses the separate hierarchical median lookup documented in EXP_REG_02.
