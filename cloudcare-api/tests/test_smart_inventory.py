"""Smart Inventory Management System: real stock in, explainable drafts out."""
from datetime import date, datetime, timedelta

from app.models import InventoryItem, InventoryTransaction
from tests.conftest import auth_headers, login, register_patient


def _admin_headers(client):
    response = login(client, "admin.test@example.com", "Admin!Pass1234", "admin")
    assert response.status_code == 200, response.text
    return auth_headers(response.json()["access_token"])


def _item_payload(**overrides):
    payload = {
        "name": "Glucose test strips",
        "category": "Diagnostics",
        "sku": "DIAG-100",
        "quantity": 5,
        "unit_cost": 12.5,
        "selling_price": 20,
        "supplier": "MedSupply",
        "reorder_threshold": 10,
        "expiry_date": (date.today() + timedelta(days=120)).isoformat(),
        "location": "Main Pharmacy",
        "notes": None,
    }
    payload.update(overrides)
    return payload


def test_admin_gets_forecasts_and_reorder_drafts(client, db_session, seeded_ids):
    headers = _admin_headers(client)
    created = client.post(
        "/api/inventory/items", headers=headers, json=_item_payload()
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    # Twenty days of steady use: enough for the trend path, not just a guess.
    for days_ago in range(20, 0, -1):
        db_session.add(
            InventoryTransaction(
                item_id=item_id,
                adjustment_type="usage",
                quantity_change=-2,
                new_quantity=5,
                reason="Ward use",
                created_at=datetime.combine(
                    date.today() - timedelta(days=days_ago), datetime.min.time()
                )
                + timedelta(hours=10),
            )
        )
    db_session.commit()

    response = client.get("/api/inventory/insights", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["model"] == "Smart Inventory Management System"
    assert data["summary"]["total_items"] >= 1
    assert data["summary"]["reorder_now"] >= 1

    row = next(r for r in data["forecasts"] if r["item_id"] == item_id)
    assert row["average_daily_use"] > 0
    assert row["predicted_30_day_demand"] > 0
    assert row["method"] == "Recent-use trend"
    assert row["status"] == "Reorder now"
    assert row["recommended_order_quantity"] > 0
    assert "reorder level" in row["reason"]

    draft = next(d for d in data["reorder_drafts"] if d["item_id"] == item_id)
    assert draft["recommended_order_quantity"] == row["recommended_order_quantity"]


def test_one_busy_day_does_not_look_like_permanent_demand(client, db_session, seeded_ids):
    headers = _admin_headers(client)
    created = client.post(
        "/api/inventory/items",
        headers=headers,
        json=_item_payload(sku="DIAG-ONE", name="One-day reagent", quantity=100),
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    db_session.add(
        InventoryTransaction(
            item_id=item_id,
            adjustment_type="usage",
            quantity_change=-15,
            new_quantity=85,
            reason="Busy clinic day",
            created_at=datetime.now(),
        )
    )
    db_session.commit()

    data = client.get("/api/inventory/insights", headers=headers).json()
    row = next(r for r in data["forecasts"] if r["item_id"] == item_id)
    # 15 units in one day is not a 15/day trend; a 30-day window applies.
    assert row["average_daily_use"] == 0.5
    assert row["status"] == "Healthy"
    assert row["recommended_order_quantity"] == 0


def test_expired_items_are_called_out_first(client, seeded_ids):
    headers = _admin_headers(client)
    created = client.post(
        "/api/inventory/items",
        headers=headers,
        json=_item_payload(
            sku="DIAG-EXP",
            name="Expired reagent",
            expiry_date=(date.today() - timedelta(days=3)).isoformat(),
        ),
    )
    assert created.status_code == 201, created.text

    response = client.get("/api/inventory/insights", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["summary"]["expired"] == 1
    assert data["forecasts"][0]["status"] == "Expired stock"
    assert "expired" in data["forecasts"][0]["reason"].lower()
    assert data["expiring"][0]["sku"] == "DIAG-EXP"


def test_inventory_insights_are_admin_only(client):
    register_patient(client, "inventory.patient@example.com")
    login_response = login(
        client, "inventory.patient@example.com", "Patient!Pass123", "patient"
    )
    assert login_response.status_code == 200, login_response.text
    headers = auth_headers(login_response.json()["access_token"])

    response = client.get("/api/inventory/insights", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == (
        "You do not have permission to access this resource."
    )
