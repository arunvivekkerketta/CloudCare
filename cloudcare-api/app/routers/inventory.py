"""Inventory management routes backed by the database.

Stock levels survive restarts and are shared by every worker. Every write is
recorded in the audit trail; stock movements also keep their own history and
take the acting user from the signed-in session, never from the request.
"""
import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.database import get_db
from app.deps import require_roles
from app.models import InventoryItem, InventoryTransaction, User, UserRole

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

MONEY = Decimal("0.01")


# ---------------------------------------------------------------------------
# Pydantic schemas for inventory items
# ---------------------------------------------------------------------------

class InventoryItemCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    category: str = Field(..., min_length=2, max_length=100)
    sku: str = Field(..., min_length=3, max_length=50)
    quantity: int = Field(..., ge=0)
    unit_cost: float = Field(..., ge=0)
    selling_price: float = Field(..., ge=0)
    supplier: str = Field(..., min_length=2, max_length=200)
    reorder_threshold: int = Field(default=10, ge=0)
    expiry_date: Optional[str] = Field(None, description="ISO date string YYYY-MM-DD")
    location: str = Field(default="Main Pharmacy", min_length=2, max_length=200)
    notes: Optional[str] = Field(None, max_length=500)

    @field_validator("expiry_date")
    @classmethod
    def _valid_expiry(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        try:
            datetime.strptime(value.strip(), "%Y-%m-%d")
        except ValueError:
            raise ValueError("Enter the expiry date as YYYY-MM-DD.") from None
        return value.strip()

    @field_validator("sku")
    @classmethod
    def _clean_sku(cls, value: str) -> str:
        return value.strip().upper()


class InventoryItemUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    category: Optional[str] = Field(default=None, min_length=2, max_length=100)
    quantity: Optional[int] = Field(default=None, ge=0)
    unit_cost: Optional[float] = Field(default=None, ge=0)
    selling_price: Optional[float] = Field(default=None, ge=0)
    supplier: Optional[str] = Field(default=None, min_length=2, max_length=200)
    reorder_threshold: Optional[int] = Field(default=None, ge=0)
    expiry_date: Optional[str] = Field(None)
    location: Optional[str] = Field(default=None, min_length=2, max_length=200)
    notes: Optional[str] = Field(None, max_length=500)

    @field_validator("expiry_date")
    @classmethod
    def _valid_expiry(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        try:
            datetime.strptime(value.strip(), "%Y-%m-%d")
        except ValueError:
            raise ValueError("Enter the expiry date as YYYY-MM-DD.") from None
        return value.strip()


class InventoryItemResponse(BaseModel):
    id: int
    name: str
    category: str
    sku: str
    quantity: int
    unit_cost: float
    selling_price: float
    total_value: float
    profit_margin: float
    supplier: str
    reorder_threshold: int
    is_low_stock: bool
    expiry_date: Optional[str]
    days_to_expiry: Optional[int]
    location: str
    notes: Optional[str]
    last_updated: str


class InventoryAdjustment(BaseModel):
    item_id: int
    adjustment_type: str = Field(
        ..., pattern=r"^(addition|usage|return|discard|correction)$"
    )
    quantity_change: int = Field(
        ..., description="Positive for additions, negative for usage/discard"
    )
    reason: str = Field(..., min_length=2, max_length=500)


class InventoryTransactionResponse(BaseModel):
    id: int
    item_id: int
    item_name: str
    adjustment_type: str
    quantity_change: int
    new_quantity: int
    reason: str
    performed_by: str
    timestamp: str


class InventoryKPI(BaseModel):
    total_items: int
    total_categories: int
    total_inventory_value: float
    total_potential_revenue: float
    low_stock_items: int
    expiring_soon_items: int
    average_margin: float
    top_category_by_value: Optional[str]


class CategoryPerformance(BaseModel):
    category: str
    item_count: int
    total_quantity: int
    total_value: float
    total_potential_revenue: float
    average_margin: float


class SupplierPerformance(BaseModel):
    supplier: str
    item_count: int
    total_value: float
    average_cost: float
    categories_served: int


class InventoryCsvUpload(BaseModel):
    """CSV content travels in the request body, not the query string."""

    csv_content: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_margin(unit_cost: Decimal, selling_price: Decimal) -> float:
    if selling_price <= 0:
        return 0.0
    return round(float((selling_price - unit_cost) / selling_price * 100), 2)


def _days_to_expiry(expiry_date: date | None) -> Optional[int]:
    if expiry_date is None:
        return None
    return (expiry_date - date.today()).days


def _serialize_item(item: InventoryItem) -> dict:
    unit_cost = Decimal(item.unit_cost)
    selling_price = Decimal(item.selling_price)
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "sku": item.sku,
        "quantity": item.quantity,
        "unit_cost": float(unit_cost),
        "selling_price": float(selling_price),
        "total_value": round(float(unit_cost * item.quantity), 2),
        "profit_margin": _compute_margin(unit_cost, selling_price),
        "supplier": item.supplier,
        "reorder_threshold": item.reorder_threshold,
        "is_low_stock": item.quantity <= item.reorder_threshold,
        "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
        "days_to_expiry": _days_to_expiry(item.expiry_date),
        "location": item.location,
        "notes": item.notes,
        "last_updated": item.updated_at.isoformat() if item.updated_at else "",
    }


def _serialize_transaction(txn: InventoryTransaction) -> dict:
    return {
        "id": txn.id,
        "item_id": txn.item_id,
        "item_name": txn.item.name,
        "adjustment_type": txn.adjustment_type,
        "quantity_change": txn.quantity_change,
        "new_quantity": txn.new_quantity,
        "reason": txn.reason,
        "performed_by": txn.user.name if txn.user else "Unknown user",
        "timestamp": txn.created_at.isoformat() if txn.created_at else "",
    }


def _get_item_or_404(db: Session, item_id: int) -> InventoryItem:
    item = db.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    return item


def _create_item(
    db: Session, payload: InventoryItemCreate, actor: User
) -> InventoryItem:
    item = InventoryItem(
        name=payload.name.strip(),
        category=payload.category.strip(),
        sku=payload.sku,
        quantity=payload.quantity,
        unit_cost=Decimal(str(payload.unit_cost)).quantize(MONEY),
        selling_price=Decimal(str(payload.selling_price)).quantize(MONEY),
        supplier=payload.supplier.strip(),
        reorder_threshold=payload.reorder_threshold,
        expiry_date=(
            datetime.strptime(payload.expiry_date, "%Y-%m-%d").date()
            if payload.expiry_date
            else None
        ),
        location=payload.location.strip(),
        notes=payload.notes,
    )
    db.add(item)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An item with this SKU already exists.",
        ) from None

    record_audit(
        db,
        action="inventory_item_create",
        resource=f"inventory:{item.id}",
        actor=actor,
        detail=f"{item.sku} {item.name}",
    )
    return item


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/items", response_model=InventoryItemResponse, status_code=201)
def create_inventory_item(
    payload: InventoryItemCreate,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Add a new inventory item."""
    item = _create_item(db, payload, current_user)
    db.commit()
    db.refresh(item)
    return _serialize_item(item)


@router.get("/items", response_model=list[InventoryItemResponse])
def list_inventory_items(
    category: Optional[str] = Query(None, description="Filter by category"),
    low_stock_only: bool = Query(False, description="Only show low stock items"),
    expiring_soon: bool = Query(False, description="Only show items expiring within 30 days"),
    search: Optional[str] = Query(None, description="Search by name or SKU"),
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """List all inventory items with optional filters."""
    query = db.query(InventoryItem)
    if category:
        query = query.filter(func.lower(InventoryItem.category) == category.lower())
    if low_stock_only:
        query = query.filter(InventoryItem.quantity <= InventoryItem.reorder_threshold)
    if search:
        like = f"%{search.lower()}%"
        query = query.filter(
            func.lower(InventoryItem.name).like(like)
            | func.lower(InventoryItem.sku).like(like)
        )
    items = query.order_by(func.lower(InventoryItem.name)).all()
    if expiring_soon:
        items = [
            item
            for item in items
            if _days_to_expiry(item.expiry_date) is not None
            and _days_to_expiry(item.expiry_date) <= 30
        ]
    return [_serialize_item(item) for item in items]


@router.get("/items/{item_id}", response_model=InventoryItemResponse)
def get_inventory_item(
    item_id: int,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Get a specific inventory item."""
    return _serialize_item(_get_item_or_404(db, item_id))


@router.patch("/items/{item_id}", response_model=InventoryItemResponse)
def update_inventory_item(
    item_id: int,
    payload: InventoryItemUpdate,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Update an inventory item."""
    item = _get_item_or_404(db, item_id)
    data = payload.model_dump(exclude_unset=True)

    for field, value in data.items():
        if field in {"unit_cost", "selling_price"} and value is not None:
            setattr(item, field, Decimal(str(value)).quantize(MONEY))
        elif field == "expiry_date":
            item.expiry_date = (
                datetime.strptime(value, "%Y-%m-%d").date() if value else None
            )
        elif value is not None:
            setattr(item, field, value.strip() if isinstance(value, str) else value)

    record_audit(
        db,
        action="inventory_item_update",
        resource=f"inventory:{item.id}",
        actor=current_user,
        detail=", ".join(data.keys()),
    )
    db.commit()
    db.refresh(item)
    return _serialize_item(item)


@router.delete("/items/{item_id}", status_code=204)
def delete_inventory_item(
    item_id: int,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Remove an inventory item and its movement history."""
    item = _get_item_or_404(db, item_id)
    record_audit(
        db,
        action="inventory_item_delete",
        resource=f"inventory:{item.id}",
        actor=current_user,
        detail=f"{item.sku} {item.name}",
    )
    db.delete(item)
    db.commit()


@router.post("/adjustments", response_model=InventoryTransactionResponse, status_code=201)
def record_adjustment(
    adjustment: InventoryAdjustment,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Record a stock adjustment (addition, usage, return, discard, correction)."""
    item = _get_item_or_404(db, adjustment.item_id)

    new_quantity = item.quantity + adjustment.quantity_change
    if new_quantity < 0:
        raise HTTPException(
            status_code=422,
            detail="This change would make the stock level negative.",
        )

    item.quantity = new_quantity
    txn = InventoryTransaction(
        item_id=item.id,
        adjustment_type=adjustment.adjustment_type,
        quantity_change=adjustment.quantity_change,
        new_quantity=new_quantity,
        reason=adjustment.reason.strip(),
        performed_by=current_user.id,
    )
    db.add(txn)
    record_audit(
        db,
        action="inventory_adjustment",
        resource=f"inventory:{item.id}",
        actor=current_user,
        detail=(
            f"{adjustment.adjustment_type} {adjustment.quantity_change:+d} "
            f"-> {new_quantity}"
        ),
    )
    db.commit()
    db.refresh(txn)
    return _serialize_transaction(txn)


@router.get("/adjustments", response_model=list[InventoryTransactionResponse])
def list_adjustments(
    item_id: Optional[int] = Query(None, description="Filter by item"),
    adjustment_type: Optional[str] = Query(None, description="Filter by type"),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """List recent stock adjustments."""
    query = db.query(InventoryTransaction)
    if item_id:
        query = query.filter(InventoryTransaction.item_id == item_id)
    if adjustment_type:
        query = query.filter(InventoryTransaction.adjustment_type == adjustment_type)
    rows = (
        query.order_by(InventoryTransaction.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_transaction(txn) for txn in rows]


@router.get("/kpi", response_model=InventoryKPI)
def get_inventory_kpi(
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Get key performance indicators for inventory."""
    items = db.query(InventoryItem).all()
    categories = {item.category for item in items}
    total_value = sum(Decimal(item.unit_cost) * item.quantity for item in items)
    potential_revenue = sum(
        Decimal(item.selling_price) * item.quantity for item in items
    )
    margins = [
        _compute_margin(Decimal(item.unit_cost), Decimal(item.selling_price))
        for item in items
        if item.selling_price > 0
    ]

    category_values: dict[str, Decimal] = {}
    for item in items:
        category_values[item.category] = (
            category_values.get(item.category, Decimal("0"))
            + Decimal(item.unit_cost) * item.quantity
        )

    return InventoryKPI(
        total_items=len(items),
        total_categories=len(categories),
        total_inventory_value=round(float(total_value), 2),
        total_potential_revenue=round(float(potential_revenue), 2),
        low_stock_items=sum(
            1 for item in items if item.quantity <= item.reorder_threshold
        ),
        expiring_soon_items=sum(
            1
            for item in items
            if _days_to_expiry(item.expiry_date) is not None
            and _days_to_expiry(item.expiry_date) <= 30
        ),
        average_margin=round(sum(margins) / len(margins), 2) if margins else 0.0,
        top_category_by_value=(
            max(category_values, key=category_values.get) if category_values else None
        ),
    )


@router.get("/analytics/categories", response_model=list[CategoryPerformance])
def get_category_performance(
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Get inventory performance broken down by category."""
    items = db.query(InventoryItem).all()
    category_data: dict[str, dict] = {}
    for item in items:
        bucket = category_data.setdefault(
            item.category,
            {
                "category": item.category,
                "item_count": 0,
                "total_quantity": 0,
                "total_value": Decimal("0"),
                "total_potential_revenue": Decimal("0"),
                "margins": [],
            },
        )
        bucket["item_count"] += 1
        bucket["total_quantity"] += item.quantity
        bucket["total_value"] += Decimal(item.unit_cost) * item.quantity
        bucket["total_potential_revenue"] += (
            Decimal(item.selling_price) * item.quantity
        )
        if item.selling_price > 0:
            bucket["margins"].append(
                _compute_margin(Decimal(item.unit_cost), Decimal(item.selling_price))
            )

    result = []
    for bucket in category_data.values():
        margins = bucket["margins"]
        result.append(
            CategoryPerformance(
                category=bucket["category"],
                item_count=bucket["item_count"],
                total_quantity=bucket["total_quantity"],
                total_value=round(float(bucket["total_value"]), 2),
                total_potential_revenue=round(
                    float(bucket["total_potential_revenue"]), 2
                ),
                average_margin=(
                    round(sum(margins) / len(margins), 2) if margins else 0.0
                ),
            )
        )
    result.sort(key=lambda row: row.total_value, reverse=True)
    return result


@router.get("/analytics/suppliers", response_model=list[SupplierPerformance])
def get_supplier_performance(
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Get inventory breakdown by supplier."""
    items = db.query(InventoryItem).all()
    supplier_data: dict[str, dict] = {}
    for item in items:
        bucket = supplier_data.setdefault(
            item.supplier,
            {
                "supplier": item.supplier,
                "item_count": 0,
                "total_value": Decimal("0"),
                "costs": [],
                "categories": set(),
            },
        )
        bucket["item_count"] += 1
        bucket["total_value"] += Decimal(item.unit_cost) * item.quantity
        bucket["costs"].append(Decimal(item.unit_cost))
        bucket["categories"].add(item.category)

    result = []
    for bucket in supplier_data.values():
        costs = bucket["costs"]
        result.append(
            SupplierPerformance(
                supplier=bucket["supplier"],
                item_count=bucket["item_count"],
                total_value=round(float(bucket["total_value"]), 2),
                average_cost=round(float(sum(costs) / len(costs)), 2) if costs else 0.0,
                categories_served=len(bucket["categories"]),
            )
        )
    result.sort(key=lambda row: row.total_value, reverse=True)
    return result


@router.get("/analytics/low-stock", response_model=list[InventoryItemResponse])
def get_low_stock_items(
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Get all items that are at or below their reorder threshold."""
    items = (
        db.query(InventoryItem)
        .filter(InventoryItem.quantity <= InventoryItem.reorder_threshold)
        .order_by(InventoryItem.quantity)
        .all()
    )
    return [_serialize_item(item) for item in items]


@router.get("/analytics/expiring", response_model=list[InventoryItemResponse])
def get_expiring_items(
    days: int = Query(30, ge=1, le=365, description="Lookahead window in days"),
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Items expiring within the window, plus anything already expired."""
    items = db.query(InventoryItem).filter(InventoryItem.expiry_date.isnot(None)).all()
    matches = [
        item
        for item in items
        if (_days_to_expiry(item.expiry_date) is not None and _days_to_expiry(item.expiry_date) <= days)
    ]
    matches.sort(key=lambda item: item.expiry_date)
    return [_serialize_item(item) for item in matches]


@router.post("/analytics/upload-csv")
def upload_inventory_csv(
    payload: InventoryCsvUpload,
    current_user: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Bulk import inventory items from CSV sent in the request body."""
    if not payload.csv_content.strip():
        raise HTTPException(status_code=400, detail="The CSV file is empty.")

    reader = csv.DictReader(io.StringIO(payload.csv_content))
    created = 0
    errors: list[str] = []
    seen_skus: set[str] = set()

    for row_number, row in enumerate(reader, start=2):
        try:
            item_payload = InventoryItemCreate(
                name=(row.get("name") or "").strip(),
                category=(row.get("category") or "").strip(),
                sku=(row.get("sku") or "").strip(),
                quantity=int((row.get("quantity") or "0").strip()),
                unit_cost=float((row.get("unit_cost") or "0").strip()),
                selling_price=float((row.get("selling_price") or "0").strip()),
                supplier=(row.get("supplier") or "").strip(),
                reorder_threshold=int((row.get("reorder_threshold") or "10").strip()),
                expiry_date=(row.get("expiry_date") or "").strip() or None,
                location=(row.get("location") or "Main Pharmacy").strip(),
                notes=(row.get("notes") or "").strip() or None,
            )
        except Exception:
            errors.append(f"Row {row_number}: check the values and try again.")
            continue

        if item_payload.sku in seen_skus or db.query(InventoryItem).filter(
            InventoryItem.sku == item_payload.sku
        ).first():
            errors.append(f"Row {row_number}: SKU {item_payload.sku} already exists.")
            continue

        seen_skus.add(item_payload.sku)
        db.add(
            InventoryItem(
                name=item_payload.name.strip(),
                category=item_payload.category.strip(),
                sku=item_payload.sku,
                quantity=item_payload.quantity,
                unit_cost=Decimal(str(item_payload.unit_cost)).quantize(MONEY),
                selling_price=Decimal(str(item_payload.selling_price)).quantize(MONEY),
                supplier=item_payload.supplier.strip(),
                reorder_threshold=item_payload.reorder_threshold,
                expiry_date=(
                    datetime.strptime(item_payload.expiry_date, "%Y-%m-%d").date()
                    if item_payload.expiry_date
                    else None
                ),
                location=item_payload.location.strip(),
                notes=item_payload.notes,
            )
        )
        created += 1

    if created:
        record_audit(
            db,
            action="inventory_csv_import",
            resource="inventory:bulk",
            actor=current_user,
            detail=f"created {created} item(s)",
        )
    db.commit()
    return {"created": created, "errors": errors, "total_rows": created + len(errors)}
