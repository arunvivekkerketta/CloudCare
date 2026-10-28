from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InvoiceCounter(Base):
    """One locked counter row per numbered document series.

    A row lock (SELECT ... FOR UPDATE) serializes invoice allocation on
    PostgreSQL; SQLite serializes the write transaction itself.
    """

    __tablename__ = "invoice_counters"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
