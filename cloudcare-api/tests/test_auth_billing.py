"""Gate tests, batch 2: token expiry (10a), server-side logout (14a),
default billing at registration (18) and billing PATCH persistence (19).
"""
import jwt
from tests.conftest import auth_headers, login, register_patient

from app.config import get_settings
from app.models import User, UserRole
from app.security import hash_password

settings = get_settings()


def _decode(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])


# --------------------------------------------------------------- gate 10a
def test_login_token_expires_in_30_minutes(client, seeded_ids):
    register_patient(client, "expiry@example.com")
    resp = login(client, "expiry@example.com", "Patient!Pass123", "patient")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Documented session length: 30 minutes, advertised to the client.
    assert body["expires_in"] == 30 * 60
    assert body["token_type"] == "bearer"

    claims = _decode(body["access_token"])
    lifetime = claims["exp"] - claims["iat"]
    assert lifetime == 30 * 60

    # The session outlives several consecutive authenticated requests
    # (persistence within the token's lifetime).
    for _ in range(3):
        me = client.get("/api/auth/me", headers=auth_headers(body["access_token"]))
        assert me.status_code == 200, me.text
        assert me.json()["email"] == "expiry@example.com"


def test_legacy_token_without_tv_claim_is_rejected(client, seeded_ids, db_session):
    """A pre-`tv` token (issued before token_version existed) reads as version
    0, so once the user's stored version moves ahead of it, it is rejected.
    We prove that by logging out (bump) with a fresh token and then presenting
    the tv-less one."""
    register_patient(client, "legacy.tv@example.com")
    login(client, "legacy.tv@example.com", "Patient!Pass123", "patient")
    user = db_session.query(User).filter(User.email == "legacy.tv@example.com").first()
    assert user is not None and user.token_version == 0

    # Hand-craft a token with NO `tv` claim, signed with the real secret.
    stale = jwt.encode(
        {"sub": str(user.id), "role": "patient", "iat": 0, "exp": 4102444800},
        settings.secret_key,
        algorithm=settings.algorithm,
    )
    # While versions match (0 == 0) the tv-less token still authenticates.
    ok = client.get("/api/auth/me", headers=auth_headers(stale))
    assert ok.status_code == 200, ok.text

    # Logout bumps token_version to 1 -> the tv-less token (reads as 0) dies.
    fresh = login(client, "legacy.tv@example.com", "Patient!Pass123", "patient")
    assert fresh.status_code == 200
    client.post("/api/auth/logout", headers=auth_headers(fresh.json()["access_token"]))
    denied = client.get("/api/auth/me", headers=auth_headers(stale))
    assert denied.status_code == 401


# --------------------------------------------------------------- gate 14a
def test_logout_invalidates_token_server_side(client, seeded_ids):
    register_patient(client, "logout@example.com")
    resp = login(client, "logout@example.com", "Patient!Pass123", "patient")
    token = resp.json()["access_token"]

    me = client.get("/api/auth/me", headers=auth_headers(token))
    assert me.status_code == 200

    out = client.post("/api/auth/logout", headers=auth_headers(token))
    assert out.status_code == 200, out.text

    # Same token, still unexpired -> rejected because token_version moved on.
    denied = client.get("/api/auth/me", headers=auth_headers(token))
    assert denied.status_code == 401, denied.text
    assert "sign in" in denied.json()["detail"].lower()

    # A brand-new login issues a fresh token with the bumped version.
    again = login(client, "logout@example.com", "Patient!Pass123", "patient")
    assert again.status_code == 200
    new_token = again.json()["access_token"]
    assert new_token != token
    me2 = client.get("/api/auth/me", headers=auth_headers(new_token))
    assert me2.status_code == 200


def test_logout_requires_authentication(client):
    anon = client.post("/api/auth/logout")
    assert anon.status_code == 401


# --------------------------------------------------------------- gate 18
def test_registration_creates_default_billing_record(client, seeded_ids):
    register_patient(
        client,
        "billing.default@example.com",
        name="Bella Default",
    )
    login(client, "billing.default@example.com", "Patient!Pass123", "patient")

    # The register endpoint itself cannot be introspected directly (it returns
    # the user, not the billing row), so verify via the API + the DB row.
    resp = client.get("/api/billing/me")
    # not authenticated yet -> expected 401; do the real check with a token
    assert resp.status_code == 401

    token = login(
        client, "billing.default@example.com", "Patient!Pass123", "patient"
    ).json()["access_token"]
    detail = client.get("/api/billing/me", headers=auth_headers(token))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["full_name"] == "Bella Default"
    assert body["email"] == "billing.default@example.com"
    assert body["billing_address"] == ""
    assert body["payment_method"] == "Not set"
    assert body["currency"] == "INR"
    assert body["status"] == "Default"


def test_billing_backfill_for_preexisting_user(client, seeded_ids, db_session):
    """Accounts created before the billing feature get the default row on
    first open (lazy backfill) from their profile name/email."""
    legacy = User(
        name="Legacy User",
        email="legacy.billing@example.com",
        hashed_password=hash_password("Patient!Pass123"),
        role=UserRole.patient,
    )
    db_session.add(legacy)
    db_session.commit()

    token = login(
        client, "legacy.billing@example.com", "Patient!Pass123", "patient"
    ).json()["access_token"]
    detail = client.get("/api/billing/me", headers=auth_headers(token))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["full_name"] == "Legacy User"
    assert body["email"] == "legacy.billing@example.com"
    assert body["payment_method"] == "Not set"
    assert body["currency"] == "INR"
    assert body["status"] == "Default"

    # Second open returns the same row (no duplicates).
    again = client.get("/api/billing/me", headers=auth_headers(token))
    assert again.json() == body


# --------------------------------------------------------------- gate 19
def test_billing_patch_persists_partial_update(client, seeded_ids):
    register_patient(client, "billing.patch@example.com", name="Patch Me")
    token = login(
        client, "billing.patch@example.com", "Patient!Pass123", "patient"
    ).json()["access_token"]
    headers = auth_headers(token)

    patched = client.patch(
        "/api/billing/me",
        headers=headers,
        json={
            "full_name": "Patched Name",
            "billing_address": "12 MG Road, Bengaluru 560001",
            "payment_method": "UPI",
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["full_name"] == "Patched Name"
    assert body["billing_address"] == "12 MG Road, Bengaluru 560001"
    assert body["payment_method"] == "UPI"
    # Untouched fields keep their defaults.
    assert body["email"] == "billing.patch@example.com"
    assert body["currency"] == "INR"
    assert body["status"] == "Default"

    # Persists across a fresh read.
    reread = client.get("/api/billing/me", headers=headers)
    assert reread.json()["full_name"] == "Patched Name"
    assert reread.json()["payment_method"] == "UPI"


def test_billing_patch_rejects_empty_and_invalid(client, seeded_ids):
    register_patient(client, "billing.valid@example.com")
    token = login(
        client, "billing.valid@example.com", "Patient!Pass123", "patient"
    ).json()["access_token"]
    headers = auth_headers(token)

    empty = client.patch("/api/billing/me", headers=headers, json={})
    assert empty.status_code == 400

    # currency shorter than 3 chars fails schema validation.
    bad = client.patch("/api/billing/me", headers=headers, json={"currency": "I"})
    assert bad.status_code == 422

    # Billing endpoints require authentication.
    assert client.get("/api/billing/me").status_code == 401
    assert client.patch("/api/billing/me", json={"currency": "INR"}).status_code == 401
