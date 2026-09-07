"""Resolve and run durable ETF portfolio configurations."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from etfhextractor.portfolio_publish import US_EQUITY_CALENDAR_KEY, ensure_trading_calendar

from src.portfolios.alpaca_etf_signal import build_alpaca_etf_holdings_signal
from src.portfolios.configurations import (
    AlpacaETFPortfolioConfiguration,
    PortfolioRebalanceConfiguration,
    get_portfolio_configuration,
    update_portfolio_configuration_row,
    validate_portfolio_references,
)
from src.portfolios.etf_tracking import (
    build_alpaca_interpolated_prices,
    resolve_alpaca_bars_time_index_meta_table_uid,
)
from src.portfolios.signal_history import (
    read_signal_observation_bounds,
    read_signal_observation_matrix,
)


@dataclass(frozen=True, slots=True)
class ResolvedPortfolioConfiguration:
    configuration: AlpacaETFPortfolioConfiguration
    signal_configuration: Any
    bars_configuration: Any
    rebalance_configuration: PortfolioRebalanceConfiguration
    signal_uid: str
    signal_start_time: dt.datetime
    asset_identifiers: tuple[str, ...]
    source_time_index_meta_table_uid: str

    def summary(self) -> dict[str, Any]:
        return {
            "configuration_uid": str(self.configuration.uid),
            "signal_configuration_uid": str(self.signal_configuration.uid),
            "bars_configuration_uid": str(self.bars_configuration.uid),
            "rebalance_configuration_uid": str(self.rebalance_configuration.uid),
            "signal_uid": self.signal_uid,
            "signal_start_time": self.signal_start_time.isoformat(),
            "asset_count": len(self.asset_identifiers),
            "source_time_index_meta_table_uid": self.source_time_index_meta_table_uid,
            "rebalance_strategy": self.rebalance_configuration.strategy,
        }


@dataclass(frozen=True, slots=True)
class PortfolioExecutionResult:
    resolved: ResolvedPortfolioConfiguration
    portfolio_uid: str
    portfolio_unique_identifier: str
    interpolation_result: Any
    portfolio_result: Any

    def summary(self) -> dict[str, Any]:
        return {
            **self.resolved.summary(),
            "portfolio_uid": self.portfolio_uid,
            "portfolio_unique_identifier": self.portfolio_unique_identifier,
            "interpolation": _summarize_run_result(self.interpolation_result),
            "portfolio": _summarize_run_result(self.portfolio_result),
        }


def portfolio_unique_identifier(configuration_uid: Any) -> str:
    return f"ALPACA_ETF_PORTFOLIO__{str(configuration_uid).replace('-', '').upper()}"


def resolve_portfolio_configuration(
    configuration_uid: Any,
) -> ResolvedPortfolioConfiguration:
    """Resolve stored calculation inputs and require an existing signal observation."""
    from src.operations import signal_uid_for_configuration

    configuration = get_portfolio_configuration(configuration_uid)
    if configuration is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    signal_configuration, bars_configuration, rebalance_configuration = (
        validate_portfolio_references(
            signal_configuration_uid=configuration.signal_configuration_uid,
            bars_configuration_uid=configuration.bars_configuration_uid,
            rebalance_configuration_uid=configuration.rebalance_configuration_uid,
        )
    )
    signal_uid = signal_uid_for_configuration(signal_configuration)
    latest = read_signal_observation_matrix(signal_uid=signal_uid, observation_limit=1)
    bounds = read_signal_observation_bounds(signal_uid=signal_uid)
    if (
        not latest.time_indexes
        or not latest.assets
        or bounds.first_observation_at is None
        or bounds.last_observation_at is None
    ):
        raise ValueError(
            f"Signal {signal_uid} has no published observations. Run its Signal Job before "
            "running this portfolio."
        )
    asset_identifiers = tuple(sorted({asset.asset_identifier for asset in latest.assets}))
    source_uid = resolve_alpaca_bars_time_index_meta_table_uid(
        frequency_id=bars_configuration.frequency_id,
        feed=bars_configuration.feed,
        adjustment=bars_configuration.adjustment,
    )
    return ResolvedPortfolioConfiguration(
        configuration=configuration,
        signal_configuration=signal_configuration,
        bars_configuration=bars_configuration,
        rebalance_configuration=rebalance_configuration,
        signal_uid=signal_uid,
        signal_start_time=bounds.first_observation_at,
        asset_identifiers=asset_identifiers,
        source_time_index_meta_table_uid=source_uid,
    )


def build_portfolio_graph(resolved: ResolvedPortfolioConfiguration) -> tuple[Any, Any, Any]:
    """Build the persistent interpolation and ImmediateSignal analytical portfolio graph."""
    from msm.api.portfolios import Portfolio
    from msm_portfolios.configuration import (
        BacktestingWeightsConfig,
        FrontEndDetails,
        PortfolioBuildConfiguration,
        PortfolioConfiguration,
        PortfolioExecutionConfiguration,
        PortfolioMarketsConfig,
        PriceAlignmentPolicy,
    )
    from msm_portfolios.data_nodes import PortfoliosDataNode
    from msm_portfolios.rebalance_strategy.immediate_signal import ImmediateSignal

    configuration = resolved.configuration
    if resolved.rebalance_configuration.strategy != "immediate_signal":
        raise ValueError("Phase 1 portfolio execution supports ImmediateSignal only.")
    signal = build_alpaca_etf_holdings_signal(
        universe_uid=str(resolved.signal_configuration.universe_uid),
        account_uid=str(resolved.signal_configuration.account_uid),
    ).set_runtime_asset_list(list(resolved.asset_identifiers))
    if signal.signal_uid != resolved.signal_uid:
        raise RuntimeError("Resolved Signal identity changed while building the portfolio graph.")
    valuation_source = build_alpaca_interpolated_prices(
        source_time_index_meta_table_uid=resolved.source_time_index_meta_table_uid,
        asset_identifiers=list(resolved.asset_identifiers),
        upsample_frequency_id=configuration.upsample_frequency_id,
        intraday_bar_interpolation_rule=configuration.intraday_bar_interpolation_rule,
    )
    backtest_days = max((dt.date.today() - resolved.signal_start_time.date()).days, 0)
    calendar_row = ensure_trading_calendar(
        US_EQUITY_CALENDAR_KEY,
        backtest_start_days=backtest_days,
    )
    description = configuration.description or (
        "Analytical ETF portfolio using already-published observed holdings weights and "
        "persistent interpolated Alpaca bars. Signal timestamps are observation times and do "
        "not guarantee the weights' exact economic effective time."
    )
    portfolio_configuration = PortfolioConfiguration(
        portfolio_build_configuration=PortfolioBuildConfiguration(
            valuation_source_instance=valuation_source,
            valuation_column=configuration.valuation_column,
            price_alignment_policy=PriceAlignmentPolicy(
                forward_fill_to_now=configuration.forward_fill_to_now,
                fail_on_missing_prices=configuration.fail_on_missing_prices,
            ),
            portfolio_prices_frequency=configuration.portfolio_prices_frequency,
            execution_configuration=PortfolioExecutionConfiguration(
                commission_fee=configuration.commission_fee
            ),
            backtesting_weights_configuration=BacktestingWeightsConfig(
                rebalance_strategy_instance=ImmediateSignal(calendar_key=US_EQUITY_CALENDAR_KEY),
                signal_weights_instance=signal,
            ),
        ),
        portfolio_markets_configuration=PortfolioMarketsConfig(
            portfolio_name=configuration.name,
            front_end_details=FrontEndDetails(
                description=description,
                signal_name=str(resolved.signal_configuration.name),
                signal_description=(
                    "Observed ETF component weights. Observation timestamps do not guarantee "
                    "exact economic effective times."
                ),
                rebalance_strategy_name="ImmediateSignal",
                rebalance_strategy_description=(
                    "Analytical backtest assumption: apply each observed signal immediately."
                ),
            ),
        ),
    )
    unique_identifier = portfolio_unique_identifier(configuration.uid)
    portfolio_row = Portfolio.upsert(
        unique_identifier=unique_identifier,
        calendar_uid=calendar_row.uid,
    )
    portfolio_node = PortfoliosDataNode(portfolio_configuration=portfolio_configuration)
    portfolio_node.set_portfolio_configuration(
        portfolio_configuration,
        portfolio_description=description,
    )
    portfolio_node.target_portfolio = portfolio_row
    portfolio_node._explicit_portfolio_identifier = unique_identifier
    # An observation-time signal cannot support a portfolio before its first stored
    # observation. Keep this runtime calculation boundary out of durable portfolio identity.
    portfolio_node.OFFSET_START = resolved.signal_start_time
    return valuation_source, portfolio_node, portfolio_row


def _set_initial_portfolio_price_lookback(
    *,
    resolved: ResolvedPortfolioConfiguration,
    valuation_source: Any,
    portfolio_node: Any,
) -> None:
    """Include every required asset's latest known price in a forward-fill run.

    ms-markets extends the valuation index to now, but its local alignment read starts only
    one portfolio period before the calculation boundary. For an inactive constituent, that
    can exclude the exact prior observation that must be carried forward. Moving the initial
    runtime read boundary does not backdate the signal or create synthetic stored prices;
    signal interpolation still removes all output dates before the first observation.
    """
    if not resolved.configuration.forward_fill_to_now:
        return

    statistics = valuation_source.update_statistics
    if statistics is None:
        statistics = valuation_source.get_update_statistics()
        valuation_source.update_statistics = statistics
    latest_prices = [
        statistics.get_last_update_for_identity(asset_identifier)
        for asset_identifier in resolved.asset_identifiers
    ]
    missing_assets = [
        asset_identifier
        for asset_identifier, latest_price in zip(
            resolved.asset_identifiers,
            latest_prices,
            strict=True,
        )
        if latest_price is None
    ]
    if missing_assets and resolved.configuration.fail_on_missing_prices:
        raise ValueError(
            "Portfolio valuation source has no usable observation for required signal assets: "
            + ", ".join(missing_assets)
        )
    usable_latest_prices = [value for value in latest_prices if value is not None]
    if usable_latest_prices:
        portfolio_node.OFFSET_START = min(
            resolved.signal_start_time,
            *usable_latest_prices,
        )


def execute_portfolio_configuration(configuration_uid: Any) -> PortfolioExecutionResult:
    """Update persistent interpolation, then calculate without traversing dependencies."""
    resolved = resolve_portfolio_configuration(configuration_uid)
    valuation_source, portfolio_node, portfolio_row = build_portfolio_graph(resolved)
    interpolation_result = valuation_source.run(update_tree=False)
    _set_initial_portfolio_price_lookback(
        resolved=resolved,
        valuation_source=valuation_source,
        portfolio_node=portfolio_node,
    )
    signal_weights = portfolio_node.signal_weights
    signal_statistics = signal_weights.get_update_statistics()
    signal_statistics.filter_identity_level(level=1, filters=[resolved.signal_uid])
    signal_weights.update_statistics = signal_statistics
    portfolio_result = portfolio_node.run(update_tree=False, update_pointers=True)
    persisted_portfolio = getattr(portfolio_node, "target_portfolio", None) or portfolio_row
    portfolio_uid = str(persisted_portfolio.uid)
    if (
        resolved.configuration.portfolio_uid is None
        or str(resolved.configuration.portfolio_uid) != portfolio_uid
    ):
        update_portfolio_configuration_row(
            resolved.configuration.uid,
            values={"portfolio_uid": uuid.UUID(portfolio_uid)},
        )
    return PortfolioExecutionResult(
        resolved=resolved,
        portfolio_uid=portfolio_uid,
        portfolio_unique_identifier=str(persisted_portfolio.unique_identifier),
        interpolation_result=interpolation_result,
        portfolio_result=portfolio_result,
    )


def _summarize_run_result(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: _summarize_run_result(item) for key, item in value.items()}
    if isinstance(value, tuple) and len(value) == 2:
        error, frame = value
        if error:
            raise RuntimeError("Portfolio pipeline update failed; see DataNode logs.")
        return {"error": False, "rows": int(getattr(frame, "shape", (0,))[0])}
    uid = getattr(value, "uid", None)
    if uid is not None:
        return {"uid": str(uid)}
    return str(value)


__all__ = [
    "PortfolioExecutionResult",
    "ResolvedPortfolioConfiguration",
    "build_portfolio_graph",
    "execute_portfolio_configuration",
    "portfolio_unique_identifier",
    "resolve_portfolio_configuration",
]
