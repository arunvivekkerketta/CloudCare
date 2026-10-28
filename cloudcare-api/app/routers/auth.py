"""Authentication routes: register, login, refresh, me, password reset."""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.config import get_settings
from app.database import get_db
from app.deps import (
    create_access_token,
    decode_access_token,
    get_current_user,
    oauth2_scheme,
)
from app.mailer import send_email
from app.models import BillingDetail, Patient, User, UserRole
from app.ratelimit import enforce_forgot_password_email_limit, limiter
from app.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    ProfileUpdate,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserResponse,
)
from app.security import hash_password, needs_rehash, verify_password
from app.utils import client_ip, serialize_user

settings = get_settings()

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Limits come from settings; the test suite sets them empty to disable them.
# Applied as decorator factories BELOW @router.post so slowapi still wraps the
# same function object that gets registered on the router.
_login_limit = settings.rate_limit_login
_register_limit = settings.rate_limit_register


def _maybe_limit(limit: str):
    """Return a slowapi limit decorator, or a no-op when disabled."""
    if not limit or not limit.strip():
        return lambda func: func
    return limiter.limit(limit)

# One generic message for every login failure — never reveal which part was wrong.
GENERIC_LOGIN_ERROR = "Invalid email or password."

# Constant dummy Argon2id hash, generated once at import. When the email does
# not match any account, verify_password still runs against this hash so the
# code path takes the same time as "wrong password for a real account" —
# closing the low-signal email-enumeration timing side channel.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))


def _client_ip(request: Request) -> str | None:
    return client_ip(request)

@router.post("/register", response_model=UserResponse, status_code=201)
@_maybe_limit(_register_limit)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Public self-registration. ALWAYS creates a role=patient account with a
    linked Patient row — any attempt to escalate privileges is ignored."""
    email = payload.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    user = User(
        name=payload.name.strip(),
        email=email,
        hashed_password=hash_password(payload.password),
        role=UserRole.patient,  # authoritative — never client-controlled
    )
    db.add(user)
    try:
        db.flush()  # assign user.id before creating the profile
    except IntegrityError:
        # Two registrations can pass the check above before either commits;
        # the unique email constraint is the real authority.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        ) from None

    patient = Patient(user_id=user.id, status="Active")
    db.add(patient)

    # Default billing details, same transaction (gate 18): name/email from
    # registration, empty address, "Not set" payment method, INR, "Default".
    db.add(
        BillingDetail(
            user_id=user.id,
            full_name=payload.name.strip(),
            email=email,
            billing_address="",
            payment_method="Not set",
            currency="INR",
            status="Default",
        )
    )

    record_audit(
        db,
        action="register",
        resource=f"user:{user.id}",
        actor=user,
        detail=f"Self-registration as patient ({email})",
        ip_address=client_ip(request),
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        ) from None
    db.refresh(user)
    return serialize_user(user)


@router.post("/login", response_model=TokenResponse)
@_maybe_limit(_login_limit)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    email = payload.email.lower()
    user = db.query(User).filter(User.email == email).first()

    password_ok = False
    if user:
        password_ok = verify_password(payload.password, user.hashed_password)
    else:
        # Unknown email: burn the same Argon2id verification cost anyway so
        # response timing does not reveal whether the account exists.
        verify_password(payload.password, _DUMMY_PASSWORD_HASH)

    if not user or not password_ok:
        # payload.role is informational only — a wrong picker is NOT a login
        # failure. But when credentials are bad AND a mismatched role was
        # sent, keep the sharper diagnostic in the audit trail.
        mismatch_hint = (
            user is not None
            and password_ok
            and payload.role
            and user.role.value != payload.role
        )
        record_audit(
            db,
            action="login_failure",
            resource=f"email:{email}",
            actor=user if password_ok else None,
            detail="role_mismatch" if mismatch_hint else "bad_credentials",
            ip_address=client_ip(request),
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_LOGIN_ERROR,
        )

    # Successful login: transparently upgrade legacy pbkdf2 hashes to Argon2id.
    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(payload.password)

    record_audit(
        db,
        action="login_success",
        resource=f"user:{user.id}",
        actor=user,
        ip_address=client_ip(request),
    )
    db.commit()

    remember = bool(payload.remember_me)
    expire_minutes = (
        settings.remember_me_expire_minutes
        if remember
        else settings.access_token_expire_minutes
    )
    token = create_access_token(
        subject=str(user.id),
        role=user.role.value,
        token_version=user.token_version,
        expire_minutes=expire_minutes,
        remember=remember,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expire_minutes * 60,
        user=serialize_user(user),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    request: Request,
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Issue a fresh token for the signed-in user, same kind (30m or 7 days).

    An already-expired token never reaches this point: get_current_user
    rejects it with 401 before the handler runs.
    """
    claims = decode_access_token(token)
    remember = bool(claims.get("rm", False))
    expire_minutes = (
        settings.remember_me_expire_minutes
        if remember
        else settings.access_token_expire_minutes
    )
    new_token = create_access_token(
        subject=str(current_user.id),
        role=current_user.role.value,
        token_version=current_user.token_version,
        expire_minutes=expire_minutes,
        remember=remember,
    )
    record_audit(
        db,
        action="token_refresh",
        resource=f"user:{current_user.id}",
        actor=current_user,
        ip_address=client_ip(request),
    )
    db.commit()
    return TokenResponse(
        access_token=new_token,
        token_type="bearer",
        expires_in=expire_minutes * 60,
        user=serialize_user(current_user),
    )


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return serialize_user(current_user)


@router.post("/logout")
def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Server-side logout: bump token_version so every token issued before
    now is rejected (gate 14a). The client should also drop its stored token.
    """
    current_user.token_version += 1
    record_audit(
        db,
        action="logout",
        resource=f"user:{current_user.id}",
        actor=current_user,
        ip_address=client_ip(request),
    )
    db.commit()
    return {"message": "Logged out successfully."}


@router.patch("/me", response_model=UserResponse)
def update_me(
    payload: ProfileUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Self-service profile update (name / email) for every role.

    Password changes stay on /change-password; patient-only clinical fields
    (age, condition, ...) stay on /api/patients/me.
    """
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="Nothing to update.")

    new_email = data.get("email")
    if new_email and new_email.lower() != current_user.email:
        # Changing the sign-in address needs the current password, so a
        # stolen session cannot quietly move the account to another inbox.
        if not verify_password(
            data.get("current_password") or "", current_user.hashed_password
        ):
            raise HTTPException(
                status_code=400,
                detail="Enter your current password to change your email.",
            )
        existing = db.query(User).filter(User.email == new_email.lower()).first()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists.",
            )
        current_user.email = new_email.lower()
    data.pop("current_password", None)

    new_name = data.get("name")
    if new_name and new_name.strip():
        current_user.name = new_name.strip()

    record_audit(
        db,
        action="profile_update_self",
        resource=f"user:{current_user.id}",
        actor=current_user,
        detail=", ".join(data.keys()),
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(current_user)
    return serialize_user(current_user)


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        record_audit(
            db,
            action="change_password_failure",
            resource=f"user:{current_user.id}",
            actor=current_user,
            detail="wrong current password",
            ip_address=client_ip(request),
        )
        db.commit()
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    current_user.hashed_password = hash_password(payload.new_password)
    # Revoke every session issued before this moment, then hand this browser
    # a fresh token so it stays signed in while all other devices are out.
    current_user.token_version += 1
    record_audit(
        db,
        action="change_password",
        resource=f"user:{current_user.id}",
        actor=current_user,
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(current_user)
    token = create_access_token(
        subject=str(current_user.id),
        role=current_user.role.value,
        token_version=current_user.token_version,
    )
    return {
        "message": "Password updated successfully.",
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
    }


NEUTRAL_RESET_MESSAGE = (
    "If that email has an account, a reset link has been sent."
)
RESET_FAILED_MESSAGE = "This reset link is invalid or has expired."


def _reset_fingerprint(hashed_password: str) -> str:
    """Ties a reset link to the current password hash, so it works only once."""
    return hashlib.sha256(hashed_password.encode()).hexdigest()[:16]


@router.post("/forgot-password")
@_maybe_limit("5/hour")
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Email a 30-minute, single-use reset link.

    The response is identical whether or not the account exists, so this
    endpoint cannot be used to enumerate registered emails.
    """
    email = payload.email.lower()
    # Per-address cap first: rotating source IPs must not multiply reset
    # emails for one inbox. Applied before the lookup so unknown addresses
    # behave identically.
    enforce_forgot_password_email_limit(email)
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if user is None:
        return {"message": NEUTRAL_RESET_MESSAGE}

    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "purpose": "reset",
            "exp": now + timedelta(minutes=30),
            "ph": _reset_fingerprint(user.hashed_password),
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )
    link = f"{settings.frontend_url.rstrip('/')}/reset-password.html?token={token}"
    background_tasks.add_task(
        send_email,
        user.email,
        "Reset your CloudCare password",
        (
            "We received a request to reset your CloudCare password.\n\n"
            f"Open this link within 30 minutes to choose a new one:\n{link}\n\n"
            "If you did not ask for this, you can ignore this email."
        ),
    )
    record_audit(
        db,
        action="password_reset_requested",
        resource=f"user:{user.id}",
        actor=user,
        ip_address=client_ip(request),
    )
    db.commit()
    return {"message": NEUTRAL_RESET_MESSAGE}


@router.post("/reset-password")
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Consume one reset token: single use, 30 minutes, current hash only."""
    failed = HTTPException(status_code=400, detail=RESET_FAILED_MESSAGE)
    try:
        claims = jwt.decode(
            payload.token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
    except jwt.InvalidTokenError:
        raise failed from None

    if claims.get("purpose") != "reset":
        raise failed
    try:
        user_id = uuid.UUID(str(claims.get("sub")))
    except (ValueError, TypeError):
        raise failed from None

    user = db.get(User, user_id)
    if user is None or claims.get("ph") != _reset_fingerprint(user.hashed_password):
        raise failed

    user.hashed_password = hash_password(payload.new_password)
    # Every existing session dies with the old password.
    user.token_version += 1
    record_audit(
        db,
        action="password_reset_done",
        resource=f"user:{user.id}",
        actor=user,
        ip_address=client_ip(request),
    )
    db.commit()
    return {"message": "Password updated. Sign in with your new password."}
