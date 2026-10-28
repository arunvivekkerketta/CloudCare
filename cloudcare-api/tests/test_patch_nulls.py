"""Patients cannot set their clinical status, and explicit nulls are 422."""
from tests.conftest import auth_headers, login
from tests.test_booking import _book, _future_day, _patient_token


def test_patient_cannot_change_their_own_status(client, seeded_ids):
    response = login(
        client, "patient.a@example.com", "Patient!Pass123", "patient"
    )
    token = response.json()["access_token"]
    headers = auth_headers(token)

    attempt = client.patch(
        "/api/patients/me", headers=headers, json={"status": "Discharged"}
    )
    assert attempt.status_code == 422, attempt.text
    assert "cannot change your own patient status" in attempt.text

    profile = client.get("/api/patients/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["status"] == "Active"


def test_patient_name_cannot_be_null(client, seeded_ids):
    response = login(
        client, "patient.a@example.com", "Patient!Pass123", "patient"
    )
    token = response.json()["access_token"]

    attempt = client.patch(
        "/api/patients/me",
        headers=auth_headers(token),
        json={"name": None},
    )
    assert attempt.status_code == 422, attempt.text
    assert "Name cannot be empty" in attempt.text


def test_appointment_patch_rejects_nulls_with_422(client, seeded_ids):
    token = _patient_token(client, "null.patch@example.com")
    booked = _book(client, token, seeded_ids["doctor_id"], _future_day(), 10)
    assert booked.status_code == 201, booked.text
    appointment_id = booked.json()["id"]

    for body, expected in [
        ({"status": None}, "status cannot be empty"),
        ({"scheduled_at": None}, "Choose a new appointment time"),
        ({"department": None}, "Choose a department"),
        ({"mode": None}, "Choose In-person or Telemedicine"),
    ]:
        response = client.patch(
            f"/api/appointments/{appointment_id}",
            headers=auth_headers(token),
            json=body,
        )
        assert response.status_code == 422, (body, response.status_code, response.text)
        assert expected.lower() in response.text.lower()

    unchanged = client.get(
        f"/api/appointments/{appointment_id}", headers=auth_headers(token)
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["status"] == "Requested"
