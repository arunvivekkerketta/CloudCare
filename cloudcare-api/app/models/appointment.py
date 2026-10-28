import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL only after the doctor's account is removed: the completed visit
    # keeps doctor_name below so the patient's history still reads correctly.
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Snapshot of the doctor's name for preserved completed visits.
    doctor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(nullable=False, default=30, server_default="30")
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="In-person"
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # A new booking is always "Requested"; the doctor (or an admin) then
    # Confirms or Declines it. Declined/Cancelled free the slot.
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="Requested")
    # Why the status is what it is (e.g. "Doctor on leave", expiry reason).
    status_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Real video link for Telemedicine appointments (doctor/admin only).
    meeting_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    patient: Mapped["Patient"] = relationship(
        "Patient",
        back_populates="appointments",
    )
    doctor: Mapped["User | None"] = relationship(
        "User",
        back_populates="appointments_as_doctor",
        foreign_keys=[doctor_id],
    )

    # Atomic double-booking guard: a doctor can hold at most ONE active
    # (Requested/Confirmed) appointment per start time. Declined/Cancelled
    # rows are excluded by the partial index, so they free the slot.
    __table_args__ = (
        Index(
            "uq_appointments_active_slot",
            "doctor_id",
            "scheduled_at",
            unique=True,
            postgresql_where=text("status IN ('Requested', 'Confirmed')"),
            sqlite_where=text("status IN ('Requested', 'Confirmed')"),
        ),
    )
