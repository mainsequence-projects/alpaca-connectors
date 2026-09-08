"""Construct Universe-backed ETF portfolios with Alpaca valuation data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from etfhextractor.portfolio_publish import (
    US_EQUITY_CALENDAR_KEY,
    ensure_trading_calendar,
)

from src.market_data import storage_for
from src.portfolios.alpaca_etf_signal import build_alpaca_etf_holdings_signal


@dataclass(frozen=True, slots=True)
class AlpacaEtfTrackingPortfolioConfig:
    """Portfolio configuration around one durable Asset Universe signal."""

    universe_uid: str
    account_uid: str
    frequency_id: str = "1d"
    feed: str = "sip"
    adjustment: str = "all"
    price_source_table_uid: str | None = None
    upsample_frequency_id: str = "1d"
    intraday_bar_interpolation_rule: str = "ffill"
    valuation_column: str = "close"
    calendar_key: str = US_EQUITY_CALENDAR_KEY
    portfolio_unique_identifier: str | None = None
    portfolio_name: str | None = None
    description: str | None = None
    commission_fee: float = 0.00018
    timeout: float = 30.0


@dataclass(frozen=True, slots=True)
class ResolvedEtfUniverse:
    universe_uid: str
    source_uid: str
    asset_category_uid: str
    etf_ticker: str
    provider: str
    component_weights_by_symbol: dict[str, float]
    asset_identifiers_by_symbol: dict[str, str]

    @property
    def component_symbols(self) -> list[str]:
        return sorted(self.component_weights_by_symbol)

    @property
    def asset_identifiers(self) -> list[str]:
        return sorted(set(self.asset_identifiers_by_symbol.values()))

    def summary(self) -> dict[str, Any]:
        return {
            "universe_uid": self.universe_uid,
            "source_uid": self.source_uid,
            "asset_category_uid": self.asset_category_uid,
            "etf_ticker": self.etf_ticker,
            "provider": self.provider,
            "component_symbol_count": len(self.component_symbols),
            "asset_identifier_count": len(self.asset_identifiers),
            "component_symbols": self.component_symbols,
            "asset_identifiers_by_symbol": dict(sorted(self.asset_identifiers_by_symbol.items())),
        }


@dataclass(frozen=True, slots=True)
class AlpacaEtfPortfolioPlan:
    config: AlpacaEtfTrackingPortfolioConfig
    universe: ResolvedEtfUniverse
    universe_run_plan: Any
    source_time_index_meta_table_uid: str
    source_storage_identifier: str
    source_storage_table_name: str
    interpolated_storage_identifier: str
    portfolio_unique_identifier: str

    def summary(self) -> dict[str, Any]:
        return {
            **self.universe.summary(),
            "account_uid": self.config.account_uid,
            "portfolio_unique_identifier": self.portfolio_unique_identifier,
            "source_time_index_meta_table_uid": self.source_time_index_meta_table_uid,
            "source_storage_identifier": self.source_storage_identifier,
            "source_storage_table_name": self.source_storage_table_name,
            "interpolated_storage_identifier": self.interpolated_storage_identifier,
            "upsample_frequency_id": self.config.upsample_frequency_id,
            "intraday_bar_interpolation_rule": self.config.intraday_bar_interpolation_rule,
            "valuation_column": self.config.valuation_column,
            "calendar_key": self.config.calendar_key,
        }


@dataclass(frozen=True, slots=True)
class AlpacaEtfPortfolioBuild:
    plan: AlpacaEtfPortfolioPlan
    calendar_row: Any
    portfolio_row: Any
    signal: Any
    valuation_source: Any
    calendar_events: Any
    portfolio_node: Any
    run_result: Any | None

    def summary(self) -> dict[str, Any]:
        return {
            **self.plan.summary(),
            "calendar_uid": str(self.calendar_row.uid),
            "portfolio_uid": str(self.portfolio_row.uid),
            "signal_uid": self.signal.signal_uid,
            "valuation_source": type(self.valuation_source).__name__,
            "calendar_events": type(self.calendar_events).__name__,
            "ran": self.run_result is not None,
            "run_result": _summarize_run_result(self.run_result),
        }


def _identifier_token(value: str) -> str:
    token = re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
    if not token:
        raise ValueError("Identifier token value must not be empty.")
    return token


def build_alpaca_etf_tracking_portfolio_unique_identifier(
    config: AlpacaEtfTrackingPortfolioConfig,
    *,
    etf_ticker: str,
) -> str:
    """Build identity from Universe business identity and valuation configuration."""
    ticker = _identifier_token(etf_ticker)
    universe_token = _identifier_token(config.universe_uid)
    bars_token = "_".join(
        [
            _identifier_token(config.frequency_id),
            _identifier_token(config.feed),
            _identifier_token(config.adjustment),
        ]
    )
    interpolation_token = "_".join(
        [
            _identifier_token(config.upsample_frequency_id),
            _identifier_token(config.intraday_bar_interpolation_rule),
        ]
    )
    return (
        f"{ticker}_UNIVERSE_{universe_token}_TRACKER_BARS_{bars_token}_"
        f"INTERP_{interpolation_token}__ALPACA"
    )


def resolve_alpaca_bars_time_index_meta_table_uid(
    *,
    frequency_id: str = "1d",
    feed: str = "sip",
    adjustment: str = "all",
) -> str:
    """Resolve the registered Alpaca bars table UID for one migrated storage profile."""
    from mainsequence.client import TimeIndexMetaTable

    storage = storage_for(frequency_id, feed, adjustment)
    catalog_identifier = storage.__table__.name
    matches = TimeIndexMetaTable.filter_by_body(
        identifier__in=[catalog_identifier],
        namespace__in=[storage.__metatable_namespace__],
        physical_table_name__in=[storage.__table__.name],
        limit=2,
    )
    exact_matches = [
        table
        for table in matches
        if table.identifier == catalog_identifier
        and table.namespace == storage.__metatable_namespace__
        and table.physical_table_name == storage.__table__.name
    ]
    if len(exact_matches) != 1:
        raise RuntimeError(
            "Expected exactly one registered Alpaca bars TimeIndexMetaTable for "
            f"{storage.__table__.name}; found {len(exact_matches)}. Run and verify the project "
            "markets migration before building an Alpaca-backed portfolio price source."
        )
    return str(exact_matches[0].uid)


def alpaca_interpolated_prices_storage_model(
    *,
    source_time_index_meta_table_uid: str,
    frequency_id: str = "1d",
    feed: str = "sip",
    adjustment: str = "all",
    upsample_frequency_id: str = "1d",
    intraday_bar_interpolation_rule: str = "ffill",
) -> type[Any]:
    """Return the dynamic msm-portfolios storage model for Alpaca-derived prices."""
    from src.portfolios.interpolated_prices_schema import (
        configured_alpaca_interpolated_prices_storage,
    )

    return configured_alpaca_interpolated_prices_storage(
        source_time_index_meta_table_uid=source_time_index_meta_table_uid,
        source_cadence=storage_for(frequency_id, feed, adjustment).__cadence__,
        upsample_frequency_id=upsample_frequency_id,
        intraday_bar_interpolation_rule=intraday_bar_interpolation_rule,
    )


def build_alpaca_interpolated_prices(
    *,
    source_time_index_meta_table_uid: str,
    asset_identifiers: list[str],
    calendar_identifier: str,
    upsample_frequency_id: str = "1d",
    intraday_bar_interpolation_rule: str = "ffill",
) -> Any:
    """Build calendar-aware InterpolatedPrices sourced from registered Alpaca bars."""
    from msm_portfolios.contrib.prices.data_nodes import (
        InterpolatedPrices,
        InterpolatedPricesConfig,
    )

    normalized_calendar_identifier = calendar_identifier.strip()
    if not normalized_calendar_identifier:
        raise ValueError("calendar_identifier must not be empty.")
    asset_scope = [
        {
            "asset_identifier": asset_identifier,
            "calendar": normalized_calendar_identifier,
        }
        for asset_identifier in sorted(set(asset_identifiers))
    ]
    return InterpolatedPrices(
        interpolation_config=InterpolatedPricesConfig(
            source_time_index_meta_table_uid=source_time_index_meta_table_uid,
            asset_list=asset_scope,
            upsample_frequency_id=upsample_frequency_id,
            intraday_bar_interpolation_rule=intraday_bar_interpolation_rule,
        )
    )


def plan_alpaca_etf_tracking_portfolio(
    config: AlpacaEtfTrackingPortfolioConfig,
    *,
    start_engine: bool = True,
) -> AlpacaEtfPortfolioPlan:
    """Resolve one registered Universe without writing assets, signals, or portfolios."""
    if start_engine:
        from src.runtime import start_portfolio_markets_engine

        start_portfolio_markets_engine()

    from src.universes import (
        asset_identifiers_by_symbol_for_universe_plan,
        preview_asset_universe,
    )

    universe_run_plan = preview_asset_universe(
        config.universe_uid,
        account_uid=config.account_uid,
        timeout=config.timeout,
    )
    if universe_run_plan.has_blockers():
        missing = ", ".join(
            universe_run_plan.registration_resolution.unresolved_symbols_from_alpaca
        )
        raise ValueError(
            f"Asset Universe {config.universe_uid} cannot produce a complete signal because "
            f"Alpaca did not resolve: {missing}."
        )
    identifiers_by_symbol = asset_identifiers_by_symbol_for_universe_plan(universe_run_plan)
    missing_identifiers = sorted(
        set(universe_run_plan.component_weights_by_symbol) - identifiers_by_symbol.keys()
    )
    if missing_identifiers:
        raise RuntimeError(
            f"Asset Universe {config.universe_uid} has no canonical Alpaca identity for: "
            + ", ".join(missing_identifiers)
        )
    resolved_universe = ResolvedEtfUniverse(
        universe_uid=universe_run_plan.universe_uid,
        source_uid=universe_run_plan.source_uid,
        asset_category_uid=universe_run_plan.asset_category_uid,
        etf_ticker=universe_run_plan.holdings_plan.etf_ticker,
        provider=universe_run_plan.holdings_plan.provider,
        component_weights_by_symbol=dict(universe_run_plan.component_weights_by_symbol),
        asset_identifiers_by_symbol=identifiers_by_symbol,
    )
    source_uid = config.price_source_table_uid or resolve_alpaca_bars_time_index_meta_table_uid(
        frequency_id=config.frequency_id,
        feed=config.feed,
        adjustment=config.adjustment,
    )
    source_storage = storage_for(config.frequency_id, config.feed, config.adjustment)
    interpolated_storage = alpaca_interpolated_prices_storage_model(
        source_time_index_meta_table_uid=source_uid,
        frequency_id=config.frequency_id,
        feed=config.feed,
        adjustment=config.adjustment,
        upsample_frequency_id=config.upsample_frequency_id,
        intraday_bar_interpolation_rule=config.intraday_bar_interpolation_rule,
    )
    portfolio_identifier = (
        config.portfolio_unique_identifier
        or build_alpaca_etf_tracking_portfolio_unique_identifier(
            config,
            etf_ticker=resolved_universe.etf_ticker,
        )
    )
    return AlpacaEtfPortfolioPlan(
        config=config,
        universe=resolved_universe,
        universe_run_plan=universe_run_plan,
        source_time_index_meta_table_uid=source_uid,
        source_storage_identifier=source_storage.__metatable_identifier__,
        source_storage_table_name=source_storage.__table__.name,
        interpolated_storage_identifier=interpolated_storage.__metatable_identifier__,
        portfolio_unique_identifier=portfolio_identifier,
    )


def build_alpaca_etf_tracking_portfolio(
    config: AlpacaEtfTrackingPortfolioConfig,
    *,
    run: bool = False,
    start_engine: bool = True,
) -> AlpacaEtfPortfolioBuild:
    """Build the ms-markets graph around the connector-owned Universe signal."""
    if start_engine:
        from src.runtime import start_portfolio_markets_engine

        start_portfolio_markets_engine()

    from msm.api.portfolios import Portfolio
    from msm_portfolios.configuration import (
        BacktestingWeightsConfig,
        FrontEndDetails,
        PortfolioBuildConfiguration,
        PortfolioConfiguration,
        PortfolioExecutionConfiguration,
        PortfolioMarketsConfig,
    )
    from msm_portfolios.data_nodes import (
        PortfolioCalendarEvents,
        PortfolioCalendarEventsConfiguration,
        PortfoliosDataNode,
    )
    from msm_portfolios.rebalance_strategy import CalendarEventSignal

    plan = plan_alpaca_etf_tracking_portfolio(config, start_engine=False)
    signal = build_alpaca_etf_holdings_signal(
        universe_uid=config.universe_uid,
        account_uid=config.account_uid,
        prepared_plan=plan.universe_run_plan,
    )
    calendar_row = ensure_trading_calendar(config.calendar_key, backtest_start_days=0)
    valuation_source = build_alpaca_interpolated_prices(
        source_time_index_meta_table_uid=plan.source_time_index_meta_table_uid,
        asset_identifiers=plan.universe.asset_identifiers,
        calendar_identifier=str(calendar_row.unique_identifier),
        upsample_frequency_id=config.upsample_frequency_id,
        intraday_bar_interpolation_rule=config.intraday_bar_interpolation_rule,
    )
    calendar_events = PortfolioCalendarEvents(
        config=PortfolioCalendarEventsConfiguration(
            calendar_identifier=str(calendar_row.unique_identifier),
            session_label="regular",
            event_types=("market_close",),
        )
    )
    portfolio_name = config.portfolio_name or f"Alpaca ETF Tracker {plan.universe.etf_ticker}"
    description = config.description or (
        f"Tracks registered Asset Universe {config.universe_uid} using observed ETF holdings "
        f"weights and interpolated Alpaca {config.frequency_id}/{config.feed}/"
        f"{config.adjustment} bars. Observation timestamps record extraction time and do not "
        "guarantee the provider weights became economically effective at that exact time."
    )
    portfolio_configuration = PortfolioConfiguration(
        portfolio_build_configuration=PortfolioBuildConfiguration(
            valuation_source_instance=valuation_source,
            valuation_column=config.valuation_column,
            execution_configuration=PortfolioExecutionConfiguration(
                commission_fee=config.commission_fee
            ),
            backtesting_weights_configuration=BacktestingWeightsConfig(
                rebalance_strategy_instance=CalendarEventSignal(
                    calendar_events_instance=calendar_events,
                    calendar_identifier=str(calendar_row.unique_identifier),
                    session_label="regular",
                    rebalance_event="market_close",
                ),
                signal_weights_instance=signal,
            ),
        ),
        portfolio_markets_configuration=PortfolioMarketsConfig(
            portfolio_name=portfolio_name,
            front_end_details=FrontEndDetails(
                description=description,
                signal_name=f"{plan.universe.etf_ticker} observed holdings",
                rebalance_strategy_name="CalendarEventSignal",
            ),
        ),
    )
    portfolio_row = Portfolio.upsert(
        unique_identifier=plan.portfolio_unique_identifier,
        calendar_uid=calendar_row.uid,
    )
    portfolio_node = PortfoliosDataNode(portfolio_configuration=portfolio_configuration)
    portfolio_node.set_portfolio_configuration(
        portfolio_configuration,
        portfolio_description=description,
    )
    portfolio_node.target_portfolio = portfolio_row
    portfolio_node._explicit_portfolio_identifier = plan.portfolio_unique_identifier

    run_result = portfolio_node.run() if run else None
    return AlpacaEtfPortfolioBuild(
        plan=plan,
        calendar_row=calendar_row,
        portfolio_row=portfolio_row,
        signal=signal,
        valuation_source=valuation_source,
        calendar_events=calendar_events,
        portfolio_node=portfolio_node,
        run_result=run_result,
    )


def _summarize_run_result(run_result: Any) -> Any:
    if run_result is None:
        return None
    if isinstance(run_result, dict):
        return {key: _summarize_run_result(value) for key, value in run_result.items()}
    if isinstance(run_result, tuple) and len(run_result) == 2:
        error_on_last_update, frame = run_result
        if error_on_last_update:
            raise RuntimeError("Portfolio pipeline update failed; see DataNode logs.")
        rows = 0 if frame is None else int(getattr(frame, "shape", (0,))[0])
        return {"error": False, "rows": rows}
    return str(run_result)


__all__ = [
    "AlpacaEtfPortfolioBuild",
    "AlpacaEtfPortfolioPlan",
    "AlpacaEtfTrackingPortfolioConfig",
    "ResolvedEtfUniverse",
    "alpaca_interpolated_prices_storage_model",
    "build_alpaca_etf_tracking_portfolio",
    "build_alpaca_etf_tracking_portfolio_unique_identifier",
    "build_alpaca_interpolated_prices",
    "plan_alpaca_etf_tracking_portfolio",
    "resolve_alpaca_bars_time_index_meta_table_uid",
]
