"""A booking must use the doctor's real department, not any specialty."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tests.conftest import auth_headers, login, register_patient

CLINIC_TZ = ZoneInfo("Asia/Kolkata")


def _future_day(days: int = 10) -> date:
    return (datetime.now(CLINIC_TZ) + timedelta(days=days)).date()


def _book(client, token, doctor_id, day, department):
    return client.post(
        "/api/appointments",
        headers=auth_headers(token),
        json={
            "doctor_id": doctor_id,
            "scheduled_at": f"{day.isoformat()}T10:00:00+05:30",
            "department": department,
            "mode": "In-person",
            "reason": "Department check",
        },
    )


def test_booking_rejects_a_different_department(client, seeded_ids):
    register_patient(client, "department.check@example.com")
    token = login(
        client, "department.check@example.com", "Patient!Pass123", "patient"
    ).json()["access_token"]

    response = _book(
        client, token, seeded_ids["doctor_id"], _future_day(), "Nephrology"
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == (
        "This doctor works in Cardiology. Please choose Cardiology."
    )


def test_booking_accepts_the_doctor_department_case_insensitively(
    client, seeded_ids
):
    register_patient(client, "department.ok@example.com")
    token = login(
        client, "department.ok@example.com", "Patient!Pass123", "patient"
    ).json()["access_token"]

    response = _book(
        client, token, seeded_ids["doctor_id"], _future_day(11), "cardiology"
    )
    assert response.status_code == 201, response.text
    assert response.json()["department"] == "cardiology"
