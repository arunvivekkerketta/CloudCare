"""Shared audit-log writer."""
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def record_audit(
    db: Session,
    *,
    action: str,
    resource: str,
    actor: User | None = None,
    detail: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Write an audit_logs row. Committed lazily by the caller's transaction;
    if the caller rolls back the audit row is lost, which is acceptable for
    login/registration events that own their own transaction."""
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        action=action[:100],
        # The column is VARCHAR(255); a long resource must be truncated here,
        # not raise a database error after the audited action succeeded.
        resource=(resource or "")[:255],
        detail=detail[:500] if detail else None,
        ip_address=ip_address[:64] if ip_address else None,
    )
    db.add(entry)
