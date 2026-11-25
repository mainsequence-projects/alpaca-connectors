"""This application's private instances of Markets time-series contracts (ms-markets ADR 0047).

Each class below is a contract-identical instance, declared with a class statement on its
contract's base and ``ProjectBase`` (ms-markets ADR 0066), in the ``alpaca-connectors`` namespace,
and migrated by this application's own Alembic provider. A node in another process imports each
one by its ``module:qualname`` (ms-markets ADR 0067), so every instance is a top-level class here,
never one a function builds. The instances of key ``main`` are named
``alpaca_connectors__<contract>__main`` and identified as ``alpaca-connectors.<Contract>.main``; the
interpolated-price tables are keyed by their configuration (ADR 0029). A class has the table the
ms-markets factory built before 6.2.0: the same physical name, identifier and DDL.

They are private, and Markets never reads them (ADR 0031, decision 2): rebalance state, calendar
events and the event ledger, which a portfolio run writes (``private_portfolio_models``); orders'
states, their events and trades, which the execution ledger writes (ms-markets ADR 0064: they name
their order by its Markets ``Order`` row); and one interpolated-price table per bars profile, which
values the portfolios (``INTERPOLATED_PRICES_BY_PROFILE``). Signal weights, portfolio weights and
portfolio values are Markets' own published series (ADR 0031, decision 1); revision 0033 dropped
this application's tables of them.
"""

from __future__ import annotations

from typing import Any

from msm.time_index.execution.storage import OrderEventsInstance, OrdersInstance, TradesInstance
from msm_portfolios.time_index.contract_instances import (
    PortfolioCalendarEventsInstance,
    PortfolioEventLedgerInstance,
    PortfolioRebalanceStateInstance,
)
from msm_portfolios.time_index.prices.storage import (
    InterpolatedPricesInstance,
    interpolated_prices_instance_key,
)

from alpaca_connectors.base import ProjectBase
from alpaca_connectors.market_data.storage import (
    AlpacaStockBars1dIexRawStorage,
    AlpacaStockBars1dSipAllStorage,
)
from alpaca_connectors.settings import PROJECT_NAMESPACE, PROJECT_NAMESPACE_SLUG

INSTANCE_KEY = "main"
INSTANCE_NAMESPACE = PROJECT_NAMESPACE

# A portfolio configuration stores these two values and refuses any other (ADR 0024).
INTERPOLATION_UPSAMPLE_FREQUENCY = "1d"
INTERPOLATION_RULE = "ffill"


class AlpacaPortfolioRebalanceStateStorage(PortfolioRebalanceStateInstance, ProjectBase):
    __instance_key__ = INSTANCE_KEY
    __metatable_namespace__ = INSTANCE_NAMESPACE
    __markets_storage_app__ = PROJECT_NAMESPACE_SLUG


class AlpacaPortfolioCalendarEventsStorage(PortfolioCalendarEventsInstance, ProjectBase):
    __instance_key__ = INSTANCE_KEY
    __metatable_namespace__ = INSTANCE_NAMESPACE
    __markets_storage_app__ = PROJECT_NAMESPACE_SLUG


class AlpacaPortfolioEventLedgerStorage(PortfolioEventLedgerInstance, ProjectBase):
    """Each portfolio's ledger, which ms-markets' ``PortfolioEngine`` books (ms-markets ADR
    0065); its published weights and values are projections of it. Revision 0037 creates it."""

    __instance_key__ = INSTANCE_KEY
    __metatable_namespace__ = INSTANCE_NAMESPACE
    __markets_storage_app__ = PROJECT_NAMESPACE_SLUG


class AlpacaOrdersStorage(OrdersInstance, ProjectBase):
    __instance_key__ = INSTANCE_KEY
    __metatable_namespace__ = INSTANCE_NAMESPACE
    __markets_storage_app__ = PROJECT_NAMESPACE_SLUG


class AlpacaOrderEventsStorage(OrderEventsInstance, ProjectBase):
    __instance_key__ = INSTANCE_KEY
    __metatable_namespace__ = INSTANCE_NAMESPACE
    __markets_storage_app__ = PROJECT_NAMESPACE_SLUG


class AlpacaTradesStorage(TradesInstance, ProjectBase):
    __instance_key__ = INSTANCE_KEY
    __metatable_namespace__ = INSTANCE_NAMESPACE
    __markets_storage_app__ = PROJECT_NAMESPACE_SLUG


def _interpolated_prices_key(bars: type[Any]) -> str:
    """ms-markets keys the table by its configuration: the bars table's physical name and
    cadence, the upsample frequency and the rule (issue 95). The physical name is the same in
    every Environment, so one committed revision (0034) creates these tables everywhere."""
    return interpolated_prices_instance_key(
        source_table_name=bars.__table__.name,
        source_cadence=bars.__cadence__,
        upsample_frequency_id=INTERPOLATION_UPSAMPLE_FREQUENCY,
        intraday_bar_interpolation_rule=INTERPOLATION_RULE,
    )


class AlpacaInterpolatedPrices1dSipAllStorage(InterpolatedPricesInstance, ProjectBase):
    """The interpolated prices of the 1d SIP bars, adjusted for every event."""

    __instance_key__ = _interpolated_prices_key(AlpacaStockBars1dSipAllStorage)
    __metatable_namespace__ = INSTANCE_NAMESPACE
    __markets_storage_app__ = PROJECT_NAMESPACE_SLUG
    __cadence__ = INTERPOLATION_UPSAMPLE_FREQUENCY


class AlpacaInterpolatedPrices1dIexRawStorage(InterpolatedPricesInstance, ProjectBase):
    """The interpolated prices of the 1d IEX bars, unadjusted."""

    __instance_key__ = _interpolated_prices_key(AlpacaStockBars1dIexRawStorage)
    __metatable_namespace__ = INSTANCE_NAMESPACE
    __markets_storage_app__ = PROJECT_NAMESPACE_SLUG
    __cadence__ = INTERPOLATION_UPSAMPLE_FREQUENCY


INTERPOLATED_PRICES_BY_PROFILE: dict[tuple[str, str, str], type[Any]] = {
    ("1d", "iex", "raw"): AlpacaInterpolatedPrices1dIexRawStorage,
    ("1d", "sip", "all"): AlpacaInterpolatedPrices1dSipAllStorage,
}
"""The interpolated-price table of each ``(frequency_id, feed, adjustment)`` bars profile.

``InterpolatedPrices`` refuses any other class as its ``output_table``. A new bars profile gets its
class here and its table in the revision that adds its bars table."""

_INSTANCES: tuple[type[Any], ...] = (
    AlpacaPortfolioRebalanceStateStorage,
    AlpacaPortfolioCalendarEventsStorage,
    AlpacaPortfolioEventLedgerStorage,
    AlpacaOrdersStorage,
    AlpacaOrderEventsStorage,
    AlpacaTradesStorage,
)


def project_instance_models() -> list[type[Any]]:
    """Every contract instance this application migrates, registers and attaches."""
    return [*_INSTANCES, *INTERPOLATED_PRICES_BY_PROFILE.values()]


def private_portfolio_models() -> list[type[Any]]:
    """The private instances a portfolio run writes: rebalance state, calendar events, the event
    ledger and the interpolated prices it values with."""
    return [
        AlpacaPortfolioRebalanceStateStorage,
        AlpacaPortfolioCalendarEventsStorage,
        AlpacaPortfolioEventLedgerStorage,
        *INTERPOLATED_PRICES_BY_PROFILE.values(),
    ]


def execution_instance_models() -> list[type[Any]]:
    """The instances the execution ledger writes with ``write_execution``, which takes all three."""
    return [AlpacaOrdersStorage, AlpacaOrderEventsStorage, AlpacaTradesStorage]


__all__ = [
    "INSTANCE_KEY",
    "INSTANCE_NAMESPACE",
    "INTERPOLATED_PRICES_BY_PROFILE",
    "INTERPOLATION_RULE",
    "INTERPOLATION_UPSAMPLE_FREQUENCY",
    "AlpacaInterpolatedPrices1dIexRawStorage",
    "AlpacaInterpolatedPrices1dSipAllStorage",
    "AlpacaOrderEventsStorage",
    "AlpacaOrdersStorage",
    "AlpacaPortfolioCalendarEventsStorage",
    "AlpacaPortfolioEventLedgerStorage",
    "AlpacaPortfolioRebalanceStateStorage",
    "AlpacaTradesStorage",
    "execution_instance_models",
    "private_portfolio_models",
    "project_instance_models",
]
