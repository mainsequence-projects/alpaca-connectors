from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from etfhextractor import (
    ETFHoldingsReader,
    derive_component_weights_from_holdings,
    resolve_asset_identifiers_by_ticker,
)
from etfhextractor.portfolio_publish import (
    US_EQUITY_CALENDAR_KEY,
    build_etf_tracking_portfolio,
)

from src.etf_holdings import infer_holdings_component_provider
from src.markets_storage.alpaca_bars import storage_for


@dataclass(frozen=True, slots=True)
class AlpacaEtfTrackingPortfolioConfig:
    etf_ticker: str
    provider: str | None = None
    fund_url: str | None = None
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
    signal_validity_days: int = 90
    min_update_interval_days: float = 1.0
    backtest_start_days: int = 60
    allowed_asset_classes: tuple[str, ...] | None = ("Equity",)
    renormalize_weights: bool = True
    commission_fee: float = 0.00018
    timeout: float = 30.0
    debug_mode: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedEtfUniverse:
    etf_ticker: str
    provider: str | None
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
    source_time_index_meta_table_uid: str
    source_storage_identifier: str
    source_storage_table_name: str
    interpolated_storage_identifier: str
    portfolio_unique_identifier: str

    def summary(self) -> dict[str, Any]:
        return {
            **self.universe.summary(),
            "portfolio_unique_identifier": self.portfolio_unique_identifier,
            "source_time_index_meta_table_uid": self.source_time_index_meta_table_uid,
            "source_storage_identifier": self.source_storage_identifier,
            "source_storage_table_name": self.source_storage_table_name,
            "interpolated_storage_identifier": self.interpolated_storage_identifier,
            "upsample_frequency_id": self.config.upsample_frequency_id,
            "intraday_bar_interpolation_rule": self.config.intraday_bar_interpolation_rule,
            "valuation_column": self.config.valuation_column,
            "allowed_asset_classes": self.config.allowed_asset_classes,
            "calendar_key": self.config.calendar_key,
        }


@dataclass(frozen=True, slots=True)
class AlpacaEtfPortfolioBuild:
    plan: AlpacaEtfPortfolioPlan
    calendar_row: Any
    portfolio_row: Any
    signal: Any
    valuation_source: Any
    portfolio_node: Any
    run_result: Any | None

    def summary(self) -> dict[str, Any]:
        return {
            **self.plan.summary(),
            "calendar_uid": str(self.calendar_row.uid),
            "portfolio_uid": str(self.portfolio_row.uid),
            "signal_uid": self.signal.signal_uid,
            "valuation_source": type(self.valuation_source).__name__,
            "ran": self.run_result is not None,
            "run_result": _summarize_run_result(self.run_result),
        }


def _normalize_ticker(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("ETF ticker must not be empty.")
    return normalized


def _identifier_token(value: str) -> str:
    token = re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")
    if not token:
        raise ValueError("Identifier token value must not be empty.")
    return token


def build_alpaca_etf_tracking_portfolio_unique_identifier(
    config: AlpacaEtfTrackingPortfolioConfig,
    *,
    etf_ticker: str | None = None,
) -> str:
    """Build the Portfolio.unique_identifier for this Alpaca-backed ETF tracker.

    The portfolio row identity must reflect the ETF and the price-source configuration, not just
    the ETF ticker. The only double-underscore segment is the venue suffix, mirroring the account
    convention: ``...__ALPACA``.
    """
    ticker = _identifier_token(etf_ticker or config.etf_ticker)
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
    return f"{ticker}_TRACKER_BARS_{bars_token}_INTERP_{interpolation_token}__ALPACA"


def _resolved_provider(config: AlpacaEtfTrackingPortfolioConfig) -> str | None:
    if config.provider is not None:
        return config.provider.strip().lower()
    if config.fund_url is not None:
        return None
    return infer_holdings_component_provider(config.etf_ticker)


def resolve_alpaca_bars_time_index_meta_table_uid(
    *,
    frequency_id: str = "1d",
    feed: str = "sip",
    adjustment: str = "all",
) -> str:
    """Resolve the registered Alpaca bars TimeIndexMetaTable uid for a storage triple."""
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


def resolve_etf_component_asset_identifiers(
    *,
    etf_ticker: str,
    provider: str | None = None,
    fund_url: str | None = None,
    timeout: float = 30.0,
    allowed_asset_classes: tuple[str, ...] | None = ("Equity",),
) -> ResolvedEtfUniverse:
    """Extract ETF weights and resolve component tickers to Asset.unique_identifier values."""
    normalized_ticker = _normalize_ticker(etf_ticker)
    reader = ETFHoldingsReader(timeout=timeout)
    holdings = (
        reader.read(fund_url)
        if fund_url is not None
        else reader.read_ticker(normalized_ticker, provider=provider)
    )
    weights_by_symbol = derive_component_weights_from_holdings(
        holdings,
        allowed_asset_classes=allowed_asset_classes,
    )
    if not weights_by_symbol:
        raise RuntimeError(f"No component weights were extracted for {normalized_ticker}.")

    identifiers_by_symbol, missing, ambiguous = resolve_asset_identifiers_by_ticker(
        component_symbols=sorted(weights_by_symbol),
    )
    if missing or ambiguous:
        raise RuntimeError(
            f"ETF {normalized_ticker} cannot be used for a portfolio until all components "
            "resolve to one registered ms-markets asset. "
            f"missing={missing} ambiguous={ambiguous}. Register missing symbols through "
            "`alpaca-connectors asset register` before building the portfolio."
        )

    return ResolvedEtfUniverse(
        etf_ticker=normalized_ticker,
        provider=provider,
        component_weights_by_symbol=dict(sorted(weights_by_symbol.items())),
        asset_identifiers_by_symbol=dict(sorted(identifiers_by_symbol.items())),
    )


def alpaca_interpolated_prices_storage_model(
    *,
    source_time_index_meta_table_uid: str,
    frequency_id: str = "1d",
    feed: str = "sip",
    adjustment: str = "all",
    upsample_frequency_id: str = "1d",
    intraday_bar_interpolation_rule: str = "ffill",
) -> type[Any]:
    """Return the dynamic msm_portfolios storage model for Alpaca-derived prices."""
    from msm_portfolios.data_nodes.prices.storage import configured_interpolated_prices_storage

    source_storage = storage_for(frequency_id, feed, adjustment)
    return configured_interpolated_prices_storage(
        source_time_index_meta_table_uid=source_time_index_meta_table_uid,
        source_cadence=source_storage.__cadence__,
        upsample_frequency_id=upsample_frequency_id,
        intraday_bar_interpolation_rule=intraday_bar_interpolation_rule,
    )


def build_alpaca_interpolated_prices(
    *,
    source_time_index_meta_table_uid: str,
    asset_identifiers: list[str],
    upsample_frequency_id: str = "1d",
    intraday_bar_interpolation_rule: str = "ffill",
) -> Any:
    """Build the InterpolatedPrices node sourced from registered Alpaca bars."""
    from msm_portfolios.contrib.prices.data_nodes import (
        InterpolatedPrices,
        InterpolatedPricesConfig,
    )

    return InterpolatedPrices(
        interpolation_config=InterpolatedPricesConfig(
            source_time_index_meta_table_uid=source_time_index_meta_table_uid,
            asset_list=sorted(set(asset_identifiers)),
            upsample_frequency_id=upsample_frequency_id,
            intraday_bar_interpolation_rule=intraday_bar_interpolation_rule,
        )
    )


def plan_alpaca_etf_tracking_portfolio(
    config: AlpacaEtfTrackingPortfolioConfig,
    *,
    start_engine: bool = True,
) -> AlpacaEtfPortfolioPlan:
    """Plan the ETF holdings portfolio without writing portfolio rows or running nodes."""
    if start_engine:
        from src.runtime import start_portfolio_markets_engine

        start_portfolio_markets_engine()

    normalized_ticker = _normalize_ticker(config.etf_ticker)
    provider = _resolved_provider(config)
    universe = resolve_etf_component_asset_identifiers(
        etf_ticker=normalized_ticker,
        provider=provider,
        fund_url=config.fund_url,
        timeout=config.timeout,
        allowed_asset_classes=config.allowed_asset_classes,
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
            etf_ticker=normalized_ticker,
        )
    )
    return AlpacaEtfPortfolioPlan(
        config=config,
        universe=universe,
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
    """Build an ETF-tracking portfolio graph backed by interpolated Alpaca bars.

    This writes/reuses the Calendar and Portfolio rows needed by ms-markets portfolios. Set
    ``run=True`` to execute the signal, interpolation, portfolio-values, and portfolio-weights
    DataNodes.
    """
    if start_engine:
        from src.runtime import start_portfolio_markets_engine

        start_portfolio_markets_engine()

    plan = plan_alpaca_etf_tracking_portfolio(config, start_engine=False)
    valuation_source = build_alpaca_interpolated_prices(
        source_time_index_meta_table_uid=plan.source_time_index_meta_table_uid,
        asset_identifiers=plan.universe.asset_identifiers,
        upsample_frequency_id=config.upsample_frequency_id,
        intraday_bar_interpolation_rule=config.intraday_bar_interpolation_rule,
    )

    portfolio_name = config.portfolio_name or f"Alpaca ETF Tracker {plan.universe.etf_ticker}"
    description = config.description or (
        f"Tracks ETF {plan.universe.etf_ticker} using etfhextractor holdings weights and "
        f"interpolated Alpaca {config.frequency_id}/{config.feed}/{config.adjustment} bars."
    )
    external_build = build_etf_tracking_portfolio(
        etf_ticker=plan.universe.etf_ticker,
        provider=plan.universe.provider,
        fund_url=config.fund_url,
        price_source_instance=valuation_source,
        portfolio_unique_identifier=plan.portfolio_unique_identifier,
        portfolio_name=portfolio_name,
        description=description,
        valuation_column=config.valuation_column,
        signal_name=f"{plan.universe.etf_ticker} holdings signal",
        rebalance_strategy_name="ImmediateSignal",
        signal_validity_days=config.signal_validity_days,
        min_update_interval_days=config.min_update_interval_days,
        renormalize_weights=config.renormalize_weights,
        commission_fee=config.commission_fee,
        calendar_key=config.calendar_key,
        backtest_start_days=config.backtest_start_days,
        timeout=config.timeout,
        run=run,
        debug_mode=config.debug_mode,
        asset_identifiers=plan.universe.asset_identifiers,
        allowed_asset_classes=config.allowed_asset_classes,
        # The project runtime already includes the Alpaca storage and the full
        # portfolio model graph; a second runtime attachment is invalid.
        start_engine=False,
    )

    return AlpacaEtfPortfolioBuild(
        plan=plan,
        calendar_row=external_build.calendar_row,
        portfolio_row=external_build.portfolio_row,
        signal=external_build.signal,
        valuation_source=external_build.valuation_source,
        portfolio_node=external_build.portfolio_node,
        run_result=external_build.run_result,
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
    "resolve_etf_component_asset_identifiers",
]
