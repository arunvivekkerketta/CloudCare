"""
Clinical Recommendation Engine for Patient Health Risk Agent.

Generates personalized, actionable, evidence-aligned recommendations
categorized into:
- Urgent Medical Actions
- Target Biomarkers (ADA, ACC/AHA, KDIGO standards)
- Lifestyle & Dietary Interventions
- Diagnostic & Monitoring Schedules

Every recommendation maintains backward-compatible fields ('message', 'priority')
while providing enriched clinical context ('category', 'target_goal', 'timeline').
"""

from typing import Dict, List, Optional


def _make_rec(
    message: str,
    priority: str = "medium",
    category: str = "Lifestyle",
    target_goal: Optional[str] = None,
    timeline: str = "Routine (3-6 months)",
    condition: bool = True,
) -> Optional[Dict]:
    if not condition:
        return None
    return {
        "message": message,
        "priority": priority,
        "category": category,
        "target_goal": target_goal,
        "timeline": timeline,
    }


def generate_recommendations(patient: Dict, risk_results: Dict) -> Dict:
    """
    patient: dict of clinical feature values
    risk_results: {disease: {"probability": float, "level": str}, ...}
    Returns:
      {
        "general": [...],
        "diabetes_risk": [...],
        "heart_disease_risk": [...],
        "kidney_disease_risk": [...],
        "stroke_risk": [...],
        "action_plan": {
            "urgent_actions": [...],
            "biomarker_targets": [...],
            "lifestyle_prescriptions": [...],
            "monitoring_schedule": [...]
        }
      }
    """
    recs: Dict[str, List[Dict]] = {
        "general": [],
        "diabetes_risk": [],
        "heart_disease_risk": [],
        "kidney_disease_risk": [],
        "stroke_risk": [],
    }

    def add(bucket: str, condition: bool, message: str, priority: str = "medium",
            category: str = "Lifestyle", target_goal: Optional[str] = None,
            timeline: str = "Routine"):
        item = _make_rec(message, priority, category, target_goal, timeline, condition)
        if item:
            recs[bucket].append(item)

    # Extract patient indicators safely
    bmi = float(patient.get("bmi", 0))
    sbp = float(patient.get("systolic_bp", 0))
    dbp = float(patient.get("diastolic_bp", 0))
    glucose = float(patient.get("fasting_glucose", 0))
    hba1c = float(patient.get("hba1c", 0))
    ldl = float(patient.get("ldl_cholesterol", 0))
    hdl = float(patient.get("hdl_cholesterol", 0))
    trig = float(patient.get("triglycerides", 0))
    egfr = float(patient.get("egfr", 100))
    creatinine = float(patient.get("creatinine", 1.0))
    uacr = float(patient.get("uacr", 10.0))
    smoker = int(patient.get("smoker", 0))
    alcohol = int(patient.get("alcohol_use", 0))
    activity = int(patient.get("physical_activity_score", 2))
    waist = float(patient.get("waist_circumference", 0))
    sex = int(patient.get("sex", 0))
    rhr = float(patient.get("resting_heart_rate", 72))
    spo2 = float(patient.get("spo2", 98))
    diet_quality = int(patient.get("diet_quality", 2))
    stress_level = int(patient.get("stress_level", 1))
    sleep_hours = float(patient.get("sleep_hours", 7.0))
    on_antihypertensives = int(patient.get("on_antihypertensives", 0))
    on_statins = int(patient.get("on_statins", 0))
    on_diabetes_meds = int(patient.get("on_diabetes_meds", 0))


    # ---- 1. General Lifestyle & Metabolic Prescriptions ----
    add(
        "general",
        bmi >= 30,
        f"BMI is {bmi:.1f} (obese class). An initial 5-10% weight loss goal creates profound reductions across cardiometabolic risks.",
        priority="high",
        category="Nutrition & Weight",
        target_goal="5-10% body weight reduction over 6 months",
        timeline="Immediate (1-2 weeks)",
    )
    add(
        "general",
        25 <= bmi < 30,
        f"BMI is {bmi:.1f} (overweight). Dietary adjustments and portion control can prevent progression to obesity.",
        priority="medium",
        category="Nutrition & Weight",
        target_goal="BMI < 25.0 kg/m²",
        timeline="Within 1 month",
    )
    add(
        "general",
        smoker == 1,
        "Active tobacco use identified. Smoking cessation is the single most potent modifiable step to reduce cardiovascular, stroke, and renal risk.",
        priority="high",
        category="Clinical Intervention",
        target_goal="Complete tobacco cessation / consider nicotine replacement or pharmacotherapy",
        timeline="Immediate (1-2 weeks)",
    )
    add(
        "general",
        activity <= 1,
        "Sedentary or low physical activity level. Prescribe 150 min/week moderate-intensity aerobic exercise plus resistance training twice weekly.",
        priority="medium",
        category="Physical Activity",
        target_goal=">= 150 minutes/week moderate aerobic activity",
        timeline="Within 2-4 weeks",
    )
    add(
        "general",
        waist > (102 if sex == 1 else 88),
        f"Waist circumference ({waist:.1f} cm) exceeds threshold for visceral adiposity and insulin resistance.",
        priority="medium",
        category="Metabolic Health",
        target_goal="Waist < 102 cm (men) / < 88 cm (women)",
        timeline="Routine (3 months)",
    )
    add(
        "general",
        alcohol == 1,
        "Moderate/frequent alcohol intake can exacerbate hypertension and hypertriglyceridemia. Limit to <= 1 drink/day (women) or <= 2 drinks/day (men).",
        priority="low",
        category="Lifestyle",
        target_goal="Moderate or minimal alcohol consumption",
        timeline="Routine",
    )
    add(
        "general",
        diet_quality <= 1,
        f"Diet quality is rated {'Poor' if diet_quality == 0 else 'Fair'} (score {diet_quality}/3). Transition to a Mediterranean or DASH dietary pattern rich in vegetables, whole grains, and healthy fats while reducing ultra-processed foods and sodium.",
        priority="high" if diet_quality == 0 else "medium",
        category="Nutrition & Diet",
        target_goal="Diet Quality Score >= 2 (Good or Excellent)",
        timeline="Within 1 month",
    )
    add(
        "general",
        stress_level >= 2,
        f"Chronic stress level is {'Severe' if stress_level >= 3 else 'Moderate'} (score {stress_level}/3). Psychosocial stress independently elevates cortisol, BP, and cardiovascular risk. Prescribe mindfulness-based stress reduction (MBSR), CBT, or behavioral health referral.",
        priority="high" if stress_level >= 3 else "medium",
        category="Mental & Behavioral Health",
        target_goal="Stress Level <= 1 (Mild or None)",
        timeline="Within 2-4 weeks",
    )
    add(
        "general",
        sleep_hours < 6 or sleep_hours > 9,
        f"Sleep duration ({sleep_hours:.1f} hrs/night) is outside optimal range (7-9 hrs). Insufficient or excessive sleep is associated with elevated cardiometabolic risk, increased cortisol, and insulin resistance. Screen for sleep apnea if < 6 hours.",
        priority="medium",
        category="Sleep Health",
        target_goal="7 - 9 hours nightly",
        timeline="Within 1 month",
    )
    add(
        "general",
        rhr > 100,
        f"Resting heart rate is elevated at {rhr:.0f} bpm. Persistent tachycardia is a marker of autonomic dysregulation and cardiometabolic stress. Clinical evaluation for arrhythmia, thyroid disease, or dehydration is warranted.",
        priority="medium",
        category="Cardiovascular Monitoring",
        target_goal="Resting HR 60-80 bpm (optimal range)",
        timeline="Within 2-4 weeks",
    )
    add(
        "general",
        spo2 < 95,
        f"SpO2 is {spo2:.0f}% — below the normal threshold of 95%. This may indicate pulmonary compromise or nocturnal hypoxemia. Pulmonology referral and sleep study are recommended.",
        priority="high" if spo2 < 90 else "medium",
        category="Pulmonary & Respiratory",
        target_goal="SpO2 >= 95%",
        timeline="Immediate (1-2 weeks)" if spo2 < 90 else "Within 1 month",
    )

    # ---- Medication-Aware Guidance ----
    add(
        "heart_disease_risk",
        (sbp >= 140 or dbp >= 90) and on_antihypertensives == 0,
        f"Blood pressure is {sbp:.0f}/{dbp:.0f} mmHg and no antihypertensive therapy is recorded. Initiation of pharmacotherapy (ACEi, ARB, or CCB) is indicated per ACC/AHA guidelines.",
        priority="high",
        category="Pharmacotherapy Gap",
        target_goal="Initiate antihypertensive therapy; target BP < 130/80 mmHg",
        timeline="Immediate (1-2 weeks)",
    )
    add(
        "heart_disease_risk",
        (sbp >= 140 or dbp >= 90) and on_antihypertensives == 1,
        f"Blood pressure is {sbp:.0f}/{dbp:.0f} mmHg despite current antihypertensive therapy. Medication intensification or combination therapy (add diuretic, CCB, or MRA) may be warranted. Reassess adherence and consider ambulatory BP monitoring.",
        priority="high",
        category="Medication Optimization",
        target_goal="Achieve BP < 130/80 mmHg with current or adjusted regimen",
        timeline="Within 2 weeks",
    )
    add(
        "heart_disease_risk",
        ldl >= 130 and on_statins == 0,
        f"LDL is {ldl:.0f} mg/dL and no statin therapy is recorded. Initiation of moderate-to-high intensity statin therapy is supported by ACC/AHA guidelines.",
        priority="high",
        category="Pharmacotherapy Gap",
        target_goal="Initiate statin; target LDL < 100 mg/dL (or < 70 mg/dL for high-risk)",
        timeline="Within 2-4 weeks",
    )
    add(
        "diabetes_risk",
        (glucose >= 126 or hba1c >= 6.5) and on_diabetes_meds == 0,
        f"Glycemic markers (Glucose: {glucose:.0f} mg/dL, HbA1c: {hba1c:.1f}%) meet diabetes diagnostic criteria but no diabetes medication is recorded. Initiation of first-line therapy (Metformin, SGLT2 inhibitor, or GLP-1 RA) is strongly recommended per ADA 2024 guidelines.",
        priority="high",
        category="Pharmacotherapy Gap",
        target_goal="HbA1c < 7.0% with appropriate first-line glycemic therapy",
        timeline="Immediate (1-2 weeks)",
    )

    # ---- 2. Type 2 Diabetes Guidance (ADA Guidelines) ----

    add(
        "diabetes_risk",
        glucose >= 126 or hba1c >= 6.5,
        f"Fasting glucose ({glucose:.0f} mg/dL) or HbA1c ({hba1c:.1f}%) meets criteria for Type 2 Diabetes. Prompt clinical evaluation and glycemic therapy needed.",
        priority="high",
        category="Diagnostic & Medical",
        target_goal="HbA1c < 7.0% (individualized) & Fasting Glucose 80-130 mg/dL",
        timeline="Immediate (1-2 weeks)",
    )
    add(
        "diabetes_risk",
        (100 <= glucose < 126) or (5.7 <= hba1c < 6.5),
        f"Glycemic markers indicate Prediabetes (glucose: {glucose:.0f} mg/dL, HbA1c: {hba1c:.1f}%). Intensive lifestyle modification can prevent diabetes development.",
        priority="medium",
        category="Prevention & Diet",
        target_goal="HbA1c < 5.7% & Fasting Glucose < 100 mg/dL",
        timeline="Within 1 month",
    )
    add(
        "diabetes_risk",
        patient.get("family_history_diabetes") == 1,
        "Positive family history of diabetes confers elevated lifetime risk. Maintain annual fasting glucose or HbA1c screening.",
        priority="low",
        category="Screening",
        target_goal="Annual Glycemic Panel",
        timeline="Annual",
    )

    # ---- 3. Heart Disease Guidance (ACC/AHA Guidelines) ----
    add(
        "heart_disease_risk",
        sbp >= 140 or dbp >= 90,
        f"Blood pressure ({sbp:.0f}/{dbp:.0f} mmHg) reflects Stage 2 Hypertension. Pharmacotherapy and dietary sodium restriction are indicated.",
        priority="high",
        category="Cardiovascular Management",
        target_goal="BP < 130/80 mmHg (ACC/AHA Guideline target)",
        timeline="Immediate (1-2 weeks)",
    )
    add(
        "heart_disease_risk",
        130 <= sbp < 140 or 80 <= dbp < 90,
        f"Stage 1 Hypertension ({sbp:.0f}/{dbp:.0f} mmHg). Adopt the DASH diet, restrict sodium (< 2,000 mg/day), and initiate home BP logging.",
        priority="medium",
        category="Cardiovascular Prevention",
        target_goal="BP < 120/80 mmHg",
        timeline="Within 1 month",
    )
    add(
        "heart_disease_risk",
        ldl >= 160,
        f"LDL cholesterol ({ldl:.0f} mg/dL) is substantially elevated. Discuss statin therapy and saturated-fat reduction with your physician.",
        priority="high",
        category="Lipid Management",
        target_goal="LDL-C < 100 mg/dL (or < 70 mg/dL if high cardiovascular risk)",
        timeline="Within 2-4 weeks",
    )
    add(
        "heart_disease_risk",
        130 <= ldl < 160,
        f"LDL cholesterol ({ldl:.0f} mg/dL) is borderline high. Increase soluble dietary fiber and plant stanols.",
        priority="medium",
        category="Lipid Management",
        target_goal="LDL-C < 100 mg/dL",
        timeline="Within 1-2 months",
    )
    add(
        "heart_disease_risk",
        hdl < 40,
        f"HDL cholesterol ({hdl:.0f} mg/dL) is low. Regular aerobic exercise and healthy fats (olive oil, omega-3s) can raise HDL.",
        priority="medium",
        category="Cardiovascular Health",
        target_goal="HDL-C > 40 mg/dL (men) / > 50 mg/dL (women)",
        timeline="Routine (3 months)",
    )
    add(
        "heart_disease_risk",
        trig >= 200,
        f"Triglycerides ({trig:.0f} mg/dL) are elevated. Minimize refined carbohydrates, added sugars, and alcohol.",
        priority="medium",
        category="Metabolic Health",
        target_goal="Triglycerides < 150 mg/dL",
        timeline="Within 1-2 months",
    )

    # ---- 4. Chronic Kidney Disease Guidance (KDIGO Guidelines) ----
    add(
        "kidney_disease_risk",
        egfr < 60,
        f"eGFR is {egfr:.0f} mL/min/1.73m² (indicative of CKD Stage 3 or greater). Comprehensive nephrology evaluation and urine albumin/creatinine ratio (uACR) test recommended.",
        priority="high",
        category="Nephrology Care",
        target_goal="Preserve renal function; tight BP (< 130/80) and glucose control",
        timeline="Immediate (1-2 weeks)",
    )
    add(
        "kidney_disease_risk",
        60 <= egfr < 90,
        f"eGFR is mildly decreased ({egfr:.0f} mL/min/1.73m²). Avoid nephrotoxic agents (e.g. chronic high-dose NSAIDs) and recheck kidney function semi-annually.",
        priority="medium",
        category="Renal Protection",
        target_goal="Maintain stable eGFR > 60 mL/min/1.73m²",
        timeline="Within 3 months",
    )
    add(
        "kidney_disease_risk",
        creatinine > 1.3,
        f"Serum creatinine ({creatinine:.2f} mg/dL) is elevated above normal reference ranges. Verify hydration status and repeat renal panel.",
        priority="medium",
        category="Renal Diagnostic",
        target_goal="Creatinine within reference range (0.6 - 1.2 mg/dL)",
        timeline="Within 1 month",
    )
    add(
        "kidney_disease_risk",
        uacr >= 300,
        f"Urine albumin-creatinine ratio (uACR) is {uacr:.0f} mg/g — indicating macroalbuminuria, a hallmark of significant glomerular injury. ACEi/ARB therapy is mandatory. Urgent nephrology referral and strict BP/glucose control are critical.",
        priority="high",
        category="Nephrology Care",
        target_goal="uACR < 30 mg/g with ACEi/ARB, BP < 130/80 mmHg, HbA1c < 7%",
        timeline="Immediate (1-2 weeks)",
    )
    add(
        "kidney_disease_risk",
        30 <= uacr < 300,
        f"uACR is {uacr:.0f} mg/g — microalbuminuria detected. This early marker of renal injury often precedes overt CKD. Initiate or confirm ACEi/ARB use, optimize blood pressure, and reassess uACR in 3 months.",
        priority="medium",
        category="Renal Protection",
        target_goal="uACR < 30 mg/g",
        timeline="Within 3 months",
    )


    # ---- 5. Stroke Prevention Guidance ----
    add(
        "stroke_risk",
        sbp >= 140,
        "Elevated systolic blood pressure is the predominant modifiable risk factor for ischemic and hemorrhagic stroke. Rigorous BP control must be prioritized.",
        priority="high",
        category="Stroke Prevention",
        target_goal="Consistent home and clinic SBP < 130 mmHg",
        timeline="Immediate (1-2 weeks)",
    )
    add(
        "stroke_risk",
        smoker == 1 and (sbp >= 130 or patient.get("family_history_stroke") == 1),
        "Combination of smoking and vascular risk factors compounds cerebrovascular threat. Consult clinician on smoking cessation and antiplatelet therapy suitability.",
        priority="high",
        category="Cerebrovascular Protection",
        target_goal="Smoking cessation + blood pressure normalization",
        timeline="Immediate (1-2 weeks)",
    )
    add(
        "stroke_risk",
        patient.get("family_history_stroke") == 1,
        "Family history of stroke noted. Stay alert to warning signs (BE-FAST: Balance, Eyes, Face, Arms, Speech, Time) and maintain regular vascular checkups.",
        priority="low",
        category="Education & Awareness",
        target_goal="Vascular screening & awareness",
        timeline="Annual",
    )

    # Sort each bucket by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    for bucket in ["general", "diabetes_risk", "heart_disease_risk", "kidney_disease_risk", "stroke_risk"]:
        recs[bucket].sort(key=lambda r: priority_order.get(r["priority"], 3))

    # Fallback if no issues identified for a condition
    for disease in ["diabetes_risk", "heart_disease_risk", "kidney_disease_risk", "stroke_risk"]:
        if not recs[disease] and risk_results.get(disease, {}).get("level") == "Low":
            recs[disease].append({
                "message": "Current clinical metrics reflect low statistical risk. Continue routine health maintenance and periodic preventative wellness checks.",
                "priority": "low",
                "category": "Maintenance",
                "target_goal": "Preserve current healthy biomarker ranges",
                "timeline": "Annual wellness visit",
            })

    # ---- Synthesize an integrated actionable clinical plan ----
    all_recs = []
    for bucket, items in recs.items():
        all_recs.extend(items)

    urgent_actions = [r for r in all_recs if r["priority"] == "high"]
    biomarker_targets = [
        {"biomarker": r["category"], "goal": r["target_goal"], "rationale": r["message"]}
        for r in all_recs if r.get("target_goal")
    ]
    lifestyle_prescriptions = [
        r for r in all_recs if r.get("category") in ["Lifestyle", "Nutrition & Weight", "Physical Activity", "Prevention & Diet"]
    ]
    monitoring_schedule = [
        {"timeline": r["timeline"], "focus": r["category"], "recommendation": r["message"]}
        for r in all_recs if r.get("timeline")
    ]

    recs["action_plan"] = {
        "urgent_actions": urgent_actions[:4],
        "biomarker_targets": biomarker_targets[:5],
        "lifestyle_prescriptions": lifestyle_prescriptions[:4],
        "monitoring_schedule": monitoring_schedule[:5],
    }

    return recs
