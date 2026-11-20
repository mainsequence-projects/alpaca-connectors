"""drop the published series instances

Signal weights, portfolio weights and portfolio values are Markets' own published series
(ADR 0031, ms-markets ADR 0060): this application publishes them to Markets and reads them back
from it, and keeps no table of them. Their history is published to Markets and verified with
``msm series publish`` before the release of this revision is pushed (ADR 0031, Rollout); this
revision then drops the three instances revision 0020 created and revision 0026 indexed.

ms-markets 4.2.0 also gives every ``PortfolioRebalanceStateTS`` instance the
``(portfolio_identifier, time_index)`` index of its core table (ms-markets issue 96), which
``delete_private_series`` and a ``Portfolio`` delete's ``RESTRICT`` check use; this revision adds
it to this application's rebalance-state instance.

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-20 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: Union[str, Sequence[str], None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SIGNAL_WEIGHTS_TABLE = "alpaca_connectors__signalweightsts__main"
PORTFOLIOS_TABLE = "alpaca_connectors__portfoliosts__main"
PORTFOLIO_WEIGHTS_TABLE = "alpaca_connectors__portfolioweightsts__main"
REBALANCE_STATE_TABLE = "alpaca_connectors__portfoliorebalancestatets__main"
# Revision 0020's unique indexes and revision 0026's series lookup indexes.
SIGNAL_WEIGHTS_INDEX = "uix__alpaca_connectors__signalweightsts__main__time_2174cdf9f7"
PORTFOLIOS_INDEX = "uix__alpaca_connectors__portfoliosts__main__time_ind_9ea4da217a"
PORTFOLIO_WEIGHTS_INDEX = "uix__alpaca_connectors__portfolioweightsts__main__ti_4114ea45c3"
SIGNAL_SERIES_INDEX = "ix__alpaca_connectors__signalweightsts__main__signal_c6c7ae0417"
PORTFOLIO_SERIES_INDEX = "ix__alpaca_connectors__portfolioweightsts__main__por_0b62811094"
# Named from the columns by the naming convention, as ms-markets 4.2.0 declares it.
REBALANCE_STATE_PORTFOLIO_INDEX = "ix__alpaca_connectors__portfoliorebalancestatets__ma_86e626943e"
INFO = {"namespace": "alpaca-connectors", "markets_storage_app": "alpaca_connectors"}


def upgrade() -> None:
    """Drop the three instances, their rows with them, and index the rebalance state."""
    op.drop_index(SIGNAL_SERIES_INDEX, table_name=SIGNAL_WEIGHTS_TABLE)
    op.drop_index(SIGNAL_WEIGHTS_INDEX, table_name=SIGNAL_WEIGHTS_TABLE)
    op.drop_table(SIGNAL_WEIGHTS_TABLE)
    op.drop_index(PORTFOLIO_SERIES_INDEX, table_name=PORTFOLIO_WEIGHTS_TABLE)
    op.drop_index(PORTFOLIO_WEIGHTS_INDEX, table_name=PORTFOLIO_WEIGHTS_TABLE)
    op.drop_table(PORTFOLIO_WEIGHTS_TABLE)
    op.drop_index(PORTFOLIOS_INDEX, table_name=PORTFOLIOS_TABLE)
    op.drop_table(PORTFOLIOS_TABLE)
    op.create_index(
        REBALANCE_STATE_PORTFOLIO_INDEX,
        REBALANCE_STATE_TABLE,
        ["portfolio_identifier", "time_index"],
        unique=False,
    )


def downgrade() -> None:
    """Recreate the three instances empty, as revisions 0020 and 0026 left them.

    Their rows are not restored: the history is in Markets' series.
    """
    op.drop_index(REBALANCE_STATE_PORTFOLIO_INDEX, table_name=REBALANCE_STATE_TABLE)
    op.create_table(
        SIGNAL_WEIGHTS_TABLE,
        sa.Column("time_index", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_uid", sa.String(length=255), nullable=False),
        sa.Column("asset_identifier", sa.String(length=255), nullable=False),
        sa.Column("signal_weight", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_identifier"],
            ["ms_markets__asset.unique_identifier"],
            name=op.f("fk__alpaca_connectors__signalweightsts__main__asset_5348d6649d"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signal_uid"],
            ["ms_markets__signalmetadata.signal_uid"],
            name=op.f("fk__alpaca_connectors__signalweightsts__main__signal_59dc0c4aa1"),
            ondelete="RESTRICT",
        ),
        info={**INFO, "identifier": "alpaca-connectors.SignalWeightsTS.main"},
    )
    op.create_index(
        SIGNAL_WEIGHTS_INDEX,
        SIGNAL_WEIGHTS_TABLE,
        ["time_index", "signal_uid", "asset_identifier"],
        unique=True,
    )
    op.create_index(SIGNAL_SERIES_INDEX, SIGNAL_WEIGHTS_TABLE, ["signal_uid", "time_index"])
    op.create_table(
        PORTFOLIOS_TABLE,
        sa.Column("time_index", sa.DateTime(timezone=True), nullable=False),
        sa.Column("portfolio_identifier", sa.String(length=255), nullable=False),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("return", sa.Float(), nullable=True),
        sa.Column("calculated_close", sa.Float(), nullable=True),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["portfolio_identifier"],
            ["ms_markets__portfolio.unique_identifier"],
            name=op.f("fk__alpaca_connectors__portfoliosts__main__portfolio_8e2c225d66"),
            ondelete="RESTRICT",
        ),
        info={**INFO, "identifier": "alpaca-connectors.PortfoliosTS.main"},
    )
    op.create_index(
        PORTFOLIOS_INDEX, PORTFOLIOS_TABLE, ["time_index", "portfolio_identifier"], unique=True
    )
    op.create_table(
        PORTFOLIO_WEIGHTS_TABLE,
        sa.Column("time_index", sa.DateTime(timezone=True), nullable=False),
        sa.Column("portfolio_identifier", sa.String(length=255), nullable=False),
        sa.Column("asset_identifier", sa.String(length=255), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("weight_before", sa.Float(), nullable=True),
        sa.Column("price_current", sa.Float(), nullable=True),
        sa.Column("price_before", sa.Float(), nullable=True),
        sa.Column("volume_current", sa.Float(), nullable=True),
        sa.Column("volume_before", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_identifier"],
            ["ms_markets__asset.unique_identifier"],
            name=op.f("fk__alpaca_connectors__portfolioweightsts__main__ass_323237e07c"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_identifier"],
            ["ms_markets__portfolio.unique_identifier"],
            name=op.f("fk__alpaca_connectors__portfolioweightsts__main__por_79faf4e534"),
            ondelete="RESTRICT",
        ),
        info={**INFO, "identifier": "alpaca-connectors.PortfolioWeightsTS.main"},
    )
    op.create_index(
        PORTFOLIO_WEIGHTS_INDEX,
        PORTFOLIO_WEIGHTS_TABLE,
        ["time_index", "portfolio_identifier", "asset_identifier"],
        unique=True,
    )
    op.create_index(
        PORTFOLIO_SERIES_INDEX, PORTFOLIO_WEIGHTS_TABLE, ["portfolio_identifier", "time_index"]
    )
