"""Fix #17: telemedicine links are real, https-only and doctor-controlled."""
from tests.conftest import auth_headers, login
from tests.test_booking import _book, _future_day, _patient_token


def _doctor_token(client) -> str:
    resp = login(client, "doctor.test@example.com", "Doctor!Pass123", "doctor")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_meeting_url_must_be_https(client, seeded_ids):
    token = _patient_token(client, "fix17.http@example.com")
    booked = _book(client, token, seeded_ids["doctor_id"], _future_day(), 9).json()
    doctor = _doctor_token(client)

    resp = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"meeting_url": "http://meet.example.com/room"},
    )
    assert resp.status_code == 422, resp.text


def test_patient_cannot_set_meeting_url(client, seeded_ids):
    token = _patient_token(client, "fix17.patient@example.com")
    booked = _book(client, token, seeded_ids["doctor_id"], _future_day(), 10).json()

    resp = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(token),
        json={"meeting_url": "https://meet.example.com/room"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Only the doctor can add a meeting link."


def test_doctor_confirms_telemedicine_with_link(client, seeded_ids):
    token = _patient_token(client, "fix17.ok@example.com")
    booked = _book(client, token, seeded_ids["doctor_id"], _future_day(11), 11).json()
    doctor = _doctor_token(client)

    resp = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={
            "status": "Confirmed",
            "meeting_url": "https://meet.example.com/dr-test-room",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "Confirmed"
    assert body["meeting_url"] == "https://meet.example.com/dr-test-room"
