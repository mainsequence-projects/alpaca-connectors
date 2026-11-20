"""This application's private instances of Markets time-series contracts (ms-markets ADR 0047).

Each class below is a contract-identical instance built by an ms-markets factory, named
``alpaca_connectors__<contract>__main``, identified as ``alpaca-connectors.<Contract>.main`` and
migrated by this application's own Alembic provider in its ``alpaca-connectors`` namespace.

They are private, and Markets never reads them (ADR 0031, decision 2): rebalance state and
calendar events, which a portfolio run writes (``private_portfolio_models``), and orders and
trades, which the execution ledger writes. Interpolated prices are dynamic (one table per bars
source); ``configured_alpaca_interpolated_prices_storage`` places them in this namespace the same
way. Signal weights, portfolio weights and portfolio values are Markets' own published series
(ADR 0031, decision 1); revision 0033 dropped this application's tables of them.
"""

from __future__ import annotations

from typing import Any

from msm.settings import markets_configured_namespace

from alpaca_connectors.settings import PROJECT_NAMESPACE, PROJECT_NAMESPACE_SLUG

INSTANCE_KEY = "main"
INSTANCE_NAMESPACE = markets_configured_namespace(PROJECT_NAMESPACE)


def _instance_kwargs() -> dict[str, Any]:
    """Factory arguments of every instance: this namespace and app segment, the Markets base.

    The instances are built on the Markets declarative base (no ``base=``), where their
    references to Markets registries resolve; this provider's migration copies the instance tables
    into its own metadata like any model (revisions 0020 and 0021 were written from it). A
    provider ``base`` would change that migration metadata, so it stays out.
    """
    return {
        "instance_key": INSTANCE_KEY,
        "namespace": INSTANCE_NAMESPACE,
        "storage_app": PROJECT_NAMESPACE_SLUG,
    }


def _build() -> dict[str, type[Any]]:
    from msm.storage_instances import configured_time_index_instance
    from msm.time_index.execution.storage import OrdersStorage, TradesStorage
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
        "trades": configured_time_index_instance(TradesStorage, **_instance_kwargs()),
    }


_INSTANCES = _build()

AlpacaPortfolioRebalanceStateStorage = _INSTANCES["portfolio_rebalance_state"]
AlpacaPortfolioCalendarEventsStorage = _INSTANCES["portfolio_calendar_events"]
AlpacaOrdersStorage = _INSTANCES["orders"]
AlpacaTradesStorage = _INSTANCES["trades"]


def interpolated_prices_storage_kwargs() -> dict[str, Any]:
    """``InterpolatedPrices`` keyword arguments placing its table in this namespace.

    No ``storage_base``, like the other instances (see ``_instance_kwargs``).
    """
    return {"storage_namespace": INSTANCE_NAMESPACE, "storage_app": PROJECT_NAMESPACE_SLUG}


def project_instance_models() -> list[type[Any]]:
    """Every contract instance this application migrates, registers and attaches."""
    return list(_INSTANCES.values())


def private_portfolio_models() -> list[type[Any]]:
    """The private instances a portfolio run writes: rebalance state and calendar events."""
    return [AlpacaPortfolioRebalanceStateStorage, AlpacaPortfolioCalendarEventsStorage]


def execution_instance_models() -> list[type[Any]]:
    """The instances the execution ledger writes."""
    return [AlpacaOrdersStorage, AlpacaTradesStorage]


__all__ = [
    "INSTANCE_KEY",
    "INSTANCE_NAMESPACE",
    "AlpacaOrdersStorage",
    "AlpacaPortfolioCalendarEventsStorage",
    "AlpacaPortfolioRebalanceStateStorage",
    "AlpacaTradesStorage",
    "execution_instance_models",
    "interpolated_prices_storage_kwargs",
    "private_portfolio_models",
    "project_instance_models",
]
