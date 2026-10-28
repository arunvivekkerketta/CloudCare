"""preserve completed visit history when a doctor leaves

Revision ID: 007
Revises: 006
Create Date: 2026-10-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("doctor_name", sa.String(length=255), nullable=True),
    )
    # Snapshot every existing doctor name before any account can be removed.
    op.execute(
        "UPDATE appointments SET doctor_name = "
        "(SELECT name FROM users WHERE users.id = appointments.doctor_id)"
    )

    with op.batch_alter_table("appointments") as batch_op:
        batch_op.alter_column(
            "doctor_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=True,
        )

    # Some SQLite batch rebuilds do not carry a partial index across. Rebuild
    # it explicitly so the double-booking guard survives the migration.
    op.execute("DROP INDEX IF EXISTS uq_appointments_active_slot")
    op.create_index(
        "uq_appointments_active_slot",
        "appointments",
        ["doctor_id", "scheduled_at"],
        unique=True,
        postgresql_where=sa.text("status IN ('Requested', 'Confirmed')"),
        sqlite_where=sa.text("status IN ('Requested', 'Confirmed')"),
    )


def downgrade() -> None:
    # Rows whose doctor was removed cannot be assigned again; drop them rather
    # than inventing a doctor id, then restore the non-null column.
    op.execute("DELETE FROM appointments WHERE doctor_id IS NULL")
    with op.batch_alter_table("appointments") as batch_op:
        batch_op.alter_column(
            "doctor_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=False,
        )
        batch_op.drop_column("doctor_name")
