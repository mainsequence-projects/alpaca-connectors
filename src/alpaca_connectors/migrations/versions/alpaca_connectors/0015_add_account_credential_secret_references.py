"""add account credential secret references

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-03 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    table_name = "alpaca_connectors__acct_alpaca"
    op.add_column(
        table_name,
        sa.Column("api_key_secret_uid", sa.String(length=64), nullable=True),
    )
    op.add_column(
        table_name,
        sa.Column("secret_key_secret_uid", sa.String(length=64), nullable=True),
    )
    # Every registration that predates ADR 0011 selected existing Secrets by name, and so does
    # every insert from the previous release, which keeps serving until this revision's code
    # deploys.  The default labels both correctly; the current code always states its source.
    op.add_column(
        table_name,
        sa.Column(
            "credential_source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'external'"),
        ),
    )
    op.add_column(
        table_name,
        sa.Column("credentials_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        table_name,
        sa.Column("credentials_updated_by_user_uid", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    table_name = "alpaca_connectors__acct_alpaca"
    op.drop_column(table_name, "credentials_updated_by_user_uid")
    op.drop_column(table_name, "credentials_updated_at")
    op.drop_column(table_name, "credential_source")
    op.drop_column(table_name, "secret_key_secret_uid")
    op.drop_column(table_name, "api_key_secret_uid")
