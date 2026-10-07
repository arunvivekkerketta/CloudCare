"""Gate tests, batch 3: reviews API, admin CRUD, guards, safe doctor delete."""
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tests.conftest import auth_headers, login, register_patient

from app.models import (
    Appointment,
    BillingDetail,
    DoctorLeave,
    DoctorShift,
    Patient,
    User,
    UserRole,
)
from app.security import hash_password

CLINIC_TZ = ZoneInfo("Asia/Kolkata")

ADMIN = ("admin.test@example.com", "Admin!Pass1234")
DOCTOR = ("doctor.test@example.com", "Doctor!Pass123")


def _admin_token(client) -> str:
    resp = login(client, ADMIN[0], ADMIN[1], "admin")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _doctor_token(client) -> str:
    resp = login(client, DOCTOR[0], DOCTOR[1], "doctor")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _patient(client, email: str) -> str:
    register_patient(client, email, name=f"Patient {email.split('@')[0]}")
    return login(client, email, "Patient!Pass123", "patient").json()["access_token"]


def _future_day(days: int = 10) -> date:
    return (datetime.now(CLINIC_TZ) + timedelta(days=days)).date()


def _ist(day: date, hour: int, minute: int = 0) -> str:
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}:00+05:30"


def _book(client, token, doctor_id, day, hour, minute=0):
    resp = client.post(
        "/api/appointments",
        headers=auth_headers(token),
        json={
            "doctor_id": doctor_id,
            "scheduled_at": _ist(day, hour, minute),
            "department": "Cardiology",
            "mode": "In-person",
            "reason": "Gate test",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------- gate 12a
def test_review_create_validation_and_upsert(client, seeded_ids):
    token = _patient(client, "reviewer1@example.com")
    doctor_id = seeded_ids["doctor_id"]
    H = auth_headers(token)

    # Validation: rating bounds.
    assert (
        client.post("/api/reviews", headers=H, json={"doctor_id": doctor_id, "rating": 0, "text": "Good job doctor!"}).status_code == 422
    )
    assert (
        client.post("/api/reviews", headers=H, json={"doctor_id": doctor_id, "rating": 6, "text": "Good job doctor!"}).status_code == 422
    )
    # Text must be 10..500 chars.
    assert (
        client.post("/api/reviews", headers=H, json={"doctor_id": doctor_id, "rating": 5, "text": "short"}).status_code == 422
    )
    assert (
        client.post("/api/reviews", headers=H, json={"doctor_id": doctor_id, "rating": 5, "text": "x" * 501}).status_code == 422
    )
    # Unknown doctor.
    assert (
        client.post("/api/reviews", headers=H, json={"doctor_id": str(uuid.uuid4()), "rating": 5, "text": "Valid review text"}).status_code == 404
    )

    first = client.post(
        "/api/reviews", headers=H,
        json={"doctor_id": doctor_id, "rating": 5, "text": "Excellent care and attention."},
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["rating"] == 5 and body["doctor_id"] == doctor_id
    assert body["author_name"] == "Patient reviewer1"

    # Second submit = edit (unique author+doctor), HTTP 200 on the same row.
    second = client.post(
        "/api/reviews", headers=H,
        json={"doctor_id": doctor_id, "rating": 4, "text": "Edited: still very good care."},
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] == body["id"]
    assert second.json()["rating"] == 4

    rows = client.get(f"/api/reviews/doctors/{doctor_id}").json()["items"]
    assert len(rows) == 1


def test_reviews_public_listing_and_summary(client, seeded_ids):
    token_a = _patient(client, "pubrev.a@example.com")
    token_b = _patient(client, "pubrev.b@example.com")
    doctor_id = seeded_ids["doctor_id"]

    ok1 = client.post("/api/reviews", headers=auth_headers(token_a), json={"doctor_id": doctor_id, "rating": 5, "text": "Outstanding diagnosis skills."})
    ok2 = client.post("/api/reviews", headers=auth_headers(token_b), json={"doctor_id": doctor_id, "rating": 3, "text": "Decent wait time overall ok."})
    assert ok1.status_code == 201 and ok2.status_code == 201

    page = client.get(f"/api/reviews/doctors/{doctor_id}").json()
    assert page["total"] == 2 and len(page["items"]) == 2
    assert {r["rating"] for r in page["items"]} == {5, 3}

    summaries = {s["doctor_id"]: s for s in client.get("/api/reviews/summary").json()}
    mine = summaries[doctor_id]
    assert mine["review_count"] == 2
    assert mine["average_rating"] == 4.0

    # Anonymous write is rejected.
    assert client.post("/api/reviews", json={"doctor_id": doctor_id, "rating": 5, "text": "Anonymous not allowed"}).status_code == 401


def test_review_delete_author_only(client, seeded_ids):
    token = _patient(client, "deleter@example.com")
    doctor_id = seeded_ids["doctor_id"]
    H = auth_headers(token)

    client.post("/api/reviews", headers=H, json={"doctor_id": doctor_id, "rating": 2, "text": "Average experience sadly here."})

    # Another user cannot delete someone else's review (route is /mine/*).
    other = _patient(client, "deleter2@example.com")
    assert client.delete(f"/api/reviews/mine/{doctor_id}", headers=auth_headers(other)).status_code == 404

    assert client.delete(f"/api/reviews/mine/{doctor_id}", headers=H).status_code == 204
    assert client.get(f"/api/reviews/doctors/{doctor_id}").json()["total"] == 0
    # Author can write a fresh one afterwards.
    again = client.post("/api/reviews", headers=H, json={"doctor_id": doctor_id, "rating": 4, "text": "Fresh review after deletion."})
    assert again.status_code == 201


# --------------------------------------------------------------- gate 15a
def test_specialties_crud_and_public(client, seeded_ids):
    admin = _admin_token(client)

    # "Cardiology" is the seeded doctor's department -> delete must be guarded.
    created = client.post("/api/admin/specialties", headers=auth_headers(admin), json={"name": "Cardiology"})
    assert created.status_code == 201, created.text
    sid = created.json()["id"]

    dup = client.post("/api/admin/specialties", headers=auth_headers(admin), json={"name": "cardiology"})
    assert dup.status_code == 409

    # Public dropdown read + admin read both list it.
    assert any(s["id"] == sid for s in client.get("/api/specialties").json())
    assert any(s["id"] == sid for s in client.get("/api/admin/specialties", headers=auth_headers(admin)).json())

    # Delete is protected while any doctor uses the name as department.
    resp = client.delete(f"/api/admin/specialties/{sid}", headers=auth_headers(admin))
    assert resp.status_code == 409, resp.text
    assert "still assigned" in resp.json()["detail"]

    # Admin can re-map that doctor's department, then delete succeeds.
    doctor_id = seeded_ids["doctor_id"]
    patched = client.patch(
        f"/api/admin/users/{doctor_id}", headers=auth_headers(admin), json={"department": "Neurology"}
    )
    assert patched.status_code == 200, patched.text
    assert client.delete(f"/api/admin/specialties/{sid}", headers=auth_headers(admin)).status_code == 204

    # Non-admin cannot manage specialties.
    patient = _patient(client, "spec.patient@example.com")
    assert client.post("/api/admin/specialties", headers=auth_headers(patient), json={"name": "Xy"}).status_code == 403


# --------------------------------------------------------------- gate 16a
def test_shifts_sync_validation_and_effect(client, seeded_ids):
    admin = _admin_token(client)
    doctor_id = seeded_ids["doctor_id"]
    H = auth_headers(admin)

    base = client.get(f"/api/admin/doctors/{doctor_id}/shifts", headers=H).json()
    assert {"Morning", "Evening"} <= {s["name"] for s in base}

    # Invalid: end before start.
    bad1 = client.put(
        f"/api/admin/doctors/{doctor_id}/shifts", headers=H,
        json={"shifts": [{"name": "Morning", "start": "14:00", "end": "06:00"}, {"name": "Evening", "start": "16:00", "end": "22:00"}]},
    )
    assert bad1.status_code == 400

    # Invalid: unaligned minutes.
    bad2 = client.put(
        f"/api/admin/doctors/{doctor_id}/shifts", headers=H,
        json={"shifts": [{"name": "Morning", "start": "09:02", "end": "13:00"}, {"name": "Evening", "start": "16:00", "end": "22:00"}]},
    )
    assert bad2.status_code == 400

    # Valid custom hours: 09:00-13:00 + 16:00-20:00.
    ok = client.put(
        f"/api/admin/doctors/{doctor_id}/shifts", headers=H,
        json={"shifts": [{"name": "Morning", "start": "09:00", "end": "13:00"}, {"name": "Evening", "start": "16:00", "end": "20:00"}]},
    )
    assert ok.status_code == 200, ok.text
    names = {s["name"]: (s["start"], s["end"]) for s in ok.json()}
    assert names["Morning"] == ("09:00", "13:00") and names["Evening"] == ("16:00", "20:00")

    day = _future_day()
    slots = client.get(f"/api/doctors/{doctor_id}/slots?date={day.isoformat()}").json()
    labels = [s["label"] for s in slots]
    assert labels and min(labels) >= "09:00" and max(labels) <= "19:30"
    assert all(not ("13:00" < l < "16:00") for l in labels)

    # Atomicity: a bad second save leaves the first intact.
    failed = client.put(
        f"/api/admin/doctors/{doctor_id}/shifts", headers=H,
        json={"shifts": [{"name": "Morning", "start": "09:00", "end": "13:00"}, {"name": "Evening", "start": "12:00", "end": "18:00"}]},
    )  # Evening starts before Morning ends
    assert failed.status_code == 400
    current = client.get(f"/api/admin/doctors/{doctor_id}/shifts", headers=H).json()
    assert {s["name"]: (s["start"], s["end"]) for s in current} == names


def test_leaves_crud_blocks_booking(client, seeded_ids):
    admin = _admin_token(client)
    doctor_id = seeded_ids["doctor_id"]
    H = auth_headers(admin)
    day = _future_day(14)

    created = client.post(
        f"/api/admin/doctors/{doctor_id}/leaves", headers=H,
        json={"leave_date": day.isoformat(), "shift_name": "Morning"},
    )
    assert created.status_code == 201, created.text
    leave = created.json()[0]
    assert leave["shift_name"] == "Morning"

    dup = client.post(
        f"/api/admin/doctors/{doctor_id}/leaves", headers=H,
        json={"leave_date": day.isoformat(), "shift_name": "Morning"},
    )
    assert dup.status_code == 409

    past = client.post(
        f"/api/admin/doctors/{doctor_id}/leaves", headers=H,
        json={"leave_date": "2020-01-01"},
    )
    assert past.status_code == 400

    # Booking into the leave shift is rejected, evening still ok.
    token = _patient(client, "leave.api@example.com")
    morning = client.post(
        "/api/appointments", headers=auth_headers(token),
        json={"doctor_id": doctor_id, "scheduled_at": _ist(day, 9), "department": "Cardiology", "mode": "In-person"},
    )
    assert morning.status_code == 400 and "leave" in morning.json()["detail"].lower()
    evening = client.post(
        "/api/appointments", headers=auth_headers(token),
        json={"doctor_id": doctor_id, "scheduled_at": _ist(day, 15), "department": "Cardiology", "mode": "In-person"},
    )
    assert evening.status_code == 201, evening.text

    listing = client.get(f"/api/admin/doctors/{doctor_id}/leaves", headers=H).json()
    assert any(row["id"] == leave["id"] for row in listing)
    assert client.delete(f"/api/admin/leaves/{leave['id']}", headers=H).status_code == 204

    # Slots return for the freed morning (future only).
    slots = client.get(f"/api/doctors/{doctor_id}/slots?date={day.isoformat()}").json()
    assert any(s["label"] == "09:00" for s in slots)


# --------------------------------------------------------------- gate 21a+
def test_admin_appointments_list_and_filters(client, seeded_ids):
    admin = _admin_token(client)
    H = auth_headers(admin)
    day = _future_day()

    p1 = _patient(client, "adminlist.a@example.com")
    booked = _book(client, p1, seeded_ids["doctor_id"], day, 9)
    doctor_id = seeded_ids["doctor_id"]

    feed = client.get("/api/admin/appointments", headers=H).json()
    assert any(a["id"] == booked["id"] for a in feed)

    by_status = client.get("/api/admin/appointments?status=Requested", headers=H).json()
    assert all(a["status"] == "Requested" for a in by_status) and by_status

    by_doctor = client.get(f"/api/admin/appointments?doctor_id={doctor_id}", headers=H).json()
    assert all(a["doctor_id"] == doctor_id for a in by_doctor)

    confirmed = client.patch(
        f"/api/appointments/{booked['id']}",
        headers=auth_headers(_doctor_token(client)),
        json={"status": "Confirmed"},
    )
    assert confirmed.status_code == 200
    feed2 = client.get("/api/admin/appointments?status=Confirmed", headers=H).json()
    assert any(a["id"] == booked["id"] for a in feed2)


# --------------------------------------------------------------- gate 23a
def test_review_moderation_admin_list_and_delete(client, seeded_ids):
    admin = _admin_token(client)
    H = auth_headers(admin)
    doctor_id = seeded_ids["doctor_id"]

    token = _patient(client, "moderated@example.com")
    created = client.post("/api/reviews", headers=auth_headers(token), json={"doctor_id": doctor_id, "rating": 1, "text": "Terrible rude service sorry."})
    review_id = created.json()["id"]

    listing = client.get("/api/admin/reviews", headers=H).json()
    assert listing["total"] >= 1 and any(r["id"] == review_id for r in listing["items"])

    filtered = client.get(f"/api/admin/reviews?author_id={created.json()['author_id']}", headers=H).json()
    assert filtered["total"] == 1

    assert client.delete(f"/api/admin/reviews/{review_id}", headers=H).status_code == 204
    assert client.get(f"/api/reviews/doctors/{doctor_id}").json()["total"] == 0
    assert client.delete(f"/api/admin/reviews/{review_id}", headers=H).status_code == 404


# --------------------------------------------------------------- gate 25a
def test_admin_cannot_change_or_delete_own_account_via_patch(client, seeded_ids):
    admin = _admin_token(client)
    H = auth_headers(admin)
    me_id = login(client, ADMIN[0], ADMIN[1], "admin").json()["user"]["id"]

    demote = client.patch(f"/api/admin/users/{me_id}", headers=H, json={"role": "patient"})
    assert demote.status_code == 400
    assert "own role" in demote.json()["detail"].lower()

    # And never delete their own account (pre-existing guard, still intact).
    suicide = client.delete(f"/api/admin/users/{me_id}", headers=H)
    assert suicide.status_code == 400
    assert "own account" in suicide.json()["detail"].lower()

    # Sanity: changing SOMEONE ELSE's role still works.
    created = register_patient(client, "role.change@example.com")
    promote = client.patch(
        f"/api/admin/users/{created['id']}", headers=H, json={"role": "nurse"}
    )
    assert promote.status_code == 200, promote.text
    assert promote.json()["role"] == "nurse"


# --------------------------------------------------------------- gate 24a
def test_doctor_delete_paths(client, seeded_ids, db_session):
    admin = _admin_token(client)
    H = auth_headers(admin)
    day = _future_day()

    # CASE 1: doctor with NO appointments deletes cleanly; dependencies die too.
    vanish = User(
        name="Dr. Vanish",
        email="dr.vanish@example.com",
        hashed_password=hash_password("Doctor!Pass123"),
        role=UserRole.doctor,
        department="Cardiology",
    )
    db_session.add(vanish)
    db_session.flush()
    vanish_id = str(vanish.id)
    db_session.add(
        DoctorShift(
            doctor_id=vanish.id,
            name="Morning",
            start_time=datetime.strptime("06:00", "%H:%M").time(),
            end_time=datetime.strptime("14:00", "%H:%M").time(),
        )
    )
    db_session.add(
        DoctorLeave(
            doctor_id=vanish.id,
            leave_date=_future_day(5),
            shift_name=None,
        )
    )
    db_session.add(
        BillingDetail(user_id=vanish.id, full_name="Dr. Vanish", email="dr.vanish@example.com")
    )
    db_session.commit()

    assert client.delete(f"/api/admin/doctors/{vanish_id}", headers=H).status_code == 204
    assert db_session.get(User, vanish.id) is None
    assert db_session.query(DoctorShift).filter(DoctorShift.doctor_id == vanish.id).count() == 0
    assert db_session.query(DoctorLeave).filter(DoctorLeave.doctor_id == vanish.id).count() == 0
    assert db_session.query(BillingDetail).filter(BillingDetail.user_id == vanish.id).count() == 0

    # CASE 2: doctor WITH an active appointment -> 409 without cancel=true.
    created = client.post(
        "/api/admin/users",
        headers=auth_headers(admin),
        json={
            "name": "Dr. Busy",
            "email": "dr.busy@example.com",
            "password": "Doctor!Pass123",
            "role": "doctor",
            "department": "Cardiology",
        },
    )
    assert created.status_code == 201, created.text
    busy_id = created.json()["id"]

    register_patient(client, "busy.patient@example.com", name="Busy Patient")
    patient_token = login(client, "busy.patient@example.com", "Patient!Pass123", "patient").json()["access_token"]
    # Give the doctor an assigned patient record + one active appointment.
    patient_row = (
        db_session.query(Patient)
        .join(User, Patient.user_id == User.id)
        .filter(User.email == "busy.patient@example.com")
        .first()
    )
    assert patient_row is not None
    patient_row.assigned_doctor_id = uuid.UUID(busy_id)
    db_session.commit()

    booked = client.post(
        "/api/appointments",
        headers=auth_headers(patient_token),
        json={
            "doctor_id": busy_id,
            "scheduled_at": _ist(day, 10),
            "department": "Cardiology",
            "mode": "In-person",
            "reason": "Delete me",
        },
    )
    assert booked.status_code == 201, booked.text

    refused = client.delete(f"/api/admin/doctors/{busy_id}", headers=H)
    assert refused.status_code == 409
    body = refused.json()["detail"]
    assert body["active_appointments"] == 1 and body["assigned_patients"] == 1

    removed = client.delete(f"/api/admin/doctors/{busy_id}?cancel=true", headers=H)
    assert removed.status_code == 204

    # Appointment records (including the just-cancelled one) are removed
    # with the doctor — doctor_id is NOT NULL, rows cannot be orphaned.
    remaining = (
        db_session.query(Appointment)
        .filter(Appointment.doctor_id == uuid.UUID(busy_id))
        .count()
    )
    assert remaining == 0
    db_session.expire_all()
    patient_row = db_session.get(Patient, patient_row.id)
    assert patient_row.assigned_doctor_id is None
    assert db_session.get(User, uuid.UUID(busy_id)) is None


# --------------------------------------------------------------- gate 20a
def test_admin_created_patient_gets_billing_row(client, seeded_ids):
    admin = _admin_token(client)
    created = client.post(
        "/api/admin/users",
        headers=auth_headers(admin),
        json={
            "name": "Created Patient",
            "email": "created.patient@example.com",
            "password": "Patient!Pass123",
            "role": "patient",
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]

    token = login(client, "created.patient@example.com", "Patient!Pass123", "patient").json()["access_token"]
    body = client.get("/api/billing/me", headers=auth_headers(token)).json()
    assert body["full_name"] == "Created Patient"
    assert body["currency"] == "INR" and body["status"] == "Default"
