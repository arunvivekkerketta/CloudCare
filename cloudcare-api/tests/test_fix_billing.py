"""Fix #18: completion bills are automatic/idempotent and currency is INR."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Appointment, Bill
from tests.conftest import auth_headers, login


def _tokens(client):
    patient = login(client, "patient.a@example.com", "Patient!Pass123", "patient")
    doctor = login(client, "doctor.test@example.com", "Doctor!Pass123", "doctor")
    admin = login(client, "admin.test@example.com", "Admin!Pass1234", "admin")
    assert patient.status_code == doctor.status_code == admin.status_code == 200
    return (
        patient.json()["access_token"],
        doctor.json()["access_token"],
        admin.json()["access_token"],
    )


def _confirmed_in_past(db_session, seeded_ids, hours_ago=3):
    appt = Appointment(
        patient_id=uuid.UUID(seeded_ids["patient_a_id"]),
        doctor_id=uuid.UUID(seeded_ids["doctor_id"]),
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        department="Cardiology",
        mode="In-person",
        status="Confirmed",
    )
    db_session.add(appt)
    db_session.commit()
    db_session.refresh(appt)
    return appt


def test_completing_creates_exactly_one_pending_bill(
    client, seeded_ids, db_session
):
    _patient_token, doctor_token, _admin_token = _tokens(client)
    appt = _confirmed_in_past(db_session, seeded_ids)

    first = client.patch(
        f"/api/appointments/{appt.id}",
        headers=auth_headers(doctor_token),
        json={"status": "Completed"},
    )
    assert first.status_code == 200, first.text

    bills = db_session.query(Bill).filter(Bill.appointment_id == appt.id).all()
    assert len(bills) == 1
    assert bills[0].status == "Pending"
    assert bills[0].amount == Decimal("500.00")
    assert "Consultation" in bills[0].description

    second = client.patch(
        f"/api/appointments/{appt.id}",
        headers=auth_headers(doctor_token),
        json={"status": "Completed"},
    )
    assert second.status_code == 200, second.text
    db_session.expire_all()
    assert db_session.query(Bill).filter(Bill.appointment_id == appt.id).count() == 1


def test_admin_can_set_the_consultation_fee(client, seeded_ids, db_session):
    _patient_token, doctor_token, admin_token = _tokens(client)
    updated = client.patch(
        f"/api/admin/users/{seeded_ids['doctor_id']}",
        headers=auth_headers(admin_token),
        json={"consultation_fee": 750},
    )
    assert updated.status_code == 200, updated.text
    assert float(updated.json()["consultation_fee"]) == 750.0

    appt = _confirmed_in_past(db_session, seeded_ids, hours_ago=4)
    done = client.patch(
        f"/api/appointments/{appt.id}",
        headers=auth_headers(doctor_token),
        json={"status": "Completed"},
    )
    assert done.status_code == 200, done.text
    bill = db_session.query(Bill).filter(Bill.appointment_id == appt.id).one()
    assert bill.amount == Decimal("750.00")


def test_billing_currency_is_inr_only(client, seeded_ids):
    patient_token, _doctor_token, _admin_token = _tokens(client)
    resp = client.patch(
        "/api/billing/me",
        headers=auth_headers(patient_token),
        json={"currency": "USD"},
    )
    assert resp.status_code == 422, resp.text

    ok = client.patch(
        "/api/billing/me",
        headers=auth_headers(patient_token),
        json={"currency": "INR"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["currency"] == "INR"
