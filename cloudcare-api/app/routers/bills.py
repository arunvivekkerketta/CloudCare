"""Billing routes. Patients read only their own bills."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.audit import record_audit
from app.database import get_db
from app.deps import get_current_user
from app.models import Bill, Patient, User, UserRole
from app.schemas import BillResponse

router = APIRouter(prefix="/api/bills", tags=["bills"])

READ_ROLES = {
    UserRole.patient,
    UserRole.nurse,
    UserRole.pharmacist,
    UserRole.lab,
    UserRole.admin,
}


def _serialize(bill: Bill) -> dict:
    return BillResponse(
        id=str(bill.id),
        invoice_no=bill.invoice_no,
        patient_id=str(bill.patient_id),
        patient_name=bill.patient.user.name,
        description=bill.description,
        amount=bill.amount,
        status=bill.status,
        created_at=bill.created_at,
    ).model_dump()


def _base_query(db: Session):
    return db.query(Bill).options(joinedload(Bill.patient).joinedload(Patient.user))


def _own_patient(db: Session, user: User) -> Patient | None:
    return db.query(Patient).filter(Patient.user_id == user.id).first()


@router.get("", response_model=list[BillResponse])
def list_bills(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in READ_ROLES:
        raise HTTPException(status_code=403, detail="Not permitted.")

    query = _base_query(db)
    if current_user.role == UserRole.patient:
        patient = _own_patient(db, current_user)
        if patient is None:
            return []
        query = query.filter(Bill.patient_id == patient.id)

    total = query.count()
    rows = (
        query.order_by(Bill.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [_serialize(b) for b in rows]


@router.get("/{bill_id}", response_model=BillResponse)
def get_bill(
    bill_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in READ_ROLES:
        raise HTTPException(status_code=403, detail="Not permitted.")

    try:
        bill_uuid = uuid.UUID(bill_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Bill not found.") from None

    bill = _base_query(db).filter(Bill.id == bill_uuid).first()
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found.")

    if current_user.role == UserRole.patient:
        patient = _own_patient(db, current_user)
        if patient is None or bill.patient_id != patient.id:
            raise HTTPException(status_code=403, detail="Not permitted.")
    return _serialize(bill)


@router.patch("/{bill_id}", response_model=BillResponse)
def update_bill_status(
    bill_id: str,
    status: str = Query(..., pattern="^(Pending|Paid|Overdue)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin marks invoices paid/pending. Gateway payments come later."""
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin role required.")

    try:
        bill_uuid = uuid.UUID(bill_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Bill not found.") from None

    bill = _base_query(db).filter(Bill.id == bill_uuid).first()
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found.")

    bill.status = status
    record_audit(
        db,
        action="bill_update",
        resource=f"bill:{bill.id}",
        actor=current_user,
        detail=f"status={status}",
    )
    db.commit()
    db.refresh(bill)
    return _serialize(bill)
