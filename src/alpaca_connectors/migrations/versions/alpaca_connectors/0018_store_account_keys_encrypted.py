"""store account keys encrypted

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-24 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__acct_alpaca"
LEGACY_SECRET_NAME_COLUMNS = ("api_key_secret_name", "secret_key_secret_name")


def upgrade() -> None:
    """Add the application-encrypted key columns (ADR 0017, MetaTables ADR 0025).

    ``api_key`` and ``secret_key`` are ``EncryptedString`` columns on the model and plain TEXT in
    the database; the application seals their values before they leave the process, so this
    revision needs no access to the key Secret. They are nullable until the credential migration
    Job has copied every registration's keys out of its Main Sequence Secrets. The legacy Secret
    name columns become nullable so that releases from this revision on can register an account
    without naming a Secret; the release still serving during the rollout keeps writing them. The
    legacy name and UID columns are dropped by a later revision, after every environment has run
    the credential migration.
    """
    op.add_column(TABLE_NAME, sa.Column("api_key", sa.Text(), nullable=True))
    op.add_column(TABLE_NAME, sa.Column("secret_key", sa.Text(), nullable=True))
    for column in LEGACY_SECRET_NAME_COLUMNS:
        op.alter_column(
            TABLE_NAME,
            column,
            existing_type=sa.String(length=255),
            nullable=True,
        )


def downgrade() -> None:
    """Remove the encrypted key columns; the legacy Secret name columns stay nullable.

    Restoring NOT NULL would fail on every registration the credential migration has emptied,
    so the downgrade leaves the names nullable.
    """
    op.drop_column(TABLE_NAME, "secret_key")
    op.drop_column(TABLE_NAME, "api_key")
