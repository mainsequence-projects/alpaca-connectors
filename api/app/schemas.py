from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.universes import SUPPORTED_COMPONENT_PROVIDERS


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
    request_user_uid: str | None = None
    registered_account_count: int = 0
    has_registered_account: bool = False
    universe_source_count: int = 0
    market_data_dataset_count: int = 0
    market_data_datasets: list[dict[str, Any]] = Field(default_factory=list)


class AssetRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] | None = Field(
        default=None,
        description="Exact symbols to resolve against Alpaca and FIGI without component extraction.",
        examples=[["AAPL", "MSFT", "NVDA"]],
    )
    seed_tickers: list[str] | None = Field(
        default=None,
        description="ETF seed tickers used for holdings expansion.",
        examples=[["IVV"]],
    )
    component_provider: str | None = Field(
        default=None,
        description="Holdings provider used with seed_tickers.",
        examples=["ishares"],
    )
    include_non_tradable: bool = Field(
        default=False,
        description="Include Alpaca assets that are active but not tradable.",
    )
    timeout: float = Field(
        default=30.0,
        gt=0,
        le=300.0,
        description="HTTP timeout in seconds for Alpaca, OpenFIGI, and provider requests.",
    )

    @model_validator(mode="after")
    def validate_scope(self) -> "AssetRegistrationRequest":
        self.symbols = _normalize_symbol_list(self.symbols)
        self.seed_tickers = _normalize_symbol_list(self.seed_tickers)
        if self.symbols and self.seed_tickers:
            raise ValueError("Use either symbols or seed_tickers, not both.")
        if not self.symbols and not self.seed_tickers:
            raise ValueError("Either symbols or seed_tickers must be provided.")
        if self.seed_tickers and self.component_provider is None:
            raise ValueError("component_provider is required when seed_tickers are provided.")
        if self.component_provider and not self.seed_tickers:
            raise ValueError("seed_tickers are required when component_provider is provided.")
        if self.component_provider and self.component_provider not in SUPPORTED_COMPONENT_PROVIDERS:
            raise ValueError(
                "Unsupported component_provider. Supported values: "
                + ", ".join(SUPPORTED_COMPONENT_PROVIDERS)
            )
        return self


class AssetRegistrationDiscoveryResponse(BaseModel):
    request: AssetRegistrationRequest
    plan_summary: dict[str, Any]
    resolution_summary: dict[str, Any]
    can_register: bool
    unresolved_symbols: list[str]
    missing_symbols_from_alpaca: list[str]
    missing_symbols_to_register: list[str]
    warnings_by_symbol: dict[str, str]


class AssetRegistrationExecuteResponse(BaseModel):
    request: AssetRegistrationRequest
    plan_summary: dict[str, Any]
    resolution_summary: dict[str, Any]
    assets_by_symbol: dict[str, str]
    existing_asset_uids_by_symbol: dict[str, str]
    created_asset_uids_by_symbol: dict[str, str]
    unresolved_symbols: list[str]
    not_registered_missing_figi_symbols: list[str]
    not_registered_missing_alpaca_symbols: list[str]
    warnings_by_symbol: dict[str, str]


class AssetRegistrationOperationStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["plan", "execute"]
    request: AssetRegistrationRequest


class AssetRegistrationStepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    status: Literal["pending", "running", "succeeded", "failed", "skipped"]
    message: str | None = None
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None


class AssetRegistrationOperationError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool


class AssetRegistrationOperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_uid: str
    action: Literal["plan", "execute"]
    status: Literal["queued", "running", "succeeded", "failed"]
    current_step: str | None
    steps: list[AssetRegistrationStepResponse]
    request: AssetRegistrationRequest
    result: dict[str, Any] | None
    error: AssetRegistrationOperationError | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    updated_at: dt.datetime
    completed_at: dt.datetime | None
    poll_after_ms: int = 500


class PageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pageIndex: int
    pageSize: int
    totalItems: int
    hasNextPage: bool
    hasPreviousPage: bool


class ResourceCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]]
    pageInfo: PageInfo


class ResourceDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["command-center.resource_discovery@v1"]
    resource: dict[str, Any]
    list: dict[str, Any]
    bulk_actions: list[dict[str, Any]]


class AccountRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_name: str | None = Field(default=None, max_length=255)
    environment: Literal["paper", "live"] = "paper"
    api_key_secret_name: str = Field(min_length=1, max_length=255)
    secret_key_secret_name: str = Field(min_length=1, max_length=255)
    capture_initial_holdings: bool = False
    register_missing_assets: bool = True


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


class SecretReferenceCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SecretReferenceResponse]
    pageInfo: PageInfo


class ActionPreflightResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    detail: str
    matched_count: int = 1
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BulkSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["explicit"]
    uids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_uids(self) -> "BulkSelection":
        if len(self.uids) != len(set(self.uids)):
            raise ValueError("selection.uids must not contain duplicates.")
        return self


class BulkActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection: BulkSelection
    options: dict[str, Any] = Field(default_factory=dict)


class HoldingsCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    register_missing_assets: bool = True


class HoldingsCaptureResponse(BaseModel):
    account_uid: str
    holdings_set_uid: str | None
    time_index: Any
    holdings_rows: int
    unresolved_symbols: list[str]
    skipped_non_equity_symbols: list[str]


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


class MaterializedUniverseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    metadata_json: dict[str, Any] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "MaterializedUniverseUpdateRequest":
        if all(getattr(self, field_name) is None for field_name in self.__class__.model_fields):
            raise ValueError("At least one universe display field must be provided.")
        return self


class MaterializedUniverseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    symbol: str = Field(min_length=1, max_length=32)
    source_url: str = Field(min_length=1, max_length=2048)


class MaterializedUniverseResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    uid: str
    unique_identifier: str
    display_name: str
    description: str | None = None
    is_active: bool
    source_uid: str | None = None
    asset_uids: list[str]
    asset_identifiers: list[str]
    asset_count: int


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
