"""Password hashing: Argon2id for new hashes, pbkdf2 for legacy verification.

Existing pbkdf2_sha256 hashes keep working; they are rehashed with Argon2id
transparently on the next successful login (see app/routers/auth.py).
"""
import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

# Cost parameters — Argon2id defaults are fine; tuned slightly for a login API.
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

PBKDF2_PREFIX = "pbkdf2_sha256$"


def hash_password(password: str) -> str:
    """Hash a password with Argon2id (PHC string format)."""
    return _ph.hash(password)


def is_argon2_hash(encoded: str) -> bool:
    return encoded.startswith("$argon2")


def needs_rehash(encoded: str) -> bool:
    """True when a stored hash should be upgraded (legacy pbkdf2, or weak params)."""
    if not encoded.startswith("$argon2"):
        return True
    try:
        return _ph.check_needs_rehash(encoded)
    except (InvalidHashError, ValueError):
        return True


def verify_password(password: str, encoded: str) -> bool:
    """Verify against Argon2id or the legacy pbkdf2_sha256 format."""
    if encoded.startswith(PBKDF2_PREFIX):
        return _verify_pbkdf2(password, encoded)
    try:
        return _ph.verify(encoded, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False
    except Exception:
        # Malformed/unexpected hash — treat as verification failure.
        return False


def _verify_pbkdf2(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_value, digest_value = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_value)
        expected = base64.b64decode(digest_value)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False
