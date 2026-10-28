"""The booking screen can label Booked and Doctor on leave times."""
import uuid
from app.models import DoctorLeave
from tests.test_booking import _book, _future_day, _patient_token


def _schedule(client, doctor_id, day):
    response = client.get(
        f"/api/doctors/{doctor_id}/schedule?date={day.isoformat()}"
    )
    assert response.status_code == 200, response.text
    return response.json()


def _by_label(schedule):
    return {row["label"]: row for row in schedule["slots"]}


def test_day_schedule_marks_booked_and_shift_leave(client, seeded_ids, db_session):
    day = _future_day()
    doctor_id = uuid.UUID(seeded_ids["doctor_id"])
    token = _patient_token(client, "schedule.booked@example.com")
    booked = _book(client, token, seeded_ids["doctor_id"], day, 9)
    assert booked.status_code == 201, booked.text
    db_session.add(
        DoctorLeave(
            doctor_id=doctor_id,
            leave_date=day,
            shift_name="Evening",
        )
    )
    db_session.commit()

    schedule = _schedule(client, seeded_ids["doctor_id"], day)
    rows = _by_label(schedule)
    assert schedule["on_leave"] is False
    assert schedule["leave_shifts"] == ["Evening"]
    assert rows["09:00"]["status"] == "booked"
    assert rows["10:00"]["status"] == "available"
    assert rows["15:00"]["status"] == "on_leave"
    assert any(shift["name"] == "Morning" for shift in schedule["shifts"])


def test_whole_day_leave_marks_every_time_on_leave(client, seeded_ids, db_session):
    day = _future_day(12)
    db_session.add(
        DoctorLeave(
            doctor_id=uuid.UUID(seeded_ids["doctor_id"]),
            leave_date=day,
            shift_name=None,
        )
    )
    db_session.commit()

    schedule = _schedule(client, seeded_ids["doctor_id"], day)
    assert schedule["on_leave"] is True
    assert schedule["slots"]
    assert {row["status"] for row in schedule["slots"]} == {"on_leave"}


def test_schedule_rejects_bad_dates_and_unknown_doctors(client, seeded_ids):
    bad_date = client.get(
        f"/api/doctors/{seeded_ids['doctor_id']}/schedule?date=not-a-date"
    )
    assert bad_date.status_code == 400
    missing = client.get(
        "/api/doctors/00000000-0000-0000-0000-000000000000/schedule?date=2099-01-01"
    )
    assert missing.status_code == 404
