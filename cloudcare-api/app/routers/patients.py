"""Patient-facing routes. A patient may read ONLY their own data."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.audit import record_audit
from app.database import get_db
from app.deps import get_current_user
from app.models import Appointment, Patient, User, UserRole
from app.schemas import (
    PatientProfile,
    PatientUpdate,
)

router = APIRouter(prefix="/api/patients", tags=["patients"])


def _patient_or_403(db: Session, current_user: User) -> Patient:
    patient = (
        db.query(Patient).filter(Patient.user_id == current_user.id).first()
    )
    if patient is None:
        raise HTTPException(
            status_code=403,
            detail="No patient profile is linked to this account.",
        )
    return patient


def _doctor_patient_filter(doctor_id):
    """A doctor's patients are the ones assigned to them PLUS anyone who has
    ever booked an appointment with them (so a new booking shows up)."""
    has_appt = (
        select(Appointment.patient_id)
        .where(Appointment.doctor_id == doctor_id)
        .scalar_subquery()
    )
    return or_(
        Patient.assigned_doctor_id == doctor_id,
        Patient.id.in_(has_appt),
    )


def _serialize_patient(patient: Patient) -> dict:
    doctor = patient.assigned_doctor
    return PatientProfile(
        id=str(patient.id),
        user_id=str(patient.user_id),
        name=patient.user.name,
        email=patient.user.email,
        age=patient.age,
        condition=patient.condition,
        risk_score=patient.risk_score,
        status=patient.status,
        assigned_doctor_id=str(patient.assigned_doctor_id) if patient.assigned_doctor_id else None,
        assigned_doctor_name=doctor.name if doctor else None,
    ).model_dump()


@router.get("/me", response_model=PatientProfile)
def my_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A patient reads their own profile. Any other role gets 403."""
    if current_user.role != UserRole.patient:
        raise HTTPException(status_code=403, detail="Patient role required.")
    patient = _patient_or_403(db, current_user)
    return _serialize_patient(patient)


@router.patch("/me", response_model=PatientProfile)
def update_my_profile(
    payload: PatientUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Patients may update only their own profile; staff use the admin routes."""
    if current_user.role != UserRole.patient:
        raise HTTPException(status_code=403, detail="Patient role required.")
    patient = _patient_or_403(db, current_user)

    data = payload.model_dump(exclude_unset=True)
    # Patients cannot reassign their own doctor or escalate their record.
    data.pop("assigned_doctor_id", None)

    if "name" in data and data["name"]:
        current_user.name = data.pop("name")
    for field, value in data.items():
        setattr(patient, field, value)

    record_audit(
        db,
        action="patient_update_self",
        resource=f"patient:{patient.id}",
        actor=current_user,
    )
    db.commit()
    db.refresh(patient)
    return _serialize_patient(patient)


@router.get("/{patient_id}", response_model=PatientProfile)
def get_patient(
    patient_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Explicit isolation endpoint: a patient can only ever fetch their own
    profile here; doctors/admin may fetch records scoped by the list rules."""
    try:
        patient_uuid = uuid.UUID(patient_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Patient not found.") from None

    patient = (
        db.query(Patient)
        .options(joinedload(Patient.user), joinedload(Patient.assigned_doctor))
        .filter(Patient.id == patient_uuid)
        .first()
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")

    if current_user.role == UserRole.patient:
        if patient.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not permitted.")
    elif current_user.role == UserRole.doctor:
        has_appointment = (
            db.query(Appointment.id)
            .filter(
                Appointment.patient_id == patient.id,
                Appointment.doctor_id == current_user.id,
            )
            .first()
            is not None
        )
        if patient.assigned_doctor_id != current_user.id and not has_appointment:
            raise HTTPException(status_code=403, detail="Not permitted.")
    elif current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Not permitted.")

    return _serialize_patient(patient)


def _require_admin_or_doctor(current_user: User) -> None:
    if current_user.role not in (UserRole.admin, UserRole.doctor):
        raise HTTPException(status_code=403, detail="Not permitted.")


@router.get("", response_model=list[PatientProfile])
def list_patients(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Doctors see only their assigned patients; admins see everyone.
    Nurses/pharmacists/lab do not get the patient directory."""
    _require_admin_or_doctor(current_user)

    query = db.query(Patient).options(joinedload(Patient.user), joinedload(Patient.assigned_doctor))
    if current_user.role == UserRole.doctor:
        query = query.filter(_doctor_patient_filter(current_user.id))

    total = query.count()
    rows = (
        query.order_by(Patient.user_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [_serialize_patient(p) for p in rows]
