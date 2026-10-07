import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    condition: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assigned_doctor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="Active")

    user: Mapped["User"] = relationship(
        "User",
        back_populates="patient_profile",
        foreign_keys=[user_id],
    )
    assigned_doctor: Mapped["User | None"] = relationship(
        "User",
        back_populates="assigned_patients",
        foreign_keys=[assigned_doctor_id],
    )

    # Named to match migration 001 so `alembic check` sees no drift.
    __table_args__ = (UniqueConstraint("user_id", name="uq_patients_user_id"),)
    appointments: Mapped[list["Appointment"]] = relationship(
        "Appointment",
        back_populates="patient",
    )
    bills: Mapped[list["Bill"]] = relationship(
        "Bill",
        back_populates="patient",
    )
