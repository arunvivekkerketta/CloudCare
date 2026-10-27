"""Fix #4: a doctor's leave cancels already-booked appointments."""
from tests.conftest import auth_headers, login
from tests.test_booking import _book, _future_day, _patient_token


def _admin_token(client) -> str:
    resp = login(client, "admin.test@example.com", "Admin!Pass1234", "admin")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_whole_day_leave_cancels_and_notifies(client, seeded_ids):
    token = _patient_token(client, "fix4.whole@example.com")
    admin = _admin_token(client)
    day = _future_day(15)

    booked = _book(client, token, seeded_ids["doctor_id"], day, 10)
    assert booked.status_code == 201, booked.text

    created = client.post(
        f"/api/admin/doctors/{seeded_ids['doctor_id']}/leaves",
        headers=auth_headers(admin),
        json={"leave_date": day.isoformat(), "shift_name": None},
    )
    assert created.status_code == 201, created.text
    body = created.json()[0]
    assert body["cancelled_appointments"] == 1

    appt = client.get(
        f"/api/appointments/{booked.json()['id']}",
        headers=auth_headers(token),
    ).json()
    assert appt["status"] == "Cancelled"
    assert appt["status_reason"] == "Doctor on leave"

    notes = client.get("/api/notifications", headers=auth_headers(token)).json()
    assert any("on leave" in n["message"] for n in notes), notes


def test_shift_leave_only_cancels_that_shift(client, seeded_ids):
    token = _patient_token(client, "fix4.shift@example.com")
    admin = _admin_token(client)
    day = _future_day(16)

    morning = _book(client, token, seeded_ids["doctor_id"], day, 9)
    evening = _book(client, token, seeded_ids["doctor_id"], day, 15)
    assert morning.status_code == 201, morning.text
    assert evening.status_code == 201, evening.text

    created = client.post(
        f"/api/admin/doctors/{seeded_ids['doctor_id']}/leaves",
        headers=auth_headers(admin),
        json={"leave_date": day.isoformat(), "shift_name": "Morning"},
    )
    assert created.status_code == 201, created.text
    assert created.json()[0]["cancelled_appointments"] == 1

    morning_after = client.get(
        f"/api/appointments/{morning.json()['id']}",
        headers=auth_headers(token),
    ).json()
    evening_after = client.get(
        f"/api/appointments/{evening.json()['id']}",
        headers=auth_headers(token),
    ).json()
    assert morning_after["status"] == "Cancelled"
    assert evening_after["status"] == "Requested"
