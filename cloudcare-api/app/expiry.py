"""Expire stale appointment requests exactly once.

A Requested appointment whose start has passed can never be confirmed. The
sweep loads the affected rows (and their people) in one query, then marks
them Expired and queues one notification per transition. Running it again is
a no-op: only rows still in Requested are selected.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.models import Appointment, Patient
from app.notify import notify
from app.scheduling import to_clinic
from app.utils import doctor_label

EXPIRED_REASON = "Not confirmed before the appointment time"


def expire_stale_appointments(db: Session) -> int:
    now = datetime.now(timezone.utc)
    stale = (
        db.query(Appointment)
        .options(
            joinedload(Appointment.patient).joinedload(Patient.user),
            joinedload(Appointment.doctor),
        )
        .filter(
            Appointment.status == "Requested",
            Appointment.scheduled_at < now,
        )
        .all()
    )
    if not stale:
        return 0

    for appt in stale:
        appt.status = "Expired"
        appt.status_reason = EXPIRED_REASON
        when = to_clinic(appt.scheduled_at).strftime("%a %d %b, %H:%M") + " IST"
        notify(
            db,
            appt.patient.user_id,
            f"Your appointment with {doctor_label(appt.doctor.name)} on {when} "
            f"has expired because it was not confirmed. Please book another "
            f"appointment.",
        )
    db.commit()
    return len(stale)
