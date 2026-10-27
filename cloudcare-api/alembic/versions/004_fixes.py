"""fixes: appointment status_reason + meeting_url, doctor consultation_fee,
bill.appointment_id (one bill per visit), in-app notifications

Revision ID: 004
Revises: 003
Create Date: 2026-10-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- appointments: why a status changed + the real video link -----------
    # Plain ADD COLUMN works on both SQLite and PostgreSQL; batch mode would
    # rebuild the table and risk dropping the partial unique slot index.
    op.add_column(
        "appointments", sa.Column("status_reason", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "appointments", sa.Column("meeting_url", sa.String(length=500), nullable=True)
    )

    # --- users: per-doctor consultation fee ---------------------------------
    op.add_column(
        "users",
        sa.Column(
            "consultation_fee",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="500",
        ),
    )

    # --- bills: one invoice per completed appointment -----------------------
    with op.batch_alter_table("bills") as batch_op:
        batch_op.add_column(
            sa.Column("appointment_id", postgresql.UUID(as_uuid=True), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_bills_appointment_id", ["appointment_id"]
        )

    # --- notifications ------------------------------------------------------
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")

    with op.batch_alter_table("bills") as batch_op:
        batch_op.drop_constraint("uq_bills_appointment_id", type_="unique")
        batch_op.drop_column("appointment_id")

    op.drop_column("users", "consultation_fee")
    op.drop_column("appointments", "meeting_url")
    op.drop_column("appointments", "status_reason")
