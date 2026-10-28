"""Status changes use the same plain words in-app and by email."""
from tests.conftest import auth_headers, login
from tests.test_booking import _book, _future_day, _patient_token


def _doctor_token(client) -> str:
    response = login(client, "doctor.test@example.com", "Doctor!Pass123", "doctor")
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _notifications(client, token):
    response = client.get("/api/notifications", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    return [row["message"] for row in response.json()]


def test_confirmed_and_declined_use_plain_words(client, seeded_ids, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.routers.appointments.send_email",
        lambda to, subject, body: sent.append((to, subject, body)) or True,
    )
    token = _patient_token(client, "copy.status@example.com")
    doctor = _doctor_token(client)
    day = _future_day()

    confirmed = _book(client, token, seeded_ids["doctor_id"], day, 9).json()
    response = client.patch(
        f"/api/appointments/{confirmed['id']}",
        headers=auth_headers(doctor),
        json={"status": "Confirmed"},
    )
    assert response.status_code == 200, response.text

    declined = _book(client, token, seeded_ids["doctor_id"], day, 10).json()
    response = client.patch(
        f"/api/appointments/{declined['id']}",
        headers=auth_headers(doctor),
        json={"status": "Declined"},
    )
    assert response.status_code == 200, response.text

    messages = _notifications(client, token)
    accepted = [m for m in messages if "The doctor has accepted your appointment" in m]
    declined_note = [m for m in messages if "could not take your appointment" in m]
    assert accepted, messages
    assert declined_note, messages
    assert "Choose another time." in declined_note[0]
    assert not any("is now Confirmed" in m or "is now Declined" in m for m in messages)

    assert len(sent) == 2
    assert sent[0][1] == "Your CloudCare appointment is confirmed"
    assert "The doctor has accepted your appointment" in sent[0][2]
    assert sent[1][1] == "Choose another time for your CloudCare appointment"
    assert "could not take your appointment" in sent[1][2]
