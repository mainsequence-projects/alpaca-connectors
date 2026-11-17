"""Portfolio construction helpers for Alpaca-backed market data.

The ETF signal and ETF portfolio modules load only when one of their names is used (ADR 0027), so
importing this package, the provided-weights portfolios included, never loads the ETF extractor.
"""

from importlib import import_module

from alpaca_connectors.portfolios.configurations import (
    DEFAULT_PORTFOLIO_CALENDAR_IDENTIFIER,
    REBALANCE_CADENCES,
    REBALANCE_EVENTS,
    REBALANCE_STRATEGIES,
    AlpacaETFPortfolioConfiguration,
    AlpacaETFPortfolioConfigurationTable,
    PortfolioRebalanceConfiguration,
    PortfolioRebalanceConfigurationTable,
    create_portfolio_configuration_row,
    create_rebalance_configuration,
    delete_portfolio_configuration_row,
    delete_rebalance_configuration,
    get_portfolio_configuration,
    get_rebalance_configuration,
    list_portfolio_configurations,
    list_rebalance_configurations,
    normalize_rebalance_strategy,
    project_portfolio_configuration_models,
    rebalance_configurations_by_uids,
    scheduled_portfolio_configurations,
    update_portfolio_configuration,
    update_portfolio_configuration_row,
    update_rebalance_configuration,
    validate_portfolio_references,
)
from alpaca_connectors.portfolios.execution import (
    PortfolioExecutionGraph,
    PortfolioExecutionResult,
    PortfolioPreflight,
    ResolvedPortfolioConfiguration,
    build_portfolio_graph,
    execute_portfolio_configuration,
    portfolio_unique_identifier,
    preflight_portfolio_configuration,
    resolve_portfolio_configuration,
)
from alpaca_connectors.portfolios.interpolated_prices import (
    alpaca_interpolated_prices_storage_model,
    build_alpaca_interpolated_prices,
    resolve_alpaca_bars_time_index_meta_table_uid,
)
from alpaca_connectors.portfolios.portfolio_history import (
    MAX_PORTFOLIO_OBSERVATIONS,
    PortfolioHistory,
    PortfolioPerformance,
    PortfolioValueObservation,
    calculate_portfolio_performance,
    read_portfolio_history,
)
from alpaca_connectors.portfolios.signal_history import (
    MAX_SIGNAL_OBSERVATIONS,
    SignalObservationAsset,
    SignalObservationMatrix,
    read_signal_observation_matrix,
)

_LAZY_IMPORTS = {
    name: f"alpaca_connectors.portfolios.{module}"
    for module, names in {
        "alpaca_etf_signal": (
            "AlpacaETFHoldingsSignal",
            "AlpacaETFHoldingsSignalConfig",
            "build_alpaca_etf_holdings_signal",
        ),
        "etf_tracking": (
            "AlpacaEtfPortfolioBuild",
            "AlpacaEtfPortfolioPlan",
            "AlpacaEtfTrackingPortfolioConfig",
            "ResolvedEtfUniverse",
            "build_alpaca_etf_tracking_portfolio",
            "build_alpaca_etf_tracking_portfolio_unique_identifier",
            "plan_alpaca_etf_tracking_portfolio",
        ),
    }.items()
    for name in names
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        value = getattr(import_module(_LAZY_IMPORTS[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AlpacaETFHoldingsSignal",
    "AlpacaETFHoldingsSignalConfig",
    "AlpacaETFPortfolioConfiguration",
    "AlpacaETFPortfolioConfigurationTable",
    "AlpacaEtfPortfolioBuild",
    "AlpacaEtfPortfolioPlan",
    "AlpacaEtfTrackingPortfolioConfig",
    "DEFAULT_PORTFOLIO_CALENDAR_IDENTIFIER",
    "MAX_SIGNAL_OBSERVATIONS",
    "MAX_PORTFOLIO_OBSERVATIONS",
    "PortfolioHistory",
    "PortfolioPerformance",
    "PortfolioRebalanceConfiguration",
    "PortfolioRebalanceConfigurationTable",
    "PortfolioValueObservation",
    "PortfolioExecutionResult",
    "PortfolioExecutionGraph",
    "PortfolioPreflight",
    "REBALANCE_CADENCES",
    "REBALANCE_EVENTS",
    "REBALANCE_STRATEGIES",
    "ResolvedEtfUniverse",
    "ResolvedPortfolioConfiguration",
    "SignalObservationAsset",
    "SignalObservationMatrix",
    "alpaca_interpolated_prices_storage_model",
    "build_alpaca_etf_holdings_signal",
    "build_alpaca_etf_tracking_portfolio",
    "build_alpaca_etf_tracking_portfolio_unique_identifier",
    "build_alpaca_interpolated_prices",
    "build_portfolio_graph",
    "calculate_portfolio_performance",
    "create_portfolio_configuration_row",
    "create_rebalance_configuration",
    "delete_portfolio_configuration_row",
    "delete_rebalance_configuration",
    "execute_portfolio_configuration",
    "get_portfolio_configuration",
    "get_rebalance_configuration",
    "list_portfolio_configurations",
    "list_rebalance_configurations",
    "normalize_rebalance_strategy",
    "plan_alpaca_etf_tracking_portfolio",
    "portfolio_unique_identifier",
    "preflight_portfolio_configuration",
    "project_portfolio_configuration_models",
    "rebalance_configurations_by_uids",
    "read_signal_observation_matrix",
    "read_portfolio_history",
    "resolve_alpaca_bars_time_index_meta_table_uid",
    "resolve_portfolio_configuration",
    "scheduled_portfolio_configurations",
    "update_portfolio_configuration",
    "update_portfolio_configuration_row",
    "update_rebalance_configuration",
    "validate_portfolio_references",
]
