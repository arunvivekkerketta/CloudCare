"""Inventory Management Router — clinic supply & pharmaceutical analytics.

Adapts the Smart Inventory Management analytics engine for medical inventory:
- Track medical supplies, pharmaceuticals, equipment
- Monitor stock levels, expiry dates, reorder thresholds
- Analytics on usage trends, cost analysis, supplier performance
- Low stock alerts and inventory health dashboard
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_roles
from app.models import UserRole

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


# ---------------------------------------------------------------------------
# Pydantic schemas for inventory items
# ---------------------------------------------------------------------------

class InventoryItemCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    category: str = Field(..., min_length=2, max_length=100)  # e.g. "Pharmaceuticals", "PPE", "Equipment"
    sku: str = Field(..., min_length=3, max_length=50)
    quantity: int = Field(..., ge=0)
    unit_cost: float = Field(..., ge=0)
    selling_price: float = Field(..., ge=0)  # For items sold to patients
    supplier: str = Field(..., min_length=2, max_length=200)
    reorder_threshold: int = Field(default=10, ge=0)
    expiry_date: Optional[str] = Field(None, description="ISO date string YYYY-MM-DD")
    location: str = Field(default="Main Pharmacy", min_length=2, max_length=200)
    notes: Optional[str] = Field(None, max_length=500)


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


class InventoryItemResponse(BaseModel):
    id: int
    name: str
    category: str
    sku: str
    quantity: int
    unit_cost: float
    selling_price: float
    total_value: float  # quantity * unit_cost
    profit_margin: float  # (selling_price - unit_cost) / selling_price * 100
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
    adjustment_type: str = Field(..., pattern=r"^(addition|usage|return|discard|correction)$")
    quantity_change: int = Field(..., description="Positive for additions, negative for usage/discard")
    reason: str = Field(..., min_length=2, max_length=500)
    performed_by: str = Field(..., description="User ID or name who made the adjustment")


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


# ---------------------------------------------------------------------------
# In-memory inventory store (SQLite-backed in production)
# ---------------------------------------------------------------------------

# For simplicity, using in-memory store. In production, this would use
# a proper database table with SQLAlchemy models.
_inventory_items: Dict[int, Dict[str, Any]] = {}
_inventory_transactions: Dict[int, Dict[str, Any]] = {}
_next_id = 1
_next_txn_id = 1


def _compute_margin(unit_cost: float, selling_price: float) -> float:
    if selling_price <= 0:
        return 0.0
    return round((selling_price - unit_cost) / selling_price * 100, 2)


def _compute_days_to_expiry(expiry_date: Optional[str]) -> Optional[int]:
    if not expiry_date:
        return None
    from datetime import date, datetime
    try:
        expiry = datetime.strptime(expiry_date, "%Y-%m-%d").date()
        today = date.today()
        return (expiry - today).days
    except ValueError:
        return None


def _create_item(payload: InventoryItemCreate) -> Dict[str, Any]:
    global _next_id
    item = {
        "id": _next_id,
        "name": payload.name,
        "category": payload.category,
        "sku": payload.sku,
        "quantity": payload.quantity,
        "unit_cost": payload.unit_cost,
        "selling_price": payload.selling_price,
        "total_value": payload.quantity * payload.unit_cost,
        "profit_margin": _compute_margin(payload.unit_cost, payload.selling_price),
        "supplier": payload.supplier,
        "reorder_threshold": payload.reorder_threshold,
        "expiry_date": payload.expiry_date,
        "days_to_expiry": _compute_days_to_expiry(payload.expiry_date),
        "location": payload.location,
        "notes": payload.notes,
        "last_updated": datetime.now().isoformat(),
    }
    _inventory_items[_next_id] = item
    _next_id += 1
    return item


def _update_item(item_id: int, payload: InventoryItemUpdate) -> Dict[str, Any]:
    item = _inventory_items.get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "quantity" and value is not None:
            item["quantity"] = value
            item["total_value"] = value * item["unit_cost"]
        elif field == "unit_cost" and value is not None:
            item["unit_cost"] = value
            item["total_value"] = item["quantity"] * value
            item["profit_margin"] = _compute_margin(value, item["selling_price"])
        elif field == "selling_price" and value is not None:
            item["selling_price"] = value
            item["profit_margin"] = _compute_margin(item["unit_cost"], value)
        elif field == "expiry_date":
            item["expiry_date"] = value
            item["days_to_expiry"] = _compute_days_to_expiry(value)
        elif field == "reorder_threshold":
            item["reorder_threshold"] = value
        elif value is not None:
            item[field] = value

    item["last_updated"] = datetime.now().isoformat()
    return item


def _adjust_stock(item_id: int, adjustment: InventoryAdjustment) -> Dict[str, Any]:
    global _next_txn_id
    item = _inventory_items.get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    # Validate adjustment type
    valid_types = {"addition", "usage", "return", "discard", "correction"}
    if adjustment.adjustment_type not in valid_types:
        raise HTTPException(status_code=422, detail=f"Invalid adjustment type. Must be one of: {valid_types}")

    # Apply quantity change
    old_quantity = item["quantity"]
    new_quantity = old_quantity + adjustment.quantity_change

    if new_quantity < 0:
        raise HTTPException(status_code=422, detail="Adjustment would result in negative quantity")

    item["quantity"] = new_quantity
    item["total_value"] = new_quantity * item["unit_cost"]
    item["last_updated"] = datetime.now().isoformat()

    # Record transaction
    txn = {
        "id": _next_txn_id,
        "item_id": item_id,
        "item_name": item["name"],
        "adjustment_type": adjustment.adjustment_type,
        "quantity_change": adjustment.quantity_change,
        "new_quantity": new_quantity,
        "reason": adjustment.reason,
        "performed_by": adjustment.performed_by,
        "timestamp": datetime.now().isoformat(),
    }
    _inventory_transactions[_next_txn_id] = txn
    _next_txn_id += 1

    return txn


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/items", response_model=InventoryItemResponse, status_code=201)
def create_inventory_item(
    payload: InventoryItemCreate,
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Add a new inventory item."""
    item = _create_item(payload)
    return InventoryItemResponse(**item)


@router.get("/items", response_model=List[InventoryItemResponse])
def list_inventory_items(
    category: Optional[str] = Query(None, description="Filter by category"),
    low_stock_only: bool = Query(False, description="Only show items below reorder threshold"),
    expiring_soon: bool = Query(False, description="Only show items expiring within 30 days"),
    search: Optional[str] = Query(None, description="Search by name or SKU"),
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """List all inventory items with optional filters."""
    items = list(_inventory_items.values())

    if category:
        items = [i for i in items if i["category"].lower() == category.lower()]
    if low_stock_only:
        items = [i for i in items if i["quantity"] <= i["reorder_threshold"]]
    if expiring_soon:
        items = [i for i in items if i["days_to_expiry"] is not None and i["days_to_expiry"] <= 30]
    if search:
        search_lower = search.lower()
        items = [i for i in items if search_lower in i["name"].lower() or search_lower in i["sku"].lower()]

    return [InventoryItemResponse(**i) for i in items]


@router.get("/items/{item_id}", response_model=InventoryItemResponse)
def get_inventory_item(
    item_id: int,
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Get a specific inventory item."""
    item = _inventory_items.get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return InventoryItemResponse(**item)


@router.patch("/items/{item_id}", response_model=InventoryItemResponse)
def update_inventory_item(
    item_id: int,
    payload: InventoryItemUpdate,
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Update an inventory item."""
    item = _update_item(item_id, payload)
    return InventoryItemResponse(**item)


@router.delete("/items/{item_id}", status_code=204)
def delete_inventory_item(
    item_id: int,
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Remove an inventory item."""
    if item_id not in _inventory_items:
        raise HTTPException(status_code=404, detail="Item not found")
    del _inventory_items[item_id]
    # Clean up associated transactions
    to_delete = [txn_id for txn_id, txn in _inventory_transactions.items() if txn["item_id"] == item_id]
    for txn_id in to_delete:
        del _inventory_transactions[txn_id]


@router.post("/adjustments", response_model=InventoryTransactionResponse, status_code=201)
def record_adjustment(
    adjustment: InventoryAdjustment,
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Record a stock adjustment (addition, usage, return, discard, correction)."""
    txn = _adjust_stock(adjustment.item_id, adjustment)
    return InventoryTransactionResponse(**txn)


@router.get("/adjustments", response_model=List[InventoryTransactionResponse])
def list_adjustments(
    item_id: Optional[int] = Query(None, description="Filter by item"),
    adjustment_type: Optional[str] = Query(None, description="Filter by type"),
    limit: int = Query(50, ge=1, le=200),
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """List recent stock adjustments."""
    txns = list(_inventory_transactions.values())

    if item_id:
        txns = [t for t in txns if t["item_id"] == item_id]
    if adjustment_type:
        txns = [t for t in txns if t["adjustment_type"] == adjustment_type]

    txns.sort(key=lambda t: t["timestamp"], reverse=True)
    return [InventoryTransactionResponse(**t) for t in txns[:limit]]


@router.get("/kpi", response_model=InventoryKPI)
def get_inventory_kpi(
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Get key performance indicators for inventory."""
    items = list(_inventory_items.values())

    total_items = len(items)
    categories = set(i["category"] for i in items)
    total_inventory_value = sum(i["total_value"] for i in items)
    total_potential_revenue = sum(i["quantity"] * i["selling_price"] for i in items)
    low_stock_items = sum(1 for i in items if i["quantity"] <= i["reorder_threshold"])
    expiring_soon_items = sum(1 for i in items if i["days_to_expiry"] is not None and i["days_to_expiry"] <= 30)

    margins = [i["profit_margin"] for i in items if i["selling_price"] > 0]
    average_margin = sum(margins) / len(margins) if margins else 0.0

    # Top category by total value
    category_values: Dict[str, float] = {}
    for i in items:
        category_values[i["category"]] = category_values.get(i["category"], 0) + i["total_value"]
    top_category = max(category_values, key=category_values.get) if category_values else None

    return InventoryKPI(
        total_items=total_items,
        total_categories=len(categories),
        total_inventory_value=round(total_inventory_value, 2),
        total_potential_revenue=round(total_potential_revenue, 2),
        low_stock_items=low_stock_items,
        expiring_soon_items=expiring_soon_items,
        average_margin=round(average_margin, 2),
        top_category_by_value=top_category,
    )


@router.get("/analytics/categories", response_model=List[CategoryPerformance])
def get_category_performance(
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Get inventory performance broken down by category."""
    items = list(_inventory_items.values())

    category_data: Dict[str, Dict[str, Any]] = {}
    for i in items:
        cat = i["category"]
        if cat not in category_data:
            category_data[cat] = {
                "category": cat,
                "item_count": 0,
                "total_quantity": 0,
                "total_value": 0.0,
                "total_potential_revenue": 0.0,
                "margins": [],
            }
        category_data[cat]["item_count"] += 1
        category_data[cat]["total_quantity"] += i["quantity"]
        category_data[cat]["total_value"] += i["total_value"]
        category_data[cat]["total_potential_revenue"] += i["quantity"] * i["selling_price"]
        if i["selling_price"] > 0:
            category_data[cat]["margins"].append(i["profit_margin"])

    result = []
    for cat_data in category_data.values():
        avg_margin = sum(cat_data["margins"]) / len(cat_data["margins"]) if cat_data["margins"] else 0.0
        result.append(CategoryPerformance(
            category=cat_data["category"],
            item_count=cat_data["item_count"],
            total_quantity=cat_data["total_quantity"],
            total_value=round(cat_data["total_value"], 2),
            total_potential_revenue=round(cat_data["total_potential_revenue"], 2),
            average_margin=round(avg_margin, 2),
        ))

    result.sort(key=lambda c: c.total_value, reverse=True)
    return result


@router.get("/analytics/suppliers", response_model=List[SupplierPerformance])
def get_supplier_performance(
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Get inventory breakdown by supplier."""
    items = list(_inventory_items.values())

    supplier_data: Dict[str, Dict[str, Any]] = {}
    for i in items:
        sup = i["supplier"]
        if sup not in supplier_data:
            supplier_data[sup] = {
                "supplier": sup,
                "item_count": 0,
                "total_value": 0.0,
                "costs": [],
                "categories": set(),
            }
        supplier_data[sup]["item_count"] += 1
        supplier_data[sup]["total_value"] += i["total_value"]
        supplier_data[sup]["costs"].append(i["unit_cost"])
        supplier_data[sup]["categories"].add(i["category"])

    result = []
    for sup_data in supplier_data.values():
        avg_cost = sum(sup_data["costs"]) / len(sup_data["costs"]) if sup_data["costs"] else 0.0
        result.append(SupplierPerformance(
            supplier=sup_data["supplier"],
            item_count=sup_data["item_count"],
            total_value=round(sup_data["total_value"], 2),
            average_cost=round(avg_cost, 2),
            categories_served=len(sup_data["categories"]),
        ))

    result.sort(key=lambda s: s.total_value, reverse=True)
    return result


@router.get("/analytics/low-stock", response_model=List[InventoryItemResponse])
def get_low_stock_items(
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Get all items that are at or below their reorder threshold."""
    items = [i for i in _inventory_items.values() if i["quantity"] <= i["reorder_threshold"]]
    items.sort(key=lambda i: i["quantity"])
    return [InventoryItemResponse(**i) for i in items]


@router.get("/analytics/expiring", response_model=List[InventoryItemResponse])
def get_expiring_items(
    days: int = Query(30, ge=1, le=365, description="Lookahead window in days"),
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Get items expiring within the specified number of days."""
    items = [
        i for i in _inventory_items.values()
        if i["days_to_expiry"] is not None and i["days_to_expiry"] <= days and i["days_to_expiry"] > 0
    ]
    items.sort(key=lambda i: i["days_to_expiry"] or 9999)
    return [InventoryItemResponse(**i) for i in items]


@router.post("/analytics/upload-csv")
def upload_inventory_csv(
    csv_content: str,
    current_user: UserRole = Depends(require_roles(UserRole.admin)),
):
    """Bulk import inventory items from CSV.

    Expected columns: name, category, sku, quantity, unit_cost, selling_price,
    supplier, reorder_threshold, expiry_date, location, notes
    """
    import csv
    import io

    if not csv_content.strip():
        raise HTTPException(status_code=400, detail="Empty CSV content")

    reader = csv.DictReader(io.StringIO(csv_content))
    created = 0
    errors = []

    for row in reader:
        try:
            payload = InventoryItemCreate(
                name=row.get("name", "").strip(),
                category=row.get("category", "").strip(),
                sku=row.get("sku", "").strip(),
                quantity=int(row.get("quantity", 0)),
                unit_cost=float(row.get("unit_cost", 0)),
                selling_price=float(row.get("selling_price", 0)),
                supplier=row.get("supplier", "").strip(),
                reorder_threshold=int(row.get("reorder_threshold", 10)),
                expiry_date=row.get("expiry_date", "").strip() or None,
                location=row.get("location", "Main Pharmacy").strip(),
                notes=row.get("notes", "").strip() or None,
            )
            _create_item(payload)
            created += 1
        except Exception as e:
            errors.append(f"Row {row}: {str(e)}")

    return {
        "created": created,
        "errors": errors,
        "total_rows": created + len(errors),
    }
