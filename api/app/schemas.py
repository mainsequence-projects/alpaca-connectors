from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from msm.api.http import (
    BulkActionExecutionRequest,
    BulkActionExplicitSelection,
    ObservableOperation,
    OperationError,
    OperationStep,
    ResourceDiscovery,
    ResourcePageInfo,
)
from msm.api.http import ResourceCollection as MarketsResourceCollection
from pydantic import BaseModel, ConfigDict, Field, model_validator


def _normalize_symbol_list(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    normalized = [value.strip().upper() for value in values if value and value.strip()]
    return list(dict.fromkeys(normalized)) or None


class HealthResponse(BaseModel):
    status: Literal["ok"]


class CapabilitySummary(BaseModel):
    key: str
    name: str
    purpose: str
    contents: list[str]
    actions: list[str]
    surfaces: list[str]
    availability: Literal["available", "partial"]


class CapabilityCatalogResponse(BaseModel):
    capabilities: list[CapabilitySummary]


class ProjectConfigurationResponse(BaseModel):
    api_version: str = "0.1.27"
    supported_component_providers: list[str]
    migrated_market_data_profiles: list[str]
    universe_sources_are_user_managed: bool = True
    organization_environment_uid: str | None = None
    organization_environment_name: str | None = None
    request_user_uid: str | None = None
    registered_account_count: int = 0
    has_registered_account: bool = False
    universe_source_count: int = 0
    market_data_dataset_count: int = 0
    market_data_datasets: list[dict[str, Any]] = Field(default_factory=list)


class AssetRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_uid: str = Field(
        min_length=1,
        description=(
            "Registered Alpaca Account UID whose stored Main Sequence Secret references are used "
            "for provider access."
        ),
    )
    symbols: list[str] = Field(
        min_length=1,
        description="Exact symbols to resolve against Alpaca without component extraction.",
        examples=[["AAPL", "MSFT", "NVDA"]],
    )
    timeout: float = Field(
        default=30.0,
        gt=0,
        le=300.0,
        description="HTTP timeout in seconds for Alpaca and optional enrichment requests.",
    )

    @model_validator(mode="after")
    def validate_scope(self) -> "AssetRegistrationRequest":
        self.account_uid = self.account_uid.strip()
        if not self.account_uid:
            raise ValueError("account_uid is required.")
        self.symbols = _normalize_symbol_list(self.symbols) or []
        if not self.symbols:
            raise ValueError("At least one exact symbol must be provided.")
        return self


class AssetRegistrationDiscoveryResponse(BaseModel):
    request: AssetRegistrationRequest
    plan_summary: dict[str, Any]
    resolution_summary: dict[str, Any]
    can_register: bool
    missing_symbols_from_alpaca: list[str]
    missing_symbols_to_register: list[str]
    openfigi_unmatched_symbols: list[str]
    warnings_by_symbol: dict[str, str]


class AssetRegistrationExecuteResponse(BaseModel):
    request: AssetRegistrationRequest
    plan_summary: dict[str, Any]
    resolution_summary: dict[str, Any]
    assets_by_symbol: dict[str, str]
    existing_asset_uids_by_symbol: dict[str, str]
    created_asset_uids_by_symbol: dict[str, str]
    not_registered_missing_alpaca_symbols: list[str]
    openfigi_unmatched_symbols: list[str]
    warnings_by_symbol: dict[str, str]


class AssetRegistrationOperationStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["plan", "execute"]
    request: AssetRegistrationRequest


class AssetRegistrationStepResponse(OperationStep):
    """Alpaca schema name for the shared observable-operation step."""


class AssetRegistrationOperationError(OperationError):
    """Alpaca schema name for the shared observable-operation error."""


class AssetRegistrationOperationResponse(
    ObservableOperation[AssetRegistrationRequest, dict[str, Any]]
):
    action: Literal["plan", "execute"]
    steps: list[AssetRegistrationStepResponse]
    error: AssetRegistrationOperationError | None = None


class PageInfo(ResourcePageInfo):
    """Alpaca schema name for the shared collection page contract."""


class ResourceCollection(MarketsResourceCollection[dict[str, Any]]):
    """Alpaca schema name for the shared resource collection contract."""

    page_info: PageInfo = Field(alias="pageInfo")


class ResourceDiscoveryResponse(ResourceDiscovery):
    """Alpaca schema name for the shared resource discovery contract."""


class AccountRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_name: str | None = Field(default=None, max_length=255)
    environment: Literal["paper", "live"] = "paper"
    api_key_secret_name: str = Field(min_length=1, max_length=255)
    secret_key_secret_name: str = Field(min_length=1, max_length=255)


class AccountUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_name: str | None = Field(default=None, min_length=1, max_length=255)
    api_key_secret_name: str | None = Field(default=None, min_length=1, max_length=255)
    secret_key_secret_name: str | None = Field(default=None, min_length=1, max_length=255)
    account_is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "AccountUpdateRequest":
        if all(getattr(self, field_name) is None for field_name in self.__class__.model_fields):
            raise ValueError("At least one mutable account field must be provided.")
        return self


class AccountResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    uid: str
    account_uid: str
    unique_identifier: str
    account_name: str
    is_paper: bool
    account_is_active: bool
    api_key_secret_name: str
    secret_key_secret_name: str
    status: str | None = None
    currency: str | None = None
    snapshot_time: Any | None = None
    equity: Any | None = None
    cash: Any | None = None
    buying_power: Any | None = None


class SecretReferenceResponse(BaseModel):
    """Safe Secret metadata exposed to credential-reference pickers."""

    model_config = ConfigDict(extra="forbid")

    name: str


class SecretReferenceCollectionResponse(MarketsResourceCollection[SecretReferenceResponse]):
    """Typed Secret-name collection over the shared pagination contract."""

    page_info: PageInfo = Field(alias="pageInfo")


class BulkSelection(BulkActionExplicitSelection):
    """Alpaca actions currently support explicit string UID selection only."""

    uids: list[str] = Field(min_length=1)


class BulkActionRequest(BulkActionExecutionRequest):
    selection: BulkSelection
    options: dict[str, Any] = Field(default_factory=dict)


class HoldingsCaptureRequest(BaseModel):
    """Holdings capture has no relaxations; complete asset registration is mandatory."""

    model_config = ConfigDict(extra="forbid")


class HoldingsCaptureResponse(BaseModel):
    account_uid: str
    holdings_set_uid: str | None
    time_index: Any
    holdings_rows: int
    unresolved_symbols: list[str]


class UniverseSourceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    symbol: str = Field(min_length=1, max_length=32)
    source_url: str = Field(min_length=1, max_length=2048)
    enabled: bool = True


class UniverseSourceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    source_url: str | None = Field(default=None, min_length=1, max_length=2048)
    enabled: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "UniverseSourceUpdateRequest":
        if all(getattr(self, field_name) is None for field_name in self.__class__.model_fields):
            raise ValueError("At least one universe-source field must be provided.")
        return self


class UniverseSourceResponse(BaseModel):
    uid: str
    name: str
    symbol: str
    source_url: str
    enabled: bool
    created_at: Any
    updated_at: Any


class UniverseSourceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout: float = Field(default=30.0, gt=0, le=300)


class UniverseSourcePreviewResponse(BaseModel):
    source: UniverseSourceResponse
    plan_summary: dict[str, Any]
    has_blockers: bool


class AssetUniverseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "AssetUniverseUpdateRequest":
        if all(getattr(self, field_name) is None for field_name in self.__class__.model_fields):
            raise ValueError("At least one universe display field must be provided.")
        return self


class AssetUniverseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    symbol: str = Field(min_length=1, max_length=32)
    source_url: str = Field(min_length=1, max_length=2048)


class AssetCategorySummaryResponse(BaseModel):
    uid: str
    unique_identifier: str
    display_name: str
    description: str | None = None


class AssetUniverseResponse(BaseModel):
    uid: str
    source_uid: str
    asset_category_uid: str
    display_name: str
    symbol: str
    source_url: str
    description: str | None = None
    is_active: bool
    asset_count: int
    asset_category: AssetCategorySummaryResponse
    created_at: Any
    updated_at: Any


class BarConfigurationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    enabled: bool = True
    account_uid: str
    asset_source: Literal["assets", "universe", "account_holdings"]
    asset_uids: list[str] = Field(default_factory=list)
    universe_uid: str | None = None
    frequency_id: str
    feed: str
    adjustment: str

    @model_validator(mode="after")
    def validate_source(self) -> "BarConfigurationCreateRequest":
        if self.asset_source == "assets" and (not self.asset_uids or self.universe_uid):
            raise ValueError("assets source requires asset_uids and forbids universe_uid.")
        if self.asset_source == "universe" and (not self.universe_uid or self.asset_uids):
            raise ValueError("universe source requires universe_uid and forbids asset_uids.")
        if self.asset_source == "account_holdings" and (self.asset_uids or self.universe_uid):
            raise ValueError("account_holdings source forbids asset_uids and universe_uid.")
        return self


class BarConfigurationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    enabled: bool | None = None
    account_uid: str | None = None
    asset_source: Literal["assets", "universe", "account_holdings"] | None = None
    asset_uids: list[str] | None = None
    universe_uid: str | None = None
    frequency_id: str | None = None
    feed: str | None = None
    adjustment: str | None = None

    @model_validator(mode="after")
    def require_change(self) -> "BarConfigurationUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one bar-configuration field must be provided.")
        return self


class BarConfigurationResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hash_namespace: str | None = None


class BarConfigurationUpdateActionRequest(BaseModel):
    """An intentionally empty body; the path UID is the complete execution input."""

    model_config = ConfigDict(extra="forbid")


class BarConfigurationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    name: str
    description: str | None
    enabled: bool
    account_uid: str
    asset_source: Literal["assets", "universe", "account_holdings"]
    asset_uids: list[str]
    universe_uid: str | None
    frequency_id: str
    feed: str
    adjustment: str
    created_at: dt.datetime
    updated_at: dt.datetime


class BarConfigurationResolutionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    configuration: BarConfigurationResponse
    dataset: dict[str, Any]
    account_uid: str
    asset_source: Literal["assets", "universe", "account_holdings"]
    asset_count: int
    asset_uids: list[str]
    asset_identifiers: list[str]
    source_snapshot: dict[str, Any] | None = None


class BarConfigurationUpdateAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configuration_uid: str
    job_uid: str
    job_run_uid: str
    status: str
    status_url: str
    poll_after_ms: int = 1000


class SignalJobConfigurationCreateRequest(BaseModel):
    """Create one durable signal configuration and its dedicated platform Job."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    universe_uid: str = Field(min_length=1)
    account_uid: str = Field(min_length=1)
    enabled: bool = True
    schedule_type: Literal["interval", "crontab"]
    schedule_every: int | None = Field(default=None, gt=0)
    schedule_period: Literal["seconds", "minutes", "hours", "days"] | None = None
    schedule_expression: str | None = Field(default=None, max_length=128)
    schedule_timezone: str | None = Field(default=None, min_length=1, max_length=64)
    schedule_start_time: dt.datetime | None = None
    cpu_request: str = "0.25"
    memory_request: str = "0.5"
    max_runtime_seconds: int = Field(default=3600, gt=0)
    spot: bool = False

    @model_validator(mode="after")
    def validate_schedule_shape(self) -> "SignalJobConfigurationCreateRequest":
        if self.schedule_type == "interval":
            if self.schedule_every is None or self.schedule_period is None:
                raise ValueError("interval schedule requires schedule_every and schedule_period.")
            if self.schedule_expression is not None:
                raise ValueError("interval schedule forbids schedule_expression.")
            if self.schedule_timezone is not None:
                raise ValueError("interval schedule forbids schedule_timezone.")
        elif (
            not self.schedule_expression
            or self.schedule_every is not None
            or self.schedule_period is not None
        ):
            raise ValueError(
                "crontab schedule requires schedule_expression and forbids interval fields."
            )
        return self


class SignalJobConfigurationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    universe_uid: str | None = None
    account_uid: str | None = None
    enabled: bool | None = None
    schedule_type: Literal["interval", "crontab"] | None = None
    schedule_every: int | None = Field(default=None, gt=0)
    schedule_period: Literal["seconds", "minutes", "hours", "days"] | None = None
    schedule_expression: str | None = Field(default=None, max_length=128)
    schedule_timezone: str | None = Field(default=None, min_length=1, max_length=64)
    schedule_start_time: dt.datetime | None = None
    cpu_request: str | None = None
    memory_request: str | None = None
    max_runtime_seconds: int | None = Field(default=None, gt=0)
    spot: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "SignalJobConfigurationUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one signal Job configuration field must be provided.")
        return self


class SignalJobConfigurationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    name: str
    description: str | None
    signal_uid: str
    universe_uid: str
    account_uid: str
    job_uid: str | None
    enabled: bool
    schedule_type: Literal["interval", "crontab"]
    schedule_every: int | None
    schedule_period: Literal["seconds", "minutes", "hours", "days"] | None
    schedule_expression: str | None
    schedule_timezone: str | None
    schedule_start_time: dt.datetime | None
    cpu_request: str
    memory_request: str
    max_runtime_seconds: int
    spot: bool
    lifecycle_state: Literal["provisioning", "ready", "paused", "error", "deleting"]
    last_error: str | None
    job_image_status: str | None = None
    job_automatic_deployment: bool | None = None
    latest_run_status: str | None = None
    latest_run_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class SignalObservationAssetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_identifier: str
    symbol: str | None
    name: str | None
    weights: list[float | None]


class SignalObservationsResponse(BaseModel):
    """Transposed latest observations for one configured signal."""

    model_config = ConfigDict(extra="forbid")

    configuration_uid: str
    signal_uid: str
    observation_count: int = Field(ge=0)
    asset_count: int = Field(ge=0)
    time_indexes: list[dt.datetime]
    assets: list[SignalObservationAssetResponse]


class SignalJobRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    job_uid: str
    job_name: str
    status: str
    execution_start: dt.datetime | None
    execution_end: dt.datetime | None
    commit_hash: str | None
    runtime_image_uid: str | None
    runtime_image_digest: str | None
    logs_url: str | None
    failure_message: str | None


class SignalJobRunAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configuration_uid: str
    job_uid: str
    job_run_uid: str
    status: str
    status_url: str
    poll_after_ms: int = 1000


class PortfolioRebalanceConfigurationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    strategy: Literal["calendar_event_signal"] = "calendar_event_signal"
    calendar_identifier: str = Field(default="NYSE", min_length=1, max_length=255)
    session_label: str = Field(default="regular", min_length=1, max_length=64)
    rebalance_event: Literal["market_open", "market_close"] = "market_close"
    event_offset_seconds: int = Field(
        default=0,
        description=(
            "Offset from the persisted session event. Zero means the actual exchange event time; "
            "negative values run before it and positive values after it."
        ),
    )
    rebalance_cadence: Literal["every_session", "weekly"] = "every_session"
    rebalance_weekday: int = Field(
        default=0,
        ge=0,
        le=6,
        description="Calendar-local weekday used only for weekly cadence; Monday is 0.",
    )


class PortfolioRebalanceConfigurationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    strategy: Literal["calendar_event_signal"] | None = None
    calendar_identifier: str | None = Field(default=None, min_length=1, max_length=255)
    session_label: str | None = Field(default=None, min_length=1, max_length=64)
    rebalance_event: Literal["market_open", "market_close"] | None = None
    event_offset_seconds: int | None = None
    rebalance_cadence: Literal["every_session", "weekly"] | None = None
    rebalance_weekday: int | None = Field(default=None, ge=0, le=6)

    @model_validator(mode="after")
    def require_change(self) -> "PortfolioRebalanceConfigurationUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one rebalance configuration field must be provided.")
        return self


class PortfolioRebalanceConfigurationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    name: str
    description: str | None
    strategy: Literal["calendar_event_signal"]
    calendar_identifier: str
    session_label: str
    rebalance_event: Literal["market_open", "market_close"]
    event_offset_seconds: int
    rebalance_cadence: Literal["every_session", "weekly"]
    rebalance_weekday: int
    created_at: dt.datetime
    updated_at: dt.datetime


class PortfolioJobSettingsRequest(BaseModel):
    """Operational settings written directly to the Main Sequence Job."""

    model_config = ConfigDict(extra="forbid")

    schedule_type: Literal["interval", "crontab"]
    schedule_every: int | None = Field(default=None, gt=0)
    schedule_period: Literal["seconds", "minutes", "hours", "days"] | None = None
    schedule_expression: str | None = Field(default=None, max_length=128)
    schedule_timezone: str | None = Field(default=None, min_length=1, max_length=64)
    schedule_start_time: dt.datetime | None = None
    cpu_request: str = "0.25"
    memory_request: str = "0.5"
    max_runtime_seconds: int = Field(default=3600, gt=0)
    spot: bool = False

    @model_validator(mode="after")
    def validate_schedule_shape(self) -> "PortfolioJobSettingsRequest":
        if self.schedule_type == "interval":
            if self.schedule_every is None or self.schedule_period is None:
                raise ValueError("interval schedule requires schedule_every and schedule_period.")
            if self.schedule_expression is not None:
                raise ValueError("interval schedule forbids schedule_expression.")
            if self.schedule_timezone is not None:
                raise ValueError("interval schedule forbids schedule_timezone.")
        elif (
            not self.schedule_expression
            or self.schedule_every is not None
            or self.schedule_period is not None
        ):
            raise ValueError(
                "crontab schedule requires schedule_expression and forbids interval fields."
            )
        return self


class PortfolioConfigurationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    signal_configuration_uid: str = Field(min_length=1)
    bars_configuration_uid: str = Field(min_length=1)
    rebalance_configuration_uid: str = Field(min_length=1)
    upsample_frequency_id: Literal["1d"] = "1d"
    intraday_bar_interpolation_rule: Literal["ffill"] = "ffill"
    valuation_column: str = Field(default="close", min_length=1, max_length=64)
    valuation_maximum_staleness_seconds: int = Field(
        default=86_400,
        gt=0,
        description=(
            "Maximum age of the latest price that may be selected at a real valuation "
            "timestamp. This does not create or extend timestamps."
        ),
    )
    fail_on_missing_prices: bool = True
    commission_fee: float = Field(default=0.00018, ge=0)
    job: PortfolioJobSettingsRequest


class PortfolioConfigurationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    signal_configuration_uid: str | None = None
    bars_configuration_uid: str | None = None
    rebalance_configuration_uid: str | None = None
    upsample_frequency_id: Literal["1d"] | None = None
    intraday_bar_interpolation_rule: Literal["ffill"] | None = None
    valuation_column: str | None = Field(default=None, min_length=1, max_length=64)
    valuation_maximum_staleness_seconds: int | None = Field(default=None, gt=0)
    fail_on_missing_prices: bool | None = None
    commission_fee: float | None = Field(default=None, ge=0)
    job: PortfolioJobSettingsRequest | None = None

    @model_validator(mode="after")
    def require_change(self) -> "PortfolioConfigurationUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one portfolio configuration field must be provided.")
        return self


class PortfolioJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    schedule_type: Literal["interval", "crontab"] | None
    schedule_every: int | None
    schedule_period: Literal["seconds", "minutes", "hours", "days"] | None
    schedule_expression: str | None
    schedule_timezone: str | None
    schedule_timezone_explicit: bool | None
    schedule_start_time: dt.datetime | None
    cpu_request: str | None
    memory_request: str | None
    max_runtime_seconds: int | None
    spot: bool
    image_status: str | None
    automatic_deployment: bool


class PortfolioConfigurationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    name: str
    description: str | None
    signal_configuration_uid: str
    signal_uid: str
    bars_configuration_uid: str
    rebalance_configuration_uid: str
    rebalance_strategy: Literal["calendar_event_signal"]
    portfolio_uid: str | None
    job_uid: str | None
    upsample_frequency_id: Literal["1d"]
    intraday_bar_interpolation_rule: Literal["ffill"]
    valuation_column: str
    valuation_maximum_staleness_seconds: int
    fail_on_missing_prices: bool
    commission_fee: float
    job: PortfolioJobResponse | None
    latest_run_status: str | None = None
    latest_run_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class PortfolioLinkedSignalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None
    enabled: bool
    universe_name: str
    universe_symbol: str
    account_name: str
    account_environment: Literal["paper", "live"]


class PortfolioLinkedBarsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None
    enabled: bool
    account_name: str
    account_environment: Literal["paper", "live"]
    asset_source: Literal["assets", "universe", "account_holdings"]
    asset_source_name: str
    asset_count: int | None = Field(default=None, ge=0)
    frequency_id: str
    feed: str
    adjustment: str


class PortfolioLinkedRebalanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None
    strategy: Literal["calendar_event_signal"]
    calendar_identifier: str
    session_label: str
    rebalance_event: Literal["market_open", "market_close"]
    event_offset_seconds: int
    rebalance_cadence: Literal["every_session", "weekly"]
    rebalance_weekday: int


class PortfolioValueObservationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_index: dt.datetime
    close: float | None
    period_return: float | None
    calculated_close: float | None
    close_time: dt.datetime | None
    cumulative_return: float | None
    drawdown: float | None


class PortfolioPerformanceResponse(BaseModel):
    """Window-scoped return and risk statistics for one canonical Portfolio."""

    model_config = ConfigDict(extra="forbid")

    methodology: Literal["empyrical-reloaded"]
    frequency: Literal["daily"]
    annualization_factor: int = Field(gt=0)
    risk_free_rate: float
    observation_count: int = Field(ge=0)
    return_observation_count: int = Field(ge=0)
    period_start: dt.datetime | None
    period_end: dt.datetime | None
    total_return: float | None
    annualized_return: float | None
    annualized_volatility: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    max_drawdown: float | None
    calmar_ratio: float | None
    best_period_return: float | None
    worst_period_return: float | None
    positive_period_ratio: float | None


class CanonicalPortfolioDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materialized: bool
    description: str | None
    calendar_name: str | None
    calendar_type: str | None
    calendar_timezone: str | None
    calendar_valid_from: dt.date | None
    calendar_valid_to: dt.date | None
    backtest_price_column: str | None
    observation_count: int = Field(ge=0)
    total_observation_count: int = Field(ge=0)
    history_window_truncated: bool
    latest_observation_at: dt.datetime | None
    latest_close: float | None
    latest_period_return: float | None
    performance: PortfolioPerformanceResponse
    observations: list[PortfolioValueObservationResponse]


class PortfolioConfigurationDetailResponse(PortfolioConfigurationResponse):
    """Resolved detail projection used only after one portfolio is selected."""

    linked_signal: PortfolioLinkedSignalResponse
    linked_bars: PortfolioLinkedBarsResponse
    linked_rebalance: PortfolioLinkedRebalanceResponse
    canonical_portfolio: CanonicalPortfolioDetailResponse


class PortfolioJobRunResponse(SignalJobRunResponse):
    pass


class PortfolioJobRunAcceptedResponse(SignalJobRunAcceptedResponse):
    pass


class JobRunFailureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["job_run_failed"]
    message: str
    retryable: bool = False


class JobRunStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uid: str
    job_uid: str
    job_name: str
    configuration_uid: str | None
    status: str
    execution_start: dt.datetime | None
    execution_end: dt.datetime | None
    commit_hash: str | None
    runtime_image_uid: str | None
    runtime_image_digest: str | None
    command_args: list[str]
    logs_url: str | None
    error: JobRunFailureResponse | None


class MarketDataDatasetResponse(BaseModel):
    uid: str
    key: str
    identifier: str
    physical_table: str
    frequency_id: str
    feed: str
    adjustment: str
    cadence: str
    columns: list[str]
    row_count: int
    earliest_observation: Any | None = None
    latest_observation: Any | None = None
