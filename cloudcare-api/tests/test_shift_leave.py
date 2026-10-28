"""A shift leave may only target a shift the doctor actually works."""
import uuid
from datetime import datetime, time

from app.models import Appointment, DoctorShift
from tests.conftest import auth_headers, login
from tests.test_booking import _book, _future_day, _patient_token


def _admin_headers(client):
    response = login(client, "admin.test@example.com", "Admin!Pass1234", "admin")
    assert response.status_code == 200, response.text
    return auth_headers(response.json()["access_token"])


def _add_morning_only(db_session, doctor_id):
    db_session.add(
        DoctorShift(
            doctor_id=uuid.UUID(doctor_id),
            name="Morning",
            start_time=time(6, 0),
            end_time=time(14, 0),
            is_active=True,
        )
    )
    db_session.commit()


def _add_both_shifts(db_session, doctor_id):
    for name, start, end in [
        ("Morning", time(6, 0), time(14, 0)),
        ("Evening", time(14, 0), time(22, 0)),
    ]:
        db_session.add(
            DoctorShift(
                doctor_id=uuid.UUID(doctor_id),
                name=name,
                start_time=start,
                end_time=end,
                is_active=True,
            )
        )
    db_session.commit()


def test_shift_leave_only_cancels_that_shift(client, seeded_ids, db_session):
    _add_both_shifts(db_session, seeded_ids["doctor_id"])
    token = _patient_token(client, "shift.leave.scope@example.com")
    day = _future_day(14)

    morning = _book(client, token, seeded_ids["doctor_id"], day, 9)
    evening = _book(client, token, seeded_ids["doctor_id"], day, 15)
    assert morning.status_code == 201, morning.text
    assert evening.status_code == 201, evening.text

    leave = client.post(
        f"/api/admin/doctors/{seeded_ids['doctor_id']}/leaves",
        headers=_admin_headers(client),
        json={"leave_date": day.isoformat(), "shift_name": "Morning"},
    )
    assert leave.status_code == 201, leave.text
    assert leave.json()[0]["cancelled_appointments"] == 1

    db_session.expire_all()
    morning_row = db_session.get(
        Appointment, uuid.UUID(morning.json()["id"])
    )
    evening_row = db_session.get(
        Appointment, uuid.UUID(evening.json()["id"])
    )
    assert morning_row.status == "Cancelled"
    assert evening_row.status == "Requested"


def test_shift_leave_rejects_a_shift_the_doctor_does_not_work(
    client, seeded_ids, db_session
):
    _add_morning_only(db_session, seeded_ids["doctor_id"])

    response = client.post(
        f"/api/admin/doctors/{seeded_ids['doctor_id']}/leaves",
        headers=_admin_headers(client),
        json={
            "leave_date": _future_day(15).isoformat(),
            "shift_name": "Evening",
        },
    )
    assert response.status_code == 400, response.text
    assert "does not work the Evening shift" in response.json()["detail"]
    assert "Morning" in response.json()["detail"]
