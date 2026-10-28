"""Messages say "Dr. Aakash Verma" once, never "Dr. Dr. ..."."""
import uuid
from datetime import datetime

from app.models import DoctorLeave
from app.utils import doctor_label
from tests.conftest import auth_headers, login
from tests.test_booking import _book, _future_day, _patient_token


def test_doctor_label_prefixes_only_an_untitled_name():
    assert doctor_label("Aakash Verma") == "Dr. Aakash Verma"
    assert doctor_label("Dr. Aakash Verma") == "Dr. Aakash Verma"
    assert doctor_label("Dr Aakash Verma") == "Dr Aakash Verma"
    assert doctor_label("DR. Aakash Verma") == "DR. Aakash Verma"
    assert doctor_label("Dravid Iyer") == "Dr. Dravid Iyer"


def test_leave_message_uses_the_stored_name_once(client, seeded_ids, db_session):
    token = _patient_token(client, "title.leave@example.com")
    day = _future_day(12)
    db_session.add(
        DoctorLeave(
            doctor_id=uuid.UUID(seeded_ids["doctor_id"]),
            leave_date=datetime.strptime(day.isoformat(), "%Y-%m-%d").date(),
            shift_name=None,
        )
    )
    db_session.commit()

    response = _book(client, token, seeded_ids["doctor_id"], day, 10)
    assert response.status_code == 400, response.text
    assert response.json()["detail"].startswith("Dr. Test is on leave")
    assert "Dr. Dr." not in response.json()["detail"]


def test_doctor_delete_conflict_uses_the_stored_name_once(client, seeded_ids):
    token = _patient_token(client, "title.delete@example.com")
    booked = _book(client, token, seeded_ids["doctor_id"], _future_day(13), 10)
    assert booked.status_code == 201, booked.text

    admin = login(client, "admin.test@example.com", "Admin!Pass1234", "admin")
    response = client.delete(
        f"/api/admin/doctors/{seeded_ids['doctor_id']}",
        headers=auth_headers(admin.json()["access_token"]),
    )
    assert response.status_code == 409, response.text
    message = response.json()["detail"]["message"]
    assert message.startswith("Dr. Test still has")
    assert "Dr. Dr." not in message
