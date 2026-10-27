"""Appointment routes with role-scoped visibility and clinic slot rules.

Statuses: Requested -> Confirmed/Declined (by the doctor or an admin);
Requested/Confirmed -> Cancelled (by the patient or an admin). Declined and
Cancelled appointments free the slot (the partial unique index only covers
active statuses).
"""
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.audit import record_audit
from app.database import get_db
from app.deps import get_current_user
from app.models import Appointment, Bill, Patient, User, UserRole
from app.notify import notify
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
    to_clinic,
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

# The appointment lifecycle. Terminal states stay terminal: a cancelled or
# declined visit can never be revived (the slot may already be re-booked).
ALLOWED_TRANSITIONS = {
    "Requested": {"Confirmed", "Declined", "Cancelled"},
    "Confirmed": {"Cancelled", "Completed"},
    "Declined": set(),
    "Cancelled": set(),
    "Completed": set(),
    "Expired": set(),
}

DOCTOR_STATUS_ONLY_MESSAGE = "Doctors can only confirm, decline or complete appointments."


def _doctor_label(name: str) -> str:
    """"Dr. X" once, even when the stored name already carries the title."""
    name = (name or "").strip() or "your doctor"
    return name if name.lower().startswith("dr") else f"Dr. {name}"


def _when_label(value: datetime) -> str:
    return to_clinic(value).strftime("%a %d %b, %H:%M")


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
        status_reason=appt.status_reason,
        meeting_url=appt.meeting_url,
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


def _expire_stale(db: Session) -> None:
    """A Requested appointment whose start has passed can never be confirmed."""
    now = datetime.now(timezone.utc)
    stale = (
        db.query(Appointment)
        .filter(Appointment.status == "Requested", Appointment.scheduled_at < now)
        .all()
    )
    for appt in stale:
        appt.status = "Expired"
        appt.status_reason = "Not confirmed before the appointment time"
        notify(
            db,
            appt.patient.user_id,
            f"Your appointment with {_doctor_label(appt.doctor.name)} on "
            f"{_when_label(appt.scheduled_at)} has expired because it was not "
            f"confirmed. Please book another slot.",
        )
    if stale:
        db.commit()


def _patient_has_overlap(
    db: Session, *, patient_id, start_utc, end_utc, exclude_id=None
) -> bool:
    """True when the patient already holds an active appointment overlapping
    [start, end) with ANY doctor."""
    rows = (
        db.query(Appointment)
        .filter(
            Appointment.patient_id == patient_id,
            Appointment.status.in_(ACTIVE_STATUSES),
            Appointment.scheduled_at >= start_utc - timedelta(hours=1),
            Appointment.scheduled_at <= end_utc + timedelta(hours=1),
        )
        .all()
    )
    for other in rows:
        if exclude_id is not None and other.id == exclude_id:
            continue
        other_start = as_utc(other.scheduled_at)
        other_end = other_start + timedelta(
            minutes=other.duration_minutes or SLOT_MINUTES
        )
        if other_start < end_utc and start_utc < other_end:
            return True
    return False


def _next_invoice_no(db: Session) -> str:
    """Sequential invoice numbers in the INV-#### shape the seed data uses."""
    highest = 1000
    for (invoice_no,) in db.query(Bill.invoice_no).all():
        match = re.fullmatch(r"INV-(\d+)", invoice_no or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return f"INV-{highest + 1}"


def _create_completion_bill(db: Session, appt: Appointment) -> Bill | None:
    """Exactly one Pending bill per completed appointment (idempotent)."""
    if db.query(Bill).filter(Bill.appointment_id == appt.id).first() is not None:
        return None
    doctor = appt.doctor
    amount = (
        doctor.consultation_fee
        if doctor.consultation_fee is not None
        else Decimal("500.00")
    )
    day = to_clinic(appt.scheduled_at).strftime("%d %b %Y")
    bill = Bill(
        patient_id=appt.patient_id,
        invoice_no=_next_invoice_no(db),
        amount=amount,
        status="Pending",
        description=f"Consultation – {_doctor_label(doctor.name)} ({day})",
        appointment_id=appt.id,
    )
    db.add(bill)
    notify(
        db,
        appt.patient.user_id,
        f"Your consultation with {_doctor_label(doctor.name)} is complete. "
        f"A bill of ₹{amount} has been added to your account.",
    )
    return bill


@router.get("", response_model=list[AppointmentResponse])
def list_appointments(
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    scope: str = Query("all", pattern="^(all|upcoming|past)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in READ_ROLES:
        raise HTTPException(status_code=403, detail="Not permitted.")

    # Anything still Requested after its start time has silently expired.
    _expire_stale(db)

    query = _visible_appointments(db, current_user)
    if status_filter:
        query = query.filter(Appointment.status == status_filter)

    now = datetime.now(timezone.utc)
    if scope == "upcoming":
        query = query.filter(
            Appointment.status.in_(ACTIVE_STATUSES),
            Appointment.scheduled_at >= now,
        )
        ordering = Appointment.scheduled_at.asc()
    elif scope == "past":
        query = query.filter(
            or_(
                Appointment.status.notin_(ACTIVE_STATUSES),
                Appointment.scheduled_at < now,
            )
        )
        ordering = Appointment.scheduled_at.desc()
    else:
        ordering = Appointment.scheduled_at.asc()

    total = query.count()
    rows = (
        query.order_by(ordering)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    response.headers["X-Total-Count"] = str(total)
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

    # A patient cannot hold two active appointments at the same time, even
    # with different doctors.
    if _patient_has_overlap(
        db,
        patient_id=patient.id,
        start_utc=start_utc,
        end_utc=end_utc,
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "You already have an appointment at this time.",
                "suggested_slots": [],
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
    # The first doctor a patient books becomes their assigned doctor.
    if patient.assigned_doctor_id is None:
        patient.assigned_doctor_id = doctor.id
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

    notify(
        db,
        doctor.id,
        f"New appointment request from {patient.user.name} for "
        f"{_when_label(start_utc)}.",
    )
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
    old_status = appt.status

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
        if "meeting_url" in data:
            raise HTTPException(
                status_code=403,
                detail="Only the doctor can add a meeting link.",
            )
        if data.get("status") == "Cancelled" and appt.status not in ACTIVE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail="Only Requested or Confirmed appointments can be cancelled.",
            )
    elif current_user.role == UserRole.doctor:
        # A doctor acts ONLY on their own appointments.
        if appt.doctor_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not permitted.")
        # Doctors drive the clinical status and the meeting link — nothing else.
        if set(data) - {"status", "meeting_url"}:
            raise HTTPException(status_code=403, detail=DOCTOR_STATUS_ONLY_MESSAGE)
    elif current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Not permitted.")

    # Expiry is system-driven only — never client-settable.
    if data.get("status") == "Expired":
        raise HTTPException(
            status_code=400,
            detail="Expired appointments are set automatically.",
        )

    now = datetime.now(timezone.utc)
    start_utc = as_utc(appt.scheduled_at)

    # Status changes follow the lifecycle table; terminal states stay final.
    if "status" in data and data["status"] != appt.status:
        new_status = data["status"]
        if new_status not in ALLOWED_TRANSITIONS.get(appt.status, set()):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot change an appointment from {appt.status} "
                    f"to {new_status}."
                ),
            )
        if new_status == "Confirmed" and start_utc <= now:
            raise HTTPException(
                status_code=400,
                detail="This appointment time has already passed.",
            )
        if new_status == "Confirmed" and appt.mode == "Telemedicine":
            if not (data.get("meeting_url") or appt.meeting_url):
                raise HTTPException(
                    status_code=400,
                    detail="Add a meeting link before confirming a video appointment.",
                )
        if new_status == "Completed":
            if start_utc > now:
                raise HTTPException(
                    status_code=400,
                    detail="An appointment can only be completed after it has started.",
                )
            if current_user.role not in (UserRole.doctor, UserRole.admin):
                raise HTTPException(
                    status_code=403,
                    detail="Only the doctor or an admin can complete an appointment.",
                )

    if data.get("scheduled_at") is not None:
        # Only a live appointment holds a slot and can be moved.
        if appt.status not in ACTIVE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail="Only Requested or Confirmed appointments can be rescheduled.",
            )
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
        if current_user.role == UserRole.patient and _patient_has_overlap(
            db,
            patient_id=appt.patient_id,
            start_utc=new_start_utc,
            end_utc=new_end_utc,
            exclude_id=appt.id,
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "You already have an appointment at this time.",
                    "suggested_slots": [],
                },
            )
        data["scheduled_at"] = new_start_utc
        # A time moved by the patient needs the doctor's confirmation again.
        if current_user.role == UserRole.patient and appt.status == "Confirmed":
            appt.status = "Requested"
            appt.status_reason = "Rescheduled by the patient"

    for field, value in data.items():
        setattr(appt, field, value)
    if "status" in data and data["status"] != old_status:
        # A new status clears the reason that belonged to the old one.
        appt.status_reason = None

    patient_name = appt.patient.user.name
    doctor_name = _doctor_label(appt.doctor.name)
    when = _when_label(appt.scheduled_at)

    # ---- notifications (same transaction; committed with the change) ----
    if "status" in data and data["status"] != old_status:
        if current_user.role == UserRole.patient:
            notify(
                db,
                appt.doctor_id,
                f"{patient_name} cancelled the appointment on {when}.",
            )
        else:
            notify(
                db,
                appt.patient.user_id,
                f"Your appointment with {doctor_name} on {when} is now "
                f"{appt.status}.",
            )
    if "scheduled_at" in data:
        if current_user.role == UserRole.patient:
            notify(
                db,
                appt.doctor_id,
                f"{patient_name} asked to reschedule their appointment to {when}. "
                "Please confirm the new time.",
            )
        else:
            notify(
                db,
                appt.patient.user_id,
                f"Your appointment with {doctor_name} was moved to {when}.",
            )

    if appt.status == "Completed" and old_status != "Completed":
        _create_completion_bill(db, appt)

    record_audit(
        db,
        action="appointment_update",
        resource=f"appointment:{appt.id}",
        actor=current_user,
        detail=", ".join(data.keys()) or None,
    )
    suggestion_date = to_clinic(appt.scheduled_at).date()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "message": SLOT_TAKEN_MESSAGE,
                "suggested_slots": _suggestions(db, appt.doctor, suggestion_date),
            },
        ) from None
    db.refresh(appt)
    return _serialize(appt)
