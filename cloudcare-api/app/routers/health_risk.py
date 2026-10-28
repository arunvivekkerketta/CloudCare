"""Health Risk Assessment Router — integrates Patient Health Risk AI Agent.

Provides endpoints for:
- Single patient risk prediction (diabetes, heart, kidney, stroke)
- Batch risk prediction
- Counterfactual intervention simulation (what-if)
- 10-year aging trajectory projection
- Clinical consultation chat
- SOAP note generation
"""
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from app.database import get_db
from app.deps import require_roles
from app.models import UserRole

logger = logging.getLogger("cloudcare.health_risk")

UNAVAILABLE_MESSAGE = "The health risk assistant is not available right now."

# ---------------------------------------------------------------------------
# Locate the optional Patient Health Risk AI Agent
# ---------------------------------------------------------------------------
# The agent is an optional external project, never part of this repository.
# CloudCare must start without it: only the /api/health-risk routes become
# unavailable (503). Set HEALTH_RISK_AGENT_DIR to the folder that contains
# the agent's "app/agent.py" to enable them.


def _candidate_dirs() -> list[Path]:
    configured = os.getenv("HEALTH_RISK_AGENT_DIR", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    # Only a folder committed inside this repository is discovered
    # automatically; everything else must be opted into explicitly.
    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(repo_root / "Patient Health Risk" / "AI Agents")
    return candidates


def _find_agent_dir() -> Path | None:
    for candidate in _candidate_dirs():
        try:
            if (candidate / "app" / "agent.py").is_file():
                return candidate
        except OSError:  # unreadable path: treat as absent, never crash startup
            continue
    return None


_ai_agents_dir = _find_agent_dir()
_ai_agents_root = str(_ai_agents_dir) if _ai_agents_dir else None
_ai_agents_app = str(_ai_agents_dir / "app") if _ai_agents_dir else None

# Lazy-loaded on first successful request.
PatientHealthRiskAgent = None
_agent: Any = None
_agent_loaded = False
_agent_failed = False


def agent_available() -> bool:
    """True when the optional agent folder was found at startup."""
    return _ai_agents_dir is not None


def _ensure_agent_loaded() -> None:
    global PatientHealthRiskAgent, _agent_loaded, _agent_failed
    if _agent_loaded or _agent_failed:
        return
    if _ai_agents_root is None or _ai_agents_app is None:
        raise HTTPException(status_code=503, detail=UNAVAILABLE_MESSAGE)

    # Temporarily modify sys.path to load the agent from its own tree.
    original_path = sys.path.copy()
    cached: dict[str, Any] = {}
    try:
        if _ai_agents_root not in sys.path:
            sys.path.insert(0, _ai_agents_root)
        if _ai_agents_app not in sys.path:
            sys.path.insert(0, _ai_agents_app)

        # Remove cached CloudCare "app" modules to force the agent's package.
        app_modules = [k for k in sys.modules if k == "app" or k.startswith("app.")]
        cached = {k: sys.modules.pop(k) for k in app_modules}

        from app.agent import PatientHealthRiskAgent as PRA

        PatientHealthRiskAgent = PRA
    except Exception:  # noqa: BLE001 — a broken optional agent must not 500
        _agent_failed = True
        logger.warning(
            "The health risk assistant could not be loaded from %s.",
            _ai_agents_dir,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail=UNAVAILABLE_MESSAGE) from None
    finally:
        for name, module in cached.items():
            sys.modules[name] = module
        sys.path = original_path

    _agent_loaded = True


def _require_agent() -> None:
    """Dependency gate for documentation and defence in depth."""
    if not agent_available():
        raise HTTPException(status_code=503, detail=UNAVAILABLE_MESSAGE)


class _AgentRequiredRoute(APIRoute):
    """Return the public 503 before auth/validation can answer first.

    A router dependency runs only after OAuth2 middleware dependencies, so an
    anonymous call would otherwise see 401. This guard runs at the very start
    of the route and covers every method, valid body or not.
    """

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request):
            if not agent_available():
                return JSONResponse(
                    status_code=503, content={"detail": UNAVAILABLE_MESSAGE}
                )
            return await handler(request)

        return guarded


# ---------------------------------------------------------------------------
# Router setup
# ---------------------------------------------------------------------------
router = APIRouter(
    prefix="/api/health-risk",
    tags=["health-risk"],
    dependencies=[Depends(_require_agent)],
    route_class=_AgentRequiredRoute,
)

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


def get_agent() -> Any:
    global _agent
    _ensure_agent_loaded()
    if _agent is None:
        try:
            _agent = PatientHealthRiskAgent()
        except Exception:  # noqa: BLE001 — model assets may be missing
            logger.warning("The health risk agent failed to start.", exc_info=True)
            raise HTTPException(
                status_code=503, detail=UNAVAILABLE_MESSAGE
            ) from None
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
def get_presets(
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
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
