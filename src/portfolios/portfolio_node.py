"""Connector-owned runtime behavior for canonical ms-markets ETF portfolios."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
from msm_portfolios.data_nodes import PortfoliosDataNode


class AlpacaETFPortfolioDataNode(PortfoliosDataNode):
    """Use canonical portfolio storage with an explicit valuation seed lookback.

    ``PortfoliosDataNode`` reads valuations from only one portfolio period before a
    requested rebalance. That is insufficient when a still-valid constituent has not
    traded recently. This adapter broadens only the runtime read window; it preserves
    the canonical ``PortfoliosDataNode`` hash/storage identity and does not synthesize
    or persist price observations.
    """

    valuation_read_start: dt.datetime | None = None

    def set_valuation_read_start(
        self,
        value: dt.datetime | None,
    ) -> AlpacaETFPortfolioDataNode:
        self.valuation_read_start = value
        return self

    def _latest_portfolio_time_index_value(self):
        """Map canonical daily storage midnight back to that session's market close."""
        latest_value = super()._latest_portfolio_time_index_value()
        if latest_value is None or self.portfolio_prices_frequency != "1d":
            return latest_value

        latest = pd.Timestamp(latest_value)
        schedule = self.rebalancer.calendar.schedule(
            start_date=latest.normalize(),
            end_date=latest.normalize() + pd.Timedelta(days=1),
        )
        if schedule.empty:
            return latest_value
        market_closes = pd.DatetimeIndex(pd.to_datetime(schedule["market_close"], utc=True))
        same_session = market_closes[market_closes.normalize() == latest.normalize()]
        return same_session.max().to_pydatetime() if len(same_session) else latest_value

    def _calculate_portfolio_workflow_values(self) -> pd.DataFrame:
        """Treat an already-current trading session as a successful no-op."""
        latest_value = self._latest_portfolio_time_index_value()
        if latest_value is not None:
            _, end_date = self._calculate_start_end_dates()
            schedule = self.rebalancer.calendar.schedule(
                start_date=latest_value,
                end_date=end_date,
            )
            if not schedule.empty:
                market_closes = pd.DatetimeIndex(
                    pd.to_datetime(schedule["market_close"], utc=True)
                )
                latest = pd.Timestamp(latest_value)
                end = pd.Timestamp(end_date)
                has_new_close = bool(((market_closes > latest) & (market_closes <= end)).any())
                if not has_new_close:
                    self._last_canonical_weights_frame = pd.DataFrame()
                    self._last_canonical_portfolio_values_frame = pd.DataFrame()
                    self.logger.info(
                        "Portfolio output already includes the latest closed trading session.",
                        latest_portfolio_time_index=latest_value,
                    )
                    return pd.DataFrame()

        return super()._calculate_portfolio_workflow_values()

    def _align_valuation_source_to_index(
        self,
        new_index: pd.DatetimeIndex,
        unique_identifiers: list,
        index_freq: str,
        valuation_source: Any,
    ):
        """Let ms-markets align normally after broadening its source read start."""
        read_start = self.valuation_read_start
        if read_start is None:
            return super()._align_valuation_source_to_index(
                new_index=new_index,
                unique_identifiers=unique_identifiers,
                index_freq=index_freq,
                valuation_source=valuation_source,
            )

        original_get_df_between_dates = valuation_source.get_df_between_dates

        def get_df_between_dates_with_seed(*args, **kwargs):
            requested_start = kwargs.get("start_date")
            if requested_start is not None and pd.Timestamp(read_start) < pd.Timestamp(
                requested_start
            ):
                kwargs = {**kwargs, "start_date": read_start}
            return original_get_df_between_dates(*args, **kwargs)

        valuation_source.get_df_between_dates = get_df_between_dates_with_seed
        try:
            return super()._align_valuation_source_to_index(
                new_index=new_index,
                unique_identifiers=unique_identifiers,
                index_freq=index_freq,
                valuation_source=valuation_source,
            )
        finally:
            valuation_source.get_df_between_dates = original_get_df_between_dates


__all__ = ["AlpacaETFPortfolioDataNode"]
