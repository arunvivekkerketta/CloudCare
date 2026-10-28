"""Listing appointments sweeps stale requests once, not on every row."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models import Appointment, Notification
from tests.conftest import auth_headers, login


def _stale(db_session, seeded_ids, hours_ago):
    appt = Appointment(
        patient_id=uuid.UUID(seeded_ids["patient_a_id"]),
        doctor_id=uuid.UUID(seeded_ids["doctor_id"]),
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        department="Cardiology",
        status="Requested",
        reason="Should expire",
    )
    db_session.add(appt)
    db_session.commit()
    db_session.refresh(appt)
    return appt


def test_listing_expires_stale_requests_once(client, db_session, seeded_ids):
    token = login(
        client, "patient.a@example.com", "Patient!Pass123", "patient"
    ).json()["access_token"]
    headers = auth_headers(token)
    first = _stale(db_session, seeded_ids, 3)
    second = _stale(db_session, seeded_ids, 4)

    for _ in range(2):
        response = client.get("/api/appointments?scope=upcoming", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == []
        assert response.headers["X-Total-Count"] == "0"

    db_session.expire_all()
    for appt in (first, second):
        stored = db_session.get(Appointment, appt.id)
        assert stored.status == "Expired"
        assert stored.status_reason == "Not confirmed before the appointment time"

    expired_notes = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == uuid.UUID(seeded_ids["user_a_id"]),
            Notification.message.ilike("%expired%"),
        )
        .count()
    )
    assert expired_notes == 2
