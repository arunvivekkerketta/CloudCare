import logging
import sys
from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger("cloudcare.database")

settings = get_settings()


def build_engine(database_url: str | None = None, sqlite_mode: bool | None = None) -> Engine:
    url = database_url or settings.database_url_resolved
    if sqlite_mode is None:
        sqlite_mode = url.startswith("sqlite")

    if sqlite_mode:
        sqlite_engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
        )

        # pysqlite's default driver-level transaction handling silently
        # breaks SAVEPOINT / nested-transaction semantics (a long-documented
        # pysqlite quirk — see the SQLAlchemy SQLite dialect docs, "Serializable
        # isolation / Savepoints / Transactional DDL"). Without this, a
        # conn.begin_nested() savepoint can end up durably committed to the
        # database instead of staying rollback-able, which is exactly what
        # tests/conftest.py's per-test transaction rollback relies on.
        @event.listens_for(sqlite_engine, "connect")
        def _sqlite_disable_pysqlite_begin(dbapi_connection, connection_record):
            dbapi_connection.isolation_level = None

        @event.listens_for(sqlite_engine, "begin")
        def _sqlite_emit_explicit_begin(conn):
            conn.exec_driver_sql("BEGIN")

        return sqlite_engine

    return create_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        pool_recycle=settings.db_pool_recycle,
    )


engine: Engine = build_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def verify_database_connection() -> None:
    """Fail fast at startup when the configured database is unavailable.

    Never leaks exception details — they are logged server-side instead.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return
    except SQLAlchemyError as exc:
        logger.error("Database connectivity check failed: %s", exc)
        raise RuntimeError(
            "Cannot connect to the CloudCare database. "
            "Check your database configuration and that the server is reachable."
        ) from exc


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
