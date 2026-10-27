"""Fix #8/#9/#10/#11/#12: registration, sessions, email change, password reset."""
import hashlib
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings
from app.models import User
from tests.conftest import auth_headers, login

settings = get_settings()


def _reset_token(
    user: User,
    *,
    purpose: str = "reset",
    expires_in: timedelta = timedelta(minutes=30),
    fingerprint: str | None = None,
) -> str:
    return jwt.encode(
        {
            "sub": str(user.id),
            "purpose": purpose,
            "exp": datetime.now(timezone.utc) + expires_in,
            "ph": fingerprint
            if fingerprint is not None
            else hashlib.sha256(user.hashed_password.encode()).hexdigest()[:16],
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def _patient_a(client) -> str:
    resp = login(client, "patient.a@example.com", "Patient!Pass123", "patient")
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ------------------------------------------------- Fix #11 registration
def test_register_rejects_blank_name(client):
    resp = client.post(
        "/api/auth/register",
        json={"name": "   ", "email": "blank.name@example.com", "password": "Passw0rd!x"},
    )
    assert resp.status_code == 422, resp.text


def test_register_rejects_common_and_weak_passwords(client):
    for password in ("12345678", "password1", "abcdefgh", "welcome1"):
        resp = client.post(
            "/api/auth/register",
            json={
                "name": "Weak Password",
                "email": f"weak.{password}@example.com",
                "password": password,
            },
        )
        assert resp.status_code == 422, (password, resp.text)


def test_register_accepts_a_strong_password(client):
    resp = client.post(
        "/api/auth/register",
        json={"name": "Strong Person", "email": "strong@example.com", "password": "Passw0rd!x"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "Strong Person"


# ------------------------------------------------- Fix #10 change password
def test_change_password_revokes_old_token_and_returns_a_new_one(client, seeded_ids):
    token = _patient_a(client)
    resp = client.post(
        "/api/auth/change-password",
        headers=auth_headers(token),
        json={"current_password": "Patient!Pass123", "new_password": "NewPass!1234"},
    )
    assert resp.status_code == 200, resp.text
    new_token = resp.json()["access_token"]

    old = client.get("/api/auth/me", headers=auth_headers(token))
    assert old.status_code == 401, old.text
    new = client.get("/api/auth/me", headers=auth_headers(new_token))
    assert new.status_code == 200, new.text


# ------------------------------------------------- Fix #12 email change
def test_email_change_requires_the_current_password(client, seeded_ids):
    token = _patient_a(client)

    missing = client.patch(
        "/api/auth/me",
        headers=auth_headers(token),
        json={"email": "moved@example.com"},
    )
    assert missing.status_code == 400, missing.text
    assert missing.json()["detail"] == "Enter your current password to change your email."

    wrong = client.patch(
        "/api/auth/me",
        headers=auth_headers(token),
        json={"email": "moved@example.com", "current_password": "not-it"},
    )
    assert wrong.status_code == 400

    ok = client.patch(
        "/api/auth/me",
        headers=auth_headers(token),
        json={"email": "moved@example.com", "current_password": "Patient!Pass123"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["email"] == "moved@example.com"


def test_name_only_change_needs_no_password(client, seeded_ids):
    token = _patient_a(client)
    resp = client.patch(
        "/api/auth/me", headers=auth_headers(token), json={"name": "Renamed Only"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed Only"


# ------------------------------------------------- Fix #9 sessions/refresh
def test_refresh_returns_a_working_token(client, seeded_ids):
    token = _patient_a(client)
    resp = client.post("/api/auth/refresh", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    refreshed = resp.json()["access_token"]
    assert refreshed != token
    assert client.get("/api/auth/me", headers=auth_headers(refreshed)).status_code == 200


def test_expired_token_cannot_be_refreshed(client, seeded_ids):
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {
            "sub": seeded_ids["user_a_id"],
            "role": "patient",
            "tv": 0,
            "rm": False,
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(minutes=1),
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )
    resp = client.post("/api/auth/refresh", headers=auth_headers(expired))
    assert resp.status_code == 401, resp.text


def test_remember_me_token_lives_seven_days(client, seeded_ids):
    resp = client.post(
        "/api/auth/login",
        json={
            "email": "patient.a@example.com",
            "password": "Patient!Pass123",
            "remember_me": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expires_in"] == 7 * 24 * 3600
    claims = jwt.decode(
        body["access_token"], settings.secret_key, algorithms=[settings.algorithm]
    )
    assert claims["rm"] is True
    assert claims["exp"] - claims["iat"] == 7 * 24 * 3600


def test_default_token_lives_thirty_minutes(client, seeded_ids):
    resp = login(client, "patient.a@example.com", "Patient!Pass123", "patient")
    assert resp.json()["expires_in"] == 30 * 60
    claims = jwt.decode(
        resp.json()["access_token"], settings.secret_key, algorithms=[settings.algorithm]
    )
    assert claims["rm"] is False


# ------------------------------------------------- Fix #8 forgot/reset
def test_forgot_password_is_always_neutral(client, seeded_ids):
    unknown = client.post(
        "/api/auth/forgot-password", json={"email": "nobody@example.com"}
    )
    known = client.post(
        "/api/auth/forgot-password", json={"email": "patient.a@example.com"}
    )
    assert unknown.status_code == known.status_code == 200
    assert unknown.json() == known.json()
    assert unknown.json()["message"] == (
        "If that email has an account, a reset link has been sent."
    )


def test_reset_token_is_single_use_and_revokes_sessions(
    client, seeded_ids, db_session
):
    token = _patient_a(client)
    user = db_session.query(User).filter(User.email == "patient.a@example.com").first()
    reset = _reset_token(user)

    resp = client.post(
        "/api/auth/reset-password",
        json={"token": reset, "new_password": "BrandNew!123"},
    )
    assert resp.status_code == 200, resp.text

    # The old session died with the password change.
    assert client.get("/api/auth/me", headers=auth_headers(token)).status_code == 401
    assert login(
        client, "patient.a@example.com", "BrandNew!123", "patient"
    ).status_code == 200

    # The same link cannot be used twice (fingerprint no longer matches).
    again = client.post(
        "/api/auth/reset-password",
        json={"token": reset, "new_password": "Another!1234"},
    )
    assert again.status_code == 400, again.text
    assert again.json()["detail"] == "This reset link is invalid or has expired."


def test_expired_reset_token_rejected(client, seeded_ids, db_session):
    user = db_session.query(User).filter(User.email == "patient.a@example.com").first()
    expired = _reset_token(user, expires_in=timedelta(minutes=-5))
    resp = client.post(
        "/api/auth/reset-password",
        json={"token": expired, "new_password": "BrandNew!123"},
    )
    assert resp.status_code == 400, resp.text


def test_reset_token_with_wrong_purpose_rejected(client, seeded_ids, db_session):
    user = db_session.query(User).filter(User.email == "patient.a@example.com").first()
    wrong = _reset_token(user, purpose="login")
    resp = client.post(
        "/api/auth/reset-password",
        json={"token": wrong, "new_password": "BrandNew!123"},
    )
    assert resp.status_code == 400, resp.text


def test_reset_password_applies_the_password_policy(client, seeded_ids, db_session):
    user = db_session.query(User).filter(User.email == "patient.a@example.com").first()
    reset = _reset_token(user)
    resp = client.post(
        "/api/auth/reset-password",
        json={"token": reset, "new_password": "12345678"},
    )
    assert resp.status_code == 422, resp.text
