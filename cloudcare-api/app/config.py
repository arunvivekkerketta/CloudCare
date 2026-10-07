from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Environment -------------------------------------------------------
    env: str = "development"  # "development" | "production"

    # --- Security ----------------------------------------------------------
    secret_key: str = "dev-secret-key-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # --- CORS --------------------------------------------------------------
    cors_origins: str = "http://localhost:5500,http://127.0.0.1:5500,http://localhost:3000,http://127.0.0.1:3000"

    # --- Database ----------------------------------------------------------
    # Preferred on managed hosts: a single DATABASE_URL. DB_* fields are the
    # fallback for local development.
    database_url: str | None = None
    db_host: str | None = None
    db_port: int = 5432
    db_user: str | None = None
    db_password: str | None = None
    db_name: str | None = None
    use_sqlite: bool = False
    db_sslmode: str | None = None  # e.g. "require"

    # Rate limits (slowapi syntax). Empty string disables a limiter — used by
    # the test suite so hundreds of register/login calls don't trip the caps.
    rate_limit_login: str = "10/minute"
    rate_limit_register: str = "5/hour"

    # Connection pool — deliberate values, not silent SQLAlchemy defaults.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    @property
    def is_production(self) -> bool:
        return self.env.strip().lower() in {"production", "prod"}

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [
            origin.strip().rstrip("/")
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]
        if not origins or "*" in origins:
            # Never allow wildcard CORS — fail safe to localhost defaults.
            return [
                "http://localhost:5500",
                "http://127.0.0.1:5500",
                "http://localhost:3000",
                "http://127.0.0.1:3000",
            ]
        return origins

    @property
    def has_db_credentials(self) -> bool:
        return all([self.db_host, self.db_user, self.db_password, self.db_name])

    @property
    def is_sqlite(self) -> bool:
        if self.database_url and self.database_url.strip().lower().startswith("sqlite"):
            return True
        return self.use_sqlite or (
            not self.database_url and not self.has_db_credentials
        )

    @staticmethod
    def _normalize_database_url(raw_url: str) -> str:
        """Convert managed-host URLs to what SQLAlchemy+psycopg2 expects."""
        url = raw_url.strip()
        # Render/Heroku style: postgres:// or postgresql:// -> psycopg2 driver.
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url

    def _sqlite_url(self) -> str:
        db_path = Path(__file__).resolve().parent.parent / "cloudcare.db"
        return f"sqlite:///{db_path.as_posix()}"

    @property
    def database_url_resolved(self) -> str:
        # Priority mirrors is_sqlite: explicit URL > USE_SQLITE > DB_* creds
        # > SQLite fallback. Without the USE_SQLITE check here, a leftover
        # .env with DB_* creds silently overrode USE_SQLITE=true.
        if self.database_url:
            url = self._normalize_database_url(self.database_url)
            if "sslmode=" not in url and self.db_sslmode:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}sslmode={self.db_sslmode}"
            return url

        if self.use_sqlite:
            return self._sqlite_url()

        if self.has_db_credentials:
            return (
                f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )

        return self._sqlite_url()

    # Backwards-compatible alias used by alembic/env.py and database.py.
    @property
    def database_url_property(self) -> str:
        return self.database_url_resolved

    def validate_production(self) -> list[str]:
        """Return a list of fatal misconfigurations for production."""
        problems: list[str] = []
        if not self.is_production:
            return problems

        if self.secret_key == "dev-secret-key-change-me":
            problems.append(
                "SECRET_KEY is still the default development value. "
                "Set a strong SECRET_KEY environment variable."
            )
        if self.is_sqlite:
            problems.append(
                "SQLite is not supported in production. Configure a PostgreSQL "
                "DATABASE_URL (or DB_* variables)."
            )
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
