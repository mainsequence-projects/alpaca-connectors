"""rebuild Alpaca bars with open time

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-07 11:20:35.756811

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the required bar-open timestamp and rebuild source observations."""
    table_names = (
        "alpaca_connectors__bars_1d_iex_raw",
        "alpaca_connectors__bars_1d_sip_all",
    )
    for table_name in table_names:
        # The existing observations were published before open_time was part of
        # the source-bar contract. Clear only these reproducible provider bars
        # so their configured DataNodes fetch and publish complete rows again.
        op.add_column(
            table_name,
            sa.Column("open_time", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(sa.text(f"DELETE FROM {table_name}"))
        op.alter_column(
            table_name,
            "open_time",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )


def downgrade() -> None:
    """Remove the bar-open timestamp; deleted observations cannot be restored."""
    for table_name in (
        "alpaca_connectors__bars_1d_sip_all",
        "alpaca_connectors__bars_1d_iex_raw",
    ):
        op.drop_column(table_name, "open_time")
