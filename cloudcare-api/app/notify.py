"""In-app notifications.

`notify` only queues a row on the caller's transaction — the caller commits,
so a notification is never stored for an action that failed.
"""
from sqlalchemy.orm import Session

from app.models import Notification


def notify(db: Session, user_id, message: str) -> Notification:
    row = Notification(user_id=user_id, message=message)
    db.add(row)
    return row
