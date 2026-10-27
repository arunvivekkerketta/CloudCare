"""Fix #5: a doctor sees every patient who booked with them, and only those."""
from tests.conftest import auth_headers, login, register_patient
from tests.test_booking import _book, _future_day, _patient_token, _second_doctor


def _doctor_token(client, email="doctor.test@example.com") -> str:
    resp = login(client, email, "Doctor!Pass123", "doctor")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _fresh_patient_token(client, email, name) -> str:
    register_patient(client, email, name=name)
    resp = login(client, email, "Patient!Pass123", "patient")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_booking_makes_patient_visible_to_that_doctor_only(client, seeded_ids):
    token = _fresh_patient_token(client, "fix5.new@example.com", "New Fix Patient")
    day = _future_day()
    other_id, other_token = _second_doctor(client)

    booked = _book(client, token, seeded_ids["doctor_id"], day, 10)
    assert booked.status_code == 201, booked.text

    mine = client.get(
        "/api/patients?page_size=100",
        headers=auth_headers(_doctor_token(client)),
    )
    assert mine.status_code == 200, mine.text
    assert any(p["name"] == "New Fix Patient" for p in mine.json())

    theirs = client.get(
        "/api/patients?page_size=100", headers=auth_headers(other_token)
    )
    assert theirs.status_code == 200, theirs.text
    assert all(p["name"] != "New Fix Patient" for p in theirs.json())


def test_doctor_can_open_a_patient_they_have_appointments_with(client, seeded_ids):
    token = _fresh_patient_token(client, "fix5.detail@example.com", "Detail Patient")
    day = _future_day()
    _book(client, token, seeded_ids["doctor_id"], day, 11)

    listing = client.get(
        "/api/patients?page_size=100",
        headers=auth_headers(_doctor_token(client)),
    ).json()
    target = next(p for p in listing if p["name"] == "Detail Patient")

    opened = client.get(
        f"/api/patients/{target['id']}",
        headers=auth_headers(_doctor_token(client)),
    )
    assert opened.status_code == 200, opened.text

    other_id, other_token = _second_doctor(client)
    denied = client.get(
        f"/api/patients/{target['id']}", headers=auth_headers(other_token)
    )
    assert denied.status_code == 403
