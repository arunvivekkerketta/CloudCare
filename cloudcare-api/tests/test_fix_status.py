"""Fix #14/#3/#16: appointment transitions, reschedule rules, patient overlap."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models import Appointment
from tests.conftest import auth_headers, login
from tests.test_booking import _book, _future_day, _ist, _patient_token, _second_doctor


def _doctor_token(client) -> str:
    resp = login(client, "doctor.test@example.com", "Doctor!Pass123", "doctor")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _appt_in_db(db_session, seeded_ids, *, starts_at, status="Requested"):
    appt = Appointment(
        patient_id=uuid.UUID(seeded_ids["patient_a_id"]),
        doctor_id=uuid.UUID(seeded_ids["doctor_id"]),
        scheduled_at=starts_at,
        department="Cardiology",
        status=status,
    )
    db_session.add(appt)
    db_session.commit()
    db_session.refresh(appt)
    return appt


def test_cancelled_to_confirmed_is_400_not_500(client, seeded_ids):
    token = _patient_token(client, "fix14.a@example.com")
    day = _future_day()
    booked = _book(client, token, seeded_ids["doctor_id"], day, 9).json()
    doctor = _doctor_token(client)

    cancelled = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"status": "Cancelled"},
    )
    assert cancelled.status_code == 200, cancelled.text

    retry = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"status": "Confirmed"},
    )
    assert retry.status_code == 400, retry.text
    assert retry.json()["detail"] == (
        "Cannot change an appointment from Cancelled to Confirmed."
    )


def test_declined_to_confirmed_is_400(client, seeded_ids):
    token = _patient_token(client, "fix14.b@example.com")
    day = _future_day()
    booked = _book(client, token, seeded_ids["doctor_id"], day, 10).json()
    doctor = _doctor_token(client)

    declined = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"status": "Declined"},
    )
    assert declined.status_code == 200, declined.text

    retry = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"status": "Confirmed"},
    )
    assert retry.status_code == 400, retry.text


def test_doctor_cannot_change_anything_but_status_and_link(client, seeded_ids):
    token = _patient_token(client, "fix14.c@example.com")
    day = _future_day()
    booked = _book(client, token, seeded_ids["doctor_id"], day, 11).json()
    doctor = _doctor_token(client)

    resp = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"scheduled_at": _ist(day, 12)},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == (
        "Doctors can only confirm, decline or complete appointments."
    )


def test_confirm_past_appointment_rejected(client, seeded_ids, db_session):
    appt = _appt_in_db(
        db_session,
        seeded_ids,
        starts_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    doctor = _doctor_token(client)
    resp = client.patch(
        f"/api/appointments/{appt.id}",
        headers=auth_headers(doctor),
        json={"status": "Confirmed"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "This appointment time has already passed."


def test_nobody_can_send_expired_status(client, seeded_ids):
    token = _patient_token(client, "fix14.d@example.com")
    day = _future_day()
    booked = _book(client, token, seeded_ids["doctor_id"], day, 13).json()
    doctor = _doctor_token(client)
    resp = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"status": "Expired"},
    )
    assert resp.status_code == 400, resp.text


def test_completed_only_after_start(client, seeded_ids):
    token = _patient_token(client, "fix14.e@example.com")
    day = _future_day()
    booked = _book(client, token, seeded_ids["doctor_id"], day, 14).json()
    doctor = _doctor_token(client)
    client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"status": "Confirmed"},
    )
    too_early = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"status": "Completed"},
    )
    assert too_early.status_code == 400, too_early.text


def test_requested_confirmed_cancelled_flow(client, seeded_ids):
    token = _patient_token(client, "fix14.f@example.com")
    day = _future_day()
    booked = _book(client, token, seeded_ids["doctor_id"], day, 15).json()
    doctor = _doctor_token(client)

    confirmed = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"status": "Confirmed"},
    )
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "Confirmed"

    cancelled = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(token),
        json={"status": "Cancelled"},
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "Cancelled"


def test_booking_off_grid_time_rejected(client, seeded_ids):
    token = _patient_token(client, "fix3.grid@example.com")
    day = _future_day()
    resp = _book(client, token, seeded_ids["doctor_id"], day, 10, 10)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Please pick one of the listed time slots."


def test_patient_reschedule_confirmed_returns_to_requested(client, seeded_ids):
    token = _patient_token(client, "fix3.resched@example.com")
    day = _future_day()
    booked = _book(client, token, seeded_ids["doctor_id"], day, 9).json()
    doctor = _doctor_token(client)
    confirmed = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"status": "Confirmed"},
    )
    assert confirmed.status_code == 200, confirmed.text

    moved = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(token),
        json={"scheduled_at": _ist(day, 11)},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == "Requested"


def test_only_live_appointments_can_be_rescheduled(client, seeded_ids):
    token = _patient_token(client, "fix3.cancelled@example.com")
    day = _future_day()
    booked = _book(client, token, seeded_ids["doctor_id"], day, 16).json()
    client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(token),
        json={"status": "Cancelled"},
    )
    moved = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(token),
        json={"scheduled_at": _ist(day, 17)},
    )
    assert moved.status_code == 400, moved.text


def test_patient_cannot_double_book_two_doctors(client, seeded_ids):
    token = _patient_token(client, "fix16.overlap@example.com")
    day = _future_day()
    other_id, _other_token = _second_doctor(client)

    first = _book(client, token, seeded_ids["doctor_id"], day, 10)
    assert first.status_code == 201, first.text

    second = _book(client, token, other_id, day, 10, department="Neurology")
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["message"] == (
        "You already have an appointment at this time."
    )
