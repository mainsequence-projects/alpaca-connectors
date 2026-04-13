from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.settings import SUPPORTED_COMPONENT_PROVIDERS


def _normalize_symbol_list(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    normalized = [value.strip().upper() for value in values if value and value.strip()]
    return list(dict.fromkeys(normalized)) or None


class HealthResponse(BaseModel):
    status: Literal["ok"]


class DiscoveryConfigResponse(BaseModel):
    supported_component_providers: list[str]
    etf_provider_map_normalized: dict[str, str]
    mag_7_category_symbols: list[str]
    etfs_main_tickers: list[str]


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


class AssetRegistrationByTickerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(
        ...,
        min_length=1,
        description="Ticker to register as a MainSequence public asset through Alpaca and FIGI.",
        examples=["NVDA"],
    )
    include_non_tradable: bool = Field(
        default=False,
        description="Include Alpaca assets that are active but not tradable.",
    )
    timeout: float = Field(
        default=30.0,
        gt=0,
        le=300.0,
        description="HTTP timeout in seconds for Alpaca and OpenFIGI requests.",
    )

    @model_validator(mode="after")
    def normalize_values(self) -> "AssetRegistrationByTickerRequest":
        self.ticker = self.ticker.strip().upper()
        if not self.ticker:
            raise ValueError("ticker must not be empty.")
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
    assets_by_symbol: dict[str, int]
    existing_asset_ids_by_symbol: dict[str, int]
    created_asset_ids_by_symbol: dict[str, int]
    unresolved_symbols: list[str]
    not_registered_missing_figi_symbols: list[str]
    not_registered_missing_alpaca_symbols: list[str]
    warnings_by_symbol: dict[str, str]


class AssetRegistrationByTickerResponse(BaseModel):
    requested_ticker: str
    alpaca_symbol: str | None
    alpaca_name: str | None
    figi: str | None
    classification_pass_name: str | None
    security_type: str | None
    security_type_2: str | None
    exchange_code: str | None
    status: Literal[
        "created",
        "existing",
        "blocked_missing_alpaca",
        "blocked_missing_figi",
    ]
    asset_id: int | None
    created: bool
    already_registered: bool
    missing_from_alpaca: bool
    missing_figi: bool
    warnings: list[str] = Field(default_factory=list)


class HoldingsCategoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    etf_ticker: str = Field(
        ...,
        min_length=1,
        description="ETF ticker whose holdings should define the category.",
        examples=["IVV"],
    )
    component_provider: str | None = Field(
        default=None,
        description="Optional provider override. When omitted, provider is inferred from settings.",
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
        description="HTTP timeout in seconds for extraction, Alpaca, FIGI, and platform requests.",
    )

    @model_validator(mode="after")
    def normalize_values(self) -> "HoldingsCategoryRequest":
        self.etf_ticker = self.etf_ticker.strip().upper()
        if not self.etf_ticker:
            raise ValueError("etf_ticker must not be empty.")
        if self.component_provider is not None:
            self.component_provider = self.component_provider.strip().lower()
            if self.component_provider not in SUPPORTED_COMPONENT_PROVIDERS:
                raise ValueError(
                    "Unsupported component_provider. Supported values: "
                    + ", ".join(SUPPORTED_COMPONENT_PROVIDERS)
                )
        return self


class HoldingsCategoryPlanResponse(BaseModel):
    request: HoldingsCategoryRequest
    plan_summary: dict[str, Any]
    has_blockers: bool
    missing_registered_symbols: list[str]
    missing_symbols_from_alpaca: list[str]
    unresolved_symbols: list[str]


class HoldingsCategoryExecuteResponse(BaseModel):
    request: HoldingsCategoryRequest
    plan_summary: dict[str, Any]
    sync_result: dict[str, Any]
