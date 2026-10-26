"""add namespaced interpolated prices

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-26 12:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the interpolated-price tables of revision 0011 in the project namespace (ADR 0047).

    Revision 0011 created the persistent ``InterpolatedPrices`` storages of the two bars
    profiles as ``ms_markets__interpolated_prices__t_<suffix>`` in ``mainsequence.markets``.
    ``configured_alpaca_interpolated_prices_storage`` now places them in ``alpaca-connectors``
    with the ``alpaca_connectors`` app segment; the storage identity, and so the suffix, is
    unchanged. The 0011 tables keep their rows until a later revision drops them;
    ``alpaca-connectors instances backfill`` copies their rows here.
    """
    op.create_table(
        "alpaca_connectors__interpolated_prices__t_0e9255594b348d8c",
        sa.Column("time_index", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_identifier", sa.String(length=255), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("trade_count", sa.Float(), nullable=True),
        sa.Column("vwap", sa.Float(), nullable=True),
        sa.Column("interpolated", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_identifier"],
            ["ms_markets__asset.unique_identifier"],
            name=op.f("fk__alpaca_connectors__interpolated_prices__t_0e9255_14f77fba53"),
            ondelete="RESTRICT",
        ),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.InterpolatedPricesTS.0e9255594b348d8c",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    op.create_index(
        "uix__alpaca_connectors__interpolated_prices__t_0e925_7a3fed130a",
        "alpaca_connectors__interpolated_prices__t_0e9255594b348d8c",
        ["time_index", "asset_identifier"],
        unique=True,
    )
    op.create_table(
        "alpaca_connectors__interpolated_prices__t_43b7a7a637ae4df3",
        sa.Column("time_index", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_identifier", sa.String(length=255), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("trade_count", sa.Float(), nullable=True),
        sa.Column("vwap", sa.Float(), nullable=True),
        sa.Column("interpolated", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_identifier"],
            ["ms_markets__asset.unique_identifier"],
            name=op.f("fk__alpaca_connectors__interpolated_prices__t_43b7a7_95ef16fca8"),
            ondelete="RESTRICT",
        ),
        info={
            "namespace": "alpaca-connectors",
            "identifier": "alpaca-connectors.InterpolatedPricesTS.43b7a7a637ae4df3",
            "markets_storage_app": "alpaca_connectors",
        },
    )
    op.create_index(
        "uix__alpaca_connectors__interpolated_prices__t_43b7a_b9d19c2bca",
        "alpaca_connectors__interpolated_prices__t_43b7a7a637ae4df3",
        ["time_index", "asset_identifier"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the namespaced interpolated-price tables; the 0011 tables are untouched."""
    op.drop_index(
        "uix__alpaca_connectors__interpolated_prices__t_43b7a_b9d19c2bca",
        table_name="alpaca_connectors__interpolated_prices__t_43b7a7a637ae4df3",
    )
    op.drop_table("alpaca_connectors__interpolated_prices__t_43b7a7a637ae4df3")
    op.drop_index(
        "uix__alpaca_connectors__interpolated_prices__t_0e925_7a3fed130a",
        table_name="alpaca_connectors__interpolated_prices__t_0e9255594b348d8c",
    )
    op.drop_table("alpaca_connectors__interpolated_prices__t_0e9255594b348d8c")
