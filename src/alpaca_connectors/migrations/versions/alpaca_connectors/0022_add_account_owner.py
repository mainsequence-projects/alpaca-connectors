"""add account owner

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-07 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__acct_alpaca"
OWNER_INDEX = "ix__alpaca_connectors__acct_alpaca__owner_gateway_ui_4fc75be3f7"


def upgrade() -> None:
    """Add the account owner pair and fill it from the key author (ADR 0019).

    Both columns are nullable: the release still serving during the rollout inserts rows without
    them. Every row whose key author is known takes that author as its owner, `(uid, uid)`, a
    workload User included (decided by the owner on 2026-11-07); a row without an author keeps a
    null owner until it is registered again. Operators review the hosted owners once.
    """
    op.add_column(TABLE_NAME, sa.Column("owner_gateway_uid", sa.String(length=64), nullable=True))
    op.add_column(TABLE_NAME, sa.Column("owner_user_uid", sa.String(length=255), nullable=True))
    op.create_index(OWNER_INDEX, TABLE_NAME, ["owner_gateway_uid", "owner_user_uid"])
    op.execute(
        sa.text(
            f"UPDATE {TABLE_NAME} "
            "SET owner_gateway_uid = credentials_updated_by_user_uid, "
            "owner_user_uid = credentials_updated_by_user_uid "
            "WHERE owner_user_uid IS NULL AND credentials_updated_by_user_uid IS NOT NULL"
        )
    )


def downgrade() -> None:
    """Remove the account owner pair."""
    op.drop_index(OWNER_INDEX, table_name=TABLE_NAME)
    op.drop_column(TABLE_NAME, "owner_user_uid")
    op.drop_column(TABLE_NAME, "owner_gateway_uid")
