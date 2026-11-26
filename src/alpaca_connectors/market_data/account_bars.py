"""The bars of an account's portfolios on one profile, as one node of their run (ADR 0033).

``AccountStockBars`` is configured by the account and the bars profile only, so its update hash
never moves with an asset. Everything else is resolved when it runs, from that configuration: the
account's assets (``resolve_account_bars_scope``), whether they already hold their last final bar,
and the account's Alpaca keys. Nothing is attached by the process that builds it, so a node Job
run that rebuilds it from its saved configuration fetches the same bars.
"""

from __future__ import annotations

import uuid

import pandas as pd
from msm.time_index.assets import AssetIndexedUpdater, AssetIndexedUpdaterConfiguration
from pydantic import Field, field_validator

from .alpaca_bars import AlpacaStockBarsFetch
from .alpaca_bars_support import normalize_adjustment, normalize_feed, normalize_frequency_id
from .storage import storage_for


class AccountStockBarsConfig(AssetIndexedUpdaterConfiguration):
    """The account and the bars profile: the whole update hash of ``AccountStockBars``."""

    account_uid: uuid.UUID = Field(
        ...,
        description="The account whose portfolios' assets are fetched, with its own keys.",
    )
    frequency_id: str = Field(..., description="Alpaca stock bar frequency identifier, e.g. 1d.")
    feed: str = Field(..., description="Alpaca market data feed, for example iex or sip.")
    adjustment: str = Field(..., description="Alpaca adjustment mode, for example raw or all.")

    @field_validator("frequency_id")
    @classmethod
    def _normalize_frequency_id(cls, value: str) -> str:
        return normalize_frequency_id(value)

    @field_validator("feed")
    @classmethod
    def _normalize_feed(cls, value: str) -> str:
        return normalize_feed(value)

    @field_validator("adjustment")
    @classmethod
    def _normalize_adjustment(cls, value: str) -> str:
        return normalize_adjustment(value)


class AccountStockBars(AlpacaStockBarsFetch, AssetIndexedUpdater):
    """Every asset ever published by the account's live portfolios on one bars profile.

    It writes the profile's bars table. Its statistics are its assets' (ms-markets'
    ``prepare_update_statistics``), and the account's interpolated prices take their assets from
    it (``InterpolatedPrices`` with this updater as its source, ms-markets 6.2.1).
    """

    def __init__(self, config: AccountStockBarsConfig, *, output_table=None):
        self._account_scope = None
        self._asset_bindings = None
        self._historical_client = None
        self._trading_client = None
        super().__init__(
            config=config,
            output_table=output_table
            or storage_for(config.frequency_id, config.feed, config.adjustment),
        )

    def account_scope(self):
        """The account's assets on the profile, resolved on the first call and kept."""
        if self._account_scope is None:
            from alpaca_connectors.portfolios.bars_scope import resolve_account_bars_scope

            self._account_scope = resolve_account_bars_scope(
                self.config.account_uid, self.bars_profile
            )
        return self._account_scope

    def get_asset_list(self) -> list[str]:
        return self.validate_asset_list(
            list(self.account_scope().asset_identifiers), allow_empty=True
        )

    def _connect(self) -> None:
        """Build the Alpaca clients from the account's stored keys (ADR 0017)."""
        if self._historical_client is not None and self._trading_client is not None:
            return
        from alpaca_connectors.account.credentials import (
            build_alpaca_historical_data_client,
            build_alpaca_trading_client,
            resolve_registered_alpaca_credentials,
        )
        from alpaca_connectors.account.services import get_account_registration

        registration = get_account_registration(str(self.config.account_uid))
        if registration is None:
            raise LookupError(
                f"Alpaca account registration {self.config.account_uid!s} does not exist."
            )
        credentials = resolve_registered_alpaca_credentials(registration)
        self._historical_client = build_alpaca_historical_data_client(credentials=credentials)
        self._trading_client = build_alpaca_trading_client(
            credentials=credentials, paper=bool(registration["is_paper"])
        )

    def update(self) -> pd.DataFrame:
        """No rows when there is nothing to fetch; otherwise the account's keys, then the fetch."""
        from alpaca_connectors.market_data.services import daily_bars_current_through

        assets = self.get_asset_list()
        if not assets:
            return pd.DataFrame()
        if daily_bars_current_through(self.bars_profile, assets) is not None:
            return pd.DataFrame()
        self._connect()
        return super().update()


__all__ = ["AccountStockBars", "AccountStockBarsConfig"]
