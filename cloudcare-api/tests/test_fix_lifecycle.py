"""Fix #7: stale requests expire and the list supports upcoming/past scopes."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models import Appointment
from tests.conftest import auth_headers, login


def _patient_a_token(client) -> str:
    resp = login(client, "patient.a@example.com", "Patient!Pass123", "patient")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _appt(db_session, seeded_ids, *, starts_at, status):
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


def test_past_requested_appointment_expires_on_listing(client, seeded_ids, db_session):
    appt = _appt(
        db_session,
        seeded_ids,
        starts_at=datetime.now(timezone.utc) - timedelta(hours=3),
        status="Requested",
    )
    token = _patient_a_token(client)

    resp = client.get("/api/appointments?scope=upcoming", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
    assert resp.headers["X-Total-Count"] == "0"

    db_session.refresh(appt)
    assert appt.status == "Expired"
    assert appt.status_reason == "Not confirmed before the appointment time"

    notes = client.get("/api/notifications", headers=auth_headers(token)).json()
    assert any("expired" in n["message"] for n in notes), notes


def test_past_scope_is_newest_first(client, seeded_ids, db_session):
    now = datetime.now(timezone.utc)
    older = _appt(
        db_session, seeded_ids, starts_at=now - timedelta(days=20), status="Cancelled"
    )
    newer = _appt(
        db_session, seeded_ids, starts_at=now - timedelta(days=2), status="Completed"
    )

    token = _patient_a_token(client)
    resp = client.get(
        "/api/appointments?scope=past&page_size=20", headers=auth_headers(token)
    )
    assert resp.status_code == 200, resp.text
    ids = [row["id"] for row in resp.json()]
    assert ids.index(str(newer.id)) < ids.index(str(older.id))
    assert resp.headers["X-Total-Count"] == "2"


def test_upcoming_scope_excludes_past_and_terminal(client, seeded_ids, db_session):
    now = datetime.now(timezone.utc)
    _appt(db_session, seeded_ids, starts_at=now - timedelta(days=1), status="Cancelled")
    future = _appt(
        db_session, seeded_ids, starts_at=now + timedelta(days=5), status="Confirmed"
    )

    token = _patient_a_token(client)
    rows = client.get(
        "/api/appointments?scope=upcoming", headers=auth_headers(token)
    ).json()
    assert [row["id"] for row in rows] == [str(future.id)]
