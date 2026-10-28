"""Inventory is database-backed, audited, and survives an app restart."""
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database import build_engine, get_db
from app.deps import create_access_token
from app.main import app
from app.models import AuditLog, InventoryItem, InventoryTransaction, User, UserRole
from app.models.base import Base
from app.security import hash_password
from tests.conftest import auth_headers, login


def _admin_headers(client):
    response = login(
        client, "admin.test@example.com", "Admin!Pass1234", "admin"
    )
    assert response.status_code == 200, response.text
    return auth_headers(response.json()["access_token"])


def _item_payload(**overrides):
    payload = {
        "name": "Surgical gloves",
        "category": "PPE",
        "sku": "ppe-001",
        "quantity": 40,
        "unit_cost": 2.5,
        "selling_price": 5,
        "supplier": "MedSupply",
        "reorder_threshold": 10,
        "expiry_date": (date.today() + timedelta(days=20)).isoformat(),
        "location": "Store room",
        "notes": None,
    }
    payload.update(overrides)
    return payload


def test_create_adjust_and_list_item(client, db_session, seeded_ids):
    headers = _admin_headers(client)

    created = client.post(
        "/api/inventory/items", headers=headers, json=_item_payload()
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["sku"] == "PPE-001"
    assert item["total_value"] == 100.0
    assert item["is_low_stock"] is False
    assert isinstance(item["unit_cost"], (int, float))

    duplicate = client.post(
        "/api/inventory/items",
        headers=headers,
        json=_item_payload(name="Another glove"),
    )
    assert duplicate.status_code == 409

    # A spoofed performed_by in the body must be ignored.
    adjusted = client.post(
        "/api/inventory/adjustments",
        headers=headers,
        json={
            "item_id": item["id"],
            "adjustment_type": "usage",
            "quantity_change": -5,
            "reason": "Used in theatre",
            "performed_by": "Somebody Else",
        },
    )
    assert adjusted.status_code == 201, adjusted.text
    txn = adjusted.json()
    assert txn["new_quantity"] == 35
    assert txn["performed_by"] == "Admin Test"
    assert txn["item_name"] == "Surgical gloves"

    listed = client.get("/api/inventory/items", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["quantity"] == 35

    listed_txns = client.get("/api/inventory/adjustments", headers=headers)
    assert listed_txns.status_code == 200
    assert listed_txns.json()[0]["performed_by"] == "Admin Test"

    assert (
        db_session.query(InventoryItem).filter(InventoryItem.sku == "PPE-001").count()
        == 1
    )
    assert db_session.query(InventoryTransaction).count() == 1
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action.in_(["inventory_item_create", "inventory_adjustment"]))
        .count()
        == 2
    )


def test_expiring_endpoint_includes_expired_items(client, seeded_ids):
    headers = _admin_headers(client)
    expired = client.post(
        "/api/inventory/items",
        headers=headers,
        json=_item_payload(
            sku="EXP-001",
            name="Expired saline",
            expiry_date=(date.today() - timedelta(days=2)).isoformat(),
        ),
    )
    assert expired.status_code == 201, expired.text

    response = client.get("/api/inventory/analytics/expiring", headers=headers)
    assert response.status_code == 200
    rows = response.json()
    assert [row["sku"] for row in rows] == ["EXP-001"]
    assert rows[0]["days_to_expiry"] == -2


def test_inventory_survives_app_restart(tmp_path):
    """Create through the API, replace the engine/session factory (a restart),
    then read the same item back through the API."""
    db_url = f"sqlite:///{(tmp_path / 'restart.db').as_posix()}"
    engines = {"current": build_engine(db_url, sqlite_mode=True)}
    Base.metadata.create_all(engines["current"])

    session = sessionmaker(bind=engines["current"])()
    admin = User(
        name="Restart Admin",
        email="restart.admin@example.com",
        hashed_password=hash_password("Admin!Pass1234"),
        role=UserRole.admin,
    )
    session.add(admin)
    session.commit()
    admin_id = admin.id
    session.close()

    def override_get_db():
        db = sessionmaker(bind=engines["current"])()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        token = create_access_token(
            subject=str(admin_id), role=UserRole.admin.value, token_version=0
        )
        headers = auth_headers(token)
        client = TestClient(app)

        created = client.post(
            "/api/inventory/items", headers=headers, json=_item_payload()
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]

        # Simulate a process restart: drop the pool and build a new engine.
        engines["current"].dispose()
        engines["current"] = build_engine(db_url, sqlite_mode=True)

        restarted_client = TestClient(app)
        listed = restarted_client.get("/api/inventory/items", headers=headers)
        assert listed.status_code == 200, listed.text
        assert [row["id"] for row in listed.json()] == [item_id]
        assert listed.json()[0]["name"] == "Surgical gloves"
    finally:
        app.dependency_overrides.pop(get_db, None)
        engines["current"].dispose()
