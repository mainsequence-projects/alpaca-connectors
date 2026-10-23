"""Project-owned ms-markets storage for Alpaca account metadata + current financials.

A single project-owned MetaTable: ``AlpacaAccountDetails`` — a one-to-one ``AccountTable`` detail
row (PK+FK on ``account_uid``) holding everything the generic ms-markets ``AccountTable`` cannot:

- static metadata (status, margin multiplier, PDT flag, shorting/blocks, options levels, account
  configuration) + the api-key fingerprint;
- the account's Alpaca API key and secret key, encrypted in the application process under the
  ``ALPACA_CONNECTORS_DATA_KEY`` Secret (MetaTables ADR 0025, ADR 0017): the API, the Admin, raw
  SQL and the database only ever see envelopes;
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from alpaca_connectors.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin
from alpaca_connectors.settings import CREDENTIAL_SECRET_NAME, PROJECT_NAMESPACE_SLUG
from metatables import EncryptedString

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

# Column label and meaning of each balance column; ``info`` adds the unit and what null means.
_ACCOUNT_BALANCE_INFO: dict[str, tuple[str, str]] = {
    "cash": ("Cash", "Cash balance, negative while the account borrows on margin"),
    "equity": ("Equity", "Account equity: cash plus the market value of all positions"),
    "last_equity": ("Previous close equity", "Account equity at the previous trading day's close"),
    "long_market_value": ("Long market value", "Market value of all long positions"),
    "short_market_value": (
        "Short market value",
        "Market value of all short positions, zero or negative",
    ),
    "position_market_value": ("Position market value", "Market value of all open positions"),
    "buying_power": ("Buying power", "Buying power available for new orders"),
    "regt_buying_power": (
        "Reg T buying power",
        "Overnight buying power under Regulation T margin rules",
    ),
    "daytrading_buying_power": (
        "Day-trading buying power",
        "Buying power available for day trades under pattern-day-trader rules",
    ),
    "non_marginable_buying_power": (
        "Non-marginable buying power",
        "Buying power for securities that cannot be bought on margin",
    ),
    "options_buying_power": ("Options buying power", "Buying power available for options trades"),
    "effective_buying_power": (
        "Effective buying power",
        "Buying power Alpaca applies to the next order",
    ),
    "bod_dtbp": (
        "Opening day-trading power",
        "Day-trading buying power at the start of the trading day",
    ),
    "initial_margin": ("Initial margin", "Initial margin requirement of the open positions"),
    "maintenance_margin": (
        "Maintenance margin",
        "Maintenance margin requirement of the open positions",
    ),
    "last_maintenance_margin": (
        "Previous maintenance margin",
        "Maintenance margin requirement at the previous trading day's close",
    ),
    "sma": (
        "Special memorandum account",
        "Special memorandum account (SMA) balance under Regulation T",
    ),
    "accrued_fees": ("Accrued fees", "Fees accrued and not yet charged to the account"),
    "pending_reg_taf_fees": (
        "Pending regulatory fees",
        "SEC and FINRA trading activity (Reg/TAF) fees not yet charged",
    ),
    "pending_transfer_in": ("Pending transfers in", "Deposits started but not yet settled"),
    "pending_transfer_out": ("Pending transfers out", "Withdrawals started but not yet settled"),
    "intraday_adjustments": (
        "Intraday adjustments",
        "Cash adjustments applied during the current trading day",
    ),
}


def _account_uid_pk_fk() -> Mapped[uuid.UUID]:
    """One-to-one detail link: primary key + foreign key to ``AccountTable.uid``."""
    return mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(f"{AccountTable.__table__.fullname}.uid", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        info={
            "label": "Account UID",
            "description": (
                "Main Sequence account UID of the registered Alpaca account these details describe."
            ),
        },
    )


class AlpacaAccountDetails(ProjectStorageNameMixin, AlpacaMarketsMetaTableMixin, MarketsBase):
    """Alpaca account metadata + current financials, one row per account, keyed by ``AccountTable.uid``."""

    __project_storage_concept__ = "acct_alpaca"
    __metatable_identifier__ = "AlpacaAccountDetails"
    __metatable_description__ = (
        "One row per Alpaca brokerage account registered in Main Sequence. Current status, "
        "trading permissions, account configuration and balances (cash, equity, buying power, "
        "margin and fees) of Alpaca paper and live trading accounts, read from the Alpaca "
        "trading API when the account is registered or refreshed. The account's API key and "
        "secret key are stored encrypted by the application and cannot be read, filtered or "
        "compared through the API or SQL."
    )
    __metatable_labels__ = ["brokerage", "accounts", "balances", "alpaca"]
    __metatable_extra_hash_components__: ClassVar[dict[str, str]] = {
        "storage_name": f"{PROJECT_NAMESPACE_SLUG}_alpaca_account_details",
    }
    __table_args__ = markets_table_args(
        "AlpacaAccountDetails",
        Index(None, "account_unique_identifier", unique=True),
        Index(None, "alpaca_account_id", unique=True),
        Index(None, "api_key_fingerprint"),
        Index(None, "api_key_secret_name"),
        Index(None, "secret_key_secret_name"),
    )

    account_uid: Mapped[uuid.UUID] = _account_uid_pk_fk()
    account_unique_identifier: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={
            "label": "Account identifier",
            "description": (
                "Main Sequence account unique identifier: the Alpaca account number followed by "
                "__ALPACA for a live account or __ALPACA_PAPER for a paper account."
            ),
        },
    )
    alpaca_account_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        info={"label": "Alpaca account ID", "description": "UUID Alpaca assigns to the account."},
    )
    account_number: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        info={
            "label": "Account number",
            "description": "Alpaca brokerage account number shown to the account holder.",
        },
    )
    api_key_fingerprint: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        info={
            "label": "API key fingerprint",
            "description": (
                "First 16 hexadecimal characters of the SHA-256 hash of the Alpaca API key ID, "
                "which recognizes a key without storing it; the secret key is never hashed."
            ),
        },
    )
    # The keys themselves, sealed in this process under the application's keyring (ADR 0017).
    # Null only on a registration written before revision 0018 that the credential migration Job
    # has not copied yet; such a registration cannot build an Alpaca client.
    api_key: Mapped[str | None] = mapped_column(
        EncryptedString(secret=CREDENTIAL_SECRET_NAME),
        nullable=True,
        info={
            "label": "API key",
            "description": (
                "Alpaca API key ID, encrypted by the application under the "
                f"{CREDENTIAL_SECRET_NAME} Secret; readable only by a process granted that "
                "Secret, never through the API or SQL. Null until the credential migration has "
                "copied a registration made before revision 0018."
            ),
        },
    )
    secret_key: Mapped[str | None] = mapped_column(
        EncryptedString(secret=CREDENTIAL_SECRET_NAME),
        nullable=True,
        info={
            "label": "Secret key",
            "description": (
                "Alpaca secret key, encrypted by the application under the "
                f"{CREDENTIAL_SECRET_NAME} Secret; readable only by a process granted that "
                "Secret, never through the API or SQL. Null until the credential migration has "
                "copied a registration made before revision 0018."
            ),
        },
    )
    # Transition columns (ADR 0011, superseded by ADR 0017): the Main Sequence Secrets a
    # registration resolved before its keys were stored here. Null once the credential migration
    # Job has copied and, for managed Secrets, deleted them; a later revision drops the columns.
    api_key_secret_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        info={
            "label": "Legacy API key secret name",
            "description": (
                "Name of the Main Sequence Secret that held the Alpaca API key ID before the keys "
                "were stored encrypted on this row; null once migrated."
            ),
        },
    )
    secret_key_secret_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        info={
            "label": "Legacy secret key secret name",
            "description": (
                "Name of the Main Sequence Secret that held the Alpaca secret key before the keys "
                "were stored encrypted on this row; null once migrated."
            ),
        },
    )
    api_key_secret_uid: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        info={
            "label": "Legacy API key secret UID",
            "description": (
                "UID of the Main Sequence Secret that held the Alpaca API key ID before the keys "
                "were stored encrypted on this row; null once migrated."
            ),
        },
    )
    secret_key_secret_uid: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        info={
            "label": "Legacy secret key secret UID",
            "description": (
                "UID of the Main Sequence Secret that held the Alpaca secret key before the keys "
                "were stored encrypted on this row; null once migrated."
            ),
        },
    )
    credential_source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        # Rows written before ADR 0011, or by a release without this column, selected Secrets by name.
        server_default=text("'external'"),
        info={
            "label": "Credential source",
            "description": (
                "managed when a person entered the keys through the API or the site; imported "
                "when they were copied once from Main Sequence Secrets selected by name; external "
                "on a registration made before revision 0018 whose keys the credential migration "
                "has not copied yet."
            ),
        },
    )
    credentials_updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={
            "label": "Credentials updated at",
            "description": (
                "Time, in UTC, the stored keys last changed; null when no change was recorded."
            ),
        },
    )
    credentials_updated_by_user_uid: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        info={
            "label": "Credentials updated by",
            "description": (
                "Main Sequence user UID of the person who last changed the credentials; null "
                "when unknown."
            ),
        },
    )
    is_paper: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        info={
            "label": "Paper account",
            "description": "True for an Alpaca paper-trading account, false for a live account.",
        },
    )
    # capture time of the current-financials snapshot held on this row.
    snapshot_time: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={
            "label": "Snapshot time",
            "description": (
                "Time, in UTC, the status and balances on this row were read from Alpaca; null "
                "when no read was recorded."
            ),
        },
    )
    status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        info={
            "label": "Account status",
            "description": (
                "Alpaca account status code, such as ACTIVE, ONBOARDING, ACCOUNT_UPDATED or "
                "ACCOUNT_CLOSED; null when Alpaca did not report it."
            ),
        },
    )
    crypto_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        info={
            "label": "Crypto status",
            "description": (
                "Alpaca crypto trading status code, using the account status codes; null when "
                "Alpaca did not report it."
            ),
        },
    )
    currency: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        info={
            "label": "Currency",
            "description": (
                "ISO 4217 code of the account currency, USD for Alpaca US accounts; null when "
                "Alpaca did not report it."
            ),
        },
    )
    multiplier: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        info={
            "label": "Margin multiplier",
            "description": (
                "Buying power multiplier Alpaca applies: 1 for a cash account, 2 for Reg T "
                "margin, 4 for pattern-day-trader margin; null when Alpaca did not report it."
            ),
        },
    )
    pattern_day_trader: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        info={
            "label": "Pattern day trader",
            "description": (
                "Whether Alpaca flags the account as a pattern day trader under FINRA rules; "
                "null when Alpaca did not report it."
            ),
        },
    )
    daytrade_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        info={
            "label": "Day trade count",
            "description": (
                "Number of day trades in the last five trading days; null when Alpaca did not "
                "report it."
            ),
        },
    )
    shorting_enabled: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        info={
            "label": "Shorting enabled",
            "description": (
                "Whether the account may sell short; null when Alpaca did not report it."
            ),
        },
    )
    trading_blocked: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        info={
            "label": "Trading blocked",
            "description": (
                "Whether Alpaca blocks the account from placing orders; null when Alpaca did not "
                "report it."
            ),
        },
    )
    transfers_blocked: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        info={
            "label": "Transfers blocked",
            "description": (
                "Whether Alpaca blocks deposits and withdrawals; null when Alpaca did not report "
                "it."
            ),
        },
    )
    account_blocked: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        info={
            "label": "Account blocked",
            "description": (
                "Whether Alpaca blocks all activity on the account; null when Alpaca did not "
                "report it."
            ),
        },
    )
    trade_suspended_by_user: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        info={
            "label": "Trading suspended by holder",
            "description": (
                "Whether the account holder suspended trading; null when Alpaca did not report it."
            ),
        },
    )
    options_approved_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        info={
            "label": "Options approved level",
            "description": (
                "Options trading level Alpaca approved, from 0 (no options) to 3; null when "
                "Alpaca did not report it."
            ),
        },
    )
    options_trading_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        info={
            "label": "Options trading level",
            "description": (
                "Options trading level in effect, from 0 (no options) to 3; null when Alpaca did "
                "not report it."
            ),
        },
    )
    balance_asof: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        info={
            "label": "Balance as of",
            "description": (
                "Date, as YYYY-MM-DD, that the previous-day figures such as previous close "
                "equity refer to; null when Alpaca did not report it."
            ),
        },
    )
    created_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        info={
            "label": "Account opened at",
            "description": (
                "Time, in UTC, Alpaca created the brokerage account; null when Alpaca did not "
                "report it."
            ),
        },
    )
    # Account configuration (GET /v2/account/configurations).
    dtbp_check: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        info={
            "label": "Buying power check",
            "description": (
                "Orders Alpaca checks against day-trading buying power: entry, exit or both; "
                "null when Alpaca did not report it."
            ),
        },
    )
    pdt_check: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        info={
            "label": "Pattern day-trader check",
            "description": (
                "Orders Alpaca checks against pattern-day-trader rules: entry, exit or both; "
                "null when Alpaca did not report it."
            ),
        },
    )
    fractional_trading: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        info={
            "label": "Fractional trading",
            "description": (
                "Whether fractional-share orders are enabled; null when Alpaca did not report it."
            ),
        },
    )
    max_margin_multiplier: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        info={
            "label": "Maximum margin multiplier",
            "description": (
                "Highest margin multiplier the account configuration allows, such as 1 for no "
                "margin or 2; null when Alpaca did not report it."
            ),
        },
    )
    no_shorting: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        info={
            "label": "Shorting disabled",
            "description": (
                "Whether the account configuration forbids short selling, making the account "
                "long-only; null when Alpaca did not report it."
            ),
        },
    )
    suspend_trade: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        info={
            "label": "Trading suspended",
            "description": (
                "Whether the account configuration suspends new orders; null when Alpaca did not "
                "report it."
            ),
        },
    )
    trade_confirm_email: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        info={
            "label": "Trade confirmation emails",
            "description": (
                "Which trade confirmations Alpaca emails: all or none; null when Alpaca did not "
                "report it."
            ),
        },
    )
    max_options_trading_level: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        info={
            "label": "Maximum options level",
            "description": (
                "Highest options trading level the account holder wants enabled, from 0 to 3; "
                "null when Alpaca did not report it."
            ),
        },
    )
    raw_account_payload: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        info={
            "label": "Raw account payload",
            "description": (
                "JSON object with the account and configuration records exactly as the Alpaca "
                "trading API returned them; null when they were not captured."
            ),
        },
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
            info={
                "label": _ACCOUNT_BALANCE_INFO[_balance_column][0],
                "description": (
                    f"{_ACCOUNT_BALANCE_INFO[_balance_column][1]}, in the account currency, at "
                    "the snapshot time; null when Alpaca did not report it."
                ),
            },
        ),
    )
del _balance_column


def project_account_models() -> list[type]:
    """Project-owned account MetaTables (for the migration provider + runtime attach)."""
    from alpaca_connectors.account.key_requests import AccountKeyRequestTable

    return [AlpacaAccountDetails, AccountKeyRequestTable]


__all__ = [
    "ACCOUNT_BALANCE_NUMERIC_COLUMNS",
    "AlpacaAccountDetails",
    "project_account_models",
]
