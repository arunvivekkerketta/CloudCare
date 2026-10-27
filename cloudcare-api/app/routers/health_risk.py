"""Health Risk Assessment Router — integrates Patient Health Risk AI Agent.

Provides endpoints for:
- Single patient risk prediction (diabetes, heart, kidney, stroke)
- Batch risk prediction
- Counterfactual intervention simulation (what-if)
- 10-year aging trajectory projection
- Clinical consultation chat
- SOAP note generation
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Locate and import the Patient Health Risk AI Agent
# ---------------------------------------------------------------------------
# The agent lives in the downloaded project tree. We need to temporarily
# redirect Python's import system to find the AI Agents' "app" package.

_ai_agents_dir = None
for candidate in [
    Path(__file__).resolve().parent.parent.parent.parent / "Patient Health Risk" / "AI Agents",
    Path(__file__).resolve().parent.parent.parent.parent.parent / "Patient Health Risk" / "AI Agents",
    Path(r"C:\Users\akerk\Downloads\Patient Health Risk\AI Agents"),
]:
    if candidate.exists() and (candidate / "app" / "agent.py").exists():
        _ai_agents_dir = candidate
        break

if _ai_agents_dir is None:
    raise FileNotFoundError(
        "Patient Health Risk AI Agents directory not found. "
        f"Looked at: {Path(__file__).resolve().parent.parent.parent.parent / 'Patient Health Risk' / 'AI Agents'}"
    )

# Add AI Agents directories to sys.path BEFORE any CloudCare imports
_ai_agents_root = str(_ai_agents_dir)
_ai_agents_app = str(_ai_agents_dir / "app")

# Insert at front so they take precedence
if _ai_agents_root not in sys.path:
    sys.path.insert(0, _ai_agents_root)
if _ai_agents_app not in sys.path:
    sys.path.insert(0, _ai_agents_app)

# Now import CloudCare modules first (they need to be found via normal path)
from app.database import get_db
from app.deps import require_roles
from app.models import UserRole

# Lazy-load the agent to avoid import conflicts
PatientHealthRiskAgent = None
_agent_loaded = False

def _ensure_agent_loaded():
    global PatientHealthRiskAgent, _agent_loaded
    if _agent_loaded:
        return
    
    # Temporarily modify sys.path to load the agent from the correct location
    original_path = sys.path.copy()
    try:
        # Ensure AI Agents directories are at the front
        if _ai_agents_root not in sys.path:
            sys.path.insert(0, _ai_agents_root)
        if _ai_agents_app not in sys.path:
            sys.path.insert(0, _ai_agents_app)
        
        # Remove any cached app modules to force re-import from correct location
        _app_modules = [k for k in sys.modules if k == 'app' or k.startswith('app.')]
        _cached = {k: sys.modules.pop(k) for k in _app_modules}
        
        # Now import from the AI Agents' app package
        from app.agent import PatientHealthRiskAgent as PRA
        PatientHealthRiskAgent = PRA
        
        # Restore CloudCare's app modules
        for k, v in _cached.items():
            sys.modules[k] = v
        
    finally:
        # Restore original path
        sys.path = original_path
    
    _agent_loaded = True

# ---------------------------------------------------------------------------
# FastAPI imports
# ---------------------------------------------------------------------------
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Router setup
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/health-risk", tags=["health-risk"])

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class PatientRiskRecord(BaseModel):
    patient_id: Optional[str] = Field(None, description="Optional patient identifier")
    age: float = Field(..., ge=1, le=120, description="Age in years")
    sex: int = Field(..., description="0 = female, 1 = male")
    bmi: float = Field(..., ge=10, le=70, description="Body Mass Index (kg/m²)")
    systolic_bp: float = Field(..., ge=60, le=260, description="Systolic Blood Pressure (mmHg)")
    diastolic_bp: float = Field(..., ge=40, le=160, description="Diastolic Blood Pressure (mmHg)")
    total_cholesterol: float = Field(..., ge=80, le=500, description="Total Cholesterol (mg/dL)")
    hdl_cholesterol: float = Field(..., ge=10, le=150, description="HDL Cholesterol (mg/dL)")
    ldl_cholesterol: float = Field(..., ge=20, le=350, description="LDL Cholesterol (mg/dL)")
    triglycerides: Optional[float] = Field(None, ge=30, le=1000, description="Triglycerides (mg/dL)")
    fasting_glucose: float = Field(..., ge=40, le=450, description="Fasting Blood Glucose (mg/dL)")
    hba1c: Optional[float] = Field(None, ge=3.5, le=18.0, description="HbA1c (%)")
    creatinine: Optional[float] = Field(None, ge=0.2, le=15.0, description="Serum Creatinine (mg/dL)")
    egfr: Optional[float] = Field(None, ge=5, le=180, description="Estimated GFR (mL/min/1.73m²)")
    uacr: Optional[float] = Field(None, ge=0, le=10000, description="Urine Albumin-Creatinine Ratio (mg/g)")
    waist_circumference: float = Field(..., ge=40, le=200, description="Waist Circumference (cm)")
    resting_heart_rate: Optional[float] = Field(None, ge=30, le=200, description="Resting Heart Rate (bpm)")
    spo2: Optional[float] = Field(None, ge=50, le=100, description="Blood Oxygen Saturation SpO2 (%)")
    diet_quality: Optional[int] = Field(None, ge=0, le=3, description="Diet Quality: 0=Poor, 1=Fair, 2=Good, 3=Excellent")
    stress_level: Optional[int] = Field(None, ge=0, le=3, description="Chronic Stress Level: 0=None, 1=Mild, 2=Moderate, 3=Severe")
    sleep_hours: Optional[float] = Field(None, ge=2, le=16, description="Average nightly sleep duration (hours)")
    on_antihypertensives: Optional[int] = Field(None, description="Antihypertensive medication: 0=No, 1=Yes")
    on_statins: Optional[int] = Field(None, description="Statin therapy: 0=No, 1=Yes")
    on_diabetes_meds: Optional[int] = Field(None, description="Diabetes medication: 0=No, 1=Yes")
    smoker: int = Field(0, description="0 = non-smoker, 1 = active smoker")
    alcohol_use: int = Field(0, description="0 = none/rare, 1 = regular/heavy")
    physical_activity_score: int = Field(2, ge=0, le=3, description="0=Sedentary, 1=Light, 2=Moderate, 3=Active")
    family_history_diabetes: int = Field(0, description="0 = no, 1 = yes")
    family_history_heart: int = Field(0, description="0 = no, 1 = yes")
    family_history_kidney: int = Field(0, description="0 = no, 1 = yes")
    family_history_stroke: int = Field(0, description="0 = no, 1 = yes")


class SimulationRequest(BaseModel):
    patient: PatientRiskRecord
    interventions: Dict[str, float] = Field(..., description="Target values to simulate")


class ChatRequest(BaseModel):
    patient: PatientRiskRecord
    query: str = Field(..., description="User medical question or inquiry")
    chat_history: Optional[List[Dict[str, str]]] = Field(default=[], description="Previous conversation turns")


class TrajectoryRequest(BaseModel):
    patient: PatientRiskRecord
    target_interventions: Optional[Dict[str, float]] = Field(default=None, description="Optional target values")


# ---------------------------------------------------------------------------
# Agent singleton (lazy-loaded on first request)
# ---------------------------------------------------------------------------

_agent: Optional[PatientHealthRiskAgent] = None


def get_agent() -> PatientHealthRiskAgent:
    global _agent
    _ensure_agent_loaded()
    if _agent is None:
        _agent = PatientHealthRiskAgent()
    return _agent


# ---------------------------------------------------------------------------
# Clinical presets (benchmark patient profiles)
# ---------------------------------------------------------------------------

PRESETS = {
    "healthy_adult": {
        "name": "Healthy Adult (Low Risk Benchmark)",
        "patient_id": "PT-101-HEALTHY",
        "data": {
            "age": 32, "sex": 0, "bmi": 22.4,
            "systolic_bp": 112, "diastolic_bp": 74,
            "total_cholesterol": 170, "hdl_cholesterol": 65, "ldl_cholesterol": 90,
            "triglycerides": 85, "fasting_glucose": 82, "hba1c": 5.0,
            "creatinine": 0.8, "egfr": 105, "uacr": 8, "waist_circumference": 72,
            "resting_heart_rate": 65, "spo2": 99,
            "diet_quality": 3, "stress_level": 0, "sleep_hours": 8.0,
            "on_antihypertensives": 0, "on_statins": 0, "on_diabetes_meds": 0,
            "smoker": 0, "alcohol_use": 0, "physical_activity_score": 3,
            "family_history_diabetes": 0, "family_history_heart": 0,
            "family_history_kidney": 0, "family_history_stroke": 0,
        },
    },
    "borderline_metabolic": {
        "name": "Borderline Metabolic (Moderate Risk)",
        "patient_id": "PT-202-METABOLIC",
        "data": {
            "age": 49, "sex": 1, "bmi": 27.8,
            "systolic_bp": 132, "diastolic_bp": 84,
            "total_cholesterol": 205, "hdl_cholesterol": 46, "ldl_cholesterol": 130,
            "triglycerides": 160, "fasting_glucose": 108, "hba1c": 5.9,
            "creatinine": 1.0, "egfr": 88, "uacr": 22, "waist_circumference": 96,
            "resting_heart_rate": 78, "spo2": 97,
            "diet_quality": 1, "stress_level": 2, "sleep_hours": 6.5,
            "on_antihypertensives": 0, "on_statins": 0, "on_diabetes_meds": 0,
            "smoker": 0, "alcohol_use": 1, "physical_activity_score": 1,
            "family_history_diabetes": 1, "family_history_heart": 0,
            "family_history_kidney": 0, "family_history_stroke": 0,
        },
    },
    "high_risk_comorbid": {
        "name": "Multiple Comorbidities (High Risk)",
        "patient_id": "PT-303-HIGH-RISK",
        "data": {
            "age": 63, "sex": 1, "bmi": 33.5,
            "systolic_bp": 156, "diastolic_bp": 96,
            "total_cholesterol": 255, "hdl_cholesterol": 34, "ldl_cholesterol": 175,
            "triglycerides": 240, "fasting_glucose": 145, "hba1c": 7.4,
            "creatinine": 1.6, "egfr": 55, "uacr": 180, "waist_circumference": 112,
            "resting_heart_rate": 88, "spo2": 94,
            "diet_quality": 0, "stress_level": 3, "sleep_hours": 5.5,
            "on_antihypertensives": 1, "on_statins": 0, "on_diabetes_meds": 1,
            "smoker": 1, "alcohol_use": 1, "physical_activity_score": 0,
            "family_history_diabetes": 1, "family_history_heart": 1,
            "family_history_kidney": 1, "family_history_stroke": 1,
        },
    },
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/presets")
def get_presets():
    """Clinical benchmark patient presets for testing and demo."""
    return PRESETS


@router.get("/health")
def health_check():
    """Health status and loaded model inventory."""
    agent = get_agent()
    return {
        "status": "healthy",
        "agent": "PatientHealthRiskAgent",
        "models_loaded": list(agent.pipelines.keys()),
    }


@router.post("/predict")
def predict_risk(
    patient: PatientRiskRecord,
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Multi-disease probability, XAI attribution & lab anomaly audit."""
    try:
        record = patient.model_dump(exclude={"patient_id"}, exclude_none=True)
        report = get_agent().predict(record)
        if patient.patient_id:
            report["patient_id"] = patient.patient_id
        return report
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/predict/batch")
def predict_batch(
    patients: List[PatientRiskRecord],
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Multi-patient cohort prediction."""
    results = []
    for patient in patients:
        try:
            record = patient.model_dump(exclude={"patient_id"}, exclude_none=True)
            report = get_agent().predict(record)
            if patient.patient_id:
                report["patient_id"] = patient.patient_id
            results.append(report)
        except ValueError as e:
            results.append({"patient_id": patient.patient_id, "error": str(e)})
    return results


@router.post("/simulate")
def simulate_intervention(
    sim_req: SimulationRequest,
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Real-time counterfactual What-If intervention lab."""
    try:
        record = sim_req.patient.model_dump(exclude={"patient_id"}, exclude_none=True)
        result = get_agent().simulate_interventions(record, sim_req.interventions)
        if sim_req.patient.patient_id:
            result["patient_id"] = sim_req.patient.patient_id
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/chat")
def clinical_consultation_chat(
    chat_req: ChatRequest,
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Conversational Clinical AI Consultation Agent."""
    try:
        record = chat_req.patient.model_dump(exclude={"patient_id"}, exclude_none=True)
        consult_result = get_agent().consult(
            patient=record,
            query=chat_req.query,
            chat_history=chat_req.chat_history,
        )
        if chat_req.patient.patient_id:
            consult_result["patient_id"] = chat_req.patient.patient_id
        return consult_result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/trajectory")
def simulate_trajectory(
    traj_req: TrajectoryRequest,
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """10-year aging & cardiometabolic trajectory projection."""
    try:
        record = traj_req.patient.model_dump(exclude={"patient_id"}, exclude_none=True)
        trajectory_result = get_agent().simulate_trajectory(record)
        return trajectory_result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/soap-note")
def generate_soap_note(
    patient: PatientRiskRecord,
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Structured clinical SOAP encounter note generator."""
    try:
        record = patient.model_dump(exclude={"patient_id"}, exclude_none=True)
        note = get_agent().generate_soap_note(record)
        return note
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
