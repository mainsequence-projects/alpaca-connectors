"""Record this application's portfolio and signal datasets in the Markets catalog (ADR 0047).

Portfolio values, weights and signal weights live in this application's contract instances
(``alpaca_connectors.instances``). Markets reads them by table uid from its dataset catalog, which
this application keeps current through the Markets API: a portfolio run registers (or refreshes)
its catalog row and reconciles it, and a signal run binds the signal to its instance.
Registration needs the Markets workload to hold ``data_viewer`` on the instance tables
(docs/operations/markets-api-boundary.md).
"""

from __future__ import annotations

from typing import Any


def portfolio_dataset_registration(portfolio_uid: Any) -> dict[str, str]:
    """The catalog row of one portfolio computed by this application."""
    from alpaca_connectors.instances import (
        AlpacaPortfoliosStorage,
        AlpacaPortfolioWeightsStorage,
        AlpacaSignalWeightsStorage,
    )

    return {
        "portfolio_uid": str(portfolio_uid),
        "values_meta_table_uid": str(AlpacaPortfoliosStorage.get_meta_table_uid()),
        "weights_meta_table_uid": str(AlpacaPortfolioWeightsStorage.get_meta_table_uid()),
        "signal_meta_table_uid": str(AlpacaSignalWeightsStorage.get_meta_table_uid()),
    }


def register_portfolio_datasets(portfolio_uid: Any, *, reconcile: bool = True) -> Any:
    """Register (idempotently) and reconcile one portfolio's catalog row after a run."""
    from msm_portfolios.api.datasets import PortfolioDatasets

    return PortfolioDatasets.register(
        portfolio_dataset_registration(portfolio_uid), reconcile=reconcile
    )


def register_signal_dataset(signal_uid: str) -> Any:
    """Bind one signal to this application's signal-weights instance after a run."""
    from msm_portfolios.api.datasets import PortfolioDatasets

    from alpaca_connectors.instances import AlpacaSignalWeightsStorage

    return PortfolioDatasets.register_signal(
        {
            "signal_uid": str(signal_uid),
            "signal_meta_table_uid": str(AlpacaSignalWeightsStorage.get_meta_table_uid()),
        }
    )


__all__ = [
    "portfolio_dataset_registration",
    "register_portfolio_datasets",
    "register_signal_dataset",
]
