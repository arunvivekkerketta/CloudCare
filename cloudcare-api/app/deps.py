import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User, UserRole

settings = get_settings()

# tokenUrl is documentation-only; login takes JSON, not form data.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


def create_access_token(
    subject: str,
    role: str,
    token_version: int = 0,
    expire_minutes: int | None = None,
    remember: bool = False,
) -> str:
    """Create a signed JWT access token for a user id + role.

    The `tv` claim mirrors users.token_version at issue time. Logging out
    bumps the stored version, which instantly invalidates every token with
    an older `tv` — server-side logout, not just client-side token deletion.
    `rm` records whether this is a long-lived "keep me signed in" session,
    so refresh can issue the same kind of token.
    """
    now = datetime.now(timezone.utc)
    minutes = (
        expire_minutes
        if expire_minutes is not None
        else settings.access_token_expire_minutes
    )
    payload = {
        "sub": subject,
        "role": role,
        "tv": token_version,
        "rm": remember,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
        # Unique per issue, so a refresh never returns the identical string
        # as the token it replaces even within the same second.
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.InvalidTokenError:
        raise CREDENTIALS_EXCEPTION from None
    # Special-purpose tokens (e.g. password reset) are never login tokens.
    if payload.get("purpose"):
        raise CREDENTIALS_EXCEPTION
    return payload


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    payload = decode_access_token(token)
    subject = payload.get("sub")
    if not subject:
        raise CREDENTIALS_EXCEPTION
    try:
        user_id = uuid.UUID(subject)
    except (ValueError, TypeError):
        raise CREDENTIALS_EXCEPTION from None

    user = db.get(User, user_id)
    if user is None:
        raise CREDENTIALS_EXCEPTION

    # Server-side logout / revocation: any token issued before the last
    # token_version bump (logout, password change, admin force-logout) is
    # rejected even though its signature and expiry are still valid.
    # Tokens with no `tv` claim (issued before this feature) read as 0.
    try:
        token_tv = int(payload.get("tv", 0))
    except (TypeError, ValueError):
        raise CREDENTIALS_EXCEPTION from None
    if token_tv != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def require_roles(*roles: UserRole):
    """Dependency factory: allow only the listed roles (403 otherwise)."""

    def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource.",
            )
        return current_user

    return checker
