"""Smart Inventory Management System — demand forecasts and reorder drafts.

This is the CloudCare port of the inventory agent's forecasting and
recommendation engine. It reads the real stock table and the audited stock
movements (never an in-memory demo store), learns each item's recent daily
use, projects the next 30 days, and explains every recommendation in plain
language.

The assistant is read-only: it never changes stock or creates an order. An
admin reviews a suggestion and records the real movement through the audited
inventory API.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from math import ceil
from typing import Any, Iterable

from sqlalchemy.orm import Session, selectinload

from app.models import InventoryItem, InventoryTransaction
from app.scheduling import clinic_now

MODEL_NAME = "Smart Inventory Management System"
FORECAST_DAYS = 30
HISTORY_DAYS = 90
EXPIRY_WATCH_DAYS = 30
SAFETY_DAYS = 7
USAGE_TYPES = ("usage", "discard")

STATUS_RANK = {
    "Expired stock": 0,
    "Reorder now": 1,
    "Use stock before expiry": 2,
    "Monitor": 3,
    "Healthy": 4,
}


def _as_date(value: datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).date()
        return value.date()
    return value


def _linear_trend(points: list[tuple[int, float]]) -> tuple[float, float, float]:
    """Return (slope, intercept, r_squared) for a simple least-squares line.

    Deliberately dependency-free: the clinic's forecast must keep working even
    when a heavier ML runtime is unavailable on the host.
    """
    n = len(points)
    if n < 2:
        return 0.0, points[0][1] if points else 0.0, 0.0

    sum_x = sum(x for x, _ in points)
    sum_y = sum(y for _, y in points)
    sum_xy = sum(x * y for x, y in points)
    sum_xx = sum(x * x for x, _ in points)
    denominator = n * sum_xx - sum_x * sum_x
    if denominator == 0:
        return 0.0, sum_y / n, 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n

    mean_y = sum_y / n
    total_variation = sum((y - mean_y) ** 2 for _, y in points)
    if total_variation == 0:
        r_squared = 0.0
    else:
        residual = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
        r_squared = max(0.0, min(1.0, 1 - residual / total_variation))
    return slope, intercept, r_squared


def _usage_by_day(
    transactions: Iterable[InventoryTransaction], today: date
) -> tuple[dict[date, int], date | None]:
    """Consumed units per clinic day, plus the first recorded movement date."""
    daily: dict[date, int] = defaultdict(int)
    first_day: date | None = None
    cutoff = today - timedelta(days=HISTORY_DAYS)

    for txn in transactions:
        day = _as_date(txn.created_at)
        if day is None:
            continue
        if first_day is None or day < first_day:
            first_day = day
        if day < cutoff:
            continue
        if txn.adjustment_type in USAGE_TYPES and txn.quantity_change < 0:
            daily[day] += abs(int(txn.quantity_change))
    return daily, first_day


def _forecast_item(item: InventoryItem, daily: dict[date, int], first_day: date | None, today: date) -> dict[str, Any]:
    window_start = today - timedelta(days=HISTORY_DAYS - 1)
    observed_start = max(window_start, first_day) if first_day else window_start
    observed_days = max(1, min(HISTORY_DAYS, (today - observed_start).days + 1))
    # A single busy day must not look like the new normal. Use at least a
    # 30-day window, with zero-use days included, before annualising demand.
    span_days = max(30, observed_days)
    series_start = today - timedelta(days=span_days - 1)

    total_used = sum(daily.values())
    average_daily = total_used / span_days if total_used else 0.0

    # Build a continuous daily series (including zero-use days). A sparse
    # series is what makes a regression honest; only observed days would
    # exaggerate both the average and the trend.
    points: list[tuple[int, float]] = []
    for offset in range(span_days):
        day = series_start + timedelta(days=offset)
        points.append((offset, float(daily.get(day, 0))))

    usage_days = sum(1 for _, value in points if value > 0)
    if usage_days >= 5:
        slope, intercept, r_squared = _linear_trend(points)
        raw_forecast = [
            max(0.0, slope * (span_days - 1 + step) + intercept)
            for step in range(1, FORECAST_DAYS + 1)
        ]
        predicted_demand = sum(raw_forecast)
        average_daily = predicted_demand / FORECAST_DAYS if predicted_demand else average_daily
        confidence = round(max(0.5, min(0.95, 0.55 + r_squared * 0.4)), 2)
        method = "Recent-use trend"
        if slope > 0.05:
            trend = "Use is rising. Order sooner than the recent average suggests."
        elif slope < -0.05:
            trend = "Use is falling. Avoid over-ordering this month."
        else:
            trend = "Use is steady."
    else:
        predicted_demand = average_daily * FORECAST_DAYS
        confidence = 0.55 if usage_days else 0.2
        method = "Recent-use average" if usage_days else "No use recorded yet"
        trend = (
            "Based on the last 90 days of recorded stock movements."
            if usage_days
            else "Record stock used on this item to improve the forecast."
        )

    quantity = int(item.quantity or 0)
    threshold = int(item.reorder_threshold or 0)
    days_to_expiry = (
        (item.expiry_date - today).days if item.expiry_date else None
    )
    days_until_stockout = (
        round(quantity / average_daily, 1) if average_daily > 0 else None
    )
    safety_stock = average_daily * SAFETY_DAYS
    target_stock = max(threshold * 3, predicted_demand + safety_stock)
    order_quantity = 0

    if days_to_expiry is not None and days_to_expiry < 0:
        status = "Expired stock"
        order_quantity = 0 if not average_daily else max(0, ceil(target_stock - quantity))
        reason = (
            "This stock has expired. Remove it from the shelf and order fresh "
            "stock before using this item again."
        )
    elif (
        days_to_expiry is not None
        and days_to_expiry <= EXPIRY_WATCH_DAYS
        and quantity > average_daily * max(days_to_expiry, 0)
    ):
        status = "Use stock before expiry"
        surplus = max(0, round(quantity - average_daily * max(days_to_expiry, 0)))
        reason = (
            f"This stock expires in {days_to_expiry} day(s). Use the oldest "
            f"stock first and avoid ordering more until about {surplus} unit(s) "
            "have been used."
        )
    elif quantity <= threshold or (
        days_until_stockout is not None and days_until_stockout <= 7
    ):
        status = "Reorder now"
        order_quantity = max(0, ceil(target_stock - quantity))
        if days_until_stockout is None:
            stockout_text = "there is not enough use history to estimate a stockout date"
        else:
            stockout_text = f"it will run out in about {days_until_stockout:g} day(s)"
        reason = (
            f"{quantity} unit(s) in stock is at or below the reorder level of "
            f"{threshold}, and {stockout_text}. {order_quantity} unit(s) would "
            "restore a comfortable buffer."
        )
    elif quantity <= threshold * 1.3 or (
        days_until_stockout is not None and days_until_stockout <= 14
    ):
        status = "Monitor"
        reason = (
            f"Stock is close to the reorder level of {threshold}. Keep watching "
            "daily use; no order is needed yet."
        )
    else:
        status = "Healthy"
        reason = "Stock is comfortable for the current rate of use."

    return {
        "item_id": item.id,
        "name": item.name,
        "sku": item.sku,
        "category": item.category,
        "quantity": quantity,
        "reorder_threshold": threshold,
        "unit_cost": float(item.unit_cost or 0),
        "average_daily_use": round(average_daily, 2),
        "predicted_30_day_demand": round(predicted_demand, 1),
        "days_until_stockout": days_until_stockout,
        "confidence": confidence,
        "method": method,
        "trend": trend,
        "status": status,
        "recommended_order_quantity": order_quantity,
        "reason": reason,
        "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
        "days_to_expiry": days_to_expiry,
    }


def build_inventory_insights(
    db: Session, today: date | None = None
) -> dict[str, Any]:
    """Analyse every stock item and return explainable forecasts + drafts."""
    today = today or clinic_now().date()
    items = (
        db.query(InventoryItem)
        .options(selectinload(InventoryItem.transactions))
        .order_by(InventoryItem.name.asc())
        .all()
    )

    forecasts = []
    for item in items:
        daily, first_day = _usage_by_day(item.transactions, today)
        forecasts.append(_forecast_item(item, daily, first_day, today))

    forecasts.sort(
        key=lambda row: (
            STATUS_RANK.get(row["status"], 9),
            -row["recommended_order_quantity"],
            row["name"].lower(),
        )
    )

    expiring = [
        row
        for row in forecasts
        if row["days_to_expiry"] is not None
        and row["days_to_expiry"] <= EXPIRY_WATCH_DAYS
    ]
    expiring.sort(key=lambda row: (row["days_to_expiry"], row["name"].lower()))

    reorder_drafts = [
        row
        for row in forecasts
        if row["recommended_order_quantity"] > 0
        or row["status"] == "Expired stock"
    ]

    total_value = sum(row["quantity"] * row["unit_cost"] for row in forecasts)
    return {
        "model": MODEL_NAME,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "history_days": HISTORY_DAYS,
        "forecast_days": FORECAST_DAYS,
        "summary": {
            "total_items": len(forecasts),
            "total_stock_value": round(total_value, 2),
            "low_stock": sum(
                1
                for row in forecasts
                if row["quantity"] <= row["reorder_threshold"]
            ),
            "reorder_now": sum(
                1 for row in forecasts if row["status"] == "Reorder now"
            ),
            "expiring_soon": sum(
                1
                for row in forecasts
                if row["days_to_expiry"] is not None
                and 0 <= row["days_to_expiry"] <= EXPIRY_WATCH_DAYS
            ),
            "expired": sum(
                1
                for row in forecasts
                if row["days_to_expiry"] is not None and row["days_to_expiry"] < 0
            ),
            "projected_30_day_demand": round(
                sum(row["predicted_30_day_demand"] for row in forecasts), 1
            ),
            "recommended_order_value": round(
                sum(
                    row["recommended_order_quantity"] * row["unit_cost"]
                    for row in forecasts
                ),
                2,
            ),
        },
        "forecasts": forecasts,
        "reorder_drafts": reorder_drafts,
        "expiring": expiring,
    }
