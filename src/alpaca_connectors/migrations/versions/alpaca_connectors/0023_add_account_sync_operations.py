"""add account sync operations

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-09 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__account_sync_operation"
INDEXES = (
    (
        "ix__alpaca_connectors__account_sync_operation__owner_94709ab20e",
        ["owner_gateway_uid", "owner_user_uid"],
    ),
    ("ix__alpaca_connectors__account_sync_operation__account_uid", ["account_uid"]),
    ("ix__alpaca_connectors__account_sync_operation__created_at", ["created_at"]),
)


def upgrade() -> None:
    """Add the durable records of account syncs (issue #14).

    A new table only, so the release still serving during the rollout is unaffected.
    """
    op.create_table(
        TABLE_NAME,
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("owner_gateway_uid", sa.String(length=64), nullable=False),
        sa.Column("owner_user_uid", sa.String(length=255), nullable=False),
        sa.Column("account_uid", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_step", sa.String(length=64), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("uid", name=op.f(f"pk__{TABLE_NAME}")),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.AccountSyncOperation",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    for name, columns in INDEXES:
        op.create_index(op.f(name), TABLE_NAME, columns, unique=False)


def downgrade() -> None:
    """Remove the account sync records."""
    for name, _columns in reversed(INDEXES):
        op.drop_index(op.f(name), table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
