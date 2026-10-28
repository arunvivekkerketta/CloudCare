"""Clinic scheduling rules — the single source of truth for slot math.

Definitions (project spec):
- Clinic timezone: "Asia/Kolkata". Timestamps are stored in UTC; every rule
  check converts to the clinic timezone first.
- Slot length: 30 minutes. A slot is identified by doctor + date + start time.
- Shifts: Morning 06:00-14:00 and Evening 14:00-22:00 are the defaults; each
  doctor may have their own admin-editable hours, plus per-shift or whole-day
  leaves.
- Booking window: a slot is bookable only if its start is >= 05:00 and its
  end is <= 22:00 (last start 21:30). Nothing can be booked from 22:00 to
  05:00 for any doctor or specialty. A start between 21:31 and 21:59 is
  explained with the dedicated last-appointment message, not the overnight
  closure message. Past slots are rejected.
- Free slot: inside the doctor's shift, inside the booking window, in the
  future, and with no appointment in status Requested or Confirmed.
  Declined and Cancelled appointments free the slot.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Appointment, DoctorLeave, DoctorShift, User
from app.utils import doctor_label

# Clinic timezone: Asia/Kolkata (IST, UTC+05:30, no DST). Windows Python has
# no system tz database, so fall back to the exact fixed offset when the IANA
# data (tzdata package) is unavailable.
try:
    CLINIC_TZ = ZoneInfo("Asia/Kolkata")
except ZoneInfoNotFoundError:  # pragma: no cover - depends on host tzdata
    CLINIC_TZ = timezone(timedelta(hours=5, minutes=30))
SLOT_MINUTES = 30
WINDOW_START = time(5, 0)  # first bookable start
WINDOW_END = time(22, 0)  # last bookable END -> last start 21:30
LAST_START = time(
    (WINDOW_END.hour * 60 + WINDOW_END.minute - SLOT_MINUTES) // 60,
    (WINDOW_END.hour * 60 + WINDOW_END.minute - SLOT_MINUTES) % 60,
)  # 21:30

DEFAULT_SHIFTS: dict[str, tuple[time, time]] = {
    "Morning": (time(6, 0), time(14, 0)),
    "Evening": (time(14, 0), time(22, 0)),
}

ACTIVE_STATUSES = ("Requested", "Confirmed")

NIGHT_BLOCK_MESSAGE = "Appointments cannot be booked between 10:00 PM and 5:00 AM"
LAST_START_MESSAGE = "The last appointment of the day starts at 9:30 PM."
SLOT_TAKEN_MESSAGE = "This slot is no longer available"
PAST_SLOT_MESSAGE = "Appointment time is in the past. Please choose a future slot."


def as_utc(value: datetime) -> datetime:
    """Normalize a stored datetime to UTC.

    SQLite returns naive datetimes (the offset is not persisted), so naive
    values are interpreted as UTC — the format the API always writes.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_clinic(value: datetime) -> datetime:
    """Convert any datetime to the clinic timezone for rule checks/display."""
    return as_utc(value).astimezone(CLINIC_TZ)


def from_client(value: datetime) -> datetime:
    """Normalize a client-supplied datetime to UTC for storage.

    Aware values are converted as-is; naive values are interpreted as
    clinic-local time (a caller writing "09:00" means 09:00 IST).
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=CLINIC_TZ).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def clinic_now() -> datetime:
    return datetime.now(CLINIC_TZ)


def shift_windows(db: Session, doctor: User) -> list[tuple[str, time, time]]:
    """The doctor's active shifts, or the clinic defaults if none configured."""
    rows = (
        db.query(DoctorShift)
        .filter(DoctorShift.doctor_id == doctor.id, DoctorShift.is_active.is_(True))
        .order_by(DoctorShift.start_time)
        .all()
    )
    if rows:
        return [(s.name, s.start_time, s.end_time) for s in rows]
    return [(name, start, end) for name, (start, end) in DEFAULT_SHIFTS.items()]


def validate_booking_start(start_utc: datetime) -> datetime:
    """Reject past slots and the 22:00-05:00 blackout. Returns clinic-time start.

    Raises HTTP 400 with the exact spec messages.
    """
    start_c = to_clinic(start_utc)
    if start_c <= clinic_now():
        raise HTTPException(status_code=400, detail=PAST_SLOT_MESSAGE)
    clock = start_c.time()
    if clock < WINDOW_START or clock >= WINDOW_END:
        # 00:00-04:59 and 22:00-23:59: the clinic is closed.
        raise HTTPException(status_code=400, detail=NIGHT_BLOCK_MESSAGE)
    if clock > LAST_START:
        # 21:31-21:59: the clinic is open, but the day's last start has
        # passed. This is different from the overnight closure and needs a
        # different, actionable message.
        raise HTTPException(status_code=400, detail=LAST_START_MESSAGE)
    return start_c


def leaves_on(db: Session, doctor_id, day: date) -> list[DoctorLeave]:
    return (
        db.query(DoctorLeave)
        .filter(DoctorLeave.doctor_id == doctor_id, DoctorLeave.leave_date == day)
        .all()
    )


def ensure_slot_in_shift(db: Session, doctor: User, start_c: datetime) -> str:
    """Verify the slot lies inside one of the doctor's active shifts and that
    they are not on leave. Returns the covering shift name; raises HTTP 400."""
    start_t = start_c.time()
    end_t = (start_c + timedelta(minutes=SLOT_MINUTES)).time()
    day = start_c.date()

    leaves = leaves_on(db, doctor.id, day)
    if any(l.shift_name is None for l in leaves):
        raise HTTPException(
            status_code=400,
            detail=f"{doctor_label(doctor.name)} is on leave on this date.",
        )

    for name, shift_start, shift_end in shift_windows(db, doctor):
        if shift_start <= start_t and end_t <= shift_end:
            if any(l.shift_name == name for l in leaves):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{doctor_label(doctor.name)} is on leave during the "
                        f"{name} shift on this date."
                    ),
                )
            # Only the aligned 30-minute grid inside the shift is bookable:
            # 10:10 is not a slot, even though it lies inside working hours.
            minutes_from_start = (start_t.hour * 60 + start_t.minute) - (
                shift_start.hour * 60 + shift_start.minute
            )
            if (
                minutes_from_start % SLOT_MINUTES != 0
                or start_c.second
                or start_c.microsecond
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Please pick one of the listed time slots.",
                )
            return name

    hours = ", ".join(
        f"{name} {s.strftime('%H:%M')}-{e.strftime('%H:%M')}"
        for name, s, e in shift_windows(db, doctor)
    )
    raise HTTPException(
        status_code=400,
        detail=(
            f"The selected time is outside {doctor_label(doctor.name)}'s "
            f"working hours ({hours})."
        ),
    )


def booked_starts(db: Session, doctor_id, day: date) -> set[datetime]:
    """Active (Requested/Confirmed) start times for the doctor on a clinic day."""
    day_start_utc = datetime(day.year, day.month, day.day, tzinfo=CLINIC_TZ).astimezone(
        timezone.utc
    )
    day_end_utc = day_start_utc + timedelta(days=1)
    rows = (
        db.query(Appointment.scheduled_at)
        .filter(
            Appointment.doctor_id == doctor_id,
            Appointment.status.in_(ACTIVE_STATUSES),
            Appointment.scheduled_at >= day_start_utc,
            Appointment.scheduled_at < day_end_utc,
        )
        .all()
    )
    return {as_utc(r[0]) for r in rows}


def generate_day_slots(db: Session, doctor: User, day: date) -> list[dict]:
    """Every free, future, in-shift, in-window 30-minute slot for a clinic day.

    Returns a list of dicts: {start (aware UTC), label, end_label, shift}.
    Slots in the blackout (22:00-05:00), outside shifts, on leave days, in
    the past, or already actively booked are never included.
    """
    slots: list[dict] = []

    day_leaves = leaves_on(db, doctor.id, day)
    if any(l.shift_name is None for l in day_leaves):
        return slots  # whole-day leave
    leave_names = {l.shift_name for l in day_leaves if l.shift_name}

    booked = booked_starts(db, doctor.id, day)
    now_utc = datetime.now(timezone.utc)

    for name, shift_start, shift_end in shift_windows(db, doctor):
        if name in leave_names:
            continue
        cursor = datetime.combine(day, shift_start, tzinfo=CLINIC_TZ)
        shift_end_dt = datetime.combine(day, shift_end, tzinfo=CLINIC_TZ)
        while cursor + timedelta(minutes=SLOT_MINUTES) <= shift_end_dt:
            start_utc = cursor.astimezone(timezone.utc)
            end_c = cursor + timedelta(minutes=SLOT_MINUTES)
            in_window = (
                cursor.time() >= WINDOW_START
                and cursor.time() <= LAST_START
                and end_c.date() == day
            )
            if in_window and start_utc > now_utc and start_utc not in booked:
                slots.append(
                    {
                        "start": start_utc,
                        "label": cursor.strftime("%H:%M"),
                        "end_label": end_c.strftime("%H:%M"),
                        "shift": name,
                    }
                )
            cursor += timedelta(minutes=SLOT_MINUTES)

    slots.sort(key=lambda s: s["start"])
    return slots


def serialize_slots(slots: list[dict], limit: int | None = None) -> list[dict]:
    """JSON-safe slot dicts (ISO-8601 UTC with offset)."""
    if limit is not None:
        slots = slots[:limit]
    return [
        {
            "start": s["start"].isoformat(),
            "label": s["label"],
            "end_label": s["end_label"],
            "shift": s["shift"],
        }
        for s in slots
    ]


# --------------------------------------------------------------- admin rules
def parse_hhmm(value: str) -> time:
    """Parse a strict "HH:MM" clinic-time string -> datetime.time, else 400."""
    try:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Shift times must be in 24-hour HH:MM format.",
        ) from None
    return time(hour, minute)


def normalize_shift_rules(rules: list[dict]) -> dict[str, tuple[time, time]]:
    """Validate a full replacement set of shift rules for one doctor.

    Each named shift must overlap the 05:00-22:00 booking window (so at least
    a 30-minute slot falls inside both the shift and the window). Times are
    clamped to quarter-hour boundaries (guards against :02/:47 ranges that
    would collide with the aligned slot grid). Returns {name: (start, end)}.
    """
    if not rules:
        raise HTTPException(status_code=400, detail="At least one shift is required.")

    normalized: dict[str, tuple[time, time]] = {}
    for rule in rules:
        name = rule.get("name")
        if name not in DEFAULT_SHIFTS:
            raise HTTPException(
                status_code=400,
                detail=f"Shift name must be 'Morning' or 'Evening' (got {name!r}).",
            )
        start = parse_hhmm(str(rule.get("start", "")))
        end = parse_hhmm(str(rule.get("end", "")))
        if start.minute % 15 or end.minute % 15:
            raise HTTPException(
                status_code=400,
                detail="Shift times must align to 15-minute boundaries.",
            )
        if end <= start:
            raise HTTPException(
                status_code=400,
                detail=f"{name} shift end must be after its start.",
            )
        # The shift must overlap the clinic booking window deeply enough to
        # hold one aligned 30-minute slot (a 21:30 start legally ends 22:00).
        usable_start = max(start, WINDOW_START)
        usable_end = min(end, WINDOW_END)
        usable_minutes = (
            datetime.combine(date.today(), usable_end)
            - datetime.combine(date.today(), usable_start)
        ).total_seconds() / 60
        if usable_minutes < SLOT_MINUTES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{name} shift must overlap the booking window "
                    f"05:00-22:00 by at least 30 minutes."
                ),
            )
        normalized[name] = (start, end)

    if "Morning" in normalized and "Evening" in normalized:
        m_start, m_end = normalized["Morning"]
        e_start, e_end = normalized["Evening"]
        if e_start < m_end:
            raise HTTPException(
                status_code=400,
                detail="Evening shift cannot start before the Morning shift ends.",
            )
    return normalized
