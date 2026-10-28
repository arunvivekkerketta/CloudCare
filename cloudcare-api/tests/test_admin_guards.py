"""Admin account guards: parsed UUIDs, last-admin protection, token bumps."""
import uuid

from app.models import User, UserRole
from tests.conftest import auth_headers, login


def _admin_token(client) -> str:
    response = login(client, "admin.test@example.com", "Admin!Pass1234", "admin")
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_uppercase_copy_of_own_id_cannot_delete_the_account(
    client, db_session, seeded_ids
):
    token = _admin_token(client)
    upper_id = str(uuid.UUID(seeded_ids["admin_id"])).upper()

    response = client.delete(
        f"/api/admin/users/{upper_id}", headers=auth_headers(token)
    )
    assert response.status_code == 400, response.text
    assert "cannot delete your own account" in response.json()["detail"].lower()

    still_there = db_session.get(User, uuid.UUID(seeded_ids["admin_id"]))
    assert still_there is not None


def test_last_admin_cannot_be_demoted(client, seeded_ids):
    token = _admin_token(client)

    response = client.patch(
        f"/api/admin/users/{seeded_ids['admin_id']}",
        headers=auth_headers(token),
        json={"role": "patient"},
    )
    assert response.status_code == 400, response.text
    assert "last admin" in response.json()["detail"].lower()


def test_role_change_revokes_existing_sessions(client, db_session, seeded_ids):
    doctor_login = login(
        client, "doctor.test@example.com", "Doctor!Pass123", "doctor"
    )
    doctor_token = doctor_login.json()["access_token"]
    admin_token = _admin_token(client)

    changed = client.patch(
        f"/api/admin/users/{seeded_ids['doctor_id']}",
        headers=auth_headers(admin_token),
        json={"role": "nurse"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["role"] == "nurse"

    old_session = client.get("/api/auth/me", headers=auth_headers(doctor_token))
    assert old_session.status_code == 401
    assert "sign in again" in old_session.json()["detail"].lower()

    user = db_session.get(User, uuid.UUID(seeded_ids["doctor_id"]))
    db_session.refresh(user)
    assert user.role == UserRole.nurse
    assert user.token_version == 1
