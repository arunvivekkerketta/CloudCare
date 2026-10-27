import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, Integer, Numeric, String, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class UserRole(str, enum.Enum):
    patient = "patient"
    doctor = "doctor"
    nurse = "nurse"
    pharmacist = "pharmacist"
    lab = "lab"
    admin = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=True),
        nullable=False,
    )
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Bumped on logout: every JWT issued before the bump is rejected, so
    # "Log out" really ends the session on the server, not just the client.
    token_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    # Consultation fee charged when a doctor completes an appointment.
    consultation_fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=Decimal("500.00"),
        server_default="500",
    )

    patient_profile: Mapped["Patient | None"] = relationship(
        "Patient",
        back_populates="user",
        foreign_keys="Patient.user_id",
        uselist=False,
    )
    assigned_patients: Mapped[list["Patient"]] = relationship(
        "Patient",
        back_populates="assigned_doctor",
        foreign_keys="Patient.assigned_doctor_id",
    )
    appointments_as_doctor: Mapped[list["Appointment"]] = relationship(
        "Appointment",
        back_populates="doctor",
        foreign_keys="Appointment.doctor_id",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="actor",
    )
    shifts: Mapped[list["DoctorShift"]] = relationship(
        "DoctorShift",
        back_populates="doctor",
    )
    leaves: Mapped[list["DoctorLeave"]] = relationship(
        "DoctorLeave",
        back_populates="doctor",
    )
    reviews_written: Mapped[list["Review"]] = relationship(
        "Review",
        back_populates="author",
        foreign_keys="Review.author_id",
    )
    reviews_received: Mapped[list["Review"]] = relationship(
        "Review",
        back_populates="doctor",
        foreign_keys="Review.doctor_id",
    )
    billing_detail: Mapped["BillingDetail | None"] = relationship(
        "BillingDetail",
        back_populates="user",
        uselist=False,
    )

    # Named to match migration 001 so `alembic check` sees no drift.
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)
