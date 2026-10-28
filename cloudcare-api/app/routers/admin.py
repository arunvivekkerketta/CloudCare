"""Admin-only routes: user management, specialties, doctor shifts/leaves,
review moderation, audit logs.

Also exposes ONE public route (GET /api/specialties) so the booking UI can
list specialties without an account; everything else below requires admin.
"""
import uuid
from datetime import date as date_cls
from datetime import datetime as dt_cls
from datetime import timedelta, timezone as timezone_cls

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.routers.appointments import _serialize as serialize_appointment
from app.routers.reviews import _serialize as serialize_review

# NOTE: the module-level dependency (require_roles) protects EVERY admin route
# in this file. The public specialties read lives on its own router without
# that dependency.

from app.audit import record_audit
from app.database import get_db
from app.deps import require_roles
from app.models import (
    Appointment,
    AuditLog,
    Bill,
    BillingDetail,
    DoctorLeave,
    DoctorShift,
    Patient,
    Review,
    Specialty,
    User,
    UserRole,
)
from app.schemas import (
    AdminLeaveCreate,
    AdminLeaveOut,
    AdminShiftOut,
    AdminUserCreate,
    AdminUserResponse,
    AdminUserUpdate,
    AuditLogPage,
    AuditLogResponse,
    ReviewPage,
    ReviewResponse,
    SpecialtyCreate,
    SpecialtyResponse,
    ShiftsSync,
)
from app.notify import notify
from app.scheduling import (
    ACTIVE_STATUSES,
    CLINIC_TZ,
    normalize_shift_rules,
    shift_windows,
    to_clinic,
)
from app.security import hash_password
from app.utils import client_ip, doctor_label

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_roles(UserRole.admin))])


def _admin_count(db: Session) -> int:
    return int(
        db.query(func.count(User.id))
        .filter(User.role == UserRole.admin)
        .scalar()
        or 0
    )


def _purge_doctor_appointments(db: Session, doctor: User) -> tuple[int, int]:
    """Cancel live visits and preserve completed history for a leaving doctor.

    Returns (cancelled, kept_completed). Every affected patient is notified.
    Completed rows keep a name snapshot and lose the account reference, so
    the visit remains in the patient's history after the doctor is deleted.
    """
    cancelled = 0
    kept = 0
    rows = (
        db.query(Appointment)
        .filter(Appointment.doctor_id == doctor.id)
        .all()
    )
    for appt in rows:
        if appt.status in ACTIVE_STATUSES:
            appt.status = "Cancelled"
            appt.status_reason = "Doctor left the clinic"
            cancelled += 1
            when = to_clinic(appt.scheduled_at).strftime("%a %d %b, %H:%M")
            notify(
                db,
                appt.patient.user_id,
                f"Your appointment with {doctor_label(doctor.name)} on {when} "
                f"was cancelled because the doctor is no longer at the clinic. "
                f"Please book with another doctor.",
            )

        if appt.status == "Completed":
            if not appt.doctor_name:
                appt.doctor_name = doctor.name
            appt.doctor_id = None
            kept += 1
        else:
            # Cancelled/declined/expired rows have no clinical history to keep.
            db.delete(appt)

    return cancelled, kept


def _get_doctor_or_404(db: Session, doctor_id: str) -> User:
    try:
        did = uuid.UUID(doctor_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Doctor not found.") from None
    doctor = db.get(User, did)
    if doctor is None or doctor.role != UserRole.doctor:
        raise HTTPException(status_code=404, detail="Doctor not found.")
    return doctor


def _serialize_admin_user(user: User) -> dict:
    return AdminUserResponse(
        id=str(user.id),
        name=user.name,
        email=user.email,
        role=user.role.value,
        department=user.department,
        created_at=user.created_at,
        consultation_fee=user.consultation_fee,
    ).model_dump()


@router.get("/users", response_model=list[AdminUserResponse])
def list_users(  # noqa: FURB — dependency enforced at router level
    role: UserRole | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(User)
    if role:
        query = query.filter(User.role == role)
    if search:
        like = f"%{search.lower()}%"
        query = query.filter(or_(func.lower(User.name).like(like), func.lower(User.email).like(like)))
    return [
        _serialize_admin_user(u)
        for u in query.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    ]


@router.post("/users", response_model=AdminUserResponse, status_code=201)
def create_staff_user(
    payload: AdminUserCreate,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """The ONLY way to create doctor/nurse/pharmacist/lab/admin accounts."""
    email = payload.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    role = UserRole(payload.role)
    user = User(
        name=payload.name.strip(),
        email=email,
        hashed_password=hash_password(payload.password),
        role=role,
        department=payload.department,
    )
    db.add(user)
    db.flush()

    if role == UserRole.patient:
        db.add(Patient(user_id=user.id, status="Active"))
        # Same transaction: default billing details (gates 18/20 parity).
        db.add(
            BillingDetail(
                user_id=user.id,
                full_name=user.name,
                email=email,
                billing_address="",
                payment_method="Not set",
                currency="INR",
                status="Default",
            )
        )

    record_audit(
        db,
        action="admin_create_user",
        resource=f"user:{user.id}",
        actor=current_user,
        detail=f"created {payload.role} account for {email}",
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(user)
    return _serialize_admin_user(user)


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="User not found.") from None

    user = db.get(User, user_uuid)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    data = payload.model_dump(exclude_unset=True)

    new_role = UserRole(data["role"]) if data.get("role") is not None else None

    # Gate 26: an admin can never change their own role — no exceptions.
    # (Demotion would instantly strip their authority mid-session;
    # promotion adds nothing but invites bugs. Another admin does it.)
    if new_role is not None and user.id == current_user.id:
        if user.role == UserRole.admin and new_role != UserRole.admin and _admin_count(db) <= 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "You cannot change your own role: this is the last admin "
                    "account."
                ),
            )
        raise HTTPException(
            status_code=400,
            detail="You cannot change your own role.",
        )

    # The clinic must always keep at least one admin who can sign in.
    if new_role is not None:
        if (
            user.role == UserRole.admin
            and new_role != UserRole.admin
            and _admin_count(db) <= 1
        ):
            raise HTTPException(
                status_code=400,
                detail="You cannot remove the last admin account.",
            )
        data["role"] = new_role

    role_changed = new_role is not None and new_role != user.role
    if "password" in data and data["password"]:
        user.hashed_password = hash_password(data.pop("password"))
        # An admin-set password revokes that user's existing sessions.
        user.token_version += 1
    for field, value in data.items():
        setattr(user, field, value)
    if role_changed:
        # A role change revokes every token issued under the old role, so a
        # demoted account cannot keep acting with its previous authority.
        user.token_version += 1

    record_audit(
        db,
        action="admin_update_user",
        resource=f"user:{user.id}",
        actor=current_user,
        detail=", ".join(payload.model_dump(exclude_unset=True).keys()),
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(user)
    return _serialize_admin_user(user)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="User not found.") from None

    # Compare parsed ids: a differently-cased string is the same account.
    if user_uuid == current_user.id:
        detail = "You cannot delete your own account."
        if current_user.role == UserRole.admin and _admin_count(db) <= 1:
            detail = (
                "You cannot delete your own account: this is the last admin "
                "account."
            )
        raise HTTPException(status_code=400, detail=detail)

    user = db.get(User, user_uuid)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    if user.role == UserRole.admin and _admin_count(db) <= 1:
        raise HTTPException(
            status_code=400,
            detail="You cannot delete the last admin account.",
        )

    # A doctor with active appointments must go through the safe doctor
    # delete endpoint (which can auto-cancel); a plain user delete would
    # silently orphan their Requested/Confirmed slots.
    if user.role == UserRole.doctor:
        active = (
            db.query(Appointment)
            .filter(
                Appointment.doctor_id == user.id,
                Appointment.status.in_(ACTIVE_STATUSES),
            )
            .count()
        )
        if active:
            raise HTTPException(
                status_code=409,
                detail=f"This doctor still has {active} active appointment(s). "
                "Use DELETE /api/admin/doctors/{id}?cancel=true instead.",
            )
        # Keep completed visits in patient history; live rows are already
        # prohibited above, and other terminal rows are removed.
        _purge_doctor_appointments(db, user)

    # Explicit cleanup of dependent rows (SQLite leaves FK enforcement off,
    # so CASCADE would not fire): remove domain rows tied to the account.
    if user.role == UserRole.patient:
        patient = db.query(Patient).filter(Patient.user_id == user.id).first()
        if patient is not None:
            db.query(Appointment).filter(Appointment.patient_id == patient.id).delete()
            db.query(Bill).filter(Bill.patient_id == patient.id).delete()
            db.delete(patient)

    db.query(Review).filter(Review.author_id == user.id).delete()
    db.query(Review).filter(Review.doctor_id == user.id).delete()
    db.query(BillingDetail).filter(BillingDetail.user_id == user.id).delete()
    db.query(DoctorShift).filter(DoctorShift.doctor_id == user.id).delete()
    db.query(DoctorLeave).filter(DoctorLeave.doctor_id == user.id).delete()

    record_audit(
        db,
        action="admin_delete_user",
        resource=f"user:{user_id}",
        actor=current_user,
        detail=f"deleted {user.role.value} account {user.email}",
        ip_address=client_ip(request),
    )
    db.delete(user)
    db.commit()


# ------------------------------------------------------------ safe doctor delete
@router.delete("/doctors/{doctor_id}", status_code=204)
def delete_doctor(
    doctor_id: str,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
    cancel: bool = Query(False, description="Cancel that doctor's active appointments, then delete."),
):
    """Delete a doctor safely.

    - No active appointments: plain delete. Related rows clean up via
      CASCADE (shifts, leaves, reviews, billing) and SET NULL (their
      assigned patients keep their records, unassigned).
    - Active appointments exist: 409 listing the counts, unless
      ?cancel=true confirms auto-cancellation (their slots are freed for
      the same times with any other doctor).
    - In all cases the doctor's appointment records (including history)
      are removed with the account: doctor_id is NOT NULL and rows cannot
      be orphaned on SQLite (FK cascades are disabled project-wide).
    """
    try:
        did = uuid.UUID(doctor_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Doctor not found.") from None

    doctor = db.get(User, did)
    if doctor is None or doctor.role != UserRole.doctor:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    active = (
        db.query(Appointment)
        .filter(Appointment.doctor_id == doctor.id, Appointment.status.in_(ACTIVE_STATUSES))
        .count()
    )
    assigned = (
        db.query(Patient).filter(Patient.assigned_doctor_id == doctor.id).count()
    )

    if active > 0 and not cancel:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"{doctor_label(doctor.name)} still has {active} active "
                    "appointment(s). Cancel them first, or retry with "
                    "cancel=true to cancel them automatically."
                ),
                "active_appointments": active,
                "assigned_patients": assigned,
            },
        )

    # Cancel live visits (notifying each patient) and keep completed visits
    # in the patient's history with a name snapshot.
    cancelled, kept = _purge_doctor_appointments(db, doctor)

    # Their calendar configuration dies with the account (always, not just
    # in the auto-cancel branch — SQLite FK cascades are off project-wide).
    db.query(DoctorShift).filter(DoctorShift.doctor_id == doctor.id).delete()
    db.query(DoctorLeave).filter(DoctorLeave.doctor_id == doctor.id).delete()

    # Reviews they wrote or received go with the account (explicit deletes:
    # SQLite FK enforcement is left off project-wide, so ondelete=CASCADE is
    # only guaranteed on PostgreSQL).
    db.query(Review).filter(Review.doctor_id == doctor.id).delete()
    db.query(Review).filter(Review.author_id == doctor.id).delete()
    db.query(BillingDetail).filter(BillingDetail.user_id == doctor.id).delete()

    # Their assigned patients keep their records but lose the assignment.
    unassigned = (
        db.query(Patient)
        .filter(Patient.assigned_doctor_id == doctor.id)
        .update({Patient.assigned_doctor_id: None}, synchronize_session=False)
    )

    detail_bits = [f"deleted doctor {doctor.email}"]
    if cancelled:
        detail_bits.append(f"cancelled {cancelled} active appointment(s)")
    if kept:
        detail_bits.append(f"kept {kept} completed visit(s) in patient history")
    if assigned or unassigned:
        detail_bits.append(f"{assigned} patient record(s) now unassigned")

    record_audit(
        db,
        action="admin_delete_doctor",
        resource=f"user:{doctor.id}",
        actor=current_user,
        detail="; ".join(detail_bits),
        ip_address=client_ip(request),
    )
    db.delete(doctor)
    db.commit()


# -------------------------------------------------------------- specialties
public_specialties = APIRouter(prefix="/api/specialties", tags=["specialties"])


@public_specialties.get("", response_model=list[SpecialtyResponse])
def specialties_public(db: Session = Depends(get_db)):
    """Public list for the booking dropdown (no auth to browse)."""
    rows = db.query(Specialty).order_by(func.lower(Specialty.name)).all()
    return [SpecialtyResponse(id=str(s.id), name=s.name).model_dump() for s in rows]


@router.get("/specialties", response_model=list[SpecialtyResponse])
def specialties_admin(db: Session = Depends(get_db)):
    """Admin read (same content, admin-role gated for the admin UI)."""
    rows = db.query(Specialty).order_by(func.lower(Specialty.name)).all()
    return [SpecialtyResponse(id=str(s.id), name=s.name).model_dump() for s in rows]


@router.post("/specialties", response_model=SpecialtyResponse, status_code=201)
def create_specialty(
    payload: SpecialtyCreate,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if db.query(Specialty).filter(func.lower(Specialty.name) == name.lower()).first():
        raise HTTPException(status_code=409, detail="This specialty already exists.")

    specialty = Specialty(name=name)
    db.add(specialty)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This specialty already exists.") from None

    record_audit(
        db,
        action="admin_create_specialty",
        resource=f"specialty:{specialty.id}",
        actor=current_user,
        detail=name,
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(specialty)
    return SpecialtyResponse(id=str(specialty.id), name=specialty.name).model_dump()


@router.delete("/specialties/{specialty_id}", status_code=204)
def delete_specialty(
    specialty_id: str,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Refuses when the name is still some doctor's department."""
    try:
        sid = uuid.UUID(specialty_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Specialty not found.") from None

    specialty = db.get(Specialty, sid)
    if specialty is None:
        raise HTTPException(status_code=404, detail="Specialty not found.")

    in_use = (
        db.query(func.count(User.id))
        .filter(User.role == UserRole.doctor, User.department == specialty.name)
        .scalar()
    )
    if int(in_use or 0) > 0:
        raise HTTPException(
            status_code=409,
            detail="This specialty is still assigned to one or more doctors.",
        )

    record_audit(
        db,
        action="admin_delete_specialty",
        resource=f"specialty:{specialty.id}",
        actor=current_user,
        detail=specialty.name,
        ip_address=client_ip(request),
    )
    db.delete(specialty)
    db.commit()


# --------------------------------------------------------------- stats/audit
# GET /api/users/summary (admin-only) lives in app/main.py so its path sits
# outside the /api/admin prefix, matching the documented API surface.


@router.get("/audit-logs", response_model=AuditLogPage)
def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    action: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(AuditLog).options(joinedload(AuditLog.actor))
    if action:
        query = query.filter(AuditLog.action == action)

    total = query.count()
    rows = (
        query.order_by(AuditLog.timestamp.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [
        AuditLogResponse(
            id=str(entry.id),
            actor_id=str(entry.actor_id) if entry.actor_id else None,
            actor_name=entry.actor.name if entry.actor else None,
            actor_email=entry.actor.email if entry.actor else None,
            action=entry.action,
            resource=entry.resource,
            timestamp=entry.timestamp,
        ).model_dump()
        for entry in rows
    ]
    return AuditLogPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    ).model_dump()


# --------------------------------------------------- doctor shifts (replace-all)
@router.get("/doctors/{doctor_id}/shifts", response_model=list[AdminShiftOut])
def get_doctor_shifts(doctor_id: str, db: Session = Depends(get_db)):
    doctor = _get_doctor_or_404(db, doctor_id)
    return [
        AdminShiftOut(name=name, start=s.strftime("%H:%M"), end=e.strftime("%H:%M")).model_dump()
        for name, s, e in shift_windows(db, doctor)
    ]


@router.put("/doctors/{doctor_id}/shifts", response_model=list[AdminShiftOut])
def put_doctor_shifts(
    doctor_id: str,
    payload: ShiftsSync,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Replace ALL shift rules for one doctor with the submitted set.

    The transaction saves both shifts together: deleted rules are removed,
    edited/added rules are upserted, and nothing commits if validation fails.
    """
    doctor = _get_doctor_or_404(db, doctor_id)
    normalized = normalize_shift_rules([r.model_dump() for r in payload.shifts])

    existing = {row.name: row for row in db.query(DoctorShift).filter(DoctorShift.doctor_id == doctor.id).all()}
    for name in list(existing):
        if name not in normalized:
            db.delete(existing[name])
    for name, (start, end) in normalized.items():
        if name in existing:
            row = existing[name]
            row.start_time = start
            row.end_time = end
            row.is_active = True
        else:
            db.add(DoctorShift(doctor_id=doctor.id, name=name, start_time=start, end_time=end, is_active=True))

    record_audit(
        db,
        action="admin_update_shifts",
        resource=f"user:{doctor.id}",
        actor=current_user,
        detail=", ".join(f"{n} {s.strftime('%H:%M')}-{e.strftime('%H:%M')}" for n, (s, e) in normalized.items()),
        ip_address=client_ip(request),
    )
    db.commit()
    return get_doctor_shifts(doctor_id, db)


# --------------------------------------------------------------------- leaves
@router.get("/doctors/{doctor_id}/leaves", response_model=list[AdminLeaveOut])
def list_doctor_leaves(
    doctor_id: str,
    future_only: bool = Query(True, description="Only leaves from today onward."),
    db: Session = Depends(get_db),
):
    doctor = _get_doctor_or_404(db, doctor_id)
    query = (
        db.query(DoctorLeave)
        .filter(DoctorLeave.doctor_id == doctor.id)
        .order_by(DoctorLeave.leave_date, DoctorLeave.shift_name)
    )
    if future_only:
        today = dt_cls.now(CLINIC_TZ).date()
        query = query.filter(DoctorLeave.leave_date >= today)
    return [
        AdminLeaveOut(
            id=str(row.id),
            doctor_id=str(row.doctor_id),
            leave_date=row.leave_date,
            shift_name=row.shift_name,
        ).model_dump(mode="json")
        for row in query.all()
    ]


@router.post("/doctors/{doctor_id}/leaves", response_model=list[AdminLeaveOut], status_code=201)
def create_doctor_leave(
    doctor_id: str,
    payload: AdminLeaveCreate,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Add a leave: whole-day (no shift_name) or one named shift.

   Duplicate leaves (same doctor/date/shift) return 409 instead of a silent
    second row.
    """
    doctor = _get_doctor_or_404(db, doctor_id)

    if isinstance(payload.leave_date, dt_cls):
        day = payload.leave_date.date()
    elif isinstance(payload.leave_date, date_cls):
        day = payload.leave_date
    else:  # pragma: no cover - schema guarantees the union
        raise HTTPException(status_code=400, detail="Invalid leave date.")
    today = dt_cls.now(CLINIC_TZ).date()
    if day < today:
        raise HTTPException(status_code=400, detail="Leave dates must be today or in the future.")

    duplicate = (
        db.query(DoctorLeave)
        .filter(
            DoctorLeave.doctor_id == doctor.id,
            DoctorLeave.leave_date == day,
            (
                DoctorLeave.shift_name == payload.shift_name
                if payload.shift_name
                else DoctorLeave.shift_name.is_(None)
            ),
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="This leave already exists.")

    row = DoctorLeave(doctor_id=doctor.id, leave_date=day, shift_name=payload.shift_name)
    db.add(row)

    # A leave must also release anything already booked in that window:
    # cancel the appointments, tell the patients and record the count.
    day_start_utc = dt_cls(day.year, day.month, day.day, tzinfo=CLINIC_TZ).astimezone(
        timezone_cls.utc
    )
    day_end_utc = day_start_utc + timedelta(days=1)
    booked = (
        db.query(Appointment)
        .filter(
            Appointment.doctor_id == doctor.id,
            Appointment.status.in_(ACTIVE_STATUSES),
            Appointment.scheduled_at >= day_start_utc,
            Appointment.scheduled_at < day_end_utc,
        )
        .all()
    )
    if payload.shift_name:
        windows = {name: (start, end) for name, start, end in shift_windows(db, doctor)}
        window = windows.get(payload.shift_name)
        if window is None:
            available = ", ".join(windows) or "none"
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{doctor_label(doctor.name)} does not work the "
                    f"{payload.shift_name} shift. Available shifts: {available}."
                ),
            )
        # A shift leave touches only that shift's appointments; the other
        # shift and any whole-day bookings outside it stay untouched.
        shift_start, shift_end = window
        booked = [
            appt
            for appt in booked
            if shift_start <= to_clinic(appt.scheduled_at).time() < shift_end
        ]

    display_name = doctor_label(doctor.name)
    for appt in booked:
        appt.status = "Cancelled"
        appt.status_reason = "Doctor on leave"
        notify(
            db,
            appt.patient.user_id,
            f"Your appointment with {display_name} on "
            f"{to_clinic(appt.scheduled_at).strftime('%a %d %b, %H:%M')} IST was "
            f"cancelled because the doctor is on leave. "
            f"Please book another slot.",
        )

    record_audit(
        db,
        action="admin_create_leave",
        resource=f"leave:{row.id}",
        actor=current_user,
        detail=(
            f"{doctor.email} {day.isoformat()} "
            f"({payload.shift_name or 'whole day'}); "
            f"cancelled {len(booked)} appointment(s)"
        ),
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(row)
    return [
        AdminLeaveOut(
            id=str(row.id),
            doctor_id=str(row.doctor_id),
            leave_date=row.leave_date,
            shift_name=row.shift_name,
            cancelled_appointments=len(booked),
        ).model_dump(mode="json")
    ]


@router.delete("/leaves/{leave_id}", status_code=204)
def delete_leave(
    leave_id: str,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    try:
        lid = uuid.UUID(leave_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Leave not found.") from None

    row = db.get(DoctorLeave, lid)
    if row is None:
        raise HTTPException(status_code=404, detail="Leave not found.")

    record_audit(
        db,
        action="admin_delete_leave",
        resource=f"leave:{row.id}",
        actor=current_user,
        detail=f"{row.leave_date.isoformat()} ({row.shift_name or 'whole day'})",
        ip_address=client_ip(request),
    )
    db.delete(row)
    db.commit()


# ------------------------------------------------------- review moderation
@router.get("/reviews", response_model=ReviewPage)
def admin_list_reviews(
    doctor_id: str | None = Query(None),
    author_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Paginated review feed with optional doctor/author filters."""
    query = (
        db.query(Review)
        .options(joinedload(Review.author), joinedload(Review.doctor))
    )
    try:
        if doctor_id:
            query = query.filter(Review.doctor_id == uuid.UUID(doctor_id))
        if author_id:
            query = query.filter(Review.author_id == uuid.UUID(author_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Review not found.") from None

    total = query.count()
    rows = (
        query.order_by(Review.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return ReviewPage(
        items=[serialize_review(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    ).model_dump()


@router.delete("/reviews/{review_id}", status_code=204)
def admin_delete_review(
    review_id: str,
    request: Request,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Admin moderation: remove any review."""
    try:
        rid = uuid.UUID(review_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Review not found.") from None

    review = db.get(Review, rid)
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found.")

    record_audit(
        db,
        action="admin_delete_review",
        resource=f"review:{review.id}",
        actor=current_user,
        detail=f"by {review.author.email} about {review.doctor.email}",
        ip_address=client_ip(request),
    )
    db.delete(review)
    db.commit()


# ----------------------------------------------------- appointments oversight
@router.get("/appointments", response_model=list[dict])
def admin_list_appointments(
    status_filter: str | None = Query(None, alias="status"),
    doctor_id: str | None = Query(None),
    within_days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Clinic-wide appointment feed for the admin console.

    Default view: from the start of the current clinic day through the next
    `within_days` days, optionally filtered by status or doctor.
    """
    ALLOWED_STATUS_FILTERS = {
        "Requested", "Confirmed", "Declined", "Cancelled", "Completed", "Expired",
    }
    if status_filter and status_filter not in ALLOWED_STATUS_FILTERS:
        raise HTTPException(status_code=422, detail=f"Unsupported status filter: {status_filter!r}")

    query = db.query(Appointment).options(
        joinedload(Appointment.patient).joinedload(Patient.user),
        joinedload(Appointment.doctor),
    )
    if status_filter:
        query = query.filter(Appointment.status == status_filter)
    if doctor_id:
        try:
            query = query.filter(Appointment.doctor_id == uuid.UUID(doctor_id))
        except (ValueError, TypeError):
            raise HTTPException(status_code=404, detail="Doctor not found.") from None

    now_c = dt_cls.now(CLINIC_TZ)
    start_of_day = now_c.replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = start_of_day + timedelta(days=within_days)
    rows = (
        query.filter(
            Appointment.scheduled_at >= start_of_day.astimezone(timezone_cls.utc),
            Appointment.scheduled_at < horizon.astimezone(timezone_cls.utc),
        )
        .order_by(Appointment.scheduled_at)
        .limit(500)
        .all()
    )
    return [serialize_appointment(a) for a in rows]
