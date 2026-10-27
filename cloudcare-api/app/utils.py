"""Shared router helpers."""
import uuid

from app.models import Patient, User


def serialize_user(user: User) -> dict:
    from app.schemas import UserResponse

    return UserResponse(
        id=str(user.id),
        name=user.name,
        email=user.email,
        role=user.role.value,
    ).model_dump()


def client_ip(request) -> str | None:
    """The real client address.

    uvicorn's --proxy-headers flag (set in render.yaml / start.bat) rewrites
    request.client from the trusted proxy chain, so this is already correct
    behind Render. A raw X-Forwarded-For header sent by a client must never
    be trusted — it would let anyone spoof another user's IP and dodge the
    per-address rate limits.
    """
    if request.client:
        return request.client.host
    return None


def parse_uuid(value: str) -> uuid.UUID:
    """Parse a UUID path/query param, raising 404-style ValueError on garbage."""
    return uuid.UUID(value)


def patient_for_user(db, user: User) -> Patient | None:
    if user.role != "patient":
        return None
    return db.query(Patient).filter(Patient.user_id == user.id).first()


def doctor_display_name(user: User | None) -> str | None:
    if not user:
        return None
    return user.name
