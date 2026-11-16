"""add weights series lookup indexes

ms-markets 3.8.1 declares ``(signal_uid, time_index)`` on ``SignalWeightsStorage`` and
``(portfolio_identifier, time_index)`` on ``PortfolioWeightsStorage``, and every instance built
with ``configured_signal_weights_storage`` and ``configured_portfolio_weights_storage`` declares
the same index (MainSequenceMarkets#77). ms-markets revision 0026 migrates only the Markets-owned
tables; this revision adds the indexes to this application's two weights instances. They are not
unique and change no row.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-16 17:15:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: Union[str, Sequence[str], None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SIGNAL_WEIGHTS_TABLE = "alpaca_connectors__signalweightsts__main"
PORTFOLIO_WEIGHTS_TABLE = "alpaca_connectors__portfolioweightsts__main"
SIGNAL_SERIES_INDEX = "ix__alpaca_connectors__signalweightsts__main__signal_c6c7ae0417"
PORTFOLIO_SERIES_INDEX = "ix__alpaca_connectors__portfolioweightsts__main__por_0b62811094"


def upgrade() -> None:
    op.create_index(SIGNAL_SERIES_INDEX, SIGNAL_WEIGHTS_TABLE, ["signal_uid", "time_index"])
    op.create_index(
        PORTFOLIO_SERIES_INDEX, PORTFOLIO_WEIGHTS_TABLE, ["portfolio_identifier", "time_index"]
    )


def downgrade() -> None:
    op.drop_index(PORTFOLIO_SERIES_INDEX, table_name=PORTFOLIO_WEIGHTS_TABLE)
    op.drop_index(SIGNAL_SERIES_INDEX, table_name=SIGNAL_WEIGHTS_TABLE)
