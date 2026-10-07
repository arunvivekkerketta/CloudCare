"""nullable patient fields, appointment mode/reason, bill description/created_at,
user department/created_at, audit log detail/ip

Revision ID: 002
Revises: 001
Create Date: 2026-09-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _now_default():
    """now() is PostgreSQL-only; SQLite needs CURRENT_TIMESTAMP."""
    return sa.text("CURRENT_TIMESTAMP") if _is_sqlite() else sa.text("now()")


def upgrade() -> None:
    # SQLite cannot ALTER COLUMN in place or DROP COLUMN; batch mode recreates
    # the table behind the scenes so the same migration works on both dialects.
    with op.batch_alter_table("patients") as batch_op:
        batch_op.alter_column("age", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column("condition", existing_type=sa.String(length=255), nullable=True)
        batch_op.alter_column("risk_score", existing_type=sa.Integer(), nullable=True)

    # --- users: department + created_at ------------------------------------
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("department", sa.String(length=100), nullable=True))
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=_now_default(),
                nullable=False,
            )
        )

    # --- appointments: mode + reason ----------------------------------------
    with op.batch_alter_table("appointments") as batch_op:
        batch_op.add_column(
            sa.Column("mode", sa.String(length=20), nullable=False, server_default="In-person")
        )
        batch_op.add_column(sa.Column("reason", sa.String(length=500), nullable=True))

    # --- bills: description + created_at ------------------------------------
    with op.batch_alter_table("bills") as batch_op:
        batch_op.add_column(sa.Column("description", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=_now_default(),
                nullable=False,
            )
        )

    # --- audit_logs: detail + ip, actor becomes nullable --------------------
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.add_column(sa.Column("detail", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("ip_address", sa.String(length=64), nullable=True))
        batch_op.alter_column(
            "actor_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=True,
        )

    # Backfill a Patient row for any pre-existing user that lacks one
    # (legacy rows created before patient profiles were mandatory).
    if _is_sqlite():
        id_expr = "lower(hex(randomblob(16)))"
    else:
        id_expr = "gen_random_uuid()"
    op.execute(
        f"""
        INSERT INTO patients (id, user_id, age, condition, risk_score, status)
        SELECT {id_expr}, u.id, NULL, NULL, NULL, 'Active'
        FROM users u
        LEFT JOIN patients p ON p.user_id = u.id
        WHERE u.role = 'patient' AND p.id IS NULL
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column(
            "actor_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=False,
        )
        batch_op.drop_column("ip_address")
        batch_op.drop_column("detail")

    with op.batch_alter_table("bills") as batch_op:
        batch_op.drop_column("created_at")
        batch_op.drop_column("description")

    with op.batch_alter_table("appointments") as batch_op:
        batch_op.drop_column("reason")
        batch_op.drop_column("mode")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("created_at")
        batch_op.drop_column("department")

    with op.batch_alter_table("patients") as batch_op:
        batch_op.alter_column("risk_score", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("condition", existing_type=sa.String(length=255), nullable=False)
        batch_op.alter_column("age", existing_type=sa.Integer(), nullable=False)
