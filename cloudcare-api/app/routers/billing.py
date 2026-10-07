"""Default billing details per user (gates 18/19).

- GET  /api/billing/me  — read, with lazy backfill for legacy accounts
- PATCH /api/billing/me — partial update of the editable fields

Every account is expected to have exactly one billing_details row (created in
the registration transaction). For users created before that feature (or by
admin/seed paths), GET and PATCH backfill the default row on first use instead
of failing.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.database import get_db
from app.deps import get_current_user
from app.models import BillingDetail, User
from app.schemas import BillingDetailResponse, BillingDetailUpdate
from app.utils import client_ip

router = APIRouter(prefix="/api/billing", tags=["billing"])


def _ensure_billing(db: Session, user: User) -> BillingDetail:
    """Return the user's billing row, creating the default one if missing."""
    detail = db.query(BillingDetail).filter(BillingDetail.user_id == user.id).first()
    if detail is None:
        detail = BillingDetail(
            user_id=user.id,
            full_name=user.name,
            email=user.email,
            billing_address="",
            payment_method="Not set",
            currency="INR",
            status="Default",
        )
        db.add(detail)
        db.commit()
        db.refresh(detail)
    return detail


def _serialize(detail: BillingDetail) -> dict:
    return BillingDetailResponse(
        full_name=detail.full_name,
        email=detail.email,
        billing_address=detail.billing_address,
        payment_method=detail.payment_method,
        currency=detail.currency,
        status=detail.status,
        updated_at=detail.updated_at,
    ).model_dump(mode="json")


@router.get("/me", response_model=BillingDetailResponse)
def get_my_billing(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    detail = _ensure_billing(db, current_user)
    return _serialize(detail)


@router.patch("/me", response_model=BillingDetailResponse)
def update_my_billing(
    payload: BillingDetailUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    detail = _ensure_billing(db, current_user)

    data = payload.model_dump(exclude_unset=True)
    changed = [key for key, value in data.items() if value is not None]
    if not changed:
        raise HTTPException(status_code=400, detail="Nothing to update.")

    for key in changed:
        value = data[key].strip() if isinstance(data[key], str) else data[key]
        setattr(detail, key, value)

    record_audit(
        db,
        action="billing_update_self",
        resource=f"user:{current_user.id}",
        actor=current_user,
        detail=", ".join(changed),
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(detail)
    return _serialize(detail)
