"""Fix #6: in-app notifications are created, scoped per user and readable."""
from tests.conftest import auth_headers, login
from tests.test_booking import _book, _future_day, _patient_token


def _doctor_token(client) -> str:
    resp = login(client, "doctor.test@example.com", "Doctor!Pass123", "doctor")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _patient_b_token(client) -> str:
    resp = login(client, "patient.b@example.com", "Patient!Pass123", "patient")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_booking_notifies_the_doctor(client, seeded_ids):
    token = _patient_token(client, "fix6.booking@example.com")
    _book(client, token, seeded_ids["doctor_id"], _future_day(), 9)

    notes = client.get(
        "/api/notifications", headers=auth_headers(_doctor_token(client))
    )
    assert notes.status_code == 200, notes.text
    messages = [n["message"] for n in notes.json()]
    assert any(m.startswith("New appointment request from") for m in messages), messages
    assert notes.headers["X-Unread-Count"] == "1"
    assert notes.json()[0]["read"] is False


def test_confirming_notifies_the_patient(client, seeded_ids):
    token = _patient_token(client, "fix6.confirm@example.com")
    booked = _book(client, token, seeded_ids["doctor_id"], _future_day(), 10).json()
    doctor = _doctor_token(client)

    client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(doctor),
        json={"status": "Confirmed"},
    )

    notes = client.get("/api/notifications", headers=auth_headers(token)).json()
    assert any("is now Confirmed." in n["message"] for n in notes), notes


def test_notifications_are_scoped_per_user(client, seeded_ids):
    token = _patient_token(client, "fix6.scope@example.com")
    _book(client, token, seeded_ids["doctor_id"], _future_day(), 11)

    patient_notes = client.get("/api/notifications", headers=auth_headers(token)).json()
    assert patient_notes == []

    doctor = _doctor_token(client)
    doctor_notes = client.get("/api/notifications", headers=auth_headers(doctor)).json()
    assert len(doctor_notes) == 1

    # Another user cannot mark the doctor's notification read.
    stolen = client.post(
        "/api/notifications/read",
        headers=auth_headers(_patient_b_token(client)),
        json={"ids": [doctor_notes[0]["id"]]},
    )
    assert stolen.status_code == 200
    assert stolen.json()["updated"] == 0

    still_unread = client.get(
        "/api/notifications", headers=auth_headers(doctor)
    ).headers["X-Unread-Count"]
    assert still_unread == "1"


def test_mark_read_and_mark_all(client, seeded_ids):
    token = _patient_token(client, "fix6.read@example.com")
    appt = _book(client, token, seeded_ids["doctor_id"], _future_day(), 12).json()
    doctor = _doctor_token(client)
    client.patch(
        f"/api/appointments/{appt['id']}",
        headers=auth_headers(doctor),
        json={"status": "Confirmed"},
    )
    client.patch(
        f"/api/appointments/{appt['id']}",
        headers=auth_headers(doctor),
        json={"status": "Cancelled"},
    )

    notes = client.get("/api/notifications", headers=auth_headers(token)).json()
    assert len(notes) == 2
    assert client.get(
        "/api/notifications", headers=auth_headers(token)
    ).headers["X-Unread-Count"] == "2"

    one = client.post(
        "/api/notifications/read",
        headers=auth_headers(token),
        json={"ids": [notes[0]["id"]]},
    )
    assert one.status_code == 200 and one.json()["updated"] == 1

    after = client.get("/api/notifications", headers=auth_headers(token))
    assert after.headers["X-Unread-Count"] == "1"
    read_flag = {n["id"]: n["read"] for n in after.json()}
    assert read_flag[notes[0]["id"]] is True

    everything = client.post(
        "/api/notifications/read",
        headers=auth_headers(token),
        json={"all": True},
    )
    assert everything.json()["updated"] == 1
    assert client.get(
        "/api/notifications", headers=auth_headers(token)
    ).headers["X-Unread-Count"] == "0"


def test_unread_only_filter(client, seeded_ids):
    token = _patient_token(client, "fix6.unread@example.com")
    appt = _book(client, token, seeded_ids["doctor_id"], _future_day(), 13).json()
    doctor = _doctor_token(client)
    client.patch(
        f"/api/appointments/{appt['id']}",
        headers=auth_headers(doctor),
        json={"status": "Confirmed"},
    )
    notes = client.get("/api/notifications", headers=auth_headers(token)).json()
    client.post(
        "/api/notifications/read",
        headers=auth_headers(token),
        json={"ids": [notes[0]["id"]]},
    )
    unread = client.get(
        "/api/notifications?unread_only=true", headers=auth_headers(token)
    ).json()
    assert unread == []
