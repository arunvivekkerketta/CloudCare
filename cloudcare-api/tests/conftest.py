"""Pytest fixtures: isolated SQLite database per test session."""
import os

# Must be set before app.config is imported.
os.environ["USE_SQLITE"] = "true"
os.environ["ENV"] = "development"
os.environ["SECRET_KEY"] = "test-secret-key-for-pytest-only"
# Disable rate limits for the whole suite: the real caps (5 registrations/hour,
# 10 logins/minute, keyed by the same test-client address) would cascade-fail
# every test after the first few register/login calls.
os.environ["RATE_LIMIT_LOGIN"] = ""
os.environ["RATE_LIMIT_REGISTER"] = ""

import pytest
from fastapi.testclient import TestClient

from app.database import build_engine, get_db
from app.main import app
from app.models.base import Base
from sqlalchemy.orm import sessionmaker
# NOTE: do NOT `import app.models` here — it rebinds the name `app` from the
# FastAPI instance to the package module, breaking app.dependency_overrides
# in every fixture. It's also unnecessary: app.main already imports
# app.models, so Base.metadata is fully populated.


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Belt & braces: if limits are ever re-enabled in tests, start each test
    with an empty limiter storage so counts never leak between tests."""
    yield
    from app.ratelimit import limiter

    try:
        limiter.reset()
    except Exception:
        pass


@pytest.fixture(scope="session")
def engine():
    # In-memory shared cache so all connections see the same schema/data.
    # NOTE: the pysqlite SAVEPOINT/transaction workaround (isolation_level=None
    # + explicit BEGIN) lives in app.database.build_engine. Do NOT register
    # duplicate listeners here — build_engine already attaches them, and a
    # second "begin" listener makes SQLite raise
    # "cannot start a transaction within a transaction" on every test.
    engine = build_engine("sqlite:///file:cloudcare_test?mode=memory&cache=shared&uri=true", sqlite_mode=True)

    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(engine):
    """Transaction-per-test isolation.

    Routes call db.commit() — with a JOINED transaction those commits are
    scoped to the outer transaction, which is rolled back after each test,
    so tests never leak data into each other."""
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
    session = Session()

    def override_get_db():
        try:
            yield session
        finally:
            pass

    original = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    yield session
    session.close()
    transaction.rollback()
    connection.close()
    if original is not None:
        app.dependency_overrides[get_db] = original
    else:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def client(db_session):
    return TestClient(app)


# ------------------------------------------------------------- helpers
def register_patient(client, email, password="Patient!Pass123", name="Test Patient"):
    response = client.post(
        "/api/auth/register",
        json={"name": name, "email": email, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(client, email, password, role):
    return client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "role": role},
    )


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def seeded_ids(db_session):
    """Create one doctor + two patients (A and B) for isolation tests."""
    from app.models import Patient, User, UserRole
    from app.security import hash_password

    doctor = User(
        name="Dr. Test",
        email="doctor.test@example.com",
        hashed_password=hash_password("Doctor!Pass123"),
        role=UserRole.doctor,
        department="Cardiology",
    )
    user_a = User(
        name="Patient A",
        email="patient.a@example.com",
        hashed_password=hash_password("Patient!Pass123"),
        role=UserRole.patient,
    )
    user_b = User(
        name="Patient B",
        email="patient.b@example.com",
        hashed_password=hash_password("Patient!Pass123"),
        role=UserRole.patient,
    )
    admin = User(
        name="Admin Test",
        email="admin.test@example.com",
        hashed_password=hash_password("Admin!Pass1234"),
        role=UserRole.admin,
    )
    db_session.add_all([doctor, user_a, user_b, admin])
    db_session.flush()

    patient_a = Patient(user_id=user_a.id, age=30, condition="Test A", risk_score=10, status="Active", assigned_doctor_id=doctor.id)
    patient_b = Patient(user_id=user_b.id, age=40, condition="Test B", risk_score=20, status="Active")
    db_session.add_all([patient_a, patient_b])
    db_session.commit()

    return {
        "doctor_id": str(doctor.id),
        "doctor_email": doctor.email,
        "user_a_id": str(user_a.id),
        "user_b_id": str(user_b.id),
        "patient_a_id": str(patient_a.id),
        "patient_b_id": str(patient_b.id),
        "admin_id": str(admin.id),
    }
