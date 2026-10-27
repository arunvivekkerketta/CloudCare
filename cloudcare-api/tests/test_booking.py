"""Gate tests for appointment booking: availability (409 + suggestions),
double-booking atomicity, the 22:00-05:00 blackout, doctor shifts/leaves,
and the Confirm/Decline lifecycle that frees slots.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models import Appointment
from tests.conftest import auth_headers, login, register_patient

CLINIC_TZ = ZoneInfo("Asia/Kolkata")

NIGHT_MESSAGE = "Appointments cannot be booked between 10:00 PM and 5:00 AM"
TAKEN_MESSAGE = "This slot is no longer available"


def _future_day(days: int = 10) -> date:
    """A clinic-local date safely in the future."""
    now_ist = datetime.now(CLINIC_TZ)
    return (now_ist + timedelta(days=days)).date()


def _ist(day: date, hour: int, minute: int = 0) -> str:
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}:00+05:30"


def _patient_token(client, email: str) -> str:
    register_patient(client, email, name=f"Patient {email.split('@')[0]}")
    resp = login(client, email, "Patient!Pass123", "patient")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _book(client, token, doctor_id, day, hour, minute=0, department="Cardiology", mode="In-person"):
    return client.post(
        "/api/appointments",
        headers=auth_headers(token),
        json={
            "doctor_id": doctor_id,
            "scheduled_at": _ist(day, hour, minute),
            "department": department,
            "mode": mode,
            "reason": "Gate test",
        },
    )


def _second_doctor(client):
    admin = login(client, "admin.test@example.com", "Admin!Pass1234", "admin")
    resp = client.post(
        "/api/admin/users",
        headers=auth_headers(admin.json()["access_token"]),
        json={
            "name": "Dr. Second",
            "email": "doctor.second@example.com",
            "password": "Doctor!Pass123",
            "role": "doctor",
            "department": "Neurology",
        },
    )
    assert resp.status_code == 201, resp.text
    doc_login = login(client, "doctor.second@example.com", "Doctor!Pass123", "doctor")
    assert doc_login.status_code == 200, doc_login.text
    return resp.json()["id"], doc_login.json()["access_token"]


# --------------------------------------------------------------- gate 1a
def test_taken_slot_returns_409_with_suggestions(client, seeded_ids):
    token = _patient_token(client, "booker1@example.com")
    day = _future_day()

    first = _book(client, token, seeded_ids["doctor_id"], day, 10)
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "Requested"

    second = _book(client, token, seeded_ids["doctor_id"], day, 10)
    assert second.status_code == 409
    body = second.json()["detail"]
    assert body["message"] == TAKEN_MESSAGE
    slots = body["suggested_slots"]
    assert isinstance(slots, list) and 0 < len(slots) <= 5
    assert all(s["start"] != first.json()["scheduled_at"] for s in slots)
    assert all(set(s) == {"start", "label", "end_label", "shift"} for s in slots)


# --------------------------------------------------------------- gate 2
def test_double_booking_only_one_succeeds(client, seeded_ids, db_session):
    day = _future_day()
    token_a = _patient_token(client, "booker.a@example.com")
    token_b = _patient_token(client, "booker.b@example.com")

    first = _book(client, token_a, seeded_ids["doctor_id"], day, 9)
    assert first.status_code == 201, first.text
    second = _book(client, token_b, seeded_ids["doctor_id"], day, 9)
    assert second.status_code == 409
    assert second.json()["detail"]["message"] == TAKEN_MESSAGE

    # The guarantee is a DB constraint: a direct duplicate active insert
    # is rejected by the partial unique index, not just by API code.
    import uuid as uuid_mod
    from sqlalchemy.exc import IntegrityError

    dup = Appointment(
        patient_id=uuid_mod.UUID(seeded_ids["patient_a_id"]),
        doctor_id=uuid_mod.UUID(seeded_ids["doctor_id"]),
        # Normalize exactly like the API does (from_client) — UTC wall time.
        scheduled_at=datetime.fromisoformat(_ist(day, 9)).astimezone(timezone.utc),
        department="Cardiology",
        status="Requested",
    )
    db_session.add(dup)
    try:
        db_session.flush()
        raised = False
    except IntegrityError:
        raised = True
        db_session.rollback()
    assert raised, "Expected the partial unique index to reject a duplicate active slot"


# --------------------------------------------------------------- gate 5
def test_night_blackout_rejected(client, seeded_ids):
    token = _patient_token(client, "night.booker@example.com")
    day = _future_day()

    late = _book(client, token, seeded_ids["doctor_id"], day, 22)
    assert late.status_code == 400
    assert late.json()["detail"] == NIGHT_MESSAGE

    deep = _book(client, token, seeded_ids["doctor_id"], day, 23)
    assert deep.status_code == 400
    assert deep.json()["detail"] == NIGHT_MESSAGE

    early = _book(client, token, seeded_ids["doctor_id"], day, 4)
    assert early.status_code == 400
    assert early.json()["detail"] == NIGHT_MESSAGE

    # Boundary: 21:30 is the last legal start (ends exactly at 22:00).
    edge = _book(client, token, seeded_ids["doctor_id"], day, 21, 30)
    assert edge.status_code == 201, edge.text

# --------------------------------------------------------------- gate 3
SHIFT_MESSAGE_FRAGMENT = "working hours"


def test_default_shifts_and_window(client, seeded_ids):
    """Morning 06:00-14:00, Evening 14:00-22:00 generate the slots."""
    day = _future_day()
    resp = client.get(f"/api/doctors/{seeded_ids['doctor_id']}/slots?date={day.isoformat()}")
    assert resp.status_code == 200, resp.text
    slots = resp.json()
    assert slots, "Expected slots from the default Morning/Evening shifts"

    labels = [s["label"] for s in slots]
    assert "06:00" in labels and "13:30" in labels  # Morning shift edges
    assert "14:00" in labels and "21:30" in labels  # Evening shift edges
    assert "21:30" == max(labels)
    # Nothing in the 22:00-05:00 blackout, nothing outside the shifts:
    assert "05:30" not in labels and "22:00" not in labels
    assert all(s["shift"] in ("Morning", "Evening") for s in slots)


def test_slot_outside_shift_hours_rejected(client, seeded_ids):
    token = _patient_token(client, "shift.booker@example.com")
    day = _future_day()

    resp = _book(client, token, seeded_ids["doctor_id"], day, 5, 30)
    assert resp.status_code == 400
    assert SHIFT_MESSAGE_FRAGMENT in resp.json()["detail"]


def test_whole_day_leave_blocks_and_shift_leave_partial(client, seeded_ids, db_session):
    from datetime import datetime as dt

    import uuid as uuid_mod

    from app.models import DoctorLeave

    doctor_id = uuid_mod.UUID(seeded_ids["doctor_id"])
    token = _patient_token(client, "leave.booker@example.com")
    day = _future_day()

    # Morning-only leave: morning slot blocked, evening slot still bookable.
    db_session.add(
        DoctorLeave(
            doctor_id=doctor_id,
            leave_date=dt.strptime(day.isoformat(), "%Y-%m-%d").date(),
            shift_name="Morning",
        )
    )
    db_session.commit()

    morning = _book(client, token, seeded_ids["doctor_id"], day, 9)
    assert morning.status_code == 400
    assert "on leave during the Morning shift" in morning.json()["detail"]

    evening = _book(client, token, seeded_ids["doctor_id"], day, 15)
    assert evening.status_code == 201, evening.text

    # Whole-day leave blocks everything.
    day2 = _future_day(12)
    db_session.add(
        DoctorLeave(
            doctor_id=doctor_id,
            leave_date=dt.strptime(day2.isoformat(), "%Y-%m-%d").date(),
            shift_name=None,
        )
    )
    db_session.commit()
    whole = _book(client, token, seeded_ids["doctor_id"], day2, 15)
    assert whole.status_code == 400
    assert "on leave on this date" in whole.json()["detail"]

    slots = client.get(
        f"/api/doctors/{seeded_ids['doctor_id']}/slots?date={day2.isoformat()}"
    ).json()
    assert slots == []


# --------------------------------------------------------------- gate 4/6

def test_slots_hide_booked_and_blackout(client, seeded_ids):
    """The preferred-time listing only shows free, bookable slots."""
    token = _patient_token(client, "listings@example.com")
    day = _future_day()

    booked = _book(client, token, seeded_ids["doctor_id"], day, 11)
    assert booked.status_code == 201
    booked_start = booked.json()["scheduled_at"]

    resp = client.get(
        f"/api/doctors/{seeded_ids['doctor_id']}/slots?date={day.isoformat()}"
    )
    slots = resp.json()
    starts = [s["start"] for s in slots]
    assert booked_start not in starts, "Booked slot must be hidden, not greyed out"
    assert all(s["label"] < "22:00" for s in slots)
    assert all(s["label"] >= "05:00" for s in slots)


# --------------------------------------------------------------- past slot

def test_past_slot_rejected(client, seeded_ids):
    token = _patient_token(client, "past.booker@example.com")
    yesterday = (datetime.now(CLINIC_TZ) - timedelta(days=1)).date()
    resp = _book(client, token, seeded_ids["doctor_id"], yesterday, 10)
    assert resp.status_code == 400
    assert "past" in resp.json()["detail"].lower()


# --------------------------------------------------------------- gates 8/9

def test_confirm_decline_scoping_and_slot_freeing(client, seeded_ids):
    day = _future_day()
    token = _patient_token(client, "flow.patient@example.com")

    booked = _book(client, token, seeded_ids["doctor_id"], day, 8)
    appt_id = booked.json()["id"]
    assert booked.json()["status"] == "Requested"

    # The owning doctor can Confirm...
    doctor = login(client, "doctor.test@example.com", "Doctor!Pass123", "doctor")
    doctor_token = doctor.json()["access_token"]
    confirmed = client.patch(
        f"/api/appointments/{appt_id}",
        headers=auth_headers(doctor_token),
        json={"status": "Confirmed"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "Confirmed"

    # ...and a different doctor is rejected.
    other_id, other_token = _second_doctor(client)
    forbidden = client.patch(
        f"/api/appointments/{appt_id}",
        headers=auth_headers(other_token),
        json={"status": "Declined"},
    )
    assert forbidden.status_code == 403

    # Patients cannot Confirm/Decline; they can only cancel.
    patient_confirm = client.patch(
        f"/api/appointments/{appt_id}",
        headers=auth_headers(token),
        json={"status": "Confirmed"},
    )
    assert patient_confirm.status_code == 403

    # Cancelling a Confirmed appointment frees the slot; terminal states stay
    # terminal (Declined is only reachable from Requested).
    cancelled_confirmed = client.patch(
        f"/api/appointments/{appt_id}",
        headers=auth_headers(doctor_token),
        json={"status": "Cancelled"},
    )
    assert cancelled_confirmed.status_code == 200
    assert cancelled_confirmed.json()["status"] == "Cancelled"
    revived = client.patch(
        f"/api/appointments/{appt_id}",
        headers=auth_headers(doctor_token),
        json={"status": "Confirmed"},
    )
    assert revived.status_code == 400

    # Decline also frees the slot again (gate 9), from a Requested booking.
    decline_booking = _book(client, token, seeded_ids["doctor_id"], day, 8, 30)
    assert decline_booking.status_code == 201, decline_booking.text
    declined = client.patch(
        f"/api/appointments/{decline_booking.json()['id']}",
        headers=auth_headers(doctor_token),
        json={"status": "Declined"},
    )
    assert declined.status_code == 200
    assert declined.json()["status"] == "Declined"

    slots = client.get(
        f"/api/doctors/{seeded_ids['doctor_id']}/slots?date={day.isoformat()}"
    ).json()
    # Compare instants, not strings ("...Z" and "...+00:00" are both valid UTC).
    booked_instant = datetime.fromisoformat(booked.json()["scheduled_at"])
    listed = [datetime.fromisoformat(s["start"]) for s in slots]
    assert booked_instant in listed

    # The patient can cancel their own Requested appointment.
    again = _book(client, token, seeded_ids["doctor_id"], day, 8)
    assert again.status_code == 201
    cancelled = client.patch(
        f"/api/appointments/{again.json()['id']}",
        headers=auth_headers(token),
        json={"status": "Cancelled"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "Cancelled"


def test_patient_cannot_book_overlapping_own_appointment(client, seeded_ids):
    """Same patient, same doctor, same slot -> 409 (never a second active row)."""
    day = _future_day()
    token = _patient_token(client, "overlap@example.com")

    first = _book(client, token, seeded_ids["doctor_id"], day, 12)
    assert first.status_code == 201

    # 10:15 / 12:15 are not on the 30-minute grid at all: 400, not 409.
    off_grid = _book(client, token, seeded_ids["doctor_id"], day, 12, 15)
    assert off_grid.status_code == 400
    assert "listed time slots" in off_grid.json()["detail"]

    overlapping = _book(client, token, seeded_ids["doctor_id"], day, 12)
    assert overlapping.status_code == 409
    assert overlapping.json()["detail"]["message"] == TAKEN_MESSAGE
