"""Appointment routes with role-scoped visibility and clinic slot rules.

Statuses: Requested -> Confirmed/Declined (by the doctor or an admin);
Requested/Confirmed -> Cancelled (by the patient or an admin). Declined and
Cancelled appointments free the slot (the partial unique index only covers
active statuses).
"""
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.audit import record_audit
from app.database import get_db
from app.deps import get_current_user
from app.models import Appointment, Patient, User, UserRole
from app.schemas import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentUpdate,
)
from app.scheduling import (
    ACTIVE_STATUSES,
    SLOT_MINUTES,
    SLOT_TAKEN_MESSAGE,
    as_utc,
    ensure_slot_in_shift,
    from_client,
    generate_day_slots,
    serialize_slots,
    validate_booking_start,
)

router = APIRouter(prefix="/api/appointments", tags=["appointments"])

READ_ROLES = {
    UserRole.patient,
    UserRole.doctor,
    UserRole.nurse,
    UserRole.pharmacist,
    UserRole.lab,
    UserRole.admin,
}


def _serialize(appt: Appointment) -> dict:
    return AppointmentResponse(
        id=str(appt.id),
        patient_id=str(appt.patient_id),
        patient_name=appt.patient.user.name,
        doctor_id=str(appt.doctor_id),
        doctor_name=appt.doctor.name,
        department=appt.department,
        scheduled_at=as_utc(appt.scheduled_at),
        duration_minutes=appt.duration_minutes or SLOT_MINUTES,
        mode=appt.mode,
        status=appt.status,
        reason=appt.reason,
    ).model_dump()


def _base_query(db: Session):
    return db.query(Appointment).options(
        joinedload(Appointment.patient).joinedload(Patient.user),
        joinedload(Appointment.doctor),
    )


def _visible_appointments(db: Session, user: User):
    """Hard server-side scoping — the client cannot widen this."""
    query = _base_query(db)
    if user.role == UserRole.patient:
        patient = db.query(Patient).filter(Patient.user_id == user.id).first()
        if patient is None:
            return query.filter(False)
        return query.filter(Appointment.patient_id == patient.id)
    if user.role == UserRole.doctor:
        return query.filter(Appointment.doctor_id == user.id)
    # nurse, pharmacist, lab, admin: clinic-wide read access
    return query


def _own_patient(db: Session, user: User) -> Patient | None:
    return db.query(Patient).filter(Patient.user_id == user.id).first()


def _suggestions(db: Session, doctor: User, clinic_date) -> list[dict]:
    """Up to 5 nearest free slots for that doctor on that date."""
    return serialize_slots(generate_day_slots(db, doctor, clinic_date)[:5])


def _slot_conflict(
    db: Session, *, doctor_id, patient_id, start_utc, end_utc, exclude_id=None
) -> bool:
    """True when an ACTIVE appointment overlaps [start, end) on this doctor.

    The partial unique index already blocks the exact same start atomically;
    this catches overlapping (non-aligned) times too, so a doctor never has
    two active appointments at once.
    """
    near = (
        db.query(Appointment)
        .filter(
            Appointment.doctor_id == doctor_id,
            Appointment.status.in_(ACTIVE_STATUSES),
            Appointment.scheduled_at >= start_utc - timedelta(hours=1),
            Appointment.scheduled_at <= end_utc + timedelta(hours=1),
        )
        .all()
    )
    for other in near:
        if exclude_id is not None and other.id == exclude_id:
            continue
        if patient_id is not None and other.patient_id != patient_id:
            continue
        other_start = as_utc(other.scheduled_at)
        other_end = other_start + timedelta(minutes=other.duration_minutes or SLOT_MINUTES)
        if other_start < end_utc and start_utc < other_end:
            return True
    return False


@router.get("", response_model=list[AppointmentResponse])
def list_appointments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in READ_ROLES:
        raise HTTPException(status_code=403, detail="Not permitted.")

    query = _visible_appointments(db, current_user)
    if status_filter:
        query = query.filter(Appointment.status == status_filter)

    total = query.count()
    rows = (
        query.order_by(Appointment.scheduled_at)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [_serialize(a) for a in rows]


@router.get("/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(
    appointment_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        appt_uuid = uuid.UUID(appointment_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Appointment not found.") from None

    appt = _base_query(db).filter(Appointment.id == appt_uuid).first()
    if appt is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    if current_user.role == UserRole.patient:
        patient = _own_patient(db, current_user)
        if patient is None or appt.patient_id != patient.id:
            raise HTTPException(status_code=403, detail="Not permitted.")
    elif current_user.role == UserRole.doctor and appt.doctor_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not permitted.")

    return _serialize(appt)


@router.post("", response_model=AppointmentResponse, status_code=201)
def create_appointment(
    payload: AppointmentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Patients book their own appointments; a new booking is always Requested.

    The backend is the source of truth for availability:
    - past slot            -> 400
    - 22:00-05:00 blackout -> 400 (exact spec message)
    - outside shift/leave  -> 400
    - slot taken / overlap -> 409 + up to 5 suggested free slots
    """
    if current_user.role not in (UserRole.patient, UserRole.admin):
        raise HTTPException(
            status_code=403,
            detail="Only patient accounts can book appointments.",
        )

    try:
        doctor_uuid = uuid.UUID(payload.doctor_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Doctor not found.") from None

    doctor = db.get(User, doctor_uuid)
    if doctor is None or doctor.role != UserRole.doctor:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    patient = _own_patient(db, current_user)
    if patient is None:
        raise HTTPException(
            status_code=403, detail="No patient profile linked to this account."
        )

    start_utc = from_client(payload.scheduled_at)
    start_c = validate_booking_start(start_utc)  # 400 past / 400 night blackout
    ensure_slot_in_shift(db, doctor, start_c)  # 400 shift hours / leave
    end_utc = start_utc + timedelta(minutes=SLOT_MINUTES)

    if _slot_conflict(
        db, doctor_id=doctor.id, patient_id=patient.id,
        start_utc=start_utc, end_utc=end_utc,
    ):
        # A taken slot and an overlapping own appointment both return the
        # same 409 + suggested free slots.
        raise HTTPException(
            status_code=409,
            detail={
                "message": SLOT_TAKEN_MESSAGE,
                "suggested_slots": _suggestions(db, doctor, start_c.date()),
            },
        )

    appt = Appointment(
        patient_id=patient.id,
        doctor_id=doctor.id,
        scheduled_at=start_utc,
        duration_minutes=SLOT_MINUTES,
        department=payload.department,
        mode=payload.mode,
        reason=payload.reason,
        status="Requested",
    )
    db.add(appt)
    try:
        db.flush()  # the partial unique index rejects a taken slot atomically
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "message": SLOT_TAKEN_MESSAGE,
                "suggested_slots": _suggestions(db, doctor, start_c.date()),
            },
        ) from None

    record_audit(
        db,
        action="appointment_create",
        resource=f"appointment:{appt.id}",
        actor=current_user,
        detail=f"with {doctor.name} on {start_utc.isoformat()}",
    )
    db.commit()
    db.refresh(appt)
    return _serialize(appt)


@router.patch("/{appointment_id}", response_model=AppointmentResponse)
def update_appointment(
    appointment_id: str,
    payload: AppointmentUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        appt_uuid = uuid.UUID(appointment_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Appointment not found.") from None

    appt = _base_query(db).filter(Appointment.id == appt_uuid).first()
    if appt is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    data = payload.model_dump(exclude_unset=True)

    if current_user.role == UserRole.patient:
        patient = _own_patient(db, current_user)
        if patient is None or appt.patient_id != patient.id:
            raise HTTPException(status_code=403, detail="Not permitted.")
        # Patients may reschedule or cancel; they can never set clinical status.
        if "status" in data and data["status"] != "Cancelled":
            raise HTTPException(
                status_code=403,
                detail="Patients can only cancel their appointments.",
            )
        if data.get("status") == "Cancelled" and appt.status not in ACTIVE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail="Only Requested or Confirmed appointments can be cancelled.",
            )
    elif current_user.role == UserRole.doctor:
        # A doctor acts ONLY on their own appointments (Confirm/Decline here).
        if appt.doctor_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not permitted.")
    elif current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Not permitted.")

    if data.get("scheduled_at") is not None:
        new_start_utc = from_client(data["scheduled_at"])
        new_start_c = validate_booking_start(new_start_utc)
        ensure_slot_in_shift(db, appt.doctor, new_start_c)
        new_end_utc = new_start_utc + timedelta(minutes=SLOT_MINUTES)
        if _slot_conflict(
            db,
            doctor_id=appt.doctor_id,
            patient_id=None,
            start_utc=new_start_utc,
            end_utc=new_end_utc,
            exclude_id=appt.id,
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": SLOT_TAKEN_MESSAGE,
                    "suggested_slots": _suggestions(db, appt.doctor, new_start_c.date()),
                },
            )
        data["scheduled_at"] = new_start_utc

    for field, value in data.items():
        setattr(appt, field, value)

    record_audit(
        db,
        action="appointment_update",
        resource=f"appointment:{appt.id}",
        actor=current_user,
        detail=", ".join(data.keys()) or None,
    )
    db.commit()
    db.refresh(appt)
    return _serialize(appt)
