"""Permission tests with real data: patient A cannot read patient B's
appointments or bills; doctors see only their assigned patients; staff
roles see only what their dashboards need."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from tests.conftest import auth_headers, login


@pytest.fixture()
def scoped_data(db_session, seeded_ids):
    """Appointments and bills for both patients, owned by the seeded doctor."""
    from app.models import Appointment as A, Bill as B, Patient, User

    patient_a = (
        db_session.query(Patient).filter(Patient.id == UUID(seeded_ids["patient_a_id"])).first()
    )
    patient_b = (
        db_session.query(Patient).filter(Patient.id == UUID(seeded_ids["patient_b_id"])).first()
    )
    doctor = db_session.query(User).filter(User.id == UUID(seeded_ids["doctor_id"])).first()

    now = datetime.now(timezone.utc)
    appt_a = A(
        patient_id=patient_a.id,
        doctor_id=doctor.id,
        scheduled_at=now + timedelta(days=1),
        department="Cardiology",
        mode="In-person",
        status="Requested",
        reason="A's appointment",
    )
    appt_b = A(
        patient_id=patient_b.id,
        doctor_id=doctor.id,
        scheduled_at=now + timedelta(days=2),
        department="Cardiology",
        mode="In-person",
        status="Requested",
        reason="B's appointment",
    )
    bill_a = B(patient_id=patient_a.id, invoice_no="INV-A1", amount="100.00", status="Pending", description="A's bill")
    bill_b = B(patient_id=patient_b.id, invoice_no="INV-B1", amount="200.00", status="Paid", description="B's bill")
    db_session.add_all([appt_a, appt_b, bill_a, bill_b])
    db_session.commit()
    return {
        "appt_a_id": str(appt_a.id),
        "appt_b_id": str(appt_b.id),
        "bill_a_id": str(bill_a.id),
        "bill_b_id": str(bill_b.id),
    }


def tokens(client):
    patient_a = login(client, "patient.a@example.com", "Patient!Pass123", "patient").json()["access_token"]
    patient_b = login(client, "patient.b@example.com", "Patient!Pass123", "patient").json()["access_token"]
    doctor = login(client, "doctor.test@example.com", "Doctor!Pass123", "doctor").json()["access_token"]
    admin = login(client, "admin.test@example.com", "Admin!Pass1234", "admin").json()["access_token"]
    return {"a": patient_a, "b": patient_b, "doctor": doctor, "admin": admin}


def test_patient_a_cannot_read_patient_b_appointment(client, scoped_data):
    tk = tokens(client)
    response = client.get(f"/api/appointments/{scoped_data['appt_b_id']}", headers=auth_headers(tk["a"]))
    assert response.status_code == 403


def test_patient_a_cannot_read_patient_b_bill(client, scoped_data):
    tk = tokens(client)
    response = client.get(f"/api/bills/{scoped_data['bill_b_id']}", headers=auth_headers(tk["a"]))
    assert response.status_code == 403


def test_patient_a_sees_only_own_lists(client, scoped_data):
    tk = tokens(client)
    appts = client.get("/api/appointments", headers=auth_headers(tk["a"])).json()
    assert [a["reason"] for a in appts] == ["A's appointment"]
    bills = client.get("/api/bills", headers=auth_headers(tk["a"])).json()
    assert [b["invoice_no"] for b in bills] == ["INV-A1"]


def test_patient_b_sees_only_own_lists(client, scoped_data):
    tk = tokens(client)
    appts = client.get("/api/appointments", headers=auth_headers(tk["b"])).json()
    assert [a["reason"] for a in appts] == ["B's appointment"]
    bills = client.get("/api/bills", headers=auth_headers(tk["b"])).json()
    assert [b["invoice_no"] for b in bills] == ["INV-B1"]


def test_patient_a_cannot_modify_patient_b_appointment(client, scoped_data):
    tk = tokens(client)
    response = client.patch(
        f"/api/appointments/{scoped_data['appt_b_id']}",
        headers=auth_headers(tk["a"]),
        json={"reason": "hijacked"},
    )
    assert response.status_code == 403


def test_patient_cannot_change_appointment_status(client, scoped_data):
    tk = tokens(client)
    response = client.patch(
        f"/api/appointments/{scoped_data['appt_a_id']}",
        headers=auth_headers(tk["a"]),
        json={"status": "Confirmed"},
    )
    assert response.status_code == 403


def test_patient_can_reschedule_own_appointment(client, scoped_data):
    tk = tokens(client)
    # A clinic-friendly hour (10:00 IST) 10 days out — must be inside the
    # doctor's default Morning shift and outside the 22:00-05:00 blackout.
    day = (datetime.now(timezone.utc) + timedelta(days=10)).date()
    new_time = f"{day.isoformat()}T10:00:00+05:30"
    response = client.patch(
        f"/api/appointments/{scoped_data['appt_a_id']}",
        headers=auth_headers(tk["a"]),
        json={"scheduled_at": new_time},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "Requested"


def test_doctor_sees_assigned_patients_only(client, seeded_ids):
    tk = tokens(client)
    patients = client.get("/api/patients", headers=auth_headers(tk["doctor"]))
    assert patients.status_code == 200
    names = {p["name"] for p in patients.json()}
    assert "Patient A" in names
    assert "Patient B" not in names


def test_doctor_scoped_appointments(client, scoped_data):
    tk = tokens(client)
    appts = client.get("/api/appointments", headers=auth_headers(tk["doctor"]))
    assert appts.status_code == 200
    reasons = {a["reason"] for a in appts.json()}
    # Both seeded appointments belong to this doctor.
    assert reasons == {"A's appointment", "B's appointment"}


def test_doctor_cannot_read_unassigned_patient_profile(client, seeded_ids):
    tk = tokens(client)
    response = client.get(
        f"/api/patients/{seeded_ids['patient_b_id']}", headers=auth_headers(tk["doctor"])
    )
    assert response.status_code == 403


def test_doctor_cannot_list_bills(client, seeded_ids):
    """Doctors don't get the billing directory in this scope."""
    tk = tokens(client)
    response = client.get("/api/bills", headers=auth_headers(tk["doctor"]))
    assert response.status_code == 403


def test_nurse_read_but_no_patient_directory(client, db_session, seeded_ids):
    from app.models import User, UserRole
    from app.security import hash_password

    nurse = User(
        name="Nurse T",
        email="nurse.t@example.com",
        hashed_password=hash_password("Nurse!Pass123"),
        role=UserRole.nurse,
    )
    db_session.add(nurse)
    db_session.commit()

    token = login(client, "nurse.t@example.com", "Nurse!Pass123", "nurse").json()["access_token"]
    assert client.get("/api/appointments", headers=auth_headers(token)).status_code == 200
    assert client.get("/api/bills", headers=auth_headers(token)).status_code == 200
    # Ward staff don't get the patient directory or admin routes
    assert client.get("/api/patients", headers=auth_headers(token)).status_code == 403
    assert client.get("/api/admin/users", headers=auth_headers(token)).status_code == 403


def test_admin_full_read_and_audit_logs(client, scoped_data):
    tk = tokens(client)
    assert client.get("/api/patients", headers=auth_headers(tk["admin"])).status_code == 200
    assert client.get("/api/appointments", headers=auth_headers(tk["admin"])).status_code == 200
    assert client.get("/api/bills", headers=auth_headers(tk["admin"])).status_code == 200

    logs = client.get("/api/admin/audit-logs", headers=auth_headers(tk["admin"]))
    assert logs.status_code == 200
    assert "items" in logs.json()


def test_bill_status_update_admin_only(client, scoped_data):
    tk = tokens(client)
    r1 = client.patch(
        f"/api/bills/{scoped_data['bill_a_id']}?status=Paid",
        headers=auth_headers(tk["a"]),
    )
    assert r1.status_code == 403

    r2 = client.patch(
        f"/api/bills/{scoped_data['bill_a_id']}?status=Paid",
        headers=auth_headers(tk["admin"]),
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "Paid"


def test_patient_cannot_book_without_doctor(client, seeded_ids):
    tk = tokens(client)
    response = client.post(
        "/api/appointments",
        headers=auth_headers(tk["a"]),
        json={
            "doctor_id": "00000000-0000-0000-0000-000000000000",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
            "department": "Cardiology",
        },
    )
    assert response.status_code == 404


def test_pagination_params_validated(client, seeded_ids):
    tk = tokens(client)
    bad = client.get("/api/appointments?page=0", headers=auth_headers(tk["a"]))
    assert bad.status_code == 422

    ok = client.get("/api/appointments?page=1&page_size=1", headers=auth_headers(tk["a"]))
    assert ok.status_code == 200
