"""This application's private instances of Markets time-series contracts (ms-markets ADR 0047).

Each class below is a contract-identical instance built by an ms-markets factory on
``ProjectBase``, in the ``alpaca-connectors`` namespace, and migrated by this application's own
Alembic provider. The four of key ``main`` are named ``alpaca_connectors__<contract>__main`` and
identified as ``alpaca-connectors.<Contract>.main``; the interpolated-price tables are keyed by
their configuration (ADR 0029).

They are private, and Markets never reads them (ADR 0031, decision 2): rebalance state and
calendar events, which a portfolio run writes (``private_portfolio_models``); orders' states,
their events and trades, which the execution ledger writes (ms-markets ADR 0064: they name their
order by its Markets ``Order`` row); and one interpolated-price table per bars profile, which values
the portfolios (``INTERPOLATED_PRICES_BY_PROFILE``). Signal weights, portfolio weights and
portfolio values are Markets' own published series (ADR 0031, decision 1); revision 0033 dropped
this application's tables of them.
"""

from __future__ import annotations

from typing import Any

from alpaca_connectors.base import ProjectBase
from alpaca_connectors.settings import PROJECT_NAMESPACE, PROJECT_NAMESPACE_SLUG

INSTANCE_KEY = "main"
INSTANCE_NAMESPACE = PROJECT_NAMESPACE


def _instance_kwargs() -> dict[str, Any]:
    """Factory arguments of every instance: this namespace, app segment and declarative base.

    ms-markets 5.0.0 requires all three (issue 95). The instances key to Markets' registries by
    Markets' own columns, so the provider's metadata, ``ProjectBase.metadata``, holds only this
    application's tables. Their names, identifiers and DDL are the ones revisions 0020 and 0026
    created.
    """
    return {
        "instance_key": INSTANCE_KEY,
        "namespace": INSTANCE_NAMESPACE,
        "storage_app": PROJECT_NAMESPACE_SLUG,
        "base": ProjectBase,
    }


def _build() -> dict[str, type[Any]]:
    from msm.storage_instances import configured_time_index_instance
    from msm.time_index.execution.storage import OrderEventsStorage, OrdersStorage, TradesStorage
    from msm_portfolios.time_index.contract_instances import (
        configured_portfolio_calendar_events_storage,
        configured_portfolio_rebalance_state_storage,
    )

    return {
        "portfolio_rebalance_state": configured_portfolio_rebalance_state_storage(
            **_instance_kwargs()
        ),
        "portfolio_calendar_events": configured_portfolio_calendar_events_storage(
            **_instance_kwargs()
        ),
        "orders": configured_time_index_instance(OrdersStorage, **_instance_kwargs()),
        "order_events": configured_time_index_instance(OrderEventsStorage, **_instance_kwargs()),
        "trades": configured_time_index_instance(TradesStorage, **_instance_kwargs()),
    }


_INSTANCES = _build()

AlpacaPortfolioRebalanceStateStorage = _INSTANCES["portfolio_rebalance_state"]
AlpacaPortfolioCalendarEventsStorage = _INSTANCES["portfolio_calendar_events"]
AlpacaOrdersStorage = _INSTANCES["orders"]
AlpacaOrderEventsStorage = _INSTANCES["order_events"]
AlpacaTradesStorage = _INSTANCES["trades"]


# A portfolio configuration stores these two values and refuses any other (ADR 0024).
INTERPOLATION_UPSAMPLE_FREQUENCY = "1d"
INTERPOLATION_RULE = "ffill"


def _build_interpolated_prices() -> dict[tuple[str, str, str], type[Any]]:
    """One ``InterpolatedPricesTS`` instance per bars profile (ADR 0029, ms-markets issue 95).

    ms-markets keys the table by its configuration: the bars table's physical name and cadence,
    the upsample frequency and the rule (``interpolated_prices_instance_key``). The physical name
    is the same in every Environment, so one committed revision (0034) creates these tables
    everywhere, and ``InterpolatedPrices`` refuses any other class as its ``output_table``. A
    new bars profile gets its interpolated-price table in the revision that adds its bars table.
    """
    from msm.storage_instances import configured_time_index_instance
    from msm_portfolios.time_index.prices.storage import (
        InterpolatedPricesStorage,
        interpolated_prices_instance_key,
    )

    from alpaca_connectors.market_data.storage import ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE

    return {
        profile: configured_time_index_instance(
            InterpolatedPricesStorage,
            instance_key=interpolated_prices_instance_key(
                source_table_name=bars.__table__.name,
                source_cadence=bars.__cadence__,
                upsample_frequency_id=INTERPOLATION_UPSAMPLE_FREQUENCY,
                intraday_bar_interpolation_rule=INTERPOLATION_RULE,
            ),
            cadence=INTERPOLATION_UPSAMPLE_FREQUENCY,
            namespace=INSTANCE_NAMESPACE,
            storage_app=PROJECT_NAMESPACE_SLUG,
            base=ProjectBase,
        )
        for profile, bars in sorted(ALPACA_STOCK_BARS_STORAGE_BY_TRIPLE.items())
    }


INTERPOLATED_PRICES_BY_PROFILE = _build_interpolated_prices()
"""The interpolated-price table of each ``(frequency_id, feed, adjustment)`` bars profile."""


def project_instance_models() -> list[type[Any]]:
    """Every contract instance this application migrates, registers and attaches."""
    return [*_INSTANCES.values(), *INTERPOLATED_PRICES_BY_PROFILE.values()]


def private_portfolio_models() -> list[type[Any]]:
    """The private instances a portfolio run writes: rebalance state, calendar events and the
    interpolated prices it values with."""
    return [
        AlpacaPortfolioRebalanceStateStorage,
        AlpacaPortfolioCalendarEventsStorage,
        *INTERPOLATED_PRICES_BY_PROFILE.values(),
    ]


def execution_instance_models() -> list[type[Any]]:
    """The instances the execution ledger writes with ``write_execution``, which takes all three."""
    return [AlpacaOrdersStorage, AlpacaOrderEventsStorage, AlpacaTradesStorage]


__all__ = [
    "INSTANCE_KEY",
    "INSTANCE_NAMESPACE",
    "AlpacaOrderEventsStorage",
    "AlpacaOrdersStorage",
    "AlpacaPortfolioCalendarEventsStorage",
    "AlpacaPortfolioRebalanceStateStorage",
    "AlpacaTradesStorage",
    "INTERPOLATED_PRICES_BY_PROFILE",
    "INTERPOLATION_RULE",
    "INTERPOLATION_UPSAMPLE_FREQUENCY",
    "execution_instance_models",
    "private_portfolio_models",
    "project_instance_models",
]
