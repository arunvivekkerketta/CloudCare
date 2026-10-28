import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

Role = Literal["patient", "doctor", "nurse", "pharmacist", "lab", "admin"]
# StaffRole remains the list for role-pickers; AdminUserCreate accepts every
# role (admins may create patient accounts too).
StaffRole = Literal["doctor", "nurse", "pharmacist", "lab", "admin"]

# The four appointment statuses from the spec. A new booking is always
# "Requested"; doctors/admins Confirm or Decline; anyone involved can Cancel.
AppointmentStatus = Literal[
    "Requested", "Confirmed", "Declined", "Cancelled", "Completed", "Expired"
]

_COMMON_PASSWORDS = {
    "password",
    "password1",
    "12345678",
    "123456789",
    "qwerty123",
    "iloveyou",
    "admin123",
    "welcome1",
}
PASSWORD_MESSAGE = "Password must be at least 8 characters and include a letter and a number."


def _validate_password(value: str, email: str | None = None) -> str:
    if len(value) < 8 or len(value) > 128:
        raise ValueError(PASSWORD_MESSAGE)
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError(PASSWORD_MESSAGE)
    if email and value.strip().lower() == email.strip().lower():
        raise ValueError(PASSWORD_MESSAGE)
    if value.strip().lower() in _COMMON_PASSWORDS:
        raise ValueError(PASSWORD_MESSAGE)
    return value


def _validate_name(value: str) -> str:
    stripped = value.strip()
    if not (2 <= len(stripped) <= 100) or not re.search(r"[A-Za-z]", stripped):
        raise ValueError("Name must be 2-100 characters and contain at least one letter.")
    return stripped


# --------------------------------------------------------------------- auth
class RegisterRequest(BaseModel):
    """Public registration — ALWAYS creates a patient account."""

    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("password")
    @classmethod
    def _password(cls, value: str, info) -> str:
        email = info.data.get("email")
        return _validate_password(value, str(email) if email else None)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    # Opt in to the longer (7 day) session lifetime on this device.
    remember_me: bool = False
    # Informational only: authentication uses email + password. The server's
    # role is authoritative and returned in the response; clients use it to
    # redirect (a wrong picker no longer blocks sign-in).
    role: Role | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password(cls, value: str) -> str:
        return _validate_password(value)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password(cls, value: str) -> str:
        return _validate_password(value)


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

    name: str | None = Field(default=None, min_length=2, max_length=100)
    email: EmailStr | None = None
    # Required only when the email is being changed.
    current_password: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_name(value)


class AdminUserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Role
    department: str | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("password")
    @classmethod
    def _password(cls, value: str, info) -> str:
        email = info.data.get("email")
        return _validate_password(value, str(email) if email else None)


class AdminUserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    department: str | None = Field(default=None, max_length=100)
    password: str | None = None
    # Admin can change roles (including promoting staff), but never their own
    # (guaranteed server-side in the router, not merely by omitting the field).
    role: Annotated[Role | None, Field(default=None)] = None
    # Doctor's per-visit consultation fee (drives automatic bills).
    consultation_fee: Decimal | None = Field(default=None, ge=0, le=1000000)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_name(value)

    @field_validator("password")
    @classmethod
    def _strong_password(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_password(value)


class AdminUserResponse(UserResponse):
    department: str | None = None
    created_at: datetime | None = None
    # JSON number for direct formatting in the admin console.
    consultation_fee: float | None = None


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
    """Fields a patient may change on their own profile.

    Clinical status is deliberately absent: only an admin or doctor may
    change it, and the patient endpoint rejects any attempt to send it.
    """

    name: str | None = Field(default=None, min_length=2, max_length=255)
    age: int | None = Field(default=None, ge=0, le=130)
    condition: str | None = Field(default=None, max_length=255)
    assigned_doctor_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_status(cls, data):
        if isinstance(data, dict) and "status" in data:
            raise ValueError("You cannot change your own patient status.")
        return data

    @field_validator("name")
    @classmethod
    def _name_not_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Name cannot be empty. Enter your full name.")
        return _validate_name(value)


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
    meeting_url: str | None = Field(default=None, max_length=500)

    # A PATCH may omit a field, but an explicit null for a required column
    # must be a clear 422 — never a database error or a misleading conflict.
    @field_validator("scheduled_at")
    @classmethod
    def _scheduled_not_null(cls, value: datetime | None) -> datetime:
        if value is None:
            raise ValueError("Choose a new appointment time.")
        return value

    @field_validator("department")
    @classmethod
    def _department_not_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Choose a department.")
        return value

    @field_validator("mode")
    @classmethod
    def _mode_not_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Choose In-person or Telemedicine.")
        return value

    @field_validator("status")
    @classmethod
    def _status_not_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Appointment status cannot be empty.")
        return value

    @field_validator("meeting_url")
    @classmethod
    def _https_meeting_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith("https://"):
            raise ValueError("Meeting links must start with https://")
        return value


class AppointmentResponse(BaseModel):
    id: str
    patient_id: str
    patient_name: str
    # None once the doctor's account has been removed; doctor_name keeps the
    # historical name for completed visits.
    doctor_id: str | None
    doctor_name: str
    department: str
    scheduled_at: datetime
    duration_minutes: int = 30
    mode: str
    status: str
    reason: str | None = None
    status_reason: str | None = None
    meeting_url: str | None = None


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
    # A JSON number, not a string: the frontend renders it with Intl.NumberFormat.
    amount: float
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

    @field_validator("currency")
    @classmethod
    def _inr_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.strip().upper() != "INR":
            raise ValueError("All invoices are in INR.")
        return "INR"


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
    # How many already-booked appointments this leave cancelled (create only).
    cancelled_appointments: int = 0


TokenResponse.model_rebuild()
