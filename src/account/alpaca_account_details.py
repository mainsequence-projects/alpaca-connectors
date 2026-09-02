"""Project-owned ms-markets storage for Alpaca account metadata + current financials.

A single project-owned MetaTable: ``AlpacaAccountDetails`` — a one-to-one ``AccountTable`` detail
row (PK+FK on ``account_uid``) holding everything the generic ms-markets ``AccountTable`` cannot:

- static metadata (status, margin multiplier, PDT flag, shorting/blocks, options levels, account
  configuration) + the api-key fingerprint;
- the **current** Alpaca account financials (cash, equity, buying-power family, margin, SMA, fees,
  daytrade count), refreshed on each register;
- the full raw account JSON (`raw_account_payload`).

Point-in-time portfolio data is **not** stored here and is **not** given a bespoke time-series
table: account positions + cash are written as account *holdings* into the ms-markets
``AccountHoldingsStorage`` (via the registration service), the canonical account-holdings store.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import ClassVar

from msm.base import MarketsBase, markets_table_args
from msm.models import AccountTable
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from src.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin
from src.settings import PROJECT_NAMESPACE_SLUG

# Money precision. Alpaca returns money as strings to preserve precision; store as Numeric.
_MONEY = Numeric(38, 12)

# Current numeric account financials captured on the detail row. Includes the six live-API-only
# fields (effective_buying_power, bod_dtbp, position_market_value, pending_reg_taf_fees,
# intraday_adjustments) populated from the raw account payload when present.
ACCOUNT_BALANCE_NUMERIC_COLUMNS: tuple[str, ...] = (
    "cash",
    "equity",
    "last_equity",
    "long_market_value",
    "short_market_value",
    "position_market_value",
    "buying_power",
    "regt_buying_power",
    "daytrading_buying_power",
    "non_marginable_buying_power",
    "options_buying_power",
    "effective_buying_power",
    "bod_dtbp",
    "initial_margin",
    "maintenance_margin",
    "last_maintenance_margin",
    "sma",
    "accrued_fees",
    "pending_reg_taf_fees",
    "pending_transfer_in",
    "pending_transfer_out",
    "intraday_adjustments",
)


def _account_uid_pk_fk() -> Mapped[uuid.UUID]:
    """One-to-one detail link: primary key + foreign key to ``AccountTable.uid``."""
    return mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AccountTable.__table__.fullname}.uid", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        info={"label": "Account UID", "description": "Canonical AccountTable.uid for this detail row."},
    )


class AlpacaAccountDetails(ProjectStorageNameMixin, AlpacaMarketsMetaTableMixin, MarketsBase):
    """Alpaca account metadata + current financials, one row per account, keyed by ``AccountTable.uid``."""

    __project_storage_concept__ = "acct_alpaca"
    __metatable_identifier__ = "AlpacaAccountDetails"
    __metatable_extra_hash_components__: ClassVar[dict[str, str]] = {
        "storage_name": f"{PROJECT_NAMESPACE_SLUG}_alpaca_account_details",
    }
    __table_args__ = markets_table_args(
        "AlpacaAccountDetails",
        Index(None, "account_unique_identifier", unique=True),
        Index(None, "alpaca_account_id", unique=True),
        Index(None, "api_key_fingerprint"),
    )

    account_uid: Mapped[uuid.UUID] = _account_uid_pk_fk()
    account_unique_identifier: Mapped[str] = mapped_column(
        String(255), nullable=False, info={"label": "Account Unique Identifier", "description": "Account.unique_identifier."}
    )
    alpaca_account_id: Mapped[str] = mapped_column(
        String(64), nullable=False, info={"label": "Alpaca Account ID", "description": "Alpaca system account UUID (str)."}
    )
    account_number: Mapped[str] = mapped_column(
        String(64), nullable=False, info={"label": "Account Number", "description": "Human Alpaca account number."}
    )
    api_key_fingerprint: Mapped[str] = mapped_column(
        String(32), nullable=False, info={"label": "API Key Fingerprint", "description": "sha256(api_key)[:16]; never the raw key/secret."}
    )
    is_paper: Mapped[bool] = mapped_column(
        Boolean, nullable=False, info={"label": "Is Paper", "description": "Paper vs live trading environment."}
    )
    # capture time of the current-financials snapshot held on this row.
    snapshot_time: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, info={"label": "Snapshot Time", "description": "When the current financials were captured."}
    )
    status: Mapped[str | None] = mapped_column(
        String(32), nullable=True, info={"label": "Status", "description": "Alpaca account status (e.g. ACTIVE)."}
    )
    crypto_status: Mapped[str | None] = mapped_column(
        String(32), nullable=True, info={"label": "Crypto Status", "description": "Crypto trading status, if enabled."}
    )
    currency: Mapped[str | None] = mapped_column(
        String(8), nullable=True, info={"label": "Currency", "description": "Account currency (USD)."}
    )
    multiplier: Mapped[str | None] = mapped_column(
        String(8), nullable=True, info={"label": "Multiplier", "description": "Margin multiplier (1/2/4)."}
    )
    pattern_day_trader: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, info={"label": "Pattern Day Trader", "description": "PDT flag."}
    )
    daytrade_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, info={"label": "Day Trade Count", "description": "Day trades in the last 5 trading days."}
    )
    shorting_enabled: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, info={"label": "Shorting Enabled", "description": "Whether shorting is permitted."}
    )
    trading_blocked: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, info={"label": "Trading Blocked", "description": "Order placement blocked."}
    )
    transfers_blocked: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, info={"label": "Transfers Blocked", "description": "Money transfers blocked."}
    )
    account_blocked: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, info={"label": "Account Blocked", "description": "All user activity prohibited."}
    )
    trade_suspended_by_user: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, info={"label": "Trade Suspended By User", "description": "User self-suspended trading."}
    )
    options_approved_level: Mapped[int | None] = mapped_column(
        Integer, nullable=True, info={"label": "Options Approved Level", "description": "Approved options level (0-3)."}
    )
    options_trading_level: Mapped[int | None] = mapped_column(
        Integer, nullable=True, info={"label": "Options Trading Level", "description": "Effective options level (0-3)."}
    )
    balance_asof: Mapped[str | None] = mapped_column(
        String(16), nullable=True, info={"label": "Balance As Of", "description": "Date the previous-day balance figures are as of."}
    )
    created_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, info={"label": "Created At", "description": "Account creation timestamp."}
    )
    # Account configuration (GET /v2/account/configurations).
    dtbp_check: Mapped[str | None] = mapped_column(String(8), nullable=True, info={"label": "DTBP Check", "description": "Day-trade buying-power check."})
    pdt_check: Mapped[str | None] = mapped_column(String(8), nullable=True, info={"label": "PDT Check", "description": "Pattern-day-trader check."})
    fractional_trading: Mapped[bool | None] = mapped_column(Boolean, nullable=True, info={"label": "Fractional Trading", "description": "Fractional trading enabled."})
    max_margin_multiplier: Mapped[str | None] = mapped_column(String(8), nullable=True, info={"label": "Max Margin Multiplier", "description": "Max margin multiplier (1-4)."})
    no_shorting: Mapped[bool | None] = mapped_column(Boolean, nullable=True, info={"label": "No Shorting", "description": "Account is long-only."})
    suspend_trade: Mapped[bool | None] = mapped_column(Boolean, nullable=True, info={"label": "Suspend Trade", "description": "New orders suspended."})
    trade_confirm_email: Mapped[str | None] = mapped_column(String(8), nullable=True, info={"label": "Trade Confirm Email", "description": "Trade-confirmation emails (all/none)."})
    max_options_trading_level: Mapped[int | None] = mapped_column(Integer, nullable=True, info={"label": "Max Options Trading Level", "description": "Desired max options level (0-3)."})
    raw_account_payload: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, info={"label": "Raw Account Payload", "description": "Full raw account + configuration JSON."}
    )


# Attach the current Numeric financial columns declaratively (keeps the long catalog DRY +
# migration-visible). These are current values refreshed on each register, not a time series.
for _balance_column in ACCOUNT_BALANCE_NUMERIC_COLUMNS:
    setattr(
        AlpacaAccountDetails,
        _balance_column,
        mapped_column(
            _balance_column,
            _MONEY,
            nullable=True,
            info={"label": _balance_column.replace("_", " ").title(), "description": f"Current Alpaca account {_balance_column}."},
        ),
    )
del _balance_column


def project_account_models() -> list[type]:
    """Project-owned account MetaTables (for the migration provider + runtime attach)."""
    return [AlpacaAccountDetails]


__all__ = [
    "ACCOUNT_BALANCE_NUMERIC_COLUMNS",
    "AlpacaAccountDetails",
    "project_account_models",
]
