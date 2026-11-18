"""drop the account secret reference columns

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-18 15:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: Union[str, Sequence[str], None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "alpaca_connectors__acct_alpaca"
# The Secret reference columns of ADR 0011 (revisions 0002 and 0015), superseded by ADR 0017.
NAME_COLUMNS = ("api_key_secret_name", "secret_key_secret_name")
UID_COLUMNS = ("api_key_secret_uid", "secret_key_secret_uid")
NAME_INDEXES = {column: f"ix__{TABLE_NAME}__{column}" for column in NAME_COLUMNS}
# The default revision 0018 inherited for rows that selected Secrets by name.
TRANSITION_SOURCE_DEFAULT = "'external'"


def refuse_while_keys_live_in_secrets() -> None:
    """Refuse while a registration's keys are still only in the Secrets these columns name.

    The credential migration copied every registration's keys and cleared its references, and
    every write since revision 0018 stores keys and names no Secret. A row with references and no
    stored keys was never migrated: dropping the columns would lose the only way to its keys.
    """
    bind = op.get_bind()
    count = int(
        bind.execute(
            sa.text(
                f"SELECT COUNT(*) FROM {TABLE_NAME} "
                "WHERE (api_key IS NULL OR secret_key IS NULL) AND ("
                + " OR ".join(f"{column} IS NOT NULL" for column in (*NAME_COLUMNS, *UID_COLUMNS))
                + ")"
            )
        ).scalar()
        or 0
    )
    if count:
        raise RuntimeError(
            f"Revision 0030 refuses to run while {count} account registration(s) keep their keys "
            "only in Main Sequence Secrets. Store their keys again (rotate them on the site), "
            "then deploy this release again (ADR 0017, issue #13)."
        )


def upgrade() -> None:
    """Drop the four Secret reference columns and the transition default (issue #13).

    The release before this one maps none of them, so nothing reads or writes them. Every write
    names ``credential_source``, which loses its ``'external'`` default.
    """
    refuse_while_keys_live_in_secrets()
    for index in NAME_INDEXES.values():
        op.drop_index(index, table_name=TABLE_NAME, if_exists=True)
    for column in (*NAME_COLUMNS, *UID_COLUMNS):
        op.drop_column(TABLE_NAME, column)
    op.alter_column(
        TABLE_NAME,
        "credential_source",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        server_default=None,
    )


def downgrade() -> None:
    """Add the columns back, empty and nullable, with their indexes and the old default."""
    op.alter_column(
        TABLE_NAME,
        "credential_source",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        server_default=sa.text(TRANSITION_SOURCE_DEFAULT),
    )
    for column in NAME_COLUMNS:
        op.add_column(TABLE_NAME, sa.Column(column, sa.String(length=255), nullable=True))
    for column in UID_COLUMNS:
        op.add_column(TABLE_NAME, sa.Column(column, sa.String(length=64), nullable=True))
    for column, index in NAME_INDEXES.items():
        op.create_index(index, TABLE_NAME, [column], unique=False)
