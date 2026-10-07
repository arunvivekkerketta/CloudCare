"""Review routes: public summaries + patient-authored reviews of doctors.

- One review per user per doctor (unique constraint): a second submit edits
  the existing review instead of failing.
- Rating must be 1-5; text 10-500 chars (schema-validated).
- DELETE is author-only; admins moderate through the admin router.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.audit import record_audit
from app.database import get_db
from app.deps import get_current_user
from app.models import Review, User, UserRole
from app.schemas import ReviewCreate, ReviewPage, ReviewResponse, ReviewSummary
from app.utils import client_ip

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


def _serialize(review: Review) -> dict:
    return ReviewResponse(
        id=str(review.id),
        author_id=str(review.author_id),
        author_name=review.author.name,
        doctor_id=str(review.doctor_id),
        doctor_name=review.doctor.name,
        rating=review.rating,
        text=review.text,
        created_at=review.created_at,
        updated_at=review.updated_at,
    ).model_dump(mode="json")


def _base_query(db: Session):
    return db.query(Review).options(
        joinedload(Review.author),
        joinedload(Review.doctor),
    )


@router.get("/summary", response_model=list[ReviewSummary])
def doctors_summary(db: Session = Depends(get_db)):
    """Per-doctor rating aggregates for every doctor (public)."""
    rows = (
        db.query(
            Review.doctor_id,
            func.avg(Review.rating).label("average"),
            func.count(Review.id).label("count"),
        )
        .group_by(Review.doctor_id)
        .all()
    )
    summaries: list[ReviewSummary] = []
    count_by_id = {row[0]: int(row[2]) for row in rows}
    avg_by_id = {row[0]: float(row[1]) for row in rows}

    doctor_ids = [row[0] for row in rows]
    if doctor_ids:
        doctor_names = {
            d.id: d.name
            for d in db.query(User).filter(User.id.in_(doctor_ids)).all()
        }
    else:
        doctor_names = {}

    for doctor_id in doctor_names:
        summaries.append(
            ReviewSummary(
                doctor_id=str(doctor_id),
                average_rating=round(avg_by_id[doctor_id], 2)
                if doctor_id in avg_by_id
                else None,
                review_count=count_by_id.get(doctor_id, 0),
            ).model_dump()
        )
    # Doctors with no reviews yet (so the frontend can show "No reviews yet").
    reviewers = (
        db.query(User.id)
        .filter(User.role == UserRole.doctor, ~User.id.in_(doctor_ids or [uuid.UUID(int=0)]))
        .all()
    )
    for (doctor_id,) in reviewers:
        summaries.append(
            ReviewSummary(doctor_id=str(doctor_id), average_rating=None, review_count=0).model_dump()
        )
    return summaries


@router.get("/doctors/{doctor_id}", response_model=ReviewPage)
def doctor_reviews(
    doctor_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Paginated reviews for one doctor (public; newest first)."""
    try:
        doctor_uuid = uuid.UUID(doctor_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Doctor not found.") from None

    doctor = db.get(User, doctor_uuid)
    if doctor is None or doctor.role != UserRole.doctor:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    total = int(db.query(func.count(Review.id)).filter(Review.doctor_id == doctor.id).scalar() or 0)
    rows = (
        _base_query(db)
        .filter(Review.doctor_id == doctor.id)
        .order_by(Review.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return ReviewPage(
        items=[_serialize(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    ).model_dump()


@router.get("/mine", response_model=list[ReviewResponse])
def my_reviews(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The signed-in patient's own reviews across doctors."""
    rows = (
        _base_query(db)
        .filter(Review.author_id == current_user.id)
        .order_by(Review.created_at.desc())
        .all()
    )
    return [_serialize(r) for r in rows]


@router.post("", response_model=ReviewResponse, status_code=201)
def upsert_review(
    payload: ReviewCreate,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create or edit the current user's review of one doctor.

    Schema validates rating 1-5 and text 10-500. Rating 1-5 stars.
    The unique (author, doctor) constraint makes a repeat submit an edit.
    """
    if current_user.role != UserRole.patient:
        raise HTTPException(
            status_code=403,
            detail="Only patient accounts can write reviews.",
        )

    try:
        doctor_uuid = uuid.UUID(payload.doctor_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Doctor not found.") from None

    doctor = db.get(User, doctor_uuid)
    if doctor is None or doctor.role != UserRole.doctor:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    existing = (
        db.query(Review).filter(
            Review.author_id == current_user.id,
            Review.doctor_id == doctor.id,
        ).first()
    )
    edited = existing is not None
    if edited:
        existing.rating = payload.rating
        existing.text = payload.text
        review = existing
        action = "review_update"
        response.status_code = 200  # upsert semantic: 200 edit / 201 create
    else:
        review = Review(
            author_id=current_user.id,
            doctor_id=doctor.id,
            rating=payload.rating,
            text=payload.text,
        )
        db.add(review)
        action = "review_create"

    try:
        db.flush()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="Review could not be saved.") from None

    record_audit(
        db,
        action=action,
        resource=f"review:{review.id}",
        actor=current_user,
        detail=f"doctor {doctor.email}, rating {payload.rating}",
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(review)
    return _serialize(review)


@router.delete("/mine/{doctor_id}", status_code=204)
def delete_my_review(
    doctor_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete the current user's review of one doctor (author-only)."""
    try:
        doctor_uuid = uuid.UUID(doctor_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Doctor not found.") from None

    review = (
        db.query(Review)
        .filter(Review.author_id == current_user.id, Review.doctor_id == doctor_uuid)
        .first()
    )
    if review is None:
        raise HTTPException(status_code=404, detail="Review not found.")

    record_audit(
        db,
        action="review_delete",
        resource=f"review:{review.id}",
        actor=current_user,
    )
    db.delete(review)
    db.commit()
