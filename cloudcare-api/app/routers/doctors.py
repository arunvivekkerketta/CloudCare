"""Public doctor directory + free-slot listing (no auth required to browse).

Powers the booking form (doctor picker with shift hours, preferred-time slot
list) and the rating display. Booking itself still requires an account.
"""
import uuid
from datetime import date as date_cls

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DoctorShift, Review, User, UserRole
from app.schemas import DoctorPublic, DoctorShiftOut, SlotInfo
from app.scheduling import (
    DEFAULT_SHIFTS,
    generate_day_slots,
    schedule_for_day,
    serialize_slots,
)

router = APIRouter(prefix="/api/doctors", tags=["doctors"])


@router.get("", response_model=list[DoctorPublic])
def list_doctors(db: Session = Depends(get_db)):
    """All doctors with their shift hours and review aggregates (public)."""
    doctors = (
        db.query(User)
        .filter(User.role == UserRole.doctor)
        .order_by(User.name)
        .all()
    )
    counts = dict(
        db.query(Review.doctor_id, func.count(Review.id)).group_by(Review.doctor_id).all()
    )
    averages = dict(
        db.query(Review.doctor_id, func.avg(Review.rating)).group_by(Review.doctor_id).all()
    )

    # One query for every doctor's shifts instead of one query per doctor.
    shift_rows = (
        db.query(DoctorShift)
        .filter(
            DoctorShift.doctor_id.in_([d.id for d in doctors]),
            DoctorShift.is_active.is_(True),
        )
        .order_by(DoctorShift.start_time)
        .all()
    )
    shifts_by_doctor: dict = {}
    for row in shift_rows:
        shifts_by_doctor.setdefault(row.doctor_id, []).append(
            (row.name, row.start_time, row.end_time)
        )

    result: list[DoctorPublic] = []
    for d in doctors:
        windows = shifts_by_doctor.get(d.id) or [
            (name, start, end) for name, (start, end) in DEFAULT_SHIFTS.items()
        ]
        shifts = [
            DoctorShiftOut(name=name, start=s.strftime("%H:%M"), end=e.strftime("%H:%M"))
            for name, s, e in windows
        ]
        count = int(counts.get(d.id, 0))
        avg = float(averages[d.id]) if d.id in averages else None
        result.append(
            DoctorPublic(
                id=str(d.id),
                name=d.name,
                department=d.department or "General",
                shifts=shifts,
                average_rating=round(avg, 2) if avg is not None else None,
                review_count=count,
            ).model_dump()
        )
    return result


@router.get("/{doctor_id}/slots", response_model=list[SlotInfo])
def list_free_slots(
    doctor_id: str,
    date: str = Query(..., description="Clinic-local date, YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """Free 30-minute slots for one doctor on one date (public).

    Only future, in-shift, in-window slots are returned; booked and blocked
    ones are hidden entirely, so an empty list means "no free slots".
    """
    try:
        doctor_uuid = uuid.UUID(doctor_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Doctor not found.") from None

    doctor = db.get(User, doctor_uuid)
    if doctor is None or doctor.role != UserRole.doctor:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    try:
        day = date_cls.fromisoformat(date)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use YYYY-MM-DD.",
        ) from None

    return serialize_slots(generate_day_slots(db, doctor, day))


@router.get("/{doctor_id}/schedule")
def doctor_day_schedule(
    doctor_id: str,
    date: str = Query(..., description="Clinic-local date, YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """The doctor's grid for one day, with Booked and Doctor on leave slots.

    Public and read-only. Patients can see availability, never another
    patient's identity: each slot only exposes its start, end and status.
    """
    try:
        doctor_uuid = uuid.UUID(doctor_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Doctor not found.") from None

    doctor = db.get(User, doctor_uuid)
    if doctor is None or doctor.role != UserRole.doctor:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    try:
        day = date_cls.fromisoformat(date)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use YYYY-MM-DD.",
        ) from None

    return schedule_for_day(db, doctor, day)
