import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

Role = Literal["patient", "doctor", "nurse", "pharmacist", "lab", "admin"]
# StaffRole remains the list for role-pickers; AdminUserCreate accepts every
# role (admins may create patient accounts too).
StaffRole = Literal["doctor", "nurse", "pharmacist", "lab", "admin"]

# The four appointment statuses from the spec. A new booking is always
# "Requested"; doctors/admins Confirm or Decline; anyone involved can Cancel.
AppointmentStatus = Literal["Requested", "Confirmed", "Declined", "Cancelled"]


# --------------------------------------------------------------------- auth
class RegisterRequest(BaseModel):
    """Public registration — ALWAYS creates a patient account."""

    name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    # Informational only: authentication uses email + password. The server's
    # role is authoritative and returned in the response; clients use it to
    # redirect (a wrong picker no longer blocks sign-in).
    role: Role | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserResponse"


class UserResponse(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: Role


class ProfileUpdate(BaseModel):
    """Self-service profile fields (PATCH /api/auth/me) — every role."""

    name: str | None = Field(default=None, min_length=2, max_length=255)
    email: EmailStr | None = None


class AdminUserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Role
    department: str | None = Field(default=None, max_length=100)


class AdminUserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    department: str | None = Field(default=None, max_length=100)
    password: str | None = None
    # Admin can change roles (including promoting staff), but never their own
    # (guaranteed server-side in the router, not merely by omitting the field).
    role: Annotated[Role | None, Field(default=None)] = None

    @field_validator("password")
    @classmethod
    def _strong_password(cls, value: str | None) -> str | None:
        if value is None:
            return None
        import re  # noqa:PLC0415 — only needed when a password is supplied

        if len(value) < 8 or len(value) > 128:
            raise ValueError("String should have at least 8 characters")
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("Password must contain letters and numbers.")
        return value


class AdminUserResponse(UserResponse):
    department: str | None = None
    created_at: datetime | None = None


class AuditLogResponse(BaseModel):
    id: str
    actor_id: str | None
    actor_name: str | None
    actor_email: str | None
    action: str
    resource: str
    timestamp: datetime


class AuditLogPage(BaseModel):
    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int
    pages: int


# ------------------------------------------------------------------ patients
class PatientProfile(BaseModel):
    id: str
    user_id: str
    name: str
    email: EmailStr
    age: int | None
    condition: str | None
    risk_score: int | None
    status: str
    assigned_doctor_id: str | None = None
    assigned_doctor_name: str | None = None


class PatientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    age: int | None = Field(default=None, ge=0, le=130)
    condition: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, max_length=50)
    assigned_doctor_id: str | None = None


class PatientPage(BaseModel):
    items: list[PatientProfile]
    total: int
    page: int
    page_size: int
    pages: int


# -------------------------------------------------------------- appointments
class AppointmentCreate(BaseModel):
    doctor_id: str
    scheduled_at: datetime
    department: str = Field(min_length=2, max_length=100)
    mode: Literal["In-person", "Telemedicine"] = "In-person"
    reason: str | None = Field(default=None, max_length=500)


class AppointmentUpdate(BaseModel):
    scheduled_at: datetime | None = None
    department: str | None = Field(default=None, min_length=2, max_length=100)
    mode: Literal["In-person", "Telemedicine"] | None = None
    reason: str | None = Field(default=None, max_length=500)
    status: AppointmentStatus | None = None


class AppointmentResponse(BaseModel):
    id: str
    patient_id: str
    patient_name: str
    doctor_id: str
    doctor_name: str
    department: str
    scheduled_at: datetime
    duration_minutes: int = 30
    mode: str
    status: str
    reason: str | None = None


class AppointmentPage(BaseModel):
    items: list[AppointmentResponse]
    total: int
    page: int
    page_size: int
    pages: int


# ------------------------------------------------------------------ doctors
class DoctorShiftOut(BaseModel):
    name: str
    start: str  # "HH:MM" clinic time
    end: str


class DoctorPublic(BaseModel):
    id: str
    name: str
    department: str
    shifts: list[DoctorShiftOut]
    average_rating: float | None = None
    review_count: int = 0


class SlotInfo(BaseModel):
    start: str  # ISO-8601 UTC with offset
    label: str  # "HH:MM" clinic time
    end_label: str
    shift: str


# ------------------------------------------------------------------ reviews
class ReviewCreate(BaseModel):
    doctor_id: str
    rating: int = Field(ge=1, le=5)
    text: str = Field(min_length=10, max_length=500)

    @field_validator("text")
    @classmethod
    def _meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Review text must not be empty.")
        return value


class ReviewResponse(BaseModel):
    id: str
    author_id: str
    author_name: str
    doctor_id: str
    doctor_name: str
    rating: int
    text: str
    created_at: datetime
    updated_at: datetime


class ReviewPage(BaseModel):
    items: list[ReviewResponse]
    total: int
    page: int
    page_size: int
    pages: int


class ReviewSummary(BaseModel):
    doctor_id: str | None = None
    average_rating: float | None = None
    review_count: int


# --------------------------------------------------------------------- bills
class BillResponse(BaseModel):
    id: str
    invoice_no: str
    patient_id: str
    patient_name: str
    description: str | None = None
    amount: Decimal
    status: Literal["Pending", "Paid", "Overdue"]
    created_at: datetime


class BillPage(BaseModel):
    items: list[BillResponse]
    total: int
    page: int
    page_size: int
    pages: int


# ------------------------------------------------------- billing details
class BillingDetailResponse(BaseModel):
    full_name: str
    email: EmailStr
    billing_address: str
    payment_method: str
    currency: str
    status: str
    updated_at: datetime | None = None


class BillingDetailUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    billing_address: str | None = Field(default=None, max_length=500)
    payment_method: str | None = Field(default=None, min_length=2, max_length=50)
    currency: str | None = Field(default=None, min_length=3, max_length=8)


# --------------------------------------------------------------- specialties
class SpecialtyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)


class SpecialtyResponse(BaseModel):
    id: str
    name: str


# ----------------------------------------------------- admin: shifts/leaves
class ShiftRule(BaseModel):
    """One shift in an admin replace-all payload."""

    name: Literal["Morning", "Evening"]
    start: str = Field(min_length=4, max_length=5, pattern=r"^\d{2}:\d{2}$")
    end: str = Field(min_length=4, max_length=5, pattern=r"^\d{2}:\d{2}$")


class ShiftsSync(BaseModel):
    shifts: list[ShiftRule] = Field(min_length=2, max_length=8)


class AdminShiftOut(BaseModel):
    id: str | None = None
    name: str
    start: str  # "HH:MM" clinic time
    end: str


class AdminLeaveCreate(BaseModel):
    leave_date: datetime | date
    shift_name: Literal["Morning", "Evening"] | None = None


class AdminLeaveOut(BaseModel):
    id: str
    doctor_id: str
    leave_date: date
    shift_name: str | None = None


TokenResponse.model_rebuild()
