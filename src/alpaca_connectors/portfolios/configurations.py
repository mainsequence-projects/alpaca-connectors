"""Durable analytical portfolio and rebalance configurations.

Every portfolio is built from the weights the application that owns it provides (ADR 0024,
ADR 0027). The rows in this module own calculation intent, the owner and one operational flag,
``scheduled``: whether the scheduled run of the portfolio Job declared in
``.mainsequence/workflows/alpaca-connectors-api.yaml`` includes the configuration (ADR 0013).
Schedule, compute, image and automatic deployment belong to that declaration, and execution
history to its JobRuns.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any, ClassVar, Literal

from msm.api.base import MarketsMetaTableRow, operation_result_rows
from msm.base import markets_table_args, new_markets_uid
from msm.models import AccountTable
from msm.models.portfolios import PortfolioTable
from pydantic import ConfigDict
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from alpaca_connectors.base import ProjectBase
from alpaca_connectors.metatables import AlpacaMarketsMetaTableMixin, ProjectStorageNameMixin
from alpaca_connectors.portfolios.signal_definitions import SignalDefinitionTable

# calendar_event_signal rebalances at the configured calendar events; immediate_signal at each
# signal observation. Both trade at the next observed price while the calendar's market is open
# (ms-markets ADR 0058), so both read the calendar's sessions.
RebalanceStrategy = Literal["calendar_event_signal", "immediate_signal"]
RebalanceEvent = Literal["market_open", "market_close"]
RebalanceCadence = Literal["every_session", "weekly"]
REBALANCE_STRATEGIES: tuple[RebalanceStrategy, ...] = ("calendar_event_signal", "immediate_signal")
REBALANCE_EVENTS: tuple[RebalanceEvent, ...] = ("market_open", "market_close")
REBALANCE_CADENCES: tuple[RebalanceCadence, ...] = ("every_session", "weekly")
DEFAULT_PORTFOLIO_CALENDAR_IDENTIFIER = "NYSE"
UTC = dt.timezone.utc
_UNSET = object()


def utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


class PortfolioRebalanceConfigurationTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    ProjectBase,
):
    """Reusable configuration for converting signal weights into executed weights."""

    __project_storage_concept__ = "portfolio_rebalance_configuration"
    __markets_base_identifier__ = "PortfolioRebalanceConfiguration"
    __metatable_description__ = (
        "One row per portfolio rebalance rule. When an analytical portfolio trades to its "
        "signal's target weights: at each new set of weights as it is observed, or at a market "
        "calendar's session open or close, every session or weekly, optionally offset in time, "
        "using the latest weights observed at or before that moment; created by operators of "
        "the Alpaca Connectors application."
    )
    __metatable_labels__ = ["portfolios", "rebalancing", "configuration", "alpaca"]
    __table_args__ = markets_table_args(
        "PortfolioRebalanceConfiguration",
        CheckConstraint(
            "strategy IN ('calendar_event_signal', 'immediate_signal')",
            name="ck_portfolio_rebalance_configuration_strategy",
        ),
        CheckConstraint(
            "length(trim(calendar_identifier)) > 0",
            name="ck_portfolio_rebalance_calendar_identifier",
        ),
        CheckConstraint(
            "length(trim(session_label)) > 0",
            name="ck_portfolio_rebalance_session_label",
        ),
        CheckConstraint(
            "rebalance_event IN ('market_open', 'market_close')",
            name="ck_portfolio_rebalance_event",
        ),
        CheckConstraint(
            "rebalance_cadence IN ('every_session', 'weekly')",
            name="ck_portfolio_rebalance_cadence",
        ),
        CheckConstraint(
            "rebalance_weekday >= 0 AND rebalance_weekday <= 6",
            name="ck_portfolio_rebalance_weekday",
        ),
        Index(None, "name"),
        Index(None, "strategy"),
        Index(None, "calendar_identifier"),
        Index(None, "portfolio_configuration_uid"),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={
            "label": "Rebalance rule UID",
            "description": "Public UUID that identifies the rebalance rule.",
        },
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={"label": "Name", "description": "Display name of the rebalance rule."},
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        info={
            "label": "Description",
            "description": "Free-text note about the rule; null when none was given.",
        },
    )
    strategy: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        info={
            "label": "Strategy",
            "description": (
                "Rebalance strategy code: calendar_event_signal trades to the latest signal "
                "weights observed at or before each selected calendar event, immediate_signal "
                "trades to each signal observation; both at the next observed price while the "
                "calendar's market is open."
            ),
        },
    )
    calendar_identifier: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={
            "label": "Calendar",
            "description": (
                "Unique identifier of the Main Sequence market calendar whose sessions supply "
                "the events, such as NYSE."
            ),
        },
    )
    session_label: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        info={
            "label": "Session",
            "description": (
                "Label of the calendar sessions whose events qualify, such as regular for the "
                "regular trading session."
            ),
        },
    )
    rebalance_event: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        info={
            "label": "Rebalance event",
            "description": "Session boundary the portfolio trades at: market_open or market_close.",
        },
    )
    event_offset_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        info={
            "label": "Event offset",
            "description": (
                "Seconds added to the event time to get the trade time; negative trades before "
                "the event and 0 trades at it."
            ),
        },
    )
    rebalance_cadence: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        info={
            "label": "Rebalance cadence",
            "description": (
                "every_session to trade at every session's event, or weekly to trade once a "
                "week on the rebalance weekday."
            ),
        },
    )
    rebalance_weekday: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        info={
            "label": "Rebalance weekday",
            "description": (
                "Local weekday of the session used by a weekly cadence, 0 for Monday to 6 for "
                "Sunday; ignored by every_session."
            ),
        },
    )
    # No foreign key: the portfolio configuration already references this rule, and the rule
    # outlives that row by one step when the portfolio ends (issue #42).
    portfolio_configuration_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        info={
            "label": "Created with portfolio",
            "description": (
                "For a rule created inline with a portfolio, the UID of that portfolio "
                "configuration; the rule is deleted when that portfolio ends and no other "
                "portfolio names it. Null for a shared rule created on its own."
            ),
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={"label": "Created at", "description": "Time, in UTC, the rule was created."},
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        info={"label": "Updated at", "description": "Time, in UTC, the rule last changed."},
    )


class AlpacaPortfolioConfigurationTable(
    ProjectStorageNameMixin,
    AlpacaMarketsMetaTableMixin,
    ProjectBase,
):
    """One analytical portfolio of one account, following one of its account's signals.

    The account (ADR 0028, decision 3) and the signal definition it follows (decision 5) are
    null for a portfolio created before ADR 0028 that revision 0029 and the ``portfolio adopt``
    command could not give one, because its owner does not own its bars' account. Such a row is
    not adopted and works as before: it follows its own signal and runs on the bars feed it names.
    """

    __project_storage_concept__ = "portfolio_configuration"
    __markets_base_identifier__ = "AlpacaPortfolioConfiguration"
    __metatable_description__ = (
        "One row per analytical portfolio valued with Alpaca stock prices and built from the "
        "weights an application provides. Saved settings for the simulated portfolio: the "
        "registered Alpaca account it belongs to, the account's signal it follows, its rebalance "
        "rule, its valuation and trading-fee settings, the application's person who owns it, "
        "and whether the scheduled portfolio update includes it; created by the account's owner "
        "through the applications that give the Alpaca Connectors application the weights."
    )
    __metatable_labels__ = ["portfolios", "configuration", "alpaca"]
    __table_args__ = markets_table_args(
        "AlpacaPortfolioConfiguration",
        CheckConstraint("commission_fee >= 0", name="ck_portfolio_commission_fee"),
        CheckConstraint(
            "length(trim(valuation_column)) > 0",
            name="ck_portfolio_valuation_column",
        ),
        CheckConstraint(
            "length(trim(upsample_frequency_id)) > 0",
            name="ck_portfolio_upsample_frequency",
        ),
        CheckConstraint(
            "length(trim(intraday_bar_interpolation_rule)) > 0",
            name="ck_portfolio_interpolation_rule",
        ),
        CheckConstraint(
            "upsample_frequency_id = '1d'",
            name="ck_portfolio_phase_one_upsample_frequency",
        ),
        CheckConstraint(
            "intraday_bar_interpolation_rule = 'ffill'",
            name="ck_portfolio_phase_one_interpolation_rule",
        ),
        CheckConstraint(
            "valuation_maximum_staleness_seconds > 0",
            name="ck_portfolio_valuation_maximum_staleness",
        ),
        # The definition must be one of the portfolio's own account (ADR 0028, decision 2).
        ForeignKeyConstraint(
            ["signal_definition_uid", "account_uid"],
            [
                f"{SignalDefinitionTable.__table__.fullname}.uid",
                f"{SignalDefinitionTable.__table__.fullname}.account_uid",
            ],
            ondelete="RESTRICT",
        ),
        Index(None, "name"),
        Index(None, "bars_configuration_uid"),
        Index(None, "rebalance_configuration_uid"),
        Index(None, "portfolio_uid", unique=True),
        Index(None, "account_uid"),
        Index(None, "signal_definition_uid"),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={
            "label": "Configuration UID",
            "description": "Public UUID that identifies the configuration and selects it to run.",
        },
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={
            "label": "Name",
            "description": "Display name of the configuration, also used as the portfolio name.",
        },
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        info={
            "label": "Description",
            "description": "Free-text description of the portfolio; null when none was given.",
        },
    )
    account_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(AccountTable.__table__.c.uid, ondelete="RESTRICT"),
        nullable=True,
        info={
            "label": "Account UID",
            "description": (
                "Main Sequence account UID of the registered Alpaca brokerage account the "
                "portfolio belongs to; the account cannot be deleted while it has portfolios. "
                "Null only for a portfolio created before accounts owned portfolios and not yet "
                "adopted."
            ),
        },
    )
    signal_definition_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        info={
            "label": "Signal definition UID",
            "description": (
                "UID of the account's signal definition whose weights the portfolio follows; "
                "null only for a portfolio not yet adopted."
            ),
        },
    )
    bars_configuration_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        info={
            "label": "Former price configuration UID",
            "description": (
                "UID of the stock price-update configuration a portfolio not yet adopted named; "
                "null once the portfolio has its own price-update configuration. Retired: a "
                "later revision drops it."
            ),
        },
    )
    rebalance_configuration_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            f"{PortfolioRebalanceConfigurationTable.__table__.fullname}.uid",
            ondelete="RESTRICT",
        ),
        nullable=False,
        info={
            "label": "Rebalance rule UID",
            "description": "UID of the rebalance rule that decides when the portfolio trades.",
        },
    )
    portfolio_uid: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(PortfolioTable.__table__.c.uid, ondelete="SET NULL"),
        nullable=True,
        info={
            "label": "Portfolio UID",
            "description": (
                "Main Sequence portfolio UID of the portfolio the configuration builds; null until "
                "the first run creates it or after that portfolio is deleted."
            ),
        },
    )
    upsample_frequency_id: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Upsample frequency",
            "description": (
                "Interval the prices are resampled to on the rebalance calendar before "
                "valuation; only 1d (daily) is accepted."
            ),
        },
    )
    intraday_bar_interpolation_rule: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        info={
            "label": "Interpolation rule",
            "description": (
                "How resampled times without a bar get a price; only ffill (carry the last "
                "price forward) is accepted."
            ),
        },
    )
    valuation_column: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        info={
            "label": "Valuation price",
            "description": "Bar price field that values each position, such as close.",
        },
    )
    valuation_maximum_staleness_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=86_400,
        info={
            "label": "Maximum price age",
            "description": (
                "Oldest a price may be, in seconds, to value an asset at a portfolio observation "
                "time; 86400 is one day."
            ),
        },
    )
    fail_on_missing_prices: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        info={
            "label": "Fail on missing prices",
            "description": (
                "Whether a run fails when an asset has no price within the maximum price age at "
                "an observation time."
            ),
        },
    )
    commission_fee: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.00018,
        info={
            "label": "Commission rate",
            "description": (
                "Trading cost per unit of turnover, as a fraction: each rebalance costs the sum of "
                "absolute weight changes times this rate, so 0.00018 is 1.8 basis points."
            ),
        },
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={"label": "Created at", "description": "Time, in UTC, the configuration was created."},
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        info={
            "label": "Updated at",
            "description": "Time, in UTC, the configuration last changed.",
        },
    )
    scheduled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
        info={
            "label": "Scheduled",
            "description": "Whether the scheduled portfolio update includes this configuration.",
        },
    )
    owner_gateway_uid: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        info={
            "label": "Owner gateway",
            "description": (
                "Gateway half of the portfolio's owner: the owner's own user UID for a Main "
                "Sequence user, or the gateway application's workload user UID for an external "
                "person."
            ),
        },
    )
    owner_user_uid: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={
            "label": "Owner",
            "description": (
                "Person half of the portfolio's owner: a Main Sequence user UID, or the "
                "gateway's own id for an external person."
            ),
        },
    )


class EndedPortfolioTable(ProjectStorageNameMixin, AlpacaMarketsMetaTableMixin, ProjectBase):
    """A deleted portfolio whose retired ``Portfolio`` or signal ms-markets still keeps.

    No key names the account, so the record never blocks ms-markets' account delete; the
    account delete reads it to finish what it may delete (ADR 0028, decision 8 and open
    question 2).
    """

    __project_storage_concept__ = "ended_portfolio"
    __markets_base_identifier__ = "AlpacaEndedPortfolio"
    __metatable_description__ = (
        "One row per deleted Alpaca portfolio whose retired Main Sequence portfolio, or whose "
        "signal, still exists: the account it belonged to, the portfolio and the signal it "
        "followed, kept until both can be deleted with the account; written by the Alpaca "
        "Connectors application when a portfolio is deleted."
    )
    __metatable_labels__ = ["portfolios", "configuration", "alpaca"]
    __table_args__ = markets_table_args(
        "AlpacaEndedPortfolio",
        Index(None, "account_uid"),
        Index(None, "portfolio_uid", unique=True),
        Index(None, "signal_uid"),
    )

    uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=new_markets_uid,
        info={"label": "UID", "description": "UUID of this record."},
    )
    account_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        info={
            "label": "Account UID",
            "description": (
                "Main Sequence account UID of the account the portfolio belonged to; no key, so "
                "the record outlives the account until its portfolio and signal are deleted."
            ),
        },
    )
    portfolio_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        info={
            "label": "Portfolio UID",
            "description": "Main Sequence portfolio UID of the retired portfolio.",
        },
    )
    portfolio_configuration_uid: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        info={
            "label": "Configuration UID",
            "description": "UID the deleted portfolio configuration had.",
        },
    )
    signal_uid: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        info={
            "label": "Signal UID",
            "description": "UID of the Main Sequence signal the portfolio followed.",
        },
    )
    ended_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        info={"label": "Ended at", "description": "Time, in UTC, the portfolio was deleted."},
    )


class PortfolioRebalanceConfiguration(MarketsMetaTableRow):
    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[PortfolioRebalanceConfigurationTable]] = (
        PortfolioRebalanceConfigurationTable
    )
    __required_tables__: ClassVar[list[type[ProjectBase]]] = [PortfolioRebalanceConfigurationTable]

    name: str
    description: str | None
    strategy: RebalanceStrategy
    calendar_identifier: str
    session_label: str
    rebalance_event: RebalanceEvent
    event_offset_seconds: int
    rebalance_cadence: RebalanceCadence
    rebalance_weekday: int
    portfolio_configuration_uid: uuid.UUID | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class AlpacaPortfolioConfiguration(MarketsMetaTableRow):
    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[AlpacaPortfolioConfigurationTable]] = AlpacaPortfolioConfigurationTable
    # Own tables only; the Account and Portfolio keys are references (ms-markets ADR 0047).
    __required_tables__: ClassVar[list[type[ProjectBase]]] = [
        SignalDefinitionTable,
        PortfolioRebalanceConfigurationTable,
        AlpacaPortfolioConfigurationTable,
    ]

    name: str
    description: str | None
    account_uid: uuid.UUID | None = None
    signal_definition_uid: uuid.UUID | None = None
    bars_configuration_uid: uuid.UUID | None = None
    rebalance_configuration_uid: uuid.UUID
    portfolio_uid: uuid.UUID | None
    scheduled: bool
    upsample_frequency_id: str
    intraday_bar_interpolation_rule: str
    valuation_column: str
    valuation_maximum_staleness_seconds: int
    fail_on_missing_prices: bool
    commission_fee: float
    owner_gateway_uid: str
    owner_user_uid: str
    created_at: dt.datetime
    updated_at: dt.datetime

    @property
    def adopted(self) -> bool:
        """Whether the portfolio belongs to an account and follows one of its signals."""
        return self.account_uid is not None and self.signal_definition_uid is not None


class EndedPortfolio(MarketsMetaTableRow):
    model_config = ConfigDict(extra="ignore", frozen=True)

    __table__: ClassVar[type[EndedPortfolioTable]] = EndedPortfolioTable
    __required_tables__: ClassVar[list[type[ProjectBase]]] = [EndedPortfolioTable]

    account_uid: uuid.UUID
    portfolio_uid: uuid.UUID
    portfolio_configuration_uid: uuid.UUID
    signal_uid: str
    ended_at: dt.datetime


def _name(value: str, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty.")
    if len(normalized) > 255:
        raise ValueError(f"{label} must be at most 255 characters.")
    return normalized


def _description(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _nonempty(value: str, *, label: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty.")
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be at most {maximum} characters.")
    return normalized


def valuation_column_value(value: str) -> str:
    """The valuation column, refused when ms-markets could not trade at it.

    A trade's price must be observed at its row's time, as a close or a mark is (ms-markets ADR
    0058); ms-markets' own rule (``require_observed_price_column``) decides which columns, and its
    ``ValueError`` is the refusal. A stored configuration that already holds such a column fails
    at its run with the same message.
    """
    from msm_portfolios.rebalance_strategy.base import require_observed_price_column

    normalized = _nonempty(value, label="valuation_column", maximum=64)
    require_observed_price_column(normalized)
    return normalized


def _phase_one_value(value: str, *, label: str, expected: str, maximum: int) -> str:
    normalized = _nonempty(value, label=label, maximum=maximum)
    if normalized != expected:
        raise ValueError(f"Phase 1 requires {label}={expected!r}.")
    return normalized


def normalize_rebalance_strategy(value: str) -> RebalanceStrategy:
    normalized = str(value or "").strip().lower()
    if normalized not in REBALANCE_STRATEGIES:
        raise ValueError(
            "The rebalance strategy must be calendar_event_signal or immediate_signal."
        )
    return normalized  # type: ignore[return-value]


def normalize_rebalance_event(value: str) -> RebalanceEvent:
    normalized = str(value or "").strip().lower()
    if normalized not in REBALANCE_EVENTS:
        raise ValueError("rebalance_event must be market_open or market_close.")
    return normalized  # type: ignore[return-value]


def normalize_rebalance_cadence(value: str) -> RebalanceCadence:
    normalized = str(value or "").strip().lower()
    if normalized not in REBALANCE_CADENCES:
        raise ValueError("rebalance_cadence must be every_session or weekly.")
    return normalized  # type: ignore[return-value]


def _rebalance_weekday(value: int) -> int:
    normalized = int(value)
    if normalized < 0 or normalized > 6:
        raise ValueError("rebalance_weekday must be between 0 (Monday) and 6 (Sunday).")
    return normalized


def _positive_seconds(value: int, *, label: str) -> int:
    normalized = int(value)
    if normalized <= 0:
        raise ValueError(f"{label} must be positive.")
    return normalized


def _start_runtime() -> None:
    from alpaca_connectors.runtime import application_runtime_models, start_markets_engine

    start_markets_engine(models=application_runtime_models())


def create_rebalance_configuration(
    *,
    name: str,
    strategy: str = "calendar_event_signal",
    description: str | None = None,
    calendar_identifier: str = DEFAULT_PORTFOLIO_CALENDAR_IDENTIFIER,
    session_label: str = "regular",
    rebalance_event: str = "market_close",
    event_offset_seconds: int = 0,
    rebalance_cadence: str = "every_session",
    rebalance_weekday: int = 0,
    uid: uuid.UUID | str | None = None,
    portfolio_configuration_uid: uuid.UUID | str | None = None,
) -> PortfolioRebalanceConfiguration:
    """Store a rebalance rule; ``portfolio_configuration_uid`` names the portfolio it is created
    with, inline, and goes with (issue #42). A rule created on its own is shared and has none."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    _start_runtime()
    now = utc_now()
    runtime = resolve_runtime(
        models=[PortfolioRebalanceConfigurationTable],
        row_model_name="PortfolioRebalanceConfiguration",
    )
    result = create_model(
        runtime.context,
        model=PortfolioRebalanceConfigurationTable,
        values={
            "uid": uuid.UUID(str(uid)) if uid is not None else new_markets_uid(),
            "name": _name(name, label="Rebalance configuration name"),
            "description": _description(description),
            "strategy": normalize_rebalance_strategy(strategy),
            "calendar_identifier": _nonempty(
                calendar_identifier,
                label="calendar_identifier",
                maximum=255,
            ),
            "session_label": _nonempty(
                session_label,
                label="session_label",
                maximum=64,
            ),
            "rebalance_event": normalize_rebalance_event(rebalance_event),
            "event_offset_seconds": int(event_offset_seconds),
            "rebalance_cadence": normalize_rebalance_cadence(rebalance_cadence),
            "rebalance_weekday": _rebalance_weekday(rebalance_weekday),
            "portfolio_configuration_uid": (
                uuid.UUID(str(portfolio_configuration_uid))
                if portfolio_configuration_uid is not None
                else None
            ),
            "created_at": now,
            "updated_at": now,
        },
    )
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Rebalance configuration create returned no row.")
    return PortfolioRebalanceConfiguration.model_validate(rows[0])


def get_rebalance_configuration(
    configuration_uid: uuid.UUID | str,
) -> PortfolioRebalanceConfiguration | None:
    _start_runtime()
    return PortfolioRebalanceConfiguration.get_by_uid(configuration_uid)


def rebalance_configurations_by_uids(
    configuration_uids: Sequence[uuid.UUID | str],
) -> dict[str, PortfolioRebalanceConfiguration]:
    """Load a rebalance configuration UID set with one governed backend query."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    normalized_uids = list(
        dict.fromkeys(uuid.UUID(str(configuration_uid)) for configuration_uid in configuration_uids)
    )
    if not normalized_uids:
        return {}
    _start_runtime()
    runtime = resolve_runtime(
        models=[PortfolioRebalanceConfigurationTable],
        row_model_name="PortfolioRebalanceConfiguration",
    )
    operation = compile_markets_statement(
        select(PortfolioRebalanceConfigurationTable).where(
            PortfolioRebalanceConfigurationTable.uid.in_(normalized_uids)
        ),
        context=runtime.context,
        operation="select",
    )
    rows = [
        PortfolioRebalanceConfiguration.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    ]
    return {str(row.uid): row for row in rows}


def list_rebalance_configurations(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    ordering: str = "name",
    uids: Sequence[uuid.UUID | str] | None = None,
) -> tuple[list[PortfolioRebalanceConfiguration], int]:
    """A page of rebalance configurations; ``uids`` keeps only those (issue #46)."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    _start_runtime()
    runtime = resolve_runtime(
        models=[PortfolioRebalanceConfigurationTable],
        row_model_name="PortfolioRebalanceConfiguration",
    )
    statement = select(PortfolioRebalanceConfigurationTable)
    if uids is not None:
        statement = statement.where(
            PortfolioRebalanceConfigurationTable.uid.in_([uuid.UUID(str(uid)) for uid in uids])
        )
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                PortfolioRebalanceConfigurationTable.name.ilike(pattern),
                PortfolioRebalanceConfigurationTable.description.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(
        statement.order_by(None).subquery()
    )
    descending = ordering.startswith("-")
    key = ordering.removeprefix("-")
    columns = {
        "name": PortfolioRebalanceConfigurationTable.name,
        "updated_at": PortfolioRebalanceConfigurationTable.updated_at,
    }
    if key not in columns:
        raise ValueError(f"Unsupported rebalance configuration ordering {ordering!r}.")
    order = columns[key].desc() if descending else columns[key].asc()
    statement = statement.order_by(order, PortfolioRebalanceConfigurationTable.uid.asc())
    page = compile_markets_statement(
        statement.limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
    )
    count = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
    )
    rows = [
        PortfolioRebalanceConfiguration.model_validate(row)
        for row in operation_result_rows(execute_markets_operation(page, context=runtime.context))
    ]
    count_rows = operation_result_rows(execute_markets_operation(count, context=runtime.context))
    return rows, int(count_rows[0].get("count", 0)) if count_rows else 0


def update_rebalance_configuration(
    configuration_uid: uuid.UUID | str,
    *,
    name: str | None = None,
    description: str | None | object = _UNSET,
    strategy: str | None = None,
    calendar_identifier: str | None = None,
    session_label: str | None = None,
    rebalance_event: str | None = None,
    event_offset_seconds: int | None = None,
    rebalance_cadence: str | None = None,
    rebalance_weekday: int | None = None,
) -> PortfolioRebalanceConfiguration:
    current = get_rebalance_configuration(configuration_uid)
    if current is None:
        raise LookupError(f"Rebalance configuration {configuration_uid!s} does not exist.")
    values: dict[str, Any] = {"updated_at": utc_now()}
    if name is not None:
        values["name"] = _name(name, label="Rebalance configuration name")
    if description is not _UNSET:
        values["description"] = _description(description if isinstance(description, str) else None)
    if strategy is not None:
        values["strategy"] = normalize_rebalance_strategy(strategy)
    if calendar_identifier is not None:
        values["calendar_identifier"] = _nonempty(
            calendar_identifier,
            label="calendar_identifier",
            maximum=255,
        )
    if session_label is not None:
        values["session_label"] = _nonempty(
            session_label,
            label="session_label",
            maximum=64,
        )
    if rebalance_event is not None:
        values["rebalance_event"] = normalize_rebalance_event(rebalance_event)
    if event_offset_seconds is not None:
        values["event_offset_seconds"] = int(event_offset_seconds)
    if rebalance_cadence is not None:
        values["rebalance_cadence"] = normalize_rebalance_cadence(rebalance_cadence)
    if rebalance_weekday is not None:
        values["rebalance_weekday"] = _rebalance_weekday(rebalance_weekday)
    return PortfolioRebalanceConfiguration.update(configuration_uid, values)


def delete_rebalance_configuration(configuration_uid: uuid.UUID | str) -> dict[str, Any]:
    if get_rebalance_configuration(configuration_uid) is None:
        raise LookupError(f"Rebalance configuration {configuration_uid!s} does not exist.")
    return PortfolioRebalanceConfiguration.delete(configuration_uid)


def validate_portfolio_references(
    *,
    rebalance_configuration_uid: uuid.UUID,
) -> PortfolioRebalanceConfiguration:
    """Resolve the rebalance configuration a portfolio names.

    Rebalance configurations stay independent rules that any portfolio may name (ADR 0028,
    decision 6); the portfolio's bars configuration is its own and names it instead.
    """
    rebalance = get_rebalance_configuration(rebalance_configuration_uid)
    if rebalance is None:
        raise LookupError(
            f"Rebalance configuration {rebalance_configuration_uid!s} does not exist."
        )
    normalize_rebalance_strategy(rebalance.strategy)
    return rebalance


def create_portfolio_configuration_row(
    *,
    name: str,
    account_uid: uuid.UUID | str,
    signal_definition_uid: uuid.UUID | str | None,
    rebalance_configuration_uid: uuid.UUID | str,
    owner_gateway_uid: str,
    owner_user_uid: str,
    description: str | None = None,
    upsample_frequency_id: str = "1d",
    intraday_bar_interpolation_rule: str = "ffill",
    valuation_column: str = "close",
    valuation_maximum_staleness_seconds: int = 86_400,
    fail_on_missing_prices: bool = True,
    commission_fee: float = 0.00018,
    scheduled: bool = True,
    uid: uuid.UUID | str | None = None,
    rebalance: PortfolioRebalanceConfiguration | None = None,
) -> AlpacaPortfolioConfiguration:
    """Store one portfolio configuration of an account, and its owner; it touches no platform Job.

    The account's owner owns its portfolios (ADR 0028, decision 3): the caller has checked that
    the owner given here owns ``account_uid``. ``signal_definition_uid`` is ``None`` only while
    the route of ADR 0024 creates the portfolio's own definition right after. ``rebalance`` is
    the named rule when the caller already holds it (it resolved it, or just created it), so it
    is not read again (issue #46).
    """
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import create_model

    if not owner_gateway_uid or not owner_user_uid:
        raise ValueError("A portfolio requires its owner.")
    rebalance_uid = uuid.UUID(str(rebalance_configuration_uid))
    if commission_fee < 0:
        raise ValueError("commission_fee must be greater than or equal to zero.")
    _start_runtime()
    if rebalance is None:
        validate_portfolio_references(rebalance_configuration_uid=rebalance_uid)
    now = utc_now()
    values = {
        "uid": uuid.UUID(str(uid)) if uid is not None else new_markets_uid(),
        "name": _name(name, label="Portfolio configuration name"),
        "description": _description(description),
        "owner_gateway_uid": owner_gateway_uid,
        "owner_user_uid": owner_user_uid,
        "account_uid": uuid.UUID(str(account_uid)),
        "signal_definition_uid": (
            uuid.UUID(str(signal_definition_uid)) if signal_definition_uid is not None else None
        ),
        "bars_configuration_uid": None,
        "rebalance_configuration_uid": rebalance_uid,
        "portfolio_uid": None,
        "scheduled": bool(scheduled),
        "upsample_frequency_id": _phase_one_value(
            upsample_frequency_id,
            label="upsample_frequency_id",
            expected="1d",
            maximum=16,
        ),
        "intraday_bar_interpolation_rule": _phase_one_value(
            intraday_bar_interpolation_rule,
            label="intraday_bar_interpolation_rule",
            expected="ffill",
            maximum=16,
        ),
        "valuation_column": valuation_column_value(valuation_column),
        "valuation_maximum_staleness_seconds": _positive_seconds(
            valuation_maximum_staleness_seconds,
            label="valuation_maximum_staleness_seconds",
        ),
        "fail_on_missing_prices": bool(fail_on_missing_prices),
        "commission_fee": float(commission_fee),
        "created_at": now,
        "updated_at": now,
    }
    runtime = resolve_runtime(
        models=[AlpacaPortfolioConfigurationTable],
        row_model_name="AlpacaPortfolioConfiguration",
    )
    result = create_model(
        runtime.context,
        model=AlpacaPortfolioConfigurationTable,
        values=values,
    )
    rows = operation_result_rows(result)
    if not rows:
        raise RuntimeError("Portfolio configuration create returned no row.")
    return AlpacaPortfolioConfiguration.model_validate(rows[0])


def get_portfolio_configuration(
    configuration_uid: uuid.UUID | str,
) -> AlpacaPortfolioConfiguration | None:
    _start_runtime()
    return AlpacaPortfolioConfiguration.get_by_uid(configuration_uid)


def list_portfolio_configurations(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    account_uids: Sequence[uuid.UUID | str] | None = None,
    signal_definition_uid: uuid.UUID | str | None = None,
    key: str | None = None,
    ordering: str = "name",
    uids: Sequence[uuid.UUID | str] | None = None,
) -> tuple[list[AlpacaPortfolioConfiguration], int]:
    """A page of portfolio configurations; ``key`` keeps those whose signal definition has it,
    the key a provided-weights create was given (issue #43), and ``uids`` only those (#46)."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    _start_runtime()
    runtime = resolve_runtime(
        models=[AlpacaPortfolioConfigurationTable],
        row_model_name="AlpacaPortfolioConfiguration",
    )
    statement = select(AlpacaPortfolioConfigurationTable)
    if account_uids is not None:
        accounts = [uuid.UUID(str(uid)) for uid in account_uids]
        if not accounts:
            return [], 0
        statement = statement.where(AlpacaPortfolioConfigurationTable.account_uid.in_(accounts))
    if uids is not None:
        statement = statement.where(
            AlpacaPortfolioConfigurationTable.uid.in_([uuid.UUID(str(uid)) for uid in uids])
        )
    if signal_definition_uid is not None:
        statement = statement.where(
            AlpacaPortfolioConfigurationTable.signal_definition_uid
            == uuid.UUID(str(signal_definition_uid))
        )
    if key is not None:
        statement = statement.where(
            AlpacaPortfolioConfigurationTable.signal_definition_uid.in_(
                select(SignalDefinitionTable.uid).where(SignalDefinitionTable.key == key)
            )
        )
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                AlpacaPortfolioConfigurationTable.name.ilike(pattern),
                AlpacaPortfolioConfigurationTable.description.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(
        statement.order_by(None).subquery()
    )
    descending = ordering.startswith("-")
    key = ordering.removeprefix("-")
    columns = {
        "name": AlpacaPortfolioConfigurationTable.name,
        "updated_at": AlpacaPortfolioConfigurationTable.updated_at,
    }
    if key not in columns:
        raise ValueError(f"Unsupported portfolio configuration ordering {ordering!r}.")
    order = columns[key].desc() if descending else columns[key].asc()
    statement = statement.order_by(order, AlpacaPortfolioConfigurationTable.uid.asc())
    page = compile_markets_statement(
        statement.limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
    )
    count = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
    )
    rows = [
        AlpacaPortfolioConfiguration.model_validate(row)
        for row in operation_result_rows(execute_markets_operation(page, context=runtime.context))
    ]
    count_rows = operation_result_rows(execute_markets_operation(count, context=runtime.context))
    return rows, int(count_rows[0].get("count", 0)) if count_rows else 0


def update_portfolio_configuration_row(
    configuration_uid: uuid.UUID | str,
    *,
    values: dict[str, Any],
) -> AlpacaPortfolioConfiguration:
    """Write ``values`` to one row in one statement; its ``RETURNING`` row is the answer.

    No read first (issue #46): an update that returns no row finds no configuration.
    """
    _start_runtime()
    try:
        return AlpacaPortfolioConfiguration.update(
            configuration_uid,
            {**values, "updated_at": utc_now()},
        )
    except LookupError as exc:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.") from exc


def update_portfolio_configuration(
    configuration_uid: uuid.UUID | str,
    *,
    name: str | None = None,
    description: str | None | object = _UNSET,
    rebalance_configuration_uid: uuid.UUID | str | None = None,
    upsample_frequency_id: str | None = None,
    intraday_bar_interpolation_rule: str | None = None,
    valuation_column: str | None = None,
    valuation_maximum_staleness_seconds: int | None = None,
    fail_on_missing_prices: bool | None = None,
    commission_fee: float | None = None,
    scheduled: bool | None = None,
) -> AlpacaPortfolioConfiguration:
    """Update calculation fields, the scheduled flag, or both.

    Changing only ``scheduled`` (pause or resume) skips the reference validation. The account and
    the signal definition never change; the bars profile is changed on the portfolio's own bars
    configuration.
    """
    current = get_portfolio_configuration(configuration_uid)
    if current is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    calculation_values = (
        name,
        rebalance_configuration_uid,
        upsample_frequency_id,
        intraday_bar_interpolation_rule,
        valuation_column,
        valuation_maximum_staleness_seconds,
        fail_on_missing_prices,
        commission_fee,
    )
    if description is _UNSET and all(value is None for value in calculation_values):
        if scheduled is None:
            raise ValueError("At least one portfolio configuration field must be provided.")
        return update_portfolio_configuration_row(
            configuration_uid,
            values={"scheduled": bool(scheduled)},
        )
    rebalance_uid = uuid.UUID(
        str(rebalance_configuration_uid or current.rebalance_configuration_uid)
    )
    validate_portfolio_references(rebalance_configuration_uid=rebalance_uid)
    values: dict[str, Any] = {"rebalance_configuration_uid": rebalance_uid}
    if name is not None:
        values["name"] = _name(name, label="Portfolio configuration name")
    if description is not _UNSET:
        values["description"] = _description(description if isinstance(description, str) else None)
    if upsample_frequency_id is not None:
        values["upsample_frequency_id"] = _phase_one_value(
            upsample_frequency_id,
            label="upsample_frequency_id",
            expected="1d",
            maximum=16,
        )
    if intraday_bar_interpolation_rule is not None:
        values["intraday_bar_interpolation_rule"] = _phase_one_value(
            intraday_bar_interpolation_rule,
            label="intraday_bar_interpolation_rule",
            expected="ffill",
            maximum=16,
        )
    if valuation_column is not None:
        values["valuation_column"] = valuation_column_value(valuation_column)
    if valuation_maximum_staleness_seconds is not None:
        values["valuation_maximum_staleness_seconds"] = _positive_seconds(
            valuation_maximum_staleness_seconds,
            label="valuation_maximum_staleness_seconds",
        )
    if fail_on_missing_prices is not None:
        values["fail_on_missing_prices"] = bool(fail_on_missing_prices)
    if commission_fee is not None:
        if commission_fee < 0:
            raise ValueError("commission_fee must be greater than or equal to zero.")
        values["commission_fee"] = float(commission_fee)
    if scheduled is not None:
        values["scheduled"] = bool(scheduled)
    return update_portfolio_configuration_row(configuration_uid, values=values)


def scheduled_portfolio_configurations() -> list[AlpacaPortfolioConfiguration]:
    """Return every portfolio configuration the portfolio Job's scheduled run includes.

    A portfolio not yet adopted runs as every other one does (ADR 0028, as amended).
    """
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    table = AlpacaPortfolioConfigurationTable
    _start_runtime()
    runtime = resolve_runtime(models=[table], row_model_name="AlpacaPortfolioConfiguration")
    operation = compile_markets_statement(
        select(table).where(table.scheduled.is_(True)).order_by(table.name.asc(), table.uid.asc()),
        context=runtime.context,
        operation="select",
    )
    return [
        AlpacaPortfolioConfiguration.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    ]


def delete_portfolio_configuration_row(configuration_uid: uuid.UUID | str) -> dict[str, Any]:
    if get_portfolio_configuration(configuration_uid) is None:
        raise LookupError(f"Portfolio configuration {configuration_uid!s} does not exist.")
    return AlpacaPortfolioConfiguration.delete(configuration_uid)


def _uuid_list(values: Sequence[Any]) -> list[uuid.UUID]:
    return list(dict.fromkeys(uuid.UUID(str(value)) for value in values if value is not None))


def _select_rows(table: type[ProjectBase], statement: Any) -> list[dict[str, Any]]:
    """Run one select of this application's own tables and answer its rows."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation

    _start_runtime()
    runtime = resolve_runtime(models=[table], row_model_name=table.__markets_base_identifier__)
    operation = compile_markets_statement(statement, context=runtime.context, operation="select")
    return operation_result_rows(execute_markets_operation(operation, context=runtime.context))


def delete_rows_in(table: type[ProjectBase], column: str, values: Sequence[Any]) -> None:
    """Delete the rows whose ``column`` is one of ``values``: one statement per batch (#46)."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import delete

    from alpaca_connectors.markets_api import write_in_batches

    if not values:
        return
    _start_runtime()
    runtime = resolve_runtime(models=[table], row_model_name=table.__markets_base_identifier__)

    def write(batch: list[Any]) -> None:
        operation = compile_markets_statement(
            delete(table).where(getattr(table, column).in_(batch)),
            context=runtime.context,
            operation="delete",
        )
        execute_markets_operation(operation, context=runtime.context)

    write_in_batches(write, values)


def delete_portfolio_configuration_rows(configuration_uids: Sequence[uuid.UUID | str]) -> None:
    """Delete several portfolio configurations in one statement; their own bars go with them."""
    delete_rows_in(AlpacaPortfolioConfigurationTable, "uid", _uuid_list(configuration_uids))


def inline_rebalance_configurations(
    portfolio_configuration_uids: Sequence[uuid.UUID | str],
) -> dict[str, PortfolioRebalanceConfiguration]:
    """The rules created inline with these portfolios (issue #42), by portfolio, in one read."""
    from sqlalchemy import select

    from alpaca_connectors.markets_api import read_in_batches

    table = PortfolioRebalanceConfigurationTable
    rows = read_in_batches(
        lambda batch: _select_rows(
            table, select(table).where(table.portfolio_configuration_uid.in_(batch))
        ),
        _uuid_list(portfolio_configuration_uids),
    )
    return {
        str(uuid.UUID(str(row["portfolio_configuration_uid"]))): (
            PortfolioRebalanceConfiguration.model_validate(row)
        )
        for row in rows
    }


def rebalance_configuration_uids_named(
    rebalance_configuration_uids: Sequence[uuid.UUID | str],
) -> set[str]:
    """Which of these rules a portfolio configuration names, in one read."""
    from sqlalchemy import select

    from alpaca_connectors.markets_api import read_in_batches

    table = AlpacaPortfolioConfigurationTable
    rows = read_in_batches(
        lambda batch: _select_rows(
            table,
            select(table.rebalance_configuration_uid)
            .where(table.rebalance_configuration_uid.in_(batch))
            .distinct(),
        ),
        _uuid_list(rebalance_configuration_uids),
    )
    return {str(uuid.UUID(str(row["rebalance_configuration_uid"]))) for row in rows}


def delete_rebalance_configuration_rows(configuration_uids: Sequence[uuid.UUID | str]) -> None:
    """Delete several rebalance configurations in one statement."""
    delete_rows_in(PortfolioRebalanceConfigurationTable, "uid", _uuid_list(configuration_uids))


def portfolio_configurations_of_account(
    account_uid: uuid.UUID | str,
) -> list[AlpacaPortfolioConfiguration]:
    """Every portfolio configuration of the account, in pages (ADR 0028, decision 8)."""
    from alpaca_connectors.markets_api import MARKETS_BATCH_SIZE

    rows: list[AlpacaPortfolioConfiguration] = []
    while True:
        page, total = list_portfolio_configurations(
            limit=MARKETS_BATCH_SIZE, offset=len(rows), account_uids=[account_uid]
        )
        rows.extend(page)
        if not page or len(rows) >= total:
            return rows


def portfolio_configurations_naming_bars(
    bars_configuration_uid: uuid.UUID | str,
) -> list[AlpacaPortfolioConfiguration]:
    """The portfolios that name a bars feed: those not yet adopted, priced by it (ADR 0024)."""
    return portfolio_configurations_naming_any_bars([bars_configuration_uid])


def portfolio_configurations_naming_any_bars(
    bars_configuration_uids: Sequence[uuid.UUID | str],
) -> list[AlpacaPortfolioConfiguration]:
    """The portfolios that name any of these bars feeds, in one read (issue #46)."""
    from sqlalchemy import select

    from alpaca_connectors.markets_api import read_in_batches

    table = AlpacaPortfolioConfigurationTable
    return [
        AlpacaPortfolioConfiguration.model_validate(row)
        for row in read_in_batches(
            lambda batch: _select_rows(
                table, select(table).where(table.bars_configuration_uid.in_(batch))
            ),
            _uuid_list(bars_configuration_uids),
        )
    ]


def unadopted_portfolio_configurations() -> list[AlpacaPortfolioConfiguration]:
    """Every portfolio without an account or a signal definition yet (ADR 0028, decision 9)."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import or_, select

    table = AlpacaPortfolioConfigurationTable
    _start_runtime()
    runtime = resolve_runtime(models=[table], row_model_name="AlpacaPortfolioConfiguration")
    operation = compile_markets_statement(
        select(table)
        .where(or_(table.account_uid.is_(None), table.signal_definition_uid.is_(None)))
        .order_by(table.name.asc(), table.uid.asc()),
        context=runtime.context,
        operation="select",
    )
    return [
        AlpacaPortfolioConfiguration.model_validate(row)
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    ]


def portfolio_counts_by_definition(
    signal_definition_uids: Sequence[uuid.UUID | str],
) -> dict[str, int]:
    """How many portfolios follow each signal definition, in one grouped read."""
    from msm.bootstrap import resolve_runtime
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, select

    uids = sorted({uuid.UUID(str(uid)) for uid in signal_definition_uids if uid is not None})
    if not uids:
        return {}
    table = AlpacaPortfolioConfigurationTable
    _start_runtime()
    runtime = resolve_runtime(models=[table], row_model_name="AlpacaPortfolioConfiguration")
    operation = compile_markets_statement(
        select(table.signal_definition_uid, func.count().label("count"))
        .where(table.signal_definition_uid.in_(uids))
        .group_by(table.signal_definition_uid),
        context=runtime.context,
        operation="select",
    )
    return {
        str(uuid.UUID(str(row["signal_definition_uid"]))): int(row["count"])
        for row in operation_result_rows(
            execute_markets_operation(operation, context=runtime.context)
        )
    }


def record_ended_portfolio(
    *,
    account_uid: uuid.UUID | str,
    portfolio_uid: uuid.UUID | str,
    portfolio_configuration_uid: uuid.UUID | str,
    signal_uid: str,
) -> EndedPortfolio:
    """Record a retired ``Portfolio`` and its signal under the account it belonged to.

    The record has no key to the account, so it never blocks ms-markets' account delete, which
    reads it to delete them (ADR 0028, decision 8). Recording the same portfolio again returns
    the record already stored.
    """
    return record_ended_portfolios(
        [
            {
                "account_uid": account_uid,
                "portfolio_uid": portfolio_uid,
                "portfolio_configuration_uid": portfolio_configuration_uid,
                "signal_uid": signal_uid,
            }
        ]
    )[0]


def record_ended_portfolios(records: Sequence[dict[str, Any]]) -> list[EndedPortfolio]:
    """Record several retired ``Portfolio`` rows and their signals: one read, one insert (#46).

    Each record names ``account_uid``, ``portfolio_uid``, ``portfolio_configuration_uid`` and
    ``signal_uid``. A portfolio recorded already keeps the record stored; the answer is each
    portfolio's record, in the order given.
    """
    from msm.bootstrap import resolve_runtime
    from msm.repositories.crud import bulk_upsert_model
    from sqlalchemy import select

    from alpaca_connectors.markets_api import read_in_batches

    if not records:
        return []
    table = EndedPortfolioTable
    portfolio_uids = _uuid_list([record["portfolio_uid"] for record in records])
    stored = {
        str(row.portfolio_uid): row
        for row in (
            EndedPortfolio.model_validate(row)
            for row in read_in_batches(
                lambda batch: _select_rows(
                    table, select(table).where(table.portfolio_uid.in_(batch))
                ),
                portfolio_uids,
            )
        )
    }
    now = utc_now()
    missing = {
        str(uuid.UUID(str(record["portfolio_uid"]))): {
            "uid": new_markets_uid(),
            "account_uid": uuid.UUID(str(record["account_uid"])),
            "portfolio_uid": uuid.UUID(str(record["portfolio_uid"])),
            "portfolio_configuration_uid": uuid.UUID(str(record["portfolio_configuration_uid"])),
            "signal_uid": str(record["signal_uid"]),
            "ended_at": now,
        }
        for record in records
        if str(uuid.UUID(str(record["portfolio_uid"]))) not in stored
    }
    if missing:
        runtime = resolve_runtime(models=[table], row_model_name=table.__markets_base_identifier__)
        result = bulk_upsert_model(
            runtime.context,
            model=table,
            values=list(missing.values()),
            conflict_columns=("portfolio_uid",),
        )
        for row in operation_result_rows(result):
            created = EndedPortfolio.model_validate(row)
            stored[str(created.portfolio_uid)] = created
    return [stored[str(uuid.UUID(str(record["portfolio_uid"])))] for record in records]


def ended_portfolios_of_account(account_uid: uuid.UUID | str) -> list[EndedPortfolio]:
    """The account's ended portfolios whose retired ``Portfolio`` or signal still exist."""
    from alpaca_connectors.markets_api import MARKETS_BATCH_SIZE

    _start_runtime()
    rows: list[EndedPortfolio] = []
    while True:
        page = EndedPortfolio.filter(
            account_uid=uuid.UUID(str(account_uid)),
            limit=MARKETS_BATCH_SIZE,
            offset=len(rows),
        )
        rows.extend(page)
        if len(page) < MARKETS_BATCH_SIZE:
            return rows


def forget_ended_portfolio(record: EndedPortfolio) -> None:
    """Delete the record once its ``Portfolio`` is deleted."""
    forget_ended_portfolios([record])


def forget_ended_portfolios(records: Sequence[EndedPortfolio]) -> None:
    """Delete several records, once their ``Portfolio`` rows are deleted, in one statement."""
    delete_rows_in(EndedPortfolioTable, "uid", _uuid_list([record.uid for record in records]))


def ended_signal_uids(signal_uids: Sequence[str]) -> set[str]:
    """Which of these signals a recorded, retired ``Portfolio`` still follows, in one read."""
    from sqlalchemy import select

    from alpaca_connectors.markets_api import read_in_batches

    table = EndedPortfolioTable
    values = list(dict.fromkeys(str(uid) for uid in signal_uids))
    rows = read_in_batches(
        lambda batch: _select_rows(
            table, select(table.signal_uid).where(table.signal_uid.in_(batch)).distinct()
        ),
        values,
    )
    return {str(row["signal_uid"]) for row in rows}


def project_portfolio_configuration_models() -> list[type[ProjectBase]]:
    from alpaca_connectors.portfolios.signal_assets import SignalAssetTable

    return [
        SignalDefinitionTable,
        PortfolioRebalanceConfigurationTable,
        AlpacaPortfolioConfigurationTable,
        EndedPortfolioTable,
        SignalAssetTable,
    ]


__all__ = [
    "AlpacaPortfolioConfiguration",
    "AlpacaPortfolioConfigurationTable",
    "EndedPortfolio",
    "EndedPortfolioTable",
    "ended_portfolios_of_account",
    "delete_portfolio_configuration_rows",
    "delete_rows_in",
    "delete_rebalance_configuration_rows",
    "ended_signal_uids",
    "forget_ended_portfolio",
    "forget_ended_portfolios",
    "inline_rebalance_configurations",
    "portfolio_configurations_naming_any_bars",
    "rebalance_configuration_uids_named",
    "record_ended_portfolios",
    "portfolio_configurations_of_account",
    "portfolio_counts_by_definition",
    "record_ended_portfolio",
    "portfolio_configurations_naming_bars",
    "unadopted_portfolio_configurations",
    "PortfolioRebalanceConfiguration",
    "PortfolioRebalanceConfigurationTable",
    "DEFAULT_PORTFOLIO_CALENDAR_IDENTIFIER",
    "REBALANCE_CADENCES",
    "REBALANCE_EVENTS",
    "REBALANCE_STRATEGIES",
    "create_portfolio_configuration_row",
    "create_rebalance_configuration",
    "delete_portfolio_configuration_row",
    "delete_rebalance_configuration",
    "get_portfolio_configuration",
    "get_rebalance_configuration",
    "list_portfolio_configurations",
    "list_rebalance_configurations",
    "normalize_rebalance_cadence",
    "normalize_rebalance_event",
    "normalize_rebalance_strategy",
    "project_portfolio_configuration_models",
    "rebalance_configurations_by_uids",
    "scheduled_portfolio_configurations",
    "update_portfolio_configuration",
    "update_portfolio_configuration_row",
    "update_rebalance_configuration",
    "validate_portfolio_references",
]
