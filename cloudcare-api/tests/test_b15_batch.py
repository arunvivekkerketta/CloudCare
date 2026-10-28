"""B15 batch: doctor deletion, headers, queries, races, caps and money types."""
import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.models import (
    Appointment,
    AuditLog,
    Bill,
    DoctorShift,
    Notification,
    Patient,
    User,
    UserRole,
)
from app.security import hash_password
from tests.conftest import auth_headers, login


def _admin_headers(client):
    response = login(client, "admin.test@example.com", "Admin!Pass1234", "admin")
    assert response.status_code == 200, response.text
    return auth_headers(response.json()["access_token"])


def _patient_headers(client):
    response = login(client, "patient.a@example.com", "Patient!Pass123", "patient")
    assert response.status_code == 200, response.text
    return auth_headers(response.json()["access_token"])


def test_deleting_a_doctor_notifies_patients_and_keeps_completed_history(
    client, db_session, seeded_ids
):
    doctor_id = uuid.UUID(seeded_ids["doctor_id"])
    patient_id = uuid.UUID(seeded_ids["patient_a_id"])
    completed = Appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        scheduled_at=datetime.now(timezone.utc) - timedelta(days=2),
        department="Cardiology",
        mode="In-person",
        status="Completed",
    )
    active = Appointment(
        patient_id=patient_id,
        doctor_id=doctor_id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=5),
        department="Cardiology",
        mode="In-person",
        status="Confirmed",
    )
    db_session.add_all([completed, active])
    db_session.commit()

    response = client.delete(
        f"/api/admin/doctors/{doctor_id}?cancel=true",
        headers=_admin_headers(client),
    )
    assert response.status_code == 204, response.text

    db_session.expire_all()
    kept = db_session.get(Appointment, completed.id)
    assert kept is not None
    assert kept.status == "Completed"
    assert kept.doctor_id is None
    assert kept.doctor_name == "Dr. Test"
    assert db_session.get(Appointment, active.id) is None

    note = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == uuid.UUID(seeded_ids["user_a_id"]),
            Notification.message.ilike("%no longer at the clinic%"),
        )
        .first()
    )
    assert note is not None

    history = client.get(
        "/api/appointments?scope=past", headers=_patient_headers(client)
    )
    assert history.status_code == 200
    row = next(item for item in history.json() if item["id"] == str(completed.id))
    assert row["doctor_id"] is None
    assert row["doctor_name"] == "Dr. Test"


def test_bills_return_total_count_and_numeric_amount(client, db_session, seeded_ids):
    db_session.add(
        Bill(
            patient_id=uuid.UUID(seeded_ids["patient_a_id"]),
            invoice_no="INV-NUM-1",
            amount=Decimal("1250.50"),
            status="Pending",
            description="Numeric check",
        )
    )
    db_session.commit()

    response = client.get("/api/bills", headers=_patient_headers(client))
    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "1"
    assert isinstance(response.json()[0]["amount"], (int, float))
    assert response.json()[0]["amount"] == 1250.5

    admin = client.get("/api/admin/users", headers=_admin_headers(client))
    assert admin.status_code == 200
    fees = [row["consultation_fee"] for row in admin.json() if row["role"] == "doctor"]
    assert fees and all(isinstance(fee, (int, float)) for fee in fees)


def test_register_returns_409_when_the_unique_email_race_loses(
    client, monkeypatch
):
    def fail_commit(self, *args, **kwargs):
        raise IntegrityError("INSERT INTO users", {}, Exception("duplicate email"))

    # The second registration loses the race at commit, after its own
    # pre-check already found no account.
    monkeypatch.setattr(Session, "commit", fail_commit)
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Race Patient",
            "email": "race.patient@example.com",
            "password": "Patient!Pass123",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "An account with this email already exists."


def test_audit_resource_is_capped_at_the_column_length(db_session):
    record_audit(
        db_session,
        action="x" * 150,
        resource="resource:" + ("r" * 500),
        detail="long resource",
        actor=None,
    )
    db_session.commit()
    row = db_session.query(AuditLog).order_by(AuditLog.timestamp.desc()).first()
    assert len(row.resource) == 255
    assert len(row.action) == 100


def test_doctor_list_loads_shifts_in_one_query(client, db_session, seeded_ids):
    doctors = [db_session.get(User, uuid.UUID(seeded_ids["doctor_id"]))]
    for index in range(2):
        extra = User(
            name=f"Dr. Extra {index}",
            email=f"extra.doctor{index}@example.com",
            hashed_password=hash_password("Doctor!Pass123"),
            role=UserRole.doctor,
            department="Cardiology",
        )
        doctors.append(extra)
        db_session.add(extra)
    db_session.flush()
    for doctor in doctors:
        db_session.add(
            DoctorShift(
                doctor_id=doctor.id,
                name="Morning",
                start_time=time(6, 0),
                end_time=time(14, 0),
                is_active=True,
            )
        )
    db_session.commit()

    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FROM doctor_shifts" in statement:
            statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        response = client.get("/api/doctors")
    finally:
        event.remove(bind, "before_cursor_execute", capture)

    assert response.status_code == 200, response.text
    assert len(statements) == 1, statements
    assert len(response.json()) == 3
