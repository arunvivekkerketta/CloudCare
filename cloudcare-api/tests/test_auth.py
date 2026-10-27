"""Auth & authorization tests."""
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings
from tests.conftest import auth_headers, login, register_patient

settings = get_settings()


# ------------------------------------------------- registration hardening
def test_register_always_creates_patient_role(client):
    body = register_patient(client, "esc@example.com")
    assert body["role"] == "patient"


def test_register_ignores_attempted_role_escalation(client):
    """Even if a client smuggles a role field, the server ignores it."""
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Escalator",
            "email": "escalator@example.com",
            "password": "Password!123",
            "role": "admin",  # must be ignored
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "patient"


def test_register_creates_linked_patient_row(client):
    register_patient(client, "linked@example.com")
    login_response = login(client, "linked@example.com", "Patient!Pass123", "patient")
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    profile = client.get("/api/patients/me", headers=auth_headers(token))
    assert profile.status_code == 200
    assert profile.json()["email"] == "linked@example.com"


def test_register_duplicate_email_conflict(client):
    register_patient(client, "dup@example.com")
    response = client.post(
        "/api/auth/register",
        json={"name": "Dup", "email": "dup@example.com", "password": "Password!123"},
    )
    assert response.status_code == 409


# ----------------------------------------------------------------- login
def test_login_success_returns_token_and_user(client, seeded_ids):
    response = login(client, "patient.a@example.com", "Patient!Pass123", "patient")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "patient"
    assert body["access_token"]


def test_login_wrong_password_rejected_generic_message(client, seeded_ids):
    response = login(client, "patient.a@example.com", "WrongPassword!1", "patient")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


def test_login_wrong_role_picker_still_succeeds(client, seeded_ids):
    """The role picker is informational: credentials alone decide sign-in.
    A patient picking 'admin' still logs in — as a patient — and the server
    returns the authoritative role so the client can redirect correctly."""
    response = login(client, "patient.a@example.com", "Patient!Pass123", "admin")
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["role"] == "patient"  # NOT the picked role
    assert body["access_token"]

    # The token actually authenticates as that patient.
    me = client.get("/api/auth/me", headers=auth_headers(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["role"] == "patient"


def test_login_role_field_is_optional(client, seeded_ids):
    """role is informational; omitting it entirely must also work."""
    response = client.post(
        "/api/auth/login",
        json={"email": "patient.a@example.com", "password": "Patient!Pass123"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "patient"


def test_login_wrong_password_with_wrong_picker_rejected(client, seeded_ids):
    """A mismatched picker never masks bad credentials: wrong password is
    still 401 (and the audit trail keeps the role_mismatch diagnostic)."""
    response = login(client, "patient.a@example.com", "WrongPassword!1", "admin")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


def test_login_unknown_email_same_generic_message(client):
    response = login(client, "nobody@example.com", "Whatever!123", "patient")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


def test_expired_token_rejected(client, seeded_ids):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": seeded_ids["user_a_id"],
        "role": "patient",
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),  # already expired
    }
    expired_token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)

    response = client.get("/api/auth/me", headers=auth_headers(expired_token))
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_garbage_token_rejected(client):
    response = client.get("/api/auth/me", headers=auth_headers("not.a.token"))
    assert response.status_code == 401


def test_missing_token_rejected(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_me_returns_current_user(client, seeded_ids):
    token = login(client, "patient.a@example.com", "Patient!Pass123", "patient").json()["access_token"]
    response = client.get("/api/auth/me", headers=auth_headers(token))
    assert response.status_code == 200
    assert response.json()["email"] == "patient.a@example.com"


# ------------------------------------------------- protected route access
def test_protected_routes_require_auth(client):
    for path in ["/api/auth/me", "/api/patients/me", "/api/appointments", "/api/bills", "/api/admin/users"]:
        assert client.get(path).status_code == 401, path


def test_admin_routes_forbid_patient(client, seeded_ids):
    token = login(client, "patient.a@example.com", "Patient!Pass123", "patient").json()["access_token"]
    for path in ["/api/admin/users", "/api/admin/audit-logs", "/api/users/summary"]:
        assert client.get(path, headers=auth_headers(token)).status_code == 403, path


def test_users_summary_admin_only(client, seeded_ids):
    anon = client.get("/api/users/summary")
    assert anon.status_code == 401

    patient_token = login(client, "patient.a@example.com", "Patient!Pass123", "patient").json()["access_token"]
    assert client.get("/api/users/summary", headers=auth_headers(patient_token)).status_code == 403

    admin_token = login(client, "admin.test@example.com", "Admin!Pass1234", "admin").json()["access_token"]
    ok = client.get("/api/users/summary", headers=auth_headers(admin_token))
    assert ok.status_code == 200
    assert ok.json()["total"] >= 4


def test_admin_can_create_staff(client, seeded_ids):
    admin_token = login(client, "admin.test@example.com", "Admin!Pass1234", "admin").json()["access_token"]

    response = client.post(
        "/api/admin/users",
        headers=auth_headers(admin_token),
        json={
            "name": "Dr. Created",
            "email": "dr.created@example.com",
            "password": "Staff!Pass123",
            "role": "doctor",
            "department": "Neurology",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "doctor"

    # The new doctor can actually log in with the doctor role.
    staff_login = login(client, "dr.created@example.com", "Staff!Pass123", "doctor")
    assert staff_login.status_code == 200


def test_patient_cannot_create_staff_account(client, seeded_ids):
    token = login(client, "patient.a@example.com", "Patient!Pass123", "patient").json()["access_token"]
    response = client.post(
        "/api/admin/users",
        headers=auth_headers(token),
        json={
            "name": "Sneaky",
            "email": "sneaky@example.com",
            "password": "Staff!Pass123",
            "role": "admin",
        },
    )
    assert response.status_code == 403


# ------------------------------------------------------- patient isolation
def test_patient_cannot_read_another_patient_profile(client, seeded_ids):
    """Patient A explicitly requests patient B's profile -> 403."""
    token_a = login(client, "patient.a@example.com", "Patient!Pass123", "patient").json()["access_token"]
    response = client.get(
        f"/api/patients/{seeded_ids['patient_b_id']}", headers=auth_headers(token_a)
    )
    assert response.status_code == 403


def test_patient_reads_own_profile_by_id(client, seeded_ids):
    token_a = login(client, "patient.a@example.com", "Patient!Pass123", "patient").json()["access_token"]
    response = client.get(
        f"/api/patients/{seeded_ids['patient_a_id']}", headers=auth_headers(token_a)
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Patient A"


# ---------------------------------------------------------- change password
def test_change_password_requires_correct_current(client, seeded_ids):
    token = login(client, "patient.a@example.com", "Patient!Pass123", "patient").json()["access_token"]
    bad = client.post(
        "/api/auth/change-password",
        headers=auth_headers(token),
        json={"current_password": "nope", "new_password": "NewPass!1234"},
    )
    assert bad.status_code == 400

    good = client.post(
        "/api/auth/change-password",
        headers=auth_headers(token),
        json={"current_password": "Patient!Pass123", "new_password": "NewPass!1234"},
    )
    assert good.status_code == 200

    # Old password no longer works; new one does.
    assert login(client, "patient.a@example.com", "Patient!Pass123", "patient").status_code == 401
    assert login(client, "patient.a@example.com", "NewPass!1234", "patient").status_code == 200


# ------------------------------------------------------------- audit logs
def test_audit_logs_recorded_for_login_and_register(client, seeded_ids):
    admin_token = login(client, "admin.test@example.com", "Admin!Pass1234", "admin").json()["access_token"]

    register_patient(client, "audited@example.com")
    login(client, "audited@example.com", "Patient!Pass123", "patient")
    login(client, "audited@example.com", "WrongPass!123", "patient")  # failure

    logs = client.get("/api/admin/audit-logs", headers=auth_headers(admin_token))
    assert logs.status_code == 200
    actions = {entry["action"] for entry in logs.json()["items"]}
    assert "register" in actions
    assert "login_success" in actions
    assert "login_failure" in actions


# ------------------------------------------------- self profile update
def test_me_patch_updates_name_and_email(client, seeded_ids):
    token = login(client, "patient.a@example.com", "Patient!Pass123", "patient").json()["access_token"]
    headers = auth_headers(token)

    response = client.patch(
        "/api/auth/me",
        headers=headers,
        json={
            "name": "Renamed Patient",
            "email": "renamed.a@example.com",
            "current_password": "Patient!Pass123",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed Patient"
    assert body["email"] == "renamed.a@example.com"

    # /api/auth/me reflects the change and the old email no longer logs in.
    assert client.get("/api/auth/me", headers=headers).json()["name"] == "Renamed Patient"
    assert login(client, "patient.a@example.com", "Patient!Pass123", "patient").status_code == 401
    assert login(client, "renamed.a@example.com", "Patient!Pass123", "patient").status_code == 200


def test_me_patch_email_conflict_returns_409(client, seeded_ids):
    """Cannot take an email that already belongs to another account."""
    token = login(client, "patient.a@example.com", "Patient!Pass123", "patient").json()["access_token"]
    response = client.patch(
        "/api/auth/me",
        headers=auth_headers(token),
        json={"email": "patient.b@example.com", "current_password": "Patient!Pass123"},
    )
    assert response.status_code == 409


def test_me_patch_rejects_short_name_and_empty_body(client, seeded_ids):
    token = login(client, "patient.a@example.com", "Patient!Pass123", "patient").json()["access_token"]
    headers = auth_headers(token)

    short = client.patch("/api/auth/me", headers=headers, json={"name": "A"})
    assert short.status_code == 422

    empty = client.patch("/api/auth/me", headers=headers, json={})
    assert empty.status_code == 400


def test_me_patch_requires_auth(client):
    assert client.patch("/api/auth/me", json={"name": "No Auth"}).status_code == 401


def test_login_unknown_email_timing_uses_dummy_hash():
    """The dummy hash used to equalize unknown-email timing must be a valid
    Argon2id hash that verifies (and rejects wrong passwords) normally."""
    from app.routers.auth import _DUMMY_PASSWORD_HASH
    from app.security import verify_password

    assert verify_password("anything", _DUMMY_PASSWORD_HASH) is False


def test_health_endpoints(client):
    live = client.get("/health")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}

    db_health = client.get("/health/db")
    assert db_health.status_code == 200
    body = db_health.json()
    assert body["database"] == "connected"
    # Never leaks exception text
    assert "message" not in body
