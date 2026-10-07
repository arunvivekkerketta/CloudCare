import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class DoctorLeave(Base):
    """A doctor's leave: a whole day (shift_name NULL) or a single named shift."""

    __tablename__ = "doctor_leaves"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    leave_date: Mapped[date] = mapped_column(Date, nullable=False)
    # NULL = whole-day leave; otherwise "Morning" or "Evening".
    shift_name: Mapped[str | None] = mapped_column(String(20), nullable=True)

    doctor: Mapped["User"] = relationship("User", back_populates="leaves")
