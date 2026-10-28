"""CloudCare API — application entrypoint.

Production hardening:
- CORS restricted to CORS_ORIGINS (never "*").
- Security headers on every response.
- Global exception handler that never leaks internals.
- /docs + /openapi.json disabled in production.
- slowapi rate limits on login/register.
"""
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.database import get_db, verify_database_connection
from app.deps import require_roles
from app.models import User, UserRole
from app.ratelimit import limiter
from app.routers import (
    admin,
    appointments,
    auth,
    bills,
    billing,
    doctors,
    health_risk,
    inventory,
    notifications,
    patients,
    reviews,
)
from app.tracing import flush_tracing, init_tracing

settings = get_settings()

# Fail fast on fatal production misconfiguration.
for problem in settings.validate_production():
    logging.critical("Configuration error: %s", problem)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
logger = logging.getLogger("cloudcare")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Optional Respan tracing: a no-op unless RESPAN_API_KEY is configured.
    init_tracing()
    verify_database_connection()
    yield
    flush_tracing()


docs_url = "/docs" if not settings.is_production else None
redoc_url = "/redoc" if not settings.is_production else None

app = FastAPI(
    title="CloudCare API",
    lifespan=lifespan,
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=None if settings.is_production else "/openapi.json",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    # The frontend reads these (pagination total, unread notification count).
    expose_headers=["X-Total-Count", "X-Unread-Count"],
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Preserve intended status codes + detail for HTTPExceptions (401/403/404...)."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log the real error server-side; return a generic message to the client.

    Registered LAST so FastAPI/HTTPException handlers take precedence and
    HTTPException detail messages still reach the client untouched."""
    logger.exception("Unhandled error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(doctors.router)
app.include_router(appointments.router)
app.include_router(bills.router)
app.include_router(billing.router)
app.include_router(reviews.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(health_risk.router)
app.include_router(inventory.router)

# Public specialties list (same module as admin, but a separate router so the
# admin-guard dependency does not apply to the public read).
from app.routers.admin import public_specialties  # noqa: E402

app.include_router(public_specialties)


@app.get("/api/users/summary", tags=["users"])
def users_summary(
    _admin: User = Depends(require_roles(UserRole.admin)),
    db: Session = Depends(get_db),
):
    """Admin-only role head-count (note: /api/users, not /api/admin/users)."""
    role_counts = dict(db.query(User.role, func.count(User.id)).group_by(User.role).all())
    return {
        "total": sum(role_counts.values()),
        "by_role": {role.value: role_counts.get(role, 0) for role in UserRole},
    }


@app.get("/health", tags=["health"])
def health():
    """Plain liveness probe — no database dependency."""
    return {"status": "ok"}


@app.get("/health/db", tags=["health"])
def health_db():
    """Readiness probe. Never returns exception text — log it instead."""
    from app.database import engine

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except SQLAlchemyError as exc:
        logger.error("Health check database failure: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "unreachable"},
        )
