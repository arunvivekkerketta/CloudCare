"""Shared router helpers."""
import ipaddress
import re
import uuid

from app.config import get_settings
from app.models import Patient, User


def serialize_user(user: User) -> dict:
    from app.schemas import UserResponse

    return UserResponse(
        id=str(user.id),
        name=user.name,
        email=user.email,
        role=user.role.value,
    ).model_dump()


def _parse_trusted_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    raw = get_settings().trusted_proxy_ips
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted_proxy(peer: str) -> bool:
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in _parse_trusted_networks())


def client_ip(request) -> str | None:
    """The real client address, resistant to X-Forwarded-For spoofing.

    The header is honoured only when the immediate peer is a configured
    trusted proxy, and then only its RIGHTMOST entry is used: that is the hop
    appended by our own proxy. Entries to the left came from the caller and
    may be fabricated. Direct clients keep their socket address.
    """
    peer = request.client.host if request.client else None
    if not peer:
        return None
    if not _is_trusted_proxy(peer):
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if not hops:
        return peer
    try:
        return str(ipaddress.ip_address(hops[-1]))
    except ValueError:
        # A malformed rightmost hop is not a usable address; fall back to the
        # proxy peer rather than trusting attacker-supplied text.
        return peer


def parse_uuid(value: str) -> uuid.UUID:
    """Parse a UUID path/query param, raising 404-style ValueError on garbage."""
    return uuid.UUID(value)


def patient_for_user(db, user: User) -> Patient | None:
    if user.role != "patient":
        return None
    return db.query(Patient).filter(Patient.user_id == user.id).first()


def doctor_label(name: str | None) -> str:
    """Return "Dr. Name" exactly once.

    Stored names already carry the title ("Dr. Aakash Verma"), so they are
    returned unchanged. Only an unadorned name gets the prefix; a name like
    "Dravid" is not mistaken for a title.
    """
    cleaned = (name or "").strip() or "your doctor"
    if re.match(r"^dr\.?\s", cleaned, flags=re.IGNORECASE) or cleaned.lower() in {
        "dr",
        "dr.",
    }:
        return cleaned
    return f"Dr. {cleaned}"


def doctor_display_name(user: User | None) -> str | None:
    if not user:
        return None
    return doctor_label(user.name)
