import uuid
from datetime import time

from sqlalchemy import Boolean, ForeignKey, String, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class DoctorShift(Base):
    """A doctor's working shift (e.g. Morning 06:00-14:00).

    Defaults are seeded as Morning 06:00-14:00 and Evening 14:00-22:00;
    admins can edit hours, deactivate a shift, or delete rows entirely
    (deleted rows fall back to the clinic-wide defaults).
    """

    __tablename__ = "doctor_shifts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(20), nullable=False)  # "Morning" | "Evening"
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    doctor: Mapped["User"] = relationship("User", back_populates="shifts")

    __table_args__ = (
        UniqueConstraint("doctor_id", "name", name="uq_doctor_shifts_doctor_name"),
    )
