"""Fix #15/#21: only real visits may be reviewed; the public wall paginates."""
import uuid
from datetime import datetime, timedelta, timezone

from app.models import Appointment, Patient, Review, User, UserRole
from app.security import hash_password
from tests.conftest import auth_headers, login, register_patient


def _past_visit(db_session, doctor_id, email, name, *, visited=True):
    user = User(
        name=name,
        email=email,
        hashed_password=hash_password("Patient!Pass123"),
        role=UserRole.patient,
    )
    db_session.add(user)
    db_session.flush()
    patient = Patient(user_id=user.id, status="Active")
    db_session.add(patient)
    db_session.flush()
    if visited:
        db_session.add(
            Appointment(
                patient_id=patient.id,
                doctor_id=uuid.UUID(doctor_id),
                scheduled_at=datetime.now(timezone.utc) - timedelta(days=3),
                department="Cardiology",
                status="Confirmed",
            )
        )
    db_session.commit()
    return user


def _token(client, email):
    resp = login(client, email, "Patient!Pass123", "patient")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_review_without_a_visit_is_403(client, seeded_ids, db_session):
    _past_visit(
        db_session,
        seeded_ids["doctor_id"],
        "fix15.novisit@example.com",
        "No Visit",
        visited=False,
    )
    token = _token(client, "fix15.novisit@example.com")
    resp = client.post(
        "/api/reviews",
        headers=auth_headers(token),
        json={
            "doctor_id": seeded_ids["doctor_id"],
            "rating": 5,
            "text": "Never actually met this doctor.",
        },
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == (
        "You can review a doctor after you have visited them."
    )


def test_review_after_a_visit_is_201(client, seeded_ids, db_session):
    _past_visit(
        db_session,
        seeded_ids["doctor_id"],
        "fix15.visited@example.com",
        "Visited Patient",
    )
    token = _token(client, "fix15.visited@example.com")
    resp = client.post(
        "/api/reviews",
        headers=auth_headers(token),
        json={
            "doctor_id": seeded_ids["doctor_id"],
            "rating": 5,
            "text": "Excellent care during my visit.",
        },
    )
    assert resp.status_code == 201, resp.text


def test_eligible_doctors_requires_a_visit(client, seeded_ids, db_session):
    _past_visit(
        db_session,
        seeded_ids["doctor_id"],
        "fix15.eligible@example.com",
        "Eligible Patient",
    )
    token = _token(client, "fix15.eligible@example.com")
    rows = client.get(
        "/api/reviews/eligible-doctors", headers=auth_headers(token)
    )
    assert rows.status_code == 200, rows.text
    assert [row["doctor_id"] for row in rows.json()] == [seeded_ids["doctor_id"]]

    register_patient(client, "fix15.none@example.com", name="No Visits")
    none_token = _token(client, "fix15.none@example.com")
    empty = client.get(
        "/api/reviews/eligible-doctors", headers=auth_headers(none_token)
    )
    assert empty.status_code == 200
    assert empty.json() == []


def test_public_wall_paginates_newest_first(client, seeded_ids, db_session):
    doctor_id = uuid.UUID(seeded_ids["doctor_id"])
    base = datetime.now(timezone.utc) - timedelta(days=12)
    for i in range(12):
        user = _past_visit(
            db_session,
            seeded_ids["doctor_id"],
            f"wall.{i}@example.com",
            f"Wall Patient {i}",
        )
        db_session.add(
            Review(
                author_id=user.id,
                doctor_id=doctor_id,
                rating=5,
                text=f"Review number {i} was a good visit.",
                created_at=base + timedelta(days=i),
                updated_at=base + timedelta(days=i),
            )
        )
    db_session.commit()

    first = client.get("/api/reviews?page=1&page_size=9")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["total"] == 12
    assert body["pages"] == 2
    assert len(body["items"]) == 9
    assert body["items"][0]["text"].startswith("Review number 11")

    second = client.get("/api/reviews?page=2&page_size=9").json()
    assert len(second["items"]) == 3
    assert second["items"][-1]["text"].startswith("Review number 0")
