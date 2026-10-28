"""Invoice numbers are allocated under a lock and never surface as slot errors."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Bill
from app.routers import appointments as appointments_module
from tests.conftest import auth_headers, login
from tests.test_fix_billing import _confirmed_in_past


def _doctor_token(client):
    response = login(client, "doctor.test@example.com", "Doctor!Pass123", "doctor")
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _complete(client, token, appointment_id):
    return client.patch(
        f"/api/appointments/{appointment_id}",
        headers=auth_headers(token),
        json={"status": "Completed"},
    )


def _bill_for(db_session, appointment_id):
    return (
        db_session.query(Bill)
        .filter(Bill.appointment_id == uuid.UUID(appointment_id))
        .one()
    )


def test_completed_visits_get_unique_sequential_invoices(
    client, db_session, seeded_ids
):
    token = _doctor_token(client)
    first = _confirmed_in_past(db_session, seeded_ids, hours_ago=3)
    second = _confirmed_in_past(db_session, seeded_ids, hours_ago=4)

    assert _complete(client, token, first.id).status_code == 200
    assert _complete(client, token, second.id).status_code == 200

    first_no = _bill_for(db_session, str(first.id)).invoice_no
    second_no = _bill_for(db_session, str(second.id)).invoice_no
    assert first_no == "INV-1001"
    assert second_no == "INV-1002"


def test_invoice_collision_retries_with_the_next_number(
    client, db_session, seeded_ids, monkeypatch
):
    appt = _confirmed_in_past(db_session, seeded_ids)
    existing = Bill(
        patient_id=uuid.UUID(seeded_ids["patient_a_id"]),
        invoice_no="INV-5000",
        amount=Decimal("500.00"),
        status="Pending",
        description="Existing bill",
    )
    db_session.add(existing)
    db_session.commit()

    calls = {"count": 0}
    real_next = appointments_module._next_invoice_no

    def collide_once(db):
        calls["count"] += 1
        if calls["count"] == 1:
            return "INV-5000"
        return real_next(db)

    monkeypatch.setattr(appointments_module, "_next_invoice_no", collide_once)

    response = _complete(client, _doctor_token(client), appt.id)
    assert response.status_code == 200, response.text
    assert calls["count"] >= 2

    bill = _bill_for(db_session, str(appt.id))
    assert bill.invoice_no != "INV-5000"
    assert bill.invoice_no.startswith("INV-")


def test_persistent_invoice_collision_is_not_reported_as_a_slot_conflict(
    client, db_session, seeded_ids, monkeypatch
):
    appt = _confirmed_in_past(db_session, seeded_ids)
    db_session.add(
        Bill(
            patient_id=uuid.UUID(seeded_ids["patient_a_id"]),
            invoice_no="INV-6000",
            amount=Decimal("500.00"),
            status="Pending",
            description="Existing bill",
        )
    )
    db_session.commit()

    monkeypatch.setattr(
        appointments_module, "_next_invoice_no", lambda db: "INV-6000"
    )

    response = _complete(client, _doctor_token(client), appt.id)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert "invoice number" in detail.lower()
    assert "slot" not in detail.lower()

    db_session.expire_all()
    from app.models import Appointment

    stored = db_session.get(Appointment, appt.id)
    assert stored.status == "Confirmed"
