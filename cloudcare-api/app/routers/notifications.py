"""In-app notifications: every user reads and marks ONLY their own rows."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Notification, User

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: str
    message: str
    created_at: datetime
    read: bool


class NotificationReadRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)
    all: bool = False


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    response: Response,
    unread_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Newest first plus the unread count in a response header."""
    base = db.query(Notification).filter(Notification.user_id == current_user.id)
    unread = base.filter(Notification.read_at.is_(None)).count()
    query = base.filter(Notification.read_at.is_(None)) if unread_only else base
    rows = query.order_by(Notification.created_at.desc()).limit(limit).all()
    response.headers["X-Unread-Count"] = str(unread)
    return [
        NotificationOut(
            id=str(row.id),
            message=row.message,
            created_at=row.created_at,
            read=row.read_at is not None,
        ).model_dump(mode="json")
        for row in rows
    ]


@router.post("/read")
def mark_read(
    payload: NotificationReadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark the given ids (or everything) as read — only the caller's rows."""
    query = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.read_at.is_(None),
    )
    if not payload.all:
        ids = []
        for raw in payload.ids:
            try:
                ids.append(uuid.UUID(str(raw)))
            except (ValueError, TypeError):
                continue
        if not ids:
            return {"updated": 0}
        query = query.filter(Notification.id.in_(ids))

    updated = query.count()
    query.update(
        {Notification.read_at: datetime.now(timezone.utc)},
        synchronize_session=False,
    )
    db.commit()
    return {"updated": updated}
