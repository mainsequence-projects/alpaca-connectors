"""add account key requests

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-08 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__account_key_request"
INDEXED_COLUMNS = ("expires_at", "requester_uid", "status")


def upgrade() -> None:
    """Add the requests for a person to enter Alpaca keys on the Alpaca site.

    A new table only, so the release still serving during the rollout is unaffected.
    """
    op.create_table(
        TABLE_NAME,
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("requester_uid", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("environment", sa.String(length=8), nullable=False),
        sa.Column("account_uid", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("uid", name=op.f(f"pk__{TABLE_NAME}")),
        info={
            "namespace": "mainsequence.markets",
            "identifier": "AccountKeyRequest",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    for column in INDEXED_COLUMNS:
        op.create_index(op.f(f"ix__{TABLE_NAME}__{column}"), TABLE_NAME, [column], unique=False)


def downgrade() -> None:
    """Remove the account key requests."""
    for column in reversed(INDEXED_COLUMNS):
        op.drop_index(op.f(f"ix__{TABLE_NAME}__{column}"), table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
