"""Shared slowapi limiter and the client-IP key used by every rate limit.

Render's edge proxy appends the real client address to the RIGHT of
X-Forwarded-For. Values to its left are supplied by the caller and can be
spoofed. We therefore read the rightmost entry and only do so when the
immediate peer is one of TRUSTED_PROXY_IPS. A direct client can never widen
its own limit by inventing a fresh header value.
"""
import hashlib

from fastapi import HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings
from app.utils import client_ip

settings = get_settings()

limiter = Limiter(
    key_func=lambda request: client_ip(request) or get_remote_address(request),
    default_limits=[],
    storage_uri=settings.rate_limit_storage_uri,
)

FORGOT_PASSWORD_EMAIL_LIMIT = 5
FORGOT_PASSWORD_EMAIL_WINDOW_SECONDS = 60 * 60


def email_rate_limit_key(email: str) -> str:
    """Stable, non-reversible counter key for one email address."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return f"forgot-password:{digest}"


def enforce_forgot_password_email_limit(email: str) -> None:
    """Allow at most 5 reset emails per address per hour.

    Uses the same storage backend as the other limits, so a configured shared
    store (for example Redis) applies this across every worker. The counter is
    incremented even for unknown addresses, which keeps the response
    indistinguishable for registered and unregistered emails.
    """
    key = email_rate_limit_key(email)
    count = limiter.limiter.storage.incr(
        key, FORGOT_PASSWORD_EMAIL_WINDOW_SECONDS
    )
    if count > FORGOT_PASSWORD_EMAIL_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many reset requests for this email. "
                "Please wait an hour and try again."
            ),
        )
