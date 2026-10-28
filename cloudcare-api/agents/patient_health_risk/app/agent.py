"""
PatientHealthRiskAgent — Core Multi-Disease Clinical Decision Support AI Agent.

Loads trained and calibrated pipelines for:
  - Type 2 Diabetes
  - Coronary Heart Disease
  - Chronic Kidney Disease
  - Stroke

Key capabilities:
  1. Calibrated multi-disease risk probability & tier assessment.
  2. Explainable AI (XAI) feature attribution with clinical drivers and protective factors.
  3. Cardiometabolic composite score & estimated vascular age.
  4. Counterfactual "What-If" intervention simulation (delta risk analysis).
  5. 10-Year aging & risk trajectory projection (Status Quo vs Interventions).
  6. Clinical Safety Lab Anomaly & Outlier Triage Engine.
  7. Automated EHR-ready clinical SOAP note generator.
  8. Conversational Clinical Consultation AI Agent (natural language consultation).
  9. Actionable clinical & lifestyle action plans (ADA, ACC/AHA, KDIGO standards).
"""

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression

from app.recommendations import generate_recommendations

ARTIFACT_DIR = BASE_DIR / "models" / "artifacts"

DISEASE_LABELS = {
    "diabetes_risk": "Type 2 Diabetes",
    "heart_disease_risk": "Heart Disease",
    "kidney_disease_risk": "Chronic Kidney Disease",
    "stroke_risk": "Stroke",
}

FEATURE_METADATA = {
    "age": {"label": "Age", "unit": "years", "normal": "< 65"},
    "sex": {"label": "Biological Sex", "unit": "", "normal": "0=F, 1=M"},
    "bmi": {"label": "Body Mass Index (BMI)", "unit": "kg/m²", "normal": "18.5 - 24.9"},
    "systolic_bp": {"label": "Systolic Blood Pressure", "unit": "mmHg", "normal": "< 120"},
    "diastolic_bp": {"label": "Diastolic Blood Pressure", "unit": "mmHg", "normal": "< 80"},
    "total_cholesterol": {"label": "Total Cholesterol", "unit": "mg/dL", "normal": "< 200"},
    "hdl_cholesterol": {"label": "HDL ('Good') Cholesterol", "unit": "mg/dL", "normal": "> 40 (M), > 50 (F)"},
    "ldl_cholesterol": {"label": "LDL ('Bad') Cholesterol", "unit": "mg/dL", "normal": "< 100"},
    "triglycerides": {"label": "Triglycerides", "unit": "mg/dL", "normal": "< 150"},
    "fasting_glucose": {"label": "Fasting Blood Glucose", "unit": "mg/dL", "normal": "70 - 99"},
    "hba1c": {"label": "Hemoglobin A1c", "unit": "%", "normal": "< 5.7%"},
    "creatinine": {"label": "Serum Creatinine", "unit": "mg/dL", "normal": "0.6 - 1.2"},
    "egfr": {"label": "Estimated GFR (Kidney Function)", "unit": "mL/min/1.73m²", "normal": "> 90"},
    "uacr": {"label": "Urine Albumin-Creatinine Ratio (uACR)", "unit": "mg/g", "normal": "< 30"},
    "waist_circumference": {"label": "Waist Circumference", "unit": "cm", "normal": "< 102 (M), < 88 (F)"},
    "resting_heart_rate": {"label": "Resting Heart Rate", "unit": "bpm", "normal": "60 - 100"},
    "spo2": {"label": "Blood Oxygen Saturation (SpO2)", "unit": "%", "normal": ">= 95"},
    "diet_quality": {"label": "Diet Quality Score", "unit": "0-3", "normal": ">= 2"},
    "stress_level": {"label": "Chronic Stress Level", "unit": "0-3", "normal": "0-1"},
    "sleep_hours": {"label": "Average Sleep Duration", "unit": "hrs/night", "normal": "7 - 9"},
    "on_antihypertensives": {"label": "On Antihypertensive Medication", "unit": "", "normal": "0"},
    "on_statins": {"label": "On Statin Therapy", "unit": "", "normal": "0"},
    "on_diabetes_meds": {"label": "On Diabetes Medication", "unit": "", "normal": "0"},
    "smoker": {"label": "Tobacco Smoking Status", "unit": "", "normal": "0 (Non-smoker)"},
    "alcohol_use": {"label": "Alcohol Intake Frequency", "unit": "", "normal": "0 (Infrequent/None)"},
    "physical_activity_score": {"label": "Physical Activity Level", "unit": "level 0-3", "normal": ">= 2"},
    "family_history_diabetes": {"label": "Family History of Diabetes", "unit": "", "normal": "0"},
    "family_history_heart": {"label": "Family History of Heart Disease", "unit": "", "normal": "0"},
    "family_history_kidney": {"label": "Family History of Kidney Disease", "unit": "", "normal": "0"},
    "family_history_stroke": {"label": "Family History of Stroke", "unit": "", "normal": "0"},
}


RISK_THRESHOLDS = [(0.30, "High"), (0.12, "Moderate"), (0.0, "Low")]

REQUIRED_FIELDS = [
    "age", "sex", "bmi", "systolic_bp", "diastolic_bp",
    "total_cholesterol", "hdl_cholesterol", "ldl_cholesterol",
    "triglycerides", "fasting_glucose", "hba1c",
    "creatinine", "egfr", "uacr", "waist_circumference",
    "resting_heart_rate", "spo2", "diet_quality", "stress_level", "sleep_hours",
    "on_antihypertensives", "on_statins", "on_diabetes_meds",
    "smoker", "alcohol_use", "physical_activity_score",
    "family_history_diabetes", "family_history_heart",
    "family_history_kidney", "family_history_stroke",
]

DEFAULTS = {
    "sex": 0, "smoker": 0, "alcohol_use": 0, "physical_activity_score": 2,
    "family_history_diabetes": 0, "family_history_heart": 0,
    "family_history_kidney": 0, "family_history_stroke": 0,
    "creatinine": 0.9, "egfr": 95.0, "hba1c": 5.3, "triglycerides": 110.0,
    "uacr": 10.0,
    "resting_heart_rate": 72.0,
    "spo2": 98.0,
    "diet_quality": 2,
    "stress_level": 1,
    "sleep_hours": 7.0,
    "on_antihypertensives": 0,
    "on_statins": 0,
    "on_diabetes_meds": 0,
}



def _risk_level(prob: float) -> str:
    for threshold, label in RISK_THRESHOLDS:
        if prob >= threshold:
            return label
    return "Low"


class PatientHealthRiskAgent:
    def __init__(self, artifact_dir: Path = ARTIFACT_DIR):
        self.artifact_dir = Path(artifact_dir)
        schema_path = self.artifact_dir / "schema.json"
        if not schema_path.exists():
            raise FileNotFoundError(
                f"No trained models found at {self.artifact_dir}. "
                "Run `python models/train_models.py` first."
            )
        with open(schema_path) as f:
            schema = json.load(f)
        self.feature_columns = schema["feature_columns"]
        self.target_columns = schema["target_columns"]
        self.decision_thresholds = schema.get("decision_thresholds", {})

        self.pipelines = {}
        for target in self.target_columns:
            model_file = self.artifact_dir / f"{target}.joblib"
            if not model_file.exists():
                raise FileNotFoundError(f"Model file missing: {model_file}")
            self.pipelines[target] = joblib.load(model_file)

    def _validate_and_fill(self, patient: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(patient)
        missing_required = []
        for field in REQUIRED_FIELDS:
            if field not in record or record[field] is None:
                if field in DEFAULTS:
                    record[field] = DEFAULTS[field]
                else:
                    missing_required.append(field)
        if missing_required:
            raise ValueError(f"Missing required patient fields with no default: {missing_required}")

        for col in self.feature_columns:
            if col in record:
                record[col] = float(record[col])
        return record

    def _top_contributors(self, target: str, record: Dict[str, Any], n: int = 3) -> List[Dict[str, Any]]:
        wrapper = self.pipelines[target]
        x = pd.DataFrame([record])[self.feature_columns]

        if isinstance(wrapper, CalibratedClassifierCV):
            fold_pipelines = [cc.estimator for cc in wrapper.calibrated_classifiers_]
        else:
            fold_pipelines = [wrapper]

        contributions_sum = None
        for pipeline in fold_pipelines:
            model = pipeline.named_steps["model"]
            scaler = pipeline.named_steps["scaler"]
            x_scaled = scaler.transform(x)[0]

            if hasattr(model, "coef_"):
                contributions = model.coef_[0] * x_scaled
            elif hasattr(model, "feature_importances_"):
                contributions = model.feature_importances_ * x_scaled
            else:
                contributions = np.zeros(len(self.feature_columns))

            contributions_sum = (
                contributions if contributions_sum is None else contributions_sum + contributions
            )

        avg_contrib = contributions_sum / len(fold_pipelines)

        abs_contrib = np.abs(avg_contrib)
        total_mag = float(np.sum(abs_contrib)) if np.sum(abs_contrib) > 0 else 1.0
        idx_sorted = np.argsort(-abs_contrib)[:n]

        results = []
        for i in idx_sorted:
            feat = self.feature_columns[i]
            val = record[feat]
            direction = "increases" if avg_contrib[i] > 0 else "decreases"
            rel_pct = round(float(abs_contrib[i] / total_mag * 100), 1)

            meta = FEATURE_METADATA.get(feat, {"label": feat, "unit": ""})
            lbl = meta["label"]
            unit = f" {meta['unit']}" if meta["unit"] else ""

            if direction == "increases":
                expl = f"{lbl} ({val}{unit}) is an active driver elevating risk."
            else:
                expl = f"{lbl} ({val}{unit}) is currently in a favorable range protective against risk."

            results.append({
                "feature": feat,
                "label": lbl,
                "value": val,
                "unit": meta.get("unit", ""),
                "effect": direction,
                "relative_impact_percent": rel_pct,
                "explanation": expl,
            })
        return results

    def detect_lab_anomalies(self, patient: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Evaluates patient metrics against established clinical guideline cutoff thresholds.
        """
        anomalies = []
        rec = self._validate_and_fill(patient)

        sbp = rec["systolic_bp"]
        dbp = rec["diastolic_bp"]
        if sbp >= 160 or dbp >= 100:
            anomalies.append({
                "biomarker": "Blood Pressure",
                "value": f"{sbp:.0f}/{dbp:.0f} mmHg",
                "severity": "Critical",
                "badge": "Stage 2 Severe HTN",
                "guideline": "ACC/AHA 2017",
                "action": "Urgent medical evaluation and combination antihypertensive therapy indicated.",
            })
        elif sbp >= 140 or dbp >= 90:
            anomalies.append({
                "biomarker": "Blood Pressure",
                "value": f"{sbp:.0f}/{dbp:.0f} mmHg",
                "severity": "Warning",
                "badge": "Stage 2 HTN",
                "guideline": "ACC/AHA 2017",
                "action": "Initiate or intensify antihypertensive therapy, DASH diet, sodium reduction.",
            })
        elif sbp >= 130 or dbp >= 80:
            anomalies.append({
                "biomarker": "Blood Pressure",
                "value": f"{sbp:.0f}/{dbp:.0f} mmHg",
                "severity": "Elevated",
                "badge": "Stage 1 HTN",
                "guideline": "ACC/AHA 2017",
                "action": "Lifestyle therapy and home BP logging.",
            })

        glucose = rec["fasting_glucose"]
        hba1c = rec["hba1c"]
        if glucose >= 140 or hba1c >= 8.0:
            anomalies.append({
                "biomarker": "Glycemic Control",
                "value": f"Glucose {glucose:.0f} mg/dL, HbA1c {hba1c:.1f}%",
                "severity": "Critical",
                "badge": "Uncontrolled Glycemia",
                "guideline": "ADA Standards of Care 2024",
                "action": "Immediate clinical review for glycemic pharmacotherapy and diabetes education.",
            })
        elif glucose >= 126 or hba1c >= 6.5:
            anomalies.append({
                "biomarker": "Glycemic Control",
                "value": f"Glucose {glucose:.0f} mg/dL, HbA1c {hba1c:.1f}%",
                "severity": "Warning",
                "badge": "Diabetic Threshold",
                "guideline": "ADA Standards of Care 2024",
                "action": "Confirmatory diagnostic testing and initiation of first-line glycemic therapy.",
            })
        elif glucose >= 100 or hba1c >= 5.7:
            anomalies.append({
                "biomarker": "Glycemic Control",
                "value": f"Glucose {glucose:.0f} mg/dL, HbA1c {hba1c:.1f}%",
                "severity": "Elevated",
                "badge": "Prediabetes",
                "guideline": "ADA Standards of Care 2024",
                "action": "Intensive lifestyle intervention (weight loss, Mediterranean/low-glycemic diet).",
            })

        egfr = rec["egfr"]
        creat = rec["creatinine"]
        if egfr < 45 or creat > 2.0:
            anomalies.append({
                "biomarker": "Renal Function",
                "value": f"eGFR {egfr:.1f} mL/min/1.73m², Creatinine {creat:.2f} mg/dL",
                "severity": "Critical",
                "badge": "Moderate-Severe CKD (G3b+)",
                "guideline": "KDIGO 2024",
                "action": "Nephrology referral, avoid nephrotoxic drugs, monitor uACR and electrolytes.",
            })
        elif egfr < 60 or creat > 1.3:
            anomalies.append({
                "biomarker": "Renal Function",
                "value": f"eGFR {egfr:.1f} mL/min/1.73m², Creatinine {creat:.2f} mg/dL",
                "severity": "Warning",
                "badge": "CKD Stage 3a",
                "guideline": "KDIGO 2024",
                "action": "BP control (<130/80), SGLT2 inhibitor / ACEi/ARB discussion with clinician.",
            })

        ldl = rec["ldl_cholesterol"]
        if ldl >= 190:
            anomalies.append({
                "biomarker": "LDL Cholesterol",
                "value": f"{ldl:.0f} mg/dL",
                "severity": "Critical",
                "badge": "Severe Hypercholesterolemia",
                "guideline": "ACC/AHA Multisociety Guidelines",
                "action": "High-intensity statin indicated regardless of baseline 10-year risk.",
            })
        elif ldl >= 160:
            anomalies.append({
                "biomarker": "LDL Cholesterol",
                "value": f"{ldl:.0f} mg/dL",
                "severity": "Warning",
                "badge": "Elevated LDL",
                "guideline": "ACC/AHA Multisociety Guidelines",
                "action": "Statin therapy discussion, dietary saturated fat restriction.",
            })

        bmi = rec["bmi"]
        if bmi >= 35.0:
            anomalies.append({
                "biomarker": "Body Mass Index",
                "value": f"{bmi:.1f} kg/m²",
                "severity": "Warning",
                "badge": "Class 2+ Obesity",
                "guideline": "AACE / EASO Guidelines",
                "action": "Structured weight management, consider GLP-1 RA or bariatric metabolic therapy.",
            })
        elif bmi >= 30.0:
            anomalies.append({
                "biomarker": "Body Mass Index",
                "value": f"{bmi:.1f} kg/m²",
                "severity": "Elevated",
                "badge": "Class 1 Obesity",
                "guideline": "AACE / EASO Guidelines",
                "action": "5-10% weight reduction goal via caloric deficit and physical activity.",
            })

        if rec["smoker"] == 1:
            anomalies.append({
                "biomarker": "Tobacco Smoking",
                "value": "Active Smoker",
                "severity": "Critical",
                "badge": "High Modifiable Hazard",
                "guideline": "USPSTF Tobacco Cessation",
                "action": "Comprehensive cessation program, nicotine replacement, or varenicline.",
            })

        uacr = rec.get("uacr", 10.0)
        if uacr >= 300:
            anomalies.append({
                "biomarker": "Urine Albumin-Creatinine Ratio (uACR)",
                "value": f"{uacr:.0f} mg/g",
                "severity": "Critical",
                "badge": "Macroalbuminuria",
                "guideline": "KDIGO 2024",
                "action": "Urgent nephrology referral; ACEi/ARB therapy; strict BP and glucose control.",
            })
        elif uacr >= 30:
            anomalies.append({
                "biomarker": "Urine Albumin-Creatinine Ratio (uACR)",
                "value": f"{uacr:.0f} mg/g",
                "severity": "Warning",
                "badge": "Microalbuminuria",
                "guideline": "KDIGO 2024",
                "action": "Initiate ACEi/ARB if not on it; optimize BP and glucose; recheck uACR in 3 months.",
            })

        rhr = rec.get("resting_heart_rate", 72.0)
        if rhr > 100:
            anomalies.append({
                "biomarker": "Resting Heart Rate",
                "value": f"{rhr:.0f} bpm",
                "severity": "Warning",
                "badge": "Tachycardia",
                "guideline": "ACC/AHA 2019 Heart Rate Guidance",
                "action": "Evaluate for thyroid dysfunction, anemia, dehydration, or arrhythmia. Cardiology review if persistent.",
            })
        elif rhr < 50:
            anomalies.append({
                "biomarker": "Resting Heart Rate",
                "value": f"{rhr:.0f} bpm",
                "severity": "Elevated",
                "badge": "Bradycardia",
                "guideline": "ACC/AHA 2019",
                "action": "Assess for medication side effects (beta-blockers) or conduction abnormality. ECG recommended.",
            })

        spo2 = rec.get("spo2", 98.0)
        if spo2 < 90:
            anomalies.append({
                "biomarker": "Blood Oxygen Saturation (SpO2)",
                "value": f"{spo2:.0f}%",
                "severity": "Critical",
                "badge": "Hypoxemia",
                "guideline": "WHO / ATS Clinical Guidelines",
                "action": "Urgent clinical assessment; supplemental oxygen, pulmonary function evaluation, and possible hospitalization.",
            })
        elif spo2 < 95:
            anomalies.append({
                "biomarker": "Blood Oxygen Saturation (SpO2)",
                "value": f"{spo2:.0f}%",
                "severity": "Warning",
                "badge": "Low SpO2",
                "guideline": "WHO / ATS Clinical Guidelines",
                "action": "Screen for sleep apnea (consider polysomnography), COPD, or respiratory infection. Pulmonology referral if persistent.",
            })

        sleep_hrs = rec.get("sleep_hours", 7.0)
        if sleep_hrs < 6 or sleep_hrs > 9:
            severity = "Warning" if sleep_hrs < 5 or sleep_hrs > 10 else "Elevated"
            anomalies.append({
                "biomarker": "Sleep Duration",
                "value": f"{sleep_hrs:.1f} hrs/night",
                "severity": severity,
                "badge": "Abnormal Sleep Pattern",
                "guideline": "AHA / CDC Sleep Health Guidelines",
                "action": "Screen for sleep apnea, insomnia, or hypersomnia. Target 7-9 hours nightly. Consider CBT-I or sleep study.",
            })

        stress = rec.get("stress_level", 1)
        if stress >= 3:
            anomalies.append({
                "biomarker": "Chronic Stress Level",
                "value": f"Score {int(stress)}/3 (Severe)",
                "severity": "Warning",
                "badge": "High Psychosocial Stress",
                "guideline": "AHA / Lancet Lifestyle Medicine 2021",
                "action": "Refer to behavioral health. Stress-reduction strategies: mindfulness, CBT, structured exercise, and social support.",
            })
        elif stress >= 2:
            anomalies.append({
                "biomarker": "Chronic Stress Level",
                "value": f"Score {int(stress)}/3 (Moderate)",
                "severity": "Elevated",
                "badge": "Moderate Psychosocial Stress",
                "guideline": "AHA Lifestyle Medicine",
                "action": "Recommend mindfulness-based stress reduction (MBSR), regular exercise, and sleep hygiene protocols.",
            })

        return anomalies


    def compute_composite_score(self, risk_results: Dict[str, Any], patient: Dict[str, Any]) -> Dict[str, Any]:
        probs = [risk_results[k]["probability"] for k in self.target_columns]
        avg_prob = float(np.mean(probs))
        max_prob = float(np.max(probs))

        composite_index = round((0.6 * max_prob + 0.4 * avg_prob) * 100, 1)

        high_count = sum(1 for k in self.target_columns if risk_results[k]["level"] == "High")
        mod_count = sum(1 for k in self.target_columns if risk_results[k]["level"] == "Moderate")

        if composite_index >= 35:
            burden_tier = "High Burden"
        elif composite_index >= 15:
            burden_tier = "Moderate Burden"
        else:
            burden_tier = "Low / Optimal"

        chronological_age = float(patient.get("age", 50))
        sbp = float(patient.get("systolic_bp", 120))
        glucose = float(patient.get("fasting_glucose", 90))
        smoker = int(patient.get("smoker", 0))
        bmi = float(patient.get("bmi", 24))

        age_delta = 0.0
        if smoker:
            age_delta += 5.0
        if sbp > 140:
            age_delta += (sbp - 140) * 0.15 + 3.0
        elif sbp < 115:
            age_delta -= 2.0
        if glucose > 120:
            age_delta += (glucose - 120) * 0.08 + 2.0
        if bmi > 30:
            age_delta += (bmi - 30) * 0.4
        elif 18.5 <= bmi <= 24.9:
            age_delta -= 1.5

        estimated_vascular_age = max(18.0, round(chronological_age + age_delta, 1))

        return {
            "cardiometabolic_burden_score": composite_index,
            "burden_tier": burden_tier,
            "high_risk_conditions_count": high_count,
            "moderate_risk_conditions_count": mod_count,
            "chronological_age": chronological_age,
            "estimated_vascular_age": estimated_vascular_age,
            "vascular_age_delta": round(estimated_vascular_age - chronological_age, 1),
        }

    def generate_clinical_summary(self, patient: Dict[str, Any], report: Dict[str, Any]) -> str:
        risks = report["risks"]
        overall_flag = report["overall_flag"]
        composite = report["composite_score"]

        high_conditions = [v["disease"] for v in risks.values() if v["level"] == "High"]
        mod_conditions = [v["disease"] for v in risks.values() if v["level"] == "Moderate"]

        lines = [
            f"Patient Health Risk Profile: {overall_flag.upper()} RISK overall (Composite Burden Score: {composite['cardiometabolic_burden_score']}/100)."
        ]

        if high_conditions:
            lines.append(f"High-risk conditions identified: {', '.join(high_conditions)}.")
        elif mod_conditions:
            lines.append(f"Moderate-risk conditions identified: {', '.join(mod_conditions)}.")
        else:
            lines.append("No high or moderate risk thresholds exceeded across evaluated conditions.")

        vasc_delta = composite["vascular_age_delta"]
        if vasc_delta > 2:
            lines.append(
                f"Estimated vascular age is {composite['estimated_vascular_age']} years "
                f"(+{vasc_delta} years compared to chronological age of {composite['chronological_age']}), "
                "indicating accelerated cardiovascular aging driven by modifiable factors."
            )
        elif vasc_delta < -1:
            lines.append(
                f"Estimated vascular age is favorable at {composite['estimated_vascular_age']} years "
                f"({vasc_delta} years below chronological age)."
            )

        modifiable = []
        if patient.get("smoker") == 1:
            modifiable.append("tobacco smoking")
        if patient.get("systolic_bp", 0) >= 130:
            modifiable.append(f"elevated blood pressure ({patient.get('systolic_bp'):.0f} mmHg)")
        if patient.get("fasting_glucose", 0) >= 100:
            modifiable.append(f"elevated fasting glucose ({patient.get('fasting_glucose'):.0f} mg/dL)")
        if patient.get("bmi", 0) >= 28:
            modifiable.append(f"elevated BMI ({patient.get('bmi'):.1f})")

        if modifiable:
            lines.append(f"Key modifiable levers to target: {', '.join(modifiable)}.")

        return " ".join(lines)

    def predict(self, patient: Dict[str, Any]) -> Dict[str, Any]:
        record = self._validate_and_fill(patient)
        x = pd.DataFrame([record])[self.feature_columns]

        risk_results = {}
        for target in self.target_columns:
            prob = float(self.pipelines[target].predict_proba(x)[0, 1])
            risk_results[target] = {
                "disease": DISEASE_LABELS[target],
                "probability": round(prob, 4),
                "risk_percent": round(prob * 100, 1),
                "level": _risk_level(prob),
                "top_factors": self._top_contributors(target, record),
            }

        recs_bundle = generate_recommendations(record, risk_results)
        for target in self.target_columns:
            risk_results[target]["recommendations"] = recs_bundle[target]

        highest = max(risk_results.items(), key=lambda kv: kv[1]["probability"])
        composite_score = self.compute_composite_score(risk_results, record)
        anomalies = self.detect_lab_anomalies(record)

        report = {
            "patient_summary": {k: record[k] for k in self.feature_columns},
            "risks": risk_results,
            "composite_score": composite_score,
            "lab_anomalies": anomalies,
            "general_recommendations": recs_bundle["general"],
            "action_plan": recs_bundle.get("action_plan", {}),
            "highest_risk_condition": DISEASE_LABELS[highest[0]],
            "overall_flag": highest[1]["level"],
            "disclaimer": (
                "This output is a statistical decision-support estimate from machine-learning models, "
                "not a definitive clinical diagnosis. It is intended to assist and guide discussion with "
                "a qualified healthcare provider."
            ),
        }

        report["clinical_summary"] = self.generate_clinical_summary(record, report)
        return report

    def simulate_interventions(
        self, patient: Dict[str, Any], interventions: Dict[str, Any]
    ) -> Dict[str, Any]:
        baseline_record = self._validate_and_fill(patient)
        projected_record = dict(baseline_record)

        valid_mods = {}
        for k, v in interventions.items():
            if k in self.feature_columns and v is not None:
                projected_record[k] = float(v)
                valid_mods[k] = {
                    "original": baseline_record[k],
                    "target": float(v),
                    "label": FEATURE_METADATA.get(k, {}).get("label", k),
                    "unit": FEATURE_METADATA.get(k, {}).get("unit", ""),
                }

        baseline_report = self.predict(baseline_record)
        projected_report = self.predict(projected_record)

        comparison = {}
        for target in self.target_columns:
            b_risk = baseline_report["risks"][target]["risk_percent"]
            p_risk = projected_report["risks"][target]["risk_percent"]
            abs_diff = round(p_risk - b_risk, 1)
            rel_diff = round(((p_risk - b_risk) / b_risk * 100) if b_risk > 0 else 0.0, 1)

            comparison[target] = {
                "disease": DISEASE_LABELS[target],
                "baseline_risk_percent": b_risk,
                "projected_risk_percent": p_risk,
                "absolute_risk_delta": abs_diff,
                "relative_risk_change_percent": rel_diff,
                "baseline_level": baseline_report["risks"][target]["level"],
                "projected_level": projected_report["risks"][target]["level"],
            }

        b_comp = baseline_report["composite_score"]["cardiometabolic_burden_score"]
        p_comp = projected_report["composite_score"]["cardiometabolic_burden_score"]
        comp_diff = round(p_comp - b_comp, 1)

        b_vage = baseline_report["composite_score"]["estimated_vascular_age"]
        p_vage = projected_report["composite_score"]["estimated_vascular_age"]

        return {
            "applied_interventions": valid_mods,
            "disease_comparisons": comparison,
            "composite_burden_before": b_comp,
            "composite_burden_after": p_comp,
            "composite_burden_delta": comp_diff,
            "vascular_age_before": b_vage,
            "vascular_age_after": p_vage,
            "vascular_age_rejuvenation_years": round(b_vage - p_vage, 1),
            "projected_overall_flag": projected_report["overall_flag"],
            "summary": (
                f"Simulated interventions reduced cardiometabolic burden from {b_comp}% to {p_comp}% "
                f"({comp_diff:+0.1f}%). Estimated vascular age improved from {b_vage} to {p_vage} years "
                f"({b_vage - p_vage:+.1f} years rejuvenated)."
            ),
        }

    def simulate_trajectory(
        self, patient: Dict[str, Any], target_interventions: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Projects 10-year risk trajectories under:
          1. Status Quo (aging + typical unmanaged risk progression)
          2. Target Intervention (implementing evidence-based targets)
        """
        base_record = self._validate_and_fill(patient)
        years = [0, 3, 5, 10]

        # Status quo progression
        status_quo_points = []
        for yr in years:
            p_curr = dict(base_record)
            p_curr["age"] = min(95.0, base_record["age"] + yr)
            if yr > 0:
                p_curr["systolic_bp"] = min(210.0, base_record["systolic_bp"] + 0.45 * yr)
                p_curr["fasting_glucose"] = min(280.0, base_record["fasting_glucose"] + 0.35 * yr)
                p_curr["egfr"] = max(15.0, base_record["egfr"] - 0.75 * yr)
            rep = self.predict(p_curr)
            status_quo_points.append({
                "year": yr,
                "projected_age": p_curr["age"],
                "composite_burden": rep["composite_score"]["cardiometabolic_burden_score"],
                "diabetes_risk": rep["risks"]["diabetes_risk"]["risk_percent"],
                "heart_disease_risk": rep["risks"]["heart_disease_risk"]["risk_percent"],
                "kidney_disease_risk": rep["risks"]["kidney_disease_risk"]["risk_percent"],
                "stroke_risk": rep["risks"]["stroke_risk"]["risk_percent"],
            })

        # Intervention progression
        default_target = {
            "smoker": 0,
            "systolic_bp": min(124.0, base_record["systolic_bp"]),
            "diastolic_bp": min(78.0, base_record["diastolic_bp"]),
            "bmi": min(25.0, base_record["bmi"]),
            "fasting_glucose": min(100.0, base_record["fasting_glucose"]),
            "physical_activity_score": max(2, base_record["physical_activity_score"]),
        }
        if target_interventions:
            default_target.update(target_interventions)

        intervention_points = []
        for yr in years:
            p_curr = dict(base_record)
            p_curr["age"] = min(95.0, base_record["age"] + yr)
            if yr > 0:
                for k, v in default_target.items():
                    p_curr[k] = float(v)
            rep = self.predict(p_curr)
            intervention_points.append({
                "year": yr,
                "projected_age": p_curr["age"],
                "composite_burden": rep["composite_score"]["cardiometabolic_burden_score"],
                "diabetes_risk": rep["risks"]["diabetes_risk"]["risk_percent"],
                "heart_disease_risk": rep["risks"]["heart_disease_risk"]["risk_percent"],
                "kidney_disease_risk": rep["risks"]["kidney_disease_risk"]["risk_percent"],
                "stroke_risk": rep["risks"]["stroke_risk"]["risk_percent"],
            })

        return {
            "years": years,
            "status_quo": status_quo_points,
            "intervention": intervention_points,
            "10_year_burden_reduction": round(
                status_quo_points[-1]["composite_burden"] - intervention_points[-1]["composite_burden"], 1
            ),
        }

    def generate_soap_note(
        self, patient: Dict[str, Any], report: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generates structured clinical SOAP note for medical records / EHR export.
        """
        rec = self._validate_and_fill(patient)
        if report is None:
            report = self.predict(rec)

        comp = report["composite_score"]
        risks = report["risks"]
        plan = report.get("action_plan", {})
        anomalies = report.get("lab_anomalies", [])

        # Subjective
        smk_str = "Active smoker" if rec["smoker"] == 1 else "Non-smoker"
        alc_str = "Regular alcohol use" if rec["alcohol_use"] == 1 else "Low/none alcohol intake"
        act_levels = {0: "Sedentary", 1: "Light activity", 2: "Moderate exercise", 3: "Vigorously active"}
        act_str = act_levels.get(int(rec["physical_activity_score"]), "Moderate")

        fam_hx = []
        if rec["family_history_diabetes"]: fam_hx.append("Diabetes Mellitus")
        if rec["family_history_heart"]: fam_hx.append("Coronary Heart Disease")
        if rec["family_history_kidney"]: fam_hx.append("Renal Disease")
        if rec["family_history_stroke"]: fam_hx.append("Cerebrovascular Disease / Stroke")
        fam_str = ", ".join(fam_hx) if fam_hx else "Non-contributory"

        subjective = (
            f"Patient is a {rec['age']:.0f}-year-old {'male' if rec['sex']==1 else 'female'}. "
            f"Lifestyle: {smk_str}, {alc_str}, {act_str}. "
            f"Family History: Positive for {fam_str}."
        )

        # Objective
        objective_vitals = (
            f"Vitals & Physical: BP {rec['systolic_bp']:.0f}/{rec['diastolic_bp']:.0f} mmHg, "
            f"HR {rec.get('resting_heart_rate', 72):.0f} bpm, SpO2 {rec.get('spo2', 98):.0f}%, "
            f"BMI {rec['bmi']:.1f} kg/m², Waist {rec['waist_circumference']:.1f} cm.\n"
            f"Laboratory Data: Fasting Glucose {rec['fasting_glucose']:.0f} mg/dL, HbA1c {rec['hba1c']:.1f}%, "
            f"Lipid Panel (Total {rec['total_cholesterol']:.0f} / HDL {rec['hdl_cholesterol']:.0f} / "
            f"LDL {rec['ldl_cholesterol']:.0f} / Trig {rec['triglycerides']:.0f} mg/dL), "
            f"Creatinine (Serum Creatinine {rec['creatinine']:.2f} mg/dL, eGFR {rec['egfr']:.1f} mL/min/1.73m², "
            f"uACR {rec.get('uacr', 10):.0f} mg/g).\n"
            f"Lifestyle Metrics: Diet Quality {int(rec.get('diet_quality', 2))}/3, "
            f"Stress Level {int(rec.get('stress_level', 1))}/3, "
            f"Sleep {rec.get('sleep_hours', 7):.1f} hrs/night.\n"
            f"Current Medications: "
            f"{'Antihypertensives: Yes' if rec.get('on_antihypertensives') else 'Antihypertensives: No'}, "
            f"{'Statins: Yes' if rec.get('on_statins') else 'Statins: No'}, "
            f"{'Diabetes Meds: Yes' if rec.get('on_diabetes_meds') else 'Diabetes Meds: No'}."
        )


        # Assessment
        assessment_lines = [
            f"Overall Clinical Stratification: {report['overall_flag'].upper()} RISK.",
            f"Primary Threat: {report['highest_risk_condition']}.",
            f"Cardiometabolic Burden Score: {comp['cardiometabolic_burden_score']}/100 ({comp['burden_tier']}).",
            f"Estimated Vascular Age: {comp['estimated_vascular_age']:.1f} yrs ({comp['vascular_age_delta']:+.1f} yrs vs chronological).",
            "Disease Probability Breakdown:",
        ]
        for t, r in risks.items():
            top_str = f" (Driver: {r['top_factors'][0]['label']})" if r.get("top_factors") else ""
            assessment_lines.append(f"  - {r['disease']}: {r['risk_percent']:.1f}% [{r['level']}]{top_str}")

        if anomalies:
            assessment_lines.append("Clinical Lab Alerts:")
            for a in anomalies:
                assessment_lines.append(f"  * [{a['severity'].upper()}] {a['biomarker']} ({a['value']}): {a['badge']} - {a['action']}")

        assessment = "\n".join(assessment_lines)

        # Plan
        plan_lines = ["1. Medical / Pharmacotherapy:"]
        if plan.get("urgent_actions"):
            for u in plan["urgent_actions"]:
                plan_lines.append(f"   - {u['category']}: {u['message']}")
        else:
            plan_lines.append("   - Maintain preventative wellness goals and routine annual review.")

        plan_lines.append("\n2. Guideline Biomarker Targets:")
        for b in plan.get("biomarker_targets", [])[:4]:
            plan_lines.append(f"   - {b['biomarker']}: Target {b['goal']}")

        plan_lines.append("\n3. Lifestyle Prescriptions:")
        for l in plan.get("lifestyle_prescriptions", [])[:3]:
            plan_lines.append(f"   - {l['category']}: {l['message']}")

        plan_lines.append("\n4. Monitoring & Follow-Up:")
        for m in plan.get("monitoring_schedule", [])[:3]:
            plan_lines.append(f"   - {m['timeline']}: {m['focus']} ({m['recommendation']})")

        plan_text = "\n".join(plan_lines)

        full_soap_text = f"""================================================================================
CLINICAL ENCOUNTER NOTE (SOAP FORMAT)
Patient ID: {patient.get('patient_id', 'PT-RECORD')} | Date: Recent Evaluation
================================================================================

SUBJECTIVE:
{subjective}

OBJECTIVE:
{objective_vitals}

ASSESSMENT:
{assessment}

PLAN:
{plan_text}

DISCLAIMER:
{report['disclaimer']}
================================================================================
"""

        return {
            "subjective": subjective,
            "objective": objective_vitals,
            "assessment": assessment,
            "plan": plan_text,
            "full_text": full_soap_text,
        }

    def consult(
        self,
        patient: Dict[str, Any],
        query: str,
        chat_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Interactive clinical consultation engine answering queries based on
        patient record, predicted probabilities, and medical guidelines.
        """
        rec = self._validate_and_fill(patient)
        rep = self.predict(rec)
        q = query.lower()

        # Extract context
        highest = rep["highest_risk_condition"]
        overall = rep["overall_flag"]
        comp = rep["composite_score"]
        v_age = comp["estimated_vascular_age"]
        c_age = comp["chronological_age"]
        v_delta = comp["vascular_age_delta"]
        anomalies = rep.get("lab_anomalies", [])

        # Categorize query intent
        response_parts = []
        follow_ups = []

        if any(w in q for w in ["heart", "cardio", "cardiovascular", "bp", "blood pressure", "hypertension"]):
            r = rep["risks"]["heart_disease_risk"]
            response_parts.append(
                f"**Heart Disease Risk Assessment ({r['risk_percent']}% - {r['level']})**:\n"
                f"Your systolic blood pressure is currently {rec['systolic_bp']:.0f} mmHg and diastolic is {rec['diastolic_bp']:.0f} mmHg. "
                "According to the ACC/AHA guidelines, the target blood pressure for reducing cardiovascular risk is < 130/80 mmHg."
            )
            if rec["smoker"] == 1:
                response_parts.append(
                    "Smoking is compounding your cardiovascular threat. Tobacco cessation alone typically cuts coronary event risk by 50% within 12 months."
                )
            if rec["ldl_cholesterol"] >= 130:
                response_parts.append(
                    f"Your LDL cholesterol is {rec['ldl_cholesterol']:.0f} mg/dL (target is < 100 mg/dL, or < 70 mg/dL for high-risk patients). Discuss lipid-lowering therapy with your clinician."
                )
            follow_ups.extend([
                "How much would my heart risk decrease if my BP reaches 120/80?",
                "What dietary protocol is recommended for blood pressure?",
            ])

        elif any(w in q for w in ["diabetes", "sugar", "glucose", "hba1c", "glycemic"]):
            r = rep["risks"]["diabetes_risk"]
            response_parts.append(
                f"**Type 2 Diabetes Evaluation ({r['risk_percent']}% - {r['level']})**:\n"
                f"Your fasting glucose is {rec['fasting_glucose']:.0f} mg/dL and HbA1c is {rec['hba1c']:.1f}%. "
            )
            if rec["fasting_glucose"] >= 126 or rec["hba1c"] >= 6.5:
                response_parts.append(
                    "These metrics exceed the diagnostic threshold for Type 2 Diabetes according to the American Diabetes Association (ADA). Prompt physician evaluation for first-line therapies (such as metformin, SGLT2 inhibitors, or GLP-1 receptor agonists) and glucose monitoring is advised."
                )
            elif rec["fasting_glucose"] >= 100 or rec["hba1c"] >= 5.7:
                response_parts.append(
                    "You are currently in the Prediabetes range. An initial 7% weight reduction combined with 150 minutes of weekly moderate exercise can lower your diabetes progression risk by up to 58%."
                )
            else:
                response_parts.append("Your glycemic markers are within the normal reference range.")
            follow_ups.extend([
                "What is my target HbA1c level?",
                "What are the best foods to stabilize blood glucose?",
            ])

        elif any(w in q for w in ["kidney", "renal", "egfr", "creatinine"]):
            r = rep["risks"]["kidney_disease_risk"]
            response_parts.append(
                f"**Renal Health Evaluation ({r['risk_percent']}% - {r['level']})**:\n"
                f"Your estimated GFR is {rec['egfr']:.1f} mL/min/1.73m² and Serum Creatinine is {rec['creatinine']:.2f} mg/dL."
            )
            if rec["egfr"] < 60:
                response_parts.append(
                    "An eGFR under 60 indicates Chronic Kidney Disease (Stage 3). KDIGO clinical practice guidelines emphasize tight blood pressure control (< 130/80 mmHg), urine albumin-to-creatinine ratio (uACR) quantification, and avoidance of nephrotoxic medications like chronic high-dose NSAIDs (e.g. ibuprofen)."
                )
            else:
                response_parts.append("Your renal filtration markers currently reflect stable kidney function.")
            follow_ups.extend([
                "How does high blood pressure damage kidneys?",
                "What lab tests should be repeated for kidney monitoring?",
            ])

        elif any(w in q for w in ["stroke", "brain", "tia", "cerebrovascular"]):
            r = rep["risks"]["stroke_risk"]
            response_parts.append(
                f"**Stroke Risk Analysis ({r['risk_percent']}% - {r['level']})**:\n"
                f"Your calibrated statistical risk of stroke is {r['risk_percent']}% ({r['level']} risk tier). "
                f"The predominant driver is your blood pressure ({rec['systolic_bp']:.0f}/{rec['diastolic_bp']:.0f} mmHg). "
                "Normalizing systolic BP to < 130 mmHg is the most powerful preventative step against both ischemic and hemorrhagic stroke."
            )
            follow_ups.extend([
                "What are the warning signs of stroke (BE-FAST)?",
                "What medications help prevent stroke in high-risk patients?",
            ])

        elif any(w in q for w in ["vascular age", "age", "how old"]):
            response_parts.append(
                f"**Cardiometabolic & Vascular Age Assessment**:\n"
                f"Your chronological age is {c_age:.0f} years, but your estimated vascular age is **{v_age:.1f} years** "
                f"({v_delta:+.1f} years difference). "
            )
            if v_delta > 0:
                response_parts.append(
                    f"This indicates accelerated arterial stiffening and vascular aging, primarily driven by {'smoking, ' if rec['smoker'] else ''}"
                    f"elevated blood pressure ({rec['systolic_bp']:.0f} mmHg), and BMI ({rec['bmi']:.1f}). "
                    "Through targeted interventions, vascular aging can be significantly slowed and partially reversed."
                )
            else:
                response_parts.append("Your vascular system demonstrates favorable biological resilience relative to your age.")
            follow_ups.extend([
                "How many years can I rejuvenate my vascular age by losing weight and quitting smoking?",
                "Show me the What-If simulation for vascular rejuvenation.",
            ])

        elif any(w in q for w in ["medication", "drugs", "prescribe", "pill", "therapy"]):
            response_parts.append(
                "**Clinical Guideline Pharmacotherapy Discussion Topics**:\n"
                "Based on your clinical markers, here are the evidence-based therapies frequently evaluated with physicians:\n"
            )
            if rec["systolic_bp"] >= 130:
                response_parts.append("- **Antihypertensives**: ACE inhibitors (e.g. Lisinopril), ARBs (e.g. Losartan), or CCBs (e.g. Amlodipine) for BP reduction and organ protection.")
            if rec["ldl_cholesterol"] >= 130 or r.get("heart_disease_risk", {}).get("risk_percent", 0) > 15:
                response_parts.append("- **Lipid-Lowering Statins**: Moderate or high-intensity statin therapy (e.g. Atorvastatin, Rosuvastatin) to stabilize plaque and lower LDL.")
            if rec["fasting_glucose"] >= 126 or rec["hba1c"] >= 6.5:
                response_parts.append("- **Glycemic Therapies**: SGLT2 inhibitors (e.g. Empagliflozin) or GLP-1 receptor agonists (e.g. Semaglutide) which offer proven renal and cardiovascular risk reduction.")
            response_parts.append("\n*Note: Medications must always be prescribed and monitored by your licensed physician.*")
            follow_ups.extend([
                "What lifestyle steps can replace or reduce the need for medications?",
                "What questions should I ask my doctor at my next visit?",
            ])

        elif any(w in q for w in ["lifestyle", "diet", "exercise", "weight", "food", "eat"]):
            response_parts.append(
                "**Personalized Lifestyle & Nutrition Plan**:\n"
                f"1. **Nutrition**: Adopt the Mediterranean or DASH dietary pattern—rich in leafy greens, extra-virgin olive oil, legumes, and lean protein while limiting sodium to < 2,000 mg/day.\n"
                f"2. **Physical Activity**: Aim for at least 150 minutes of moderate aerobic exercise per week (e.g. brisk walking) plus two resistance sessions.\n"
            )
            if rec["bmi"] >= 27:
                response_parts.append(
                    f"3. **Target Weight Reduction**: Current BMI is {rec['bmi']:.1f}. An initial goal of 5-10% weight loss ({round(rec['bmi']*0.07, 1)} BMI points) will deliver substantial drops in glucose, BP, and triglycerides."
                )
            if rec["smoker"] == 1:
                response_parts.append("4. **Smoking Cessation**: Prioritize smoking cessation as the primary modifiable risk intervention.")
            follow_ups.extend([
                "What is my projected 10-year risk trajectory if I follow this plan?",
                "What are my urgent clinical action items?",
            ])

        else:
            # Comprehensive Executive Overview
            response_parts.append(
                f"**Clinical Summary for Patient Record**:\n"
                f"- **Overall Risk Tier**: {overall.upper()}\n"
                f"- **Primary Threat**: {highest}\n"
                f"- **Cardiometabolic Burden Score**: {comp['cardiometabolic_burden_score']}/100 ({comp['burden_tier']})\n"
                f"- **Estimated Vascular Age**: {v_age:.1f} years ({v_delta:+.1f} years)\n\n"
                f"**Multi-Condition Probabilities**:\n"
                f"- Type 2 Diabetes: {rep['risks']['diabetes_risk']['risk_percent']}%\n"
                f"- Heart Disease: {rep['risks']['heart_disease_risk']['risk_percent']}%\n"
                f"- Chronic Kidney Disease: {rep['risks']['kidney_disease_risk']['risk_percent']}%\n"
                f"- Stroke: {rep['risks']['stroke_risk']['risk_percent']}%\n\n"
                f"**Key Recommendation**: Focus on blood pressure normalization (<130/80 mmHg), "
                f"{'tobacco cessation, ' if rec['smoker'] else ''}and targeted weight management."
            )
            follow_ups.extend([
                "Why is my primary threat flagged as " + highest + "?",
                "What if I improve my lifestyle? Simulate the results.",
                "Generate a physician consultation note.",
            ])

        return {
            "query": query,
            "response": "\n\n".join(response_parts),
            "suggested_follow_ups": follow_ups[:3],
            "highest_risk_condition": highest,
            "overall_flag": overall,
        }

    def predict_batch(self, patients: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self.predict(p) for p in patients]


if __name__ == "__main__":
    agent = PatientHealthRiskAgent()
    sample = {
        "age": 58, "sex": 1, "bmi": 31.2, "systolic_bp": 148, "diastolic_bp": 92,
        "total_cholesterol": 240, "hdl_cholesterol": 38, "ldl_cholesterol": 165,
        "triglycerides": 210, "fasting_glucose": 132, "hba1c": 6.8,
        "creatinine": 1.4, "egfr": 68, "waist_circumference": 106,
        "smoker": 1, "alcohol_use": 1, "physical_activity_score": 1,
        "family_history_diabetes": 1, "family_history_heart": 1,
        "family_history_kidney": 0, "family_history_stroke": 1,
    }
    rep = agent.predict(sample)
    print("Baseline:")
    print(f"Overall Flag: {rep['overall_flag']}, Highest: {rep['highest_risk_condition']}")
    print(f"Summary: {rep['clinical_summary']}")

    print("\nAnomalies Detected:")
    for a in rep["lab_anomalies"]:
        print(f"  [{a['severity']}] {a['biomarker']}: {a['badge']} -> {a['action']}")

    print("\nSimulating 10-Year Trajectory:")
    traj = agent.simulate_trajectory(sample)
    print(f"  10-Year Burden Reduction: {traj['10_year_burden_reduction']}%")

    print("\nConsultation Test:")
    consult_res = agent.consult(sample, "What are my main heart disease risks and medications?")
    print(consult_res["response"])
