"""This application's instances of the Markets time-series contracts (ms-markets ADR 0047).

The time series this application produces live in its own ``alpaca-connectors`` namespace,
migrated by its own Alembic provider, instead of in the shared Markets tables: each class below is
a contract-identical instance built by an ms-markets factory, named
``alpaca_connectors__<contract>__main`` and identified as
``alpaca-connectors.<Contract>.main``. The producers are bound to them with ``output_table=``
(an output table never enters ``update_hash``, so producer identities and signal uids do not
change), and the portfolio and signal instances are recorded in the Markets dataset catalog with
``PortfolioDatasets`` so Markets reads them by table uid.

Interpolated prices are dynamic (one table per bars source); ``configured_alpaca_interpolated_prices
_storage`` places them in this namespace the same way.
"""

from __future__ import annotations

from typing import Any

from msm.settings import markets_configured_namespace
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from alpaca_connectors.settings import PROJECT_NAMESPACE, PROJECT_NAMESPACE_SLUG
from metatables import sqlalchemy_naming_convention

INSTANCE_KEY = "main"
INSTANCE_NAMESPACE = markets_configured_namespace(PROJECT_NAMESPACE)


class AlpacaInstanceBase(DeclarativeBase):
    """Declarative base of the instance tables, kept out of the Markets model registry.

    The factories copy the Markets tables an instance references (Asset, Account, Portfolio,
    SignalMetadata, Calendar, OrderManager) into this metadata, so the foreign keys resolve
    without importing the Markets classes into the migration scope.
    """

    metadata = MetaData(naming_convention=sqlalchemy_naming_convention())


def _instance_kwargs() -> dict[str, Any]:
    return {
        "instance_key": INSTANCE_KEY,
        "namespace": INSTANCE_NAMESPACE,
        "storage_app": PROJECT_NAMESPACE_SLUG,
        "base": AlpacaInstanceBase,
    }


def _build() -> dict[str, type[Any]]:
    from msm.storage_instances import configured_time_index_instance
    from msm.time_index.execution.storage import OrdersStorage, TradesStorage
    from msm_portfolios.time_index.contract_instances import (
        configured_portfolio_calendar_events_storage,
        configured_portfolio_rebalance_state_storage,
        configured_portfolio_weights_storage,
        configured_portfolios_storage,
        configured_signal_weights_storage,
    )

    return {
        "signal_weights": configured_signal_weights_storage(**_instance_kwargs()),
        "portfolios": configured_portfolios_storage(**_instance_kwargs()),
        "portfolio_weights": configured_portfolio_weights_storage(**_instance_kwargs()),
        "portfolio_rebalance_state": configured_portfolio_rebalance_state_storage(
            **_instance_kwargs()
        ),
        "portfolio_calendar_events": configured_portfolio_calendar_events_storage(
            **_instance_kwargs()
        ),
        # ms-markets 3.0.0 `configured_time_index_instance` raises TypeError when `base` already
        # holds a referenced Markets table (its `_copy_table` tests a Table's truth value), which
        # every provider base hits for TradesStorage's three Asset foreign keys. Without `base`
        # the instance table is built in the Markets declarative metadata, where the references
        # resolve; this provider's migration copies it into its own metadata like any model.
        "orders": configured_time_index_instance(OrdersStorage, **_execution_instance_kwargs()),
        "trades": configured_time_index_instance(TradesStorage, **_execution_instance_kwargs()),
    }


def _execution_instance_kwargs() -> dict[str, Any]:
    return {key: value for key, value in _instance_kwargs().items() if key != "base"}


_INSTANCES = _build()

AlpacaSignalWeightsStorage = _INSTANCES["signal_weights"]
AlpacaPortfoliosStorage = _INSTANCES["portfolios"]
AlpacaPortfolioWeightsStorage = _INSTANCES["portfolio_weights"]
AlpacaPortfolioRebalanceStateStorage = _INSTANCES["portfolio_rebalance_state"]
AlpacaPortfolioCalendarEventsStorage = _INSTANCES["portfolio_calendar_events"]
AlpacaOrdersStorage = _INSTANCES["orders"]
AlpacaTradesStorage = _INSTANCES["trades"]


def portfolio_output_tables() -> dict[str, type[Any]]:
    """``PortfoliosUpdater`` keyword arguments binding its three outputs to these instances."""
    return {
        "output_table": AlpacaPortfoliosStorage,
        "weights_output_table": AlpacaPortfolioWeightsStorage,
        "rebalance_output_table": AlpacaPortfolioRebalanceStateStorage,
    }


def interpolated_prices_storage_kwargs() -> dict[str, Any]:
    """``InterpolatedPrices`` keyword arguments placing its table in this namespace.

    No ``storage_base``: ms-markets 3.0.0 cannot copy the Asset reference into a provider base
    that already holds it (see ``_execution_instance_kwargs``), and a process builds one
    interpolated storage per bars profile.
    """
    return {"storage_namespace": INSTANCE_NAMESPACE, "storage_app": PROJECT_NAMESPACE_SLUG}


def project_instance_models() -> list[type[Any]]:
    """Every contract instance this application migrates, registers and attaches."""
    return list(_INSTANCES.values())


def portfolio_instance_models() -> list[type[Any]]:
    """The instances the signal and portfolio producers write."""
    return [
        AlpacaSignalWeightsStorage,
        AlpacaPortfoliosStorage,
        AlpacaPortfolioWeightsStorage,
        AlpacaPortfolioRebalanceStateStorage,
        AlpacaPortfolioCalendarEventsStorage,
    ]


def execution_instance_models() -> list[type[Any]]:
    """The instances the execution ledger writes."""
    return [AlpacaOrdersStorage, AlpacaTradesStorage]


__all__ = [
    "INSTANCE_KEY",
    "INSTANCE_NAMESPACE",
    "AlpacaInstanceBase",
    "AlpacaOrdersStorage",
    "AlpacaPortfolioCalendarEventsStorage",
    "AlpacaPortfolioRebalanceStateStorage",
    "AlpacaPortfolioWeightsStorage",
    "AlpacaPortfoliosStorage",
    "AlpacaSignalWeightsStorage",
    "AlpacaTradesStorage",
    "execution_instance_models",
    "interpolated_prices_storage_kwargs",
    "portfolio_instance_models",
    "portfolio_output_tables",
    "project_instance_models",
]
