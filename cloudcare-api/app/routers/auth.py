"""Authentication routes: register, login, me, change/forgot password."""
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.config import get_settings
from app.database import get_db
from app.deps import create_access_token, get_current_user
from app.models import BillingDetail, Patient, User, UserRole
from app.ratelimit import limiter
from app.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    ProfileUpdate,
    RegisterRequest,
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
    db.flush()  # assign user.id before creating the profile

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
    db.commit()
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

    token = create_access_token(
        subject=str(user.id),
        role=user.role.value,
        token_version=user.token_version,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
        user=serialize_user(user),
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
        existing = db.query(User).filter(User.email == new_email.lower()).first()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists.",
            )
        current_user.email = new_email.lower()

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
    record_audit(
        db,
        action="change_password",
        resource=f"user:{current_user.id}",
        actor=current_user,
        ip_address=client_ip(request),
    )
    db.commit()
    return {"message": "Password updated successfully."}


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Placeholder — no mail transport is configured, so no email is sent.

    Always returns 200 so the endpoint cannot be used to enumerate accounts.
    The frontend labels this clearly as unavailable.
    """
    return {
        "message": "Password reset by email is not available yet. "
        "Please contact an administrator to reset your password."
    }
