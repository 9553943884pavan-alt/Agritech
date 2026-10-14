# Project Operating Rules & Engineering Standards

This document establishes the mandatory development, dependency management, and experiment logging standards for the **AgriTech (VJ)** repository. All team members and AI coding assistants must strictly adhere to these protocols across the entire project lifecycle.

---

## 1. Environment & Dependency Management Protocol

### 1.1 Strict Virtual Environment Usage
* **Dedicated Environment:** All Python development, script execution, and testing must use the unified virtual environment located at `./venv/`.
* **No System-Level Installations:** Never execute package installation commands (`pip install`) directly on the global/system Python interpreter. All installs must target the project's virtual environment:
  ```powershell
  # Windows PowerShell
  .\venv\Scripts\pip install -r requirements.txt
  ```
* **Execution Standard:** Always execute Python scripts using the virtual environment's binary:
  ```powershell
  .\venv\Scripts\python <path_to_script.py>
  ```

### 1.2 Requirements Declaration Policy
* **Single Source of Truth:** The root [`requirements.txt`](file:///c:/Users/Pavan/Downloads/VJ/requirements.txt) is the authoritative manifest for all backend, data engineering, and machine learning dependencies.
* **Workflow for Adding Dependencies:**
  1. Determine the exact compatible package name and version range (e.g., `scikit-learn>=1.4.0, <2.0.0`).
  2. Append the requirement into the appropriate section of [`requirements.txt`](file:///c:/Users/Pavan/Downloads/VJ/requirements.txt).
  3. Install the dependency into the virtual environment using `.\venv\Scripts\pip install -r requirements.txt`.
  4. Test that the module imports cleanly inside `.\venv\Scripts\python`.

---

## 2. Machine Learning Experiment & Reporting Standards

Every completed experiment (whether exploratory, baseline, or hyperparameter tuning) must be formally recorded in a dedicated Markdown report.

### 2.1 File Organization & Naming
* **Classifier Experiments:** Save all classification reports in [`ml/reports/classifier/`](file:///c:/Users/Pavan/Downloads/VJ/ml/reports/classifier/).
* **Regressor Experiments:** Save all regression reports in [`ml/reports/regressor/`](file:///c:/Users/Pavan/Downloads/VJ/ml/reports/regressor/).
* **Naming Convention:** Use descriptive, timestamped or numbered filenames:
  * Pattern: `EXP_<MODEL_TYPE>_<MODEL_NAME>_<DESCRIPTION>.md`
  * Examples: `EXP_CLS_01_RandomForest_Baseline.md`, `EXP_REG_02_XGBoost_Tuned.md`.

### 2.2 Mandatory Report Structure
Every report must strictly include the following side headings and details:

```markdown
# Experiment Report: [Model Name / Task Description]

## 1. Experiment Overview
* **Experiment ID / Name:** [e.g., EXP_CLS_01]
* **Date & Time:** [YYYY-MM-DD HH:MM]
* **Target Objective:** [e.g., Predict optimal crop suitability out of 22 classes]
* **Target Variable:** [e.g., 'label' / 'Yield']
* **Dataset(s) Used:** [Raw/Processed file paths and shape/rows count]

## 2. Model Architecture & Pipeline
* **Model Family / Class:** [e.g., RandomForestClassifier, XGBRegressor, etc.]
* **Architecture Details:** [Base estimators, tree ensemble, loss functions, etc.]
* **Feature Engineering & Preprocessing:** [Scaling, encoding, missing value handling, column selections]

## 3. Hyperparameter Configuration
* Provide a comprehensive table listing all key hyperparameters:
  | Hyperparameter | Value | Description / Search Range |
  | :--- | :--- | :--- |
  | n_estimators | 100 | Number of decision trees |
  | max_depth | 12 | Maximum depth of each tree |
  | learning_rate | 0.05 | Boosting step size shrinkage |
  | random_state | 42 | Fixed seed for reproducibility |

## 4. Evaluation Strategy & Data Splitting
* **Split Methodology:** [e.g., 5-Fold Stratified K-Fold Cross Validation OR Train-Test Split (80/20)]
* **Stratification / Shuffling:** [State whether stratified or shuffled]
* **Random Seed:** [e.g., 42]

## 5. Quantitative Evaluation Metrics
* **For Classification:**
  * Overall Accuracy
  * Macro & Weighted Precision, Recall, and F1-Score
  * Confusion Matrix summary (top misclassifications)
  * Class-wise Performance Table (for imbalanced datasets)
* **For Regression:**
  * R² Score (Coefficient of Determination)
  * RMSE (Root Mean Squared Error)
  * MAE (Mean Absolute Error)
  * MAPE (Mean Absolute Percentage Error)
  * Residual Distribution Analysis

## 6. Feature Importance & Model Insights
* Top predictive features ranked by importance (Gini, Permutation, or SHAP values).
* Real-world agronomic alignment of learned relationships.

## 7. Artifacts & Reproducibility
* **Serialized Model Path:** [e.g., ml/experiments/classifier/models/rf_v1.joblib]
* **Executable Script / Notebook:** [Path to script used to run the experiment]
* **Generated Charts / Confusion Matrices:** [Paths to saved figures]

## 8. Key Findings & Next Steps
* Observations on underfitting / overfitting / variance.
* Actionable recommendations for the next iteration.
```

---

## 3. Cross-Platform Compatibility & Engineering Standards

* **Path Resolution:** Always use Python's `pathlib.Path` or `os.path` relative to `__file__`. Never hardcode absolute Windows drive paths (e.g., `C:\...`).
* **Console Output Encoding:** To prevent Windows `cp1252` encoding crashes (`UnicodeEncodeError`), use ASCII-safe characters (e.g., `Rs. ` instead of `₹`, `[SUCCESS]` instead of emojis).
* **Deterministic Execution:** Always set explicit random seeds (`random_state=42`) across train/test splits, cross-validators, and models to ensure identical reproduction.
