"""booking/reviews/billing: doctor shifts + leaves, specialties, reviews,
billing details, appointment statuses (Requested/Confirmed/Declined/Cancelled)
with an active-slot unique index, user token_version

Revision ID: 003
Revises: 002
Create Date: 2026-10-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _id_expr() -> str:
    return "lower(hex(randomblob(16)))" if _is_sqlite() else "gen_random_uuid()"


def upgrade() -> None:
    # --- doctor_shifts ------------------------------------------------------
    op.create_table(
        "doctor_shifts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "doctor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("doctor_id", "name", name="uq_doctor_shifts_doctor_name"),
    )

    # --- doctor_leaves ------------------------------------------------------
    op.create_table(
        "doctor_leaves",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "doctor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("leave_date", sa.Date(), nullable=False),
        sa.Column("shift_name", sa.String(length=20), nullable=True),
    )

    # --- specialties --------------------------------------------------------
    op.create_table(
        "specialties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.UniqueConstraint("name", name="uq_specialties_name"),
    )

    # --- reviews ------------------------------------------------------------
    op.create_table(
        "reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doctor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("author_id", "doctor_id", name="uq_reviews_author_doctor"),
    )

    # --- billing_details ----------------------------------------------------
    op.create_table(
        "billing_details",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("billing_address", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("payment_method", sa.String(length=50), nullable=False, server_default="Not set"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="Default"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", name="uq_billing_details_user_id"),
    )

    # --- appointments: duration + status remap + active-slot index ----------
    with op.batch_alter_table("appointments") as batch_op:
        batch_op.add_column(
            sa.Column(
                "duration_minutes",
                sa.Integer(),
                nullable=False,
                server_default="30",
            )
        )

    # Remap legacy statuses onto the four spec statuses. Anything unknown
    # becomes Requested so no appointment is stranded outside the lifecycle.
    op.execute("UPDATE appointments SET status='Requested' WHERE status='Scheduled'")
    op.execute(
        "UPDATE appointments SET status='Confirmed' "
        "WHERE status IN ('Waiting', 'Checked in', 'Completed')"
    )
    op.execute("UPDATE appointments SET status='Cancelled' WHERE status='Cancelled'")
    op.execute(
        "UPDATE appointments SET status='Requested' "
        "WHERE status NOT IN ('Requested', 'Confirmed', 'Declined', 'Cancelled')"
    )

    # Atomic double-booking guard: one active appointment per doctor+start.
    op.create_index(
        "uq_appointments_active_slot",
        "appointments",
        ["doctor_id", "scheduled_at"],
        unique=True,
        postgresql_where=sa.text("status IN ('Requested', 'Confirmed')"),
        sqlite_where=sa.text("status IN ('Requested', 'Confirmed')"),
    )

    # --- users: token_version (server-side logout) ---------------------------
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "token_version",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )

    # --- seed data -----------------------------------------------------------
    # Specialties from the departments already in use.
    op.execute(
        f"""
        INSERT INTO specialties (id, name)
        SELECT DISTINCT {_id_expr()}, department FROM users
        WHERE role = 'doctor' AND department IS NOT NULL AND department != ''
        """
    )
    # Default shifts (Morning 06:00-14:00, Evening 14:00-22:00) for every doctor.
    op.execute(
        f"""
        INSERT INTO doctor_shifts (id, doctor_id, name, start_time, end_time, is_active)
        SELECT {_id_expr()}, u.id, 'Morning', '06:00:00', '14:00:00', 1
        FROM users u WHERE u.role = 'doctor'
        """
    )
    op.execute(
        f"""
        INSERT INTO doctor_shifts (id, doctor_id, name, start_time, end_time, is_active)
        SELECT {_id_expr()}, u.id, 'Evening', '14:00:00', '22:00:00', 1
        FROM users u WHERE u.role = 'doctor'
        """
    )
    # A default billing record for every user that has none (backfill).
    op.execute(
        f"""
        INSERT INTO billing_details (id, user_id, full_name, email, billing_address,
                                     payment_method, currency, status)
        SELECT {_id_expr()}, u.id, u.name, u.email, '', 'Not set', 'INR', 'Default'
        FROM users u
        LEFT JOIN billing_details b ON b.user_id = u.id
        WHERE b.id IS NULL
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("token_version")

    op.drop_index("uq_appointments_active_slot", table_name="appointments")
    with op.batch_alter_table("appointments") as batch_op:
        batch_op.drop_column("duration_minutes")

    op.drop_table("billing_details")
    op.drop_table("reviews")
    op.drop_table("specialties")
    op.drop_table("doctor_leaves")
    op.drop_table("doctor_shifts")
