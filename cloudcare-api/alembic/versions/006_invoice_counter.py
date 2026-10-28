"""invoice numbering: one locked counter row instead of max+1 scans

Revision ID: 006
Revises: 005
Create Date: 2026-10-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoice_counters",
        sa.Column("name", sa.String(length=50), primary_key=True),
        sa.Column("value", sa.Integer(), nullable=False),
    )
    op.execute(
        "INSERT INTO invoice_counters (name, value) VALUES ('invoice_no', 1000)"
    )


def downgrade() -> None:
    op.drop_table("invoice_counters")
