from __future__ import annotations

import datetime as dt
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import requests
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.models import Asset as AlpacaTradingAsset
from alpaca.trading.requests import GetAssetsRequest
from pydantic import BaseModel, ConfigDict, Field

from src.settings import (
    OPENFIGI_DEFAULT_EXCHANGE_CODE,
    OPENFIGI_DEFAULT_TIMEOUT,
    OPENFIGI_MAPPING_URL,
    OPENFIGI_MAX_JOBS_WITH_API_KEY,
    OPENFIGI_MAX_JOBS_WITHOUT_API_KEY,
    get_figi_market_sector_equity,
    get_figi_security_type_common_stock,
    get_figi_security_type_etp,
    get_figi_security_type_reit,
    get_openfigi_api_key,
)

AssetRegistrationProgressCallback = Callable[[str, str, str | None], None]


def _emit_registration_progress(
    progress: AssetRegistrationProgressCallback | None,
    step_key: str,
    status: str,
    message: str,
) -> None:
    if progress is not None:
        progress(step_key, status, message)


def _chunked(values: Sequence[str], chunk_size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), chunk_size):
        yield list(values[start : start + chunk_size])


def _build_symbol_alias_candidates(symbol: str) -> list[str]:
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        return []

    candidates = [normalized_symbol]
    alias_candidates = [
        normalized_symbol.replace("/", ".").replace("-", "."),
        normalized_symbol.replace(".", "/").replace("-", "/"),
        normalized_symbol.replace(".", "-").replace("/", "-"),
    ]
    if (
        all(separator not in normalized_symbol for separator in (".", "/", "-"))
        and len(normalized_symbol) >= 3
    ):
        base_symbol = normalized_symbol[:-1]
        share_class_suffix = normalized_symbol[-1]
        alias_candidates.extend(
            [
                f"{base_symbol}.{share_class_suffix}",
                f"{base_symbol}/{share_class_suffix}",
                f"{base_symbol}-{share_class_suffix}",
            ]
        )

    for candidate in alias_candidates:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _resolve_requested_symbols_to_alpaca_assets(
    *,
    alpaca_assets: Sequence["AlpacaUsEquity"],
    requested_symbols: Sequence[str],
) -> tuple[list["AlpacaUsEquity"], list[str], dict[str, str]]:
    assets_by_symbol = {asset.symbol: asset for asset in alpaca_assets}
    resolved_assets_by_symbol: dict[str, AlpacaUsEquity] = {}
    missing_symbols: list[str] = []
    requested_symbol_aliases: dict[str, str] = {}

    normalized_requested_symbols = sorted(
        {symbol.strip().upper() for symbol in requested_symbols if symbol.strip()}
    )
    for requested_symbol in normalized_requested_symbols:
        resolved_symbol = None
        for candidate_symbol in _build_symbol_alias_candidates(requested_symbol):
            if candidate_symbol in assets_by_symbol:
                resolved_symbol = candidate_symbol
                break

        if resolved_symbol is None:
            missing_symbols.append(requested_symbol)
            continue

        resolved_assets_by_symbol[resolved_symbol] = assets_by_symbol[resolved_symbol]
        if resolved_symbol != requested_symbol:
            requested_symbol_aliases[requested_symbol] = resolved_symbol

    return (
        sorted(resolved_assets_by_symbol.values(), key=lambda asset: asset.symbol),
        missing_symbols,
        requested_symbol_aliases,
    )


def _coerce_enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


class AlpacaAssetRecord(BaseModel):
    """Provider-native Alpaca asset record used as the registration authority."""

    model_config = ConfigDict(frozen=True)

    alpaca_asset_id: uuid.UUID
    symbol: str
    name: str | None = None
    exchange: str | None = None
    asset_class: str
    status: str
    tradable: bool
    marginable: bool | None = None
    shortable: bool | None = None
    easy_to_borrow: bool | None = None
    fractionable: bool | None = None
    attributes: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] | None = None

    @classmethod
    def from_trading_asset(cls, asset: AlpacaTradingAsset) -> "AlpacaAssetRecord":
        return cls(
            alpaca_asset_id=asset.id,
            symbol=asset.symbol.upper(),
            name=asset.name,
            exchange=_coerce_enum_value(asset.exchange),
            asset_class=_coerce_enum_value(asset.asset_class) or AssetClass.US_EQUITY.value,
            status=_coerce_enum_value(asset.status) or AssetStatus.ACTIVE.value,
            tradable=asset.tradable,
            marginable=asset.marginable,
            shortable=asset.shortable,
            easy_to_borrow=asset.easy_to_borrow,
            fractionable=asset.fractionable,
            attributes=list(asset.attributes or []),
            raw_payload=(asset.model_dump(mode="json") if hasattr(asset, "model_dump") else None),
        )


class RegisteredAlpacaAssetReference(BaseModel):
    """Canonical asset identity retained from an earlier Alpaca catalog observation."""

    model_config = ConfigDict(frozen=True)

    asset_uid: str
    unique_identifier: str
    alpaca_asset_id: uuid.UUID
    symbol: str


# Compatibility alias for the CLI/module public surface. The record is provider-native and its
# identity is no longer derived from OpenFIGI.
AlpacaUsEquity = AlpacaAssetRecord


class AlpacaEquityClassificationPass(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    security_type: str


class OpenFigiMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    figi: str
    name: str | None = None
    ticker: str | None = None
    exchange_code: str | None = None
    security_type: str | None = None
    security_type_2: str | None = None
    security_market_sector: str | None = None
    composite_figi: str | None = None
    share_class_figi: str | None = None
    security_description: str | None = None
    classification_pass_name: str

    @classmethod
    def from_openfigi_payload(
        cls,
        *,
        symbol: str,
        payload: dict[str, Any],
        classification_pass_name: str,
    ) -> "OpenFigiMatch":
        return cls(
            symbol=symbol,
            figi=payload["figi"],
            name=payload.get("name"),
            ticker=payload.get("ticker"),
            exchange_code=payload.get("exchCode"),
            security_type=payload.get("securityType"),
            security_type_2=payload.get("securityType2"),
            security_market_sector=payload.get("marketSector"),
            composite_figi=payload.get("compositeFIGI"),
            share_class_figi=payload.get("shareClassFIGI"),
            security_description=payload.get("securityDescription"),
            classification_pass_name=classification_pass_name,
        )


class AlpacaEquityRegistrationPlan(BaseModel):
    alpaca_assets: list[AlpacaAssetRecord]
    classification_passes: list[AlpacaEquityClassificationPass]
    matches_by_symbol: dict[str, OpenFigiMatch]
    openfigi_unmatched_symbols: list[str] = Field(default_factory=list)
    missing_symbols_from_alpaca: list[str] = Field(default_factory=list)
    requested_symbol_aliases: dict[str, str] = Field(default_factory=dict)
    warnings_by_symbol: dict[str, str] = Field(default_factory=dict)
    openfigi_enrichment_skipped: bool = False

    def symbols_for_security_type(self, security_type: str) -> list[str]:
        return sorted(
            [
                symbol
                for symbol, match in self.matches_by_symbol.items()
                if match.security_type == security_type
            ]
        )

    def summary(self) -> dict[str, Any]:
        return {
            "alpaca_asset_count": len(self.alpaca_assets),
            "requested_symbols": [asset.symbol for asset in self.alpaca_assets],
            "missing_symbols_from_alpaca": self.missing_symbols_from_alpaca,
            "requested_symbol_aliases": self.requested_symbol_aliases,
            "matched_common_stock_symbols": self.symbols_for_security_type(
                get_figi_security_type_common_stock()
            ),
            "matched_etp_symbols": self.symbols_for_security_type(get_figi_security_type_etp()),
            "matched_reit_symbols": self.symbols_for_security_type(get_figi_security_type_reit()),
            "openfigi_enriched_symbols": sorted(self.matches_by_symbol),
            "openfigi_unmatched_symbols": self.openfigi_unmatched_symbols,
            "warnings_by_symbol": self.warnings_by_symbol,
            "openfigi_enrichment_skipped": self.openfigi_enrichment_skipped,
        }


class AlpacaEquityRegistrationResolution(BaseModel):
    plan: AlpacaEquityRegistrationPlan
    # ms-markets asset identity is a UUID (Asset.uid), serialized as a string. (Was integer
    # Asset.id under the old SDK — see migration doc D5.)
    existing_assets_by_symbol: dict[str, str]
    existing_assets_by_alpaca_id: dict[str, str]
    existing_off_catalog_assets_by_symbol: dict[str, RegisteredAlpacaAssetReference] = Field(
        default_factory=dict
    )
    unresolved_symbols_from_alpaca: list[str] = Field(default_factory=list)
    missing_assets: list[AlpacaAssetRecord]

    def summary(self) -> dict[str, Any]:
        return {
            "existing_assets_by_symbol": self.existing_assets_by_symbol,
            "existing_assets_by_alpaca_id": self.existing_assets_by_alpaca_id,
            "existing_off_catalog_assets_by_symbol": {
                symbol: reference.model_dump(mode="json")
                for symbol, reference in self.existing_off_catalog_assets_by_symbol.items()
            },
            "unresolved_symbols_from_alpaca": self.unresolved_symbols_from_alpaca,
            "missing_symbols_to_register": [asset.symbol for asset in self.missing_assets],
            "missing_alpaca_asset_ids_to_register": [
                str(asset.alpaca_asset_id) for asset in self.missing_assets
            ],
        }


def build_default_classification_passes() -> tuple[AlpacaEquityClassificationPass, ...]:
    return (
        AlpacaEquityClassificationPass(
            name="common_stock",
            security_type=get_figi_security_type_common_stock(),
        ),
        AlpacaEquityClassificationPass(
            name="etp",
            security_type=get_figi_security_type_etp(),
        ),
        AlpacaEquityClassificationPass(
            name="reit",
            security_type=get_figi_security_type_reit(),
        ),
    )


def build_alpaca_us_equity_trading_client(*, account_uid: str) -> TradingClient:
    """Build an Alpaca client from one registered account's Secret references."""
    from src.account.services import (
        build_registered_account_client,
        get_account_registration,
    )

    registration = get_account_registration(account_uid)
    if registration is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    return build_registered_account_client(registration)


def fetch_alpaca_us_equities(
    *,
    trading_client: TradingClient,
    symbols: Sequence[str] | None = None,
) -> list[AlpacaUsEquity]:
    """Resolve Alpaca US equities without treating lifecycle state as eligibility.

    One all-status US-equity catalog request supplies the complete batch. ``status`` and
    ``tradable`` remain facts persisted on ``AlpacaAssetDetails``; neither blocks registration.
    """
    request = GetAssetsRequest(
        asset_class=AssetClass.US_EQUITY,
    )
    assets = trading_client.get_all_assets(request)
    normalized_assets = [AlpacaUsEquity.from_trading_asset(asset) for asset in assets]

    requested_symbols = sorted(
        {symbol.strip().upper() for symbol in symbols or [] if symbol.strip()}
    )
    if not requested_symbols:
        return sorted(normalized_assets, key=lambda asset: asset.symbol)

    resolved_assets, _, _ = _resolve_requested_symbols_to_alpaca_assets(
        alpaca_assets=normalized_assets,
        requested_symbols=requested_symbols,
    )
    return resolved_assets


def _openfigi_chunk_size(api_key: str | None) -> int:
    return OPENFIGI_MAX_JOBS_WITH_API_KEY if api_key else OPENFIGI_MAX_JOBS_WITHOUT_API_KEY


def _build_openfigi_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    return headers


def _select_openfigi_candidate(
    *,
    symbol: str,
    query_symbol: str,
    payload: dict[str, Any],
    security_type: str,
    exchange_code: str,
) -> dict[str, Any] | None:
    data = payload.get("data") or []
    if not data:
        return None

    exact_matches = [
        candidate
        for candidate in data
        if candidate.get("ticker") == query_symbol
        and candidate.get("securityType") == security_type
        and candidate.get("exchCode") == exchange_code
        and candidate.get("marketSector") == get_figi_market_sector_equity()
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    if len(data) == 1:
        candidate = data[0]
        if (
            candidate.get("ticker") == query_symbol
            and candidate.get("securityType") == security_type
            and candidate.get("marketSector") == get_figi_market_sector_equity()
        ):
            return candidate

    return None


def _post_openfigi_mapping_jobs(
    *,
    jobs: list[dict[str, Any]],
    api_key: str | None,
    requests_session: requests.Session,
    timeout: float,
) -> list[dict[str, Any]]:
    response = requests_session.post(
        OPENFIGI_MAPPING_URL,
        headers=_build_openfigi_headers(api_key),
        json=jobs,
        timeout=timeout,
    )
    if response.status_code == 429:
        reset_after = int(response.headers.get("ratelimit-reset", "60"))
        time.sleep(max(reset_after, 1))
        response = requests_session.post(
            OPENFIGI_MAPPING_URL,
            headers=_build_openfigi_headers(api_key),
            json=jobs,
            timeout=timeout,
        )
    response.raise_for_status()
    return response.json()


def query_openfigi_by_ticker(
    *,
    tickers: Sequence[str],
    security_type: str,
    classification_pass_name: str,
    exchange_code: str = OPENFIGI_DEFAULT_EXCHANGE_CODE,
    api_key: str | None = None,
    timeout: float = OPENFIGI_DEFAULT_TIMEOUT,
    requests_session: requests.Session | None = None,
) -> tuple[dict[str, OpenFigiMatch], dict[str, str]]:
    logical_symbols = sorted({ticker.upper() for ticker in tickers})
    if not logical_symbols:
        return {}, {}

    requests_session = requests_session or requests.Session()
    matches_by_symbol: dict[str, OpenFigiMatch] = {}
    warnings_by_symbol: dict[str, str] = {}
    responses_by_symbol_and_candidate: dict[tuple[str, str], dict[str, Any]] = {}
    symbol_candidates = {
        symbol: _build_symbol_alias_candidates(symbol) for symbol in logical_symbols
    }
    query_jobs = [
        (symbol, candidate_symbol)
        for symbol in logical_symbols
        for candidate_symbol in symbol_candidates[symbol]
    ]

    for job_batch in _chunked(query_jobs, _openfigi_chunk_size(api_key)):
        jobs = [
            {
                "idType": "TICKER",
                "idValue": candidate_symbol,
                "exchCode": exchange_code,
                "marketSecDes": get_figi_market_sector_equity(),
                "securityType": security_type,
            }
            for _, candidate_symbol in job_batch
        ]
        results = _post_openfigi_mapping_jobs(
            jobs=jobs,
            api_key=api_key,
            requests_session=requests_session,
            timeout=timeout,
        )
        for (symbol, candidate_symbol), payload in zip(job_batch, results, strict=True):
            responses_by_symbol_and_candidate[(symbol, candidate_symbol)] = payload

    for symbol in logical_symbols:
        final_warning = "No identifier found."
        for candidate_symbol in symbol_candidates[symbol]:
            payload = responses_by_symbol_and_candidate[(symbol, candidate_symbol)]
            candidate = _select_openfigi_candidate(
                symbol=symbol,
                query_symbol=candidate_symbol,
                payload=payload,
                security_type=security_type,
                exchange_code=exchange_code,
            )
            if candidate is None:
                final_warning = payload.get("warning") or payload.get("error") or final_warning
                continue
            matches_by_symbol[symbol] = OpenFigiMatch.from_openfigi_payload(
                symbol=symbol,
                payload=candidate,
                classification_pass_name=classification_pass_name,
            )
            break
        else:
            warnings_by_symbol[symbol] = final_warning

    return matches_by_symbol, warnings_by_symbol


def _openfigi_enrichment_error(exc: Exception, *, timeout: float) -> str:
    """Return a useful, credential-safe warning for optional enrichment failures."""
    if isinstance(exc, requests.Timeout):
        return f"OpenFIGI did not respond within the configured {timeout:g}-second timeout."
    if isinstance(exc, requests.ConnectionError):
        return "Could not connect to OpenFIGI."
    if isinstance(exc, requests.HTTPError):
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        return (
            f"OpenFIGI returned HTTP {status_code}."
            if status_code is not None
            else "OpenFIGI returned an HTTP error."
        )
    if isinstance(exc, (requests.JSONDecodeError, ValueError)):
        return "OpenFIGI returned an invalid response."
    return f"OpenFIGI enrichment failed ({exc.__class__.__name__})."


def classify_alpaca_us_equities(
    alpaca_assets: Sequence[AlpacaUsEquity],
    *,
    classification_passes: Sequence[AlpacaEquityClassificationPass] | None = None,
    exchange_code: str = OPENFIGI_DEFAULT_EXCHANGE_CODE,
    openfigi_api_key: str | None = None,
    timeout: float = OPENFIGI_DEFAULT_TIMEOUT,
    requests_session: requests.Session | None = None,
    missing_symbols_from_alpaca: Sequence[str] | None = None,
    requested_symbol_aliases: dict[str, str] | None = None,
    query_openfigi_fn: Callable[..., tuple[dict[str, OpenFigiMatch], dict[str, str]]] = (
        query_openfigi_by_ticker
    ),
) -> AlpacaEquityRegistrationPlan:
    classification_passes = classification_passes or build_default_classification_passes()
    remaining_symbols = [asset.symbol for asset in alpaca_assets]
    matches_by_symbol: dict[str, OpenFigiMatch] = {}
    warnings_by_symbol: dict[str, str] = {}

    for classification_pass in classification_passes:
        if not remaining_symbols:
            break
        try:
            pass_matches, pass_warnings = query_openfigi_fn(
                tickers=remaining_symbols,
                security_type=classification_pass.security_type,
                classification_pass_name=classification_pass.name,
                exchange_code=exchange_code,
                api_key=openfigi_api_key,
                timeout=timeout,
                requests_session=requests_session,
            )
        except Exception as exc:  # OpenFIGI is optional; Alpaca identity remains authoritative.
            warning = _openfigi_enrichment_error(exc, timeout=timeout)
            warnings_by_symbol.update({symbol: warning for symbol in remaining_symbols})
            break
        matches_by_symbol.update(pass_matches)
        warnings_by_symbol.update(pass_warnings)
        for matched_symbol in pass_matches:
            warnings_by_symbol.pop(matched_symbol, None)
        remaining_symbols = [symbol for symbol in remaining_symbols if symbol not in pass_matches]

    warnings_by_symbol = {
        symbol: warning
        for symbol, warning in warnings_by_symbol.items()
        if symbol in remaining_symbols
    }

    return AlpacaEquityRegistrationPlan(
        alpaca_assets=list(alpaca_assets),
        classification_passes=list(classification_passes),
        matches_by_symbol=matches_by_symbol,
        openfigi_unmatched_symbols=remaining_symbols,
        missing_symbols_from_alpaca=sorted(set(missing_symbols_from_alpaca or [])),
        requested_symbol_aliases=dict(sorted((requested_symbol_aliases or {}).items())),
        warnings_by_symbol=warnings_by_symbol,
    )


def build_alpaca_us_equity_registration_plan(
    *,
    account_uid: str,
    trading_client: TradingClient | None = None,
    symbols: Sequence[str] | None = None,
    exchange_code: str = OPENFIGI_DEFAULT_EXCHANGE_CODE,
    timeout: float = OPENFIGI_DEFAULT_TIMEOUT,
    requests_session: requests.Session | None = None,
    enrich_openfigi: bool = True,
    progress: AssetRegistrationProgressCallback | None = None,
) -> AlpacaEquityRegistrationPlan:
    _emit_registration_progress(
        progress,
        "resolve_account",
        "running",
        "Resolving the registered Alpaca account and its credential Secret references.",
    )
    active_trading_client = trading_client or build_alpaca_us_equity_trading_client(
        account_uid=account_uid
    )
    _emit_registration_progress(
        progress,
        "resolve_account",
        "succeeded",
        "Resolved the registered Alpaca account without exposing credential values.",
    )
    _emit_registration_progress(
        progress,
        "load_alpaca_assets",
        "running",
        "Resolving US-equity identities from Alpaca.",
    )
    available_alpaca_assets = fetch_alpaca_us_equities(
        trading_client=active_trading_client,
        symbols=symbols,
    )
    requested_symbol_aliases: dict[str, str] = {}
    alpaca_assets = available_alpaca_assets
    missing_symbols_from_alpaca: list[str] = []
    if symbols:
        (
            alpaca_assets,
            missing_symbols_from_alpaca,
            requested_symbol_aliases,
        ) = _resolve_requested_symbols_to_alpaca_assets(
            alpaca_assets=available_alpaca_assets,
            requested_symbols=symbols,
        )
    _emit_registration_progress(
        progress,
        "load_alpaca_assets",
        "succeeded",
        (
            f"Loaded the Alpaca catalog and matched {len(alpaca_assets)} requested symbol(s); "
            f"{len(missing_symbols_from_alpaca)} were not found."
        ),
    )
    _emit_registration_progress(
        progress,
        "resolve_alpaca_identities",
        "running",
        f"Validating {len(alpaca_assets)} immutable Alpaca asset UUID(s).",
    )
    _emit_registration_progress(
        progress,
        "resolve_alpaca_identities",
        "succeeded",
        f"Validated {len(alpaca_assets)} Alpaca asset UUID(s).",
    )
    if enrich_openfigi:
        _emit_registration_progress(
            progress,
            "enrich_openfigi_details",
            "running",
            f"Optionally enriching {len(alpaca_assets)} Alpaca asset(s) with OpenFIGI details.",
        )
        plan = classify_alpaca_us_equities(
            alpaca_assets,
            exchange_code=exchange_code,
            openfigi_api_key=get_openfigi_api_key(),
            timeout=timeout,
            requests_session=requests_session,
            missing_symbols_from_alpaca=missing_symbols_from_alpaca,
            requested_symbol_aliases=requested_symbol_aliases,
        )
        _emit_registration_progress(
            progress,
            "enrich_openfigi_details",
            "succeeded",
            (
                f"Enriched {len(plan.matches_by_symbol)} asset(s); "
                f"{len(plan.openfigi_unmatched_symbols)} asset(s) continue without OpenFIGI details."
            ),
        )
    else:
        plan = AlpacaEquityRegistrationPlan(
            alpaca_assets=alpaca_assets,
            classification_passes=[],
            matches_by_symbol={},
            missing_symbols_from_alpaca=missing_symbols_from_alpaca,
            requested_symbol_aliases=requested_symbol_aliases,
            openfigi_enrichment_skipped=True,
        )
        _emit_registration_progress(
            progress,
            "enrich_openfigi_details",
            "skipped",
            "Skipped optional OpenFIGI enrichment for this registration plan.",
        )
    return plan


def _query_existing_assets_by_alpaca_id(
    alpaca_asset_ids: Sequence[uuid.UUID | str],
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Return assets already registered under their canonical Alpaca UUID identifier."""
    if not alpaca_asset_ids:
        return {}

    from msm.api.assets import Asset
    from msm.api.base import operation_result_rows
    from msm.models import AssetTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import or_, select

    from src.assets.alpaca_asset_details import (
        AlpacaAssetDetailsTable,
        build_alpaca_unique_identifier,
        normalize_alpaca_asset_id,
    )
    from src.runtime import start_markets_engine

    normalized_ids = sorted(
        {normalize_alpaca_asset_id(value) for value in alpaca_asset_ids},
        key=str,
    )
    identifiers_by_id = {
        str(alpaca_asset_id): build_alpaca_unique_identifier(alpaca_asset_id)
        for alpaca_asset_id in normalized_ids
    }
    ids_by_identifier = {
        identifier: alpaca_asset_id for alpaca_asset_id, identifier in identifiers_by_id.items()
    }
    # Reuse the application's complete runtime configuration. Long-lived API
    # processes attach that model graph once at startup, and SDK 8 correctly
    # rejects a later ``start_engine`` call with a narrower model selection.
    runtime = start_markets_engine()
    statement = (
        select(
            AssetTable.uid,
            AssetTable.unique_identifier,
            AssetTable.asset_type,
            AlpacaAssetDetailsTable.alpaca_asset_id,
        )
        .select_from(AssetTable)
        .outerjoin(
            AlpacaAssetDetailsTable,
            AlpacaAssetDetailsTable.asset_uid == AssetTable.uid,
        )
        .where(
            or_(
                AssetTable.unique_identifier.in_(list(ids_by_identifier)),
                AlpacaAssetDetailsTable.alpaca_asset_id.in_(normalized_ids),
            )
        )
    )
    operation = compile_markets_statement(
        statement,
        context=runtime.context,
        operation="select",
        models=[AssetTable, AlpacaAssetDetailsTable],
        access="read",
    )
    rows = operation_result_rows(execute_markets_operation(operation, context=runtime.context))
    existing_assets: dict[str, Any] = {}
    for row in rows:
        asset = Asset.model_validate(row)
        detail_id = row.get("alpaca_asset_id")
        if detail_id is not None:
            alpaca_asset_id = str(normalize_alpaca_asset_id(detail_id))
            expected_identifier = identifiers_by_id.get(alpaca_asset_id)
            if expected_identifier is None:
                continue
            if asset.unique_identifier != expected_identifier:
                raise ValueError(
                    "Existing Alpaca detail identity disagrees with its parent Asset: "
                    f"Alpaca asset {alpaca_asset_id}, parent Asset {asset.uid!s}."
                )
        else:
            alpaca_asset_id = ids_by_identifier.get(asset.unique_identifier)
            if alpaca_asset_id is None:
                continue
        existing_assets[alpaca_asset_id] = asset
    return existing_assets


def _query_registered_assets_by_symbols(
    symbols: Sequence[str],
    *,
    timeout: float | None = None,
) -> dict[str, RegisteredAlpacaAssetReference]:
    """Resolve off-catalog symbols from previously registered Alpaca details in one query."""
    if not symbols:
        return {}

    from msm.api.base import operation_result_rows
    from msm.models import AssetTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from src.assets.alpaca_asset_details import (
        AlpacaAssetDetailsTable,
        build_alpaca_unique_identifier,
    )
    from src.runtime import start_markets_engine

    _ = timeout
    requested_symbols = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    candidate_symbols = sorted(
        {
            candidate
            for symbol in requested_symbols
            for candidate in _build_symbol_alias_candidates(symbol)
        }
    )
    runtime = start_markets_engine()
    statement = (
        select(
            AssetTable.uid.label("asset_uid"),
            AssetTable.unique_identifier,
            AlpacaAssetDetailsTable.alpaca_asset_id,
            AlpacaAssetDetailsTable.symbol,
        )
        .select_from(AlpacaAssetDetailsTable)
        .join(AssetTable, AssetTable.uid == AlpacaAssetDetailsTable.asset_uid)
        .where(AlpacaAssetDetailsTable.symbol.in_(candidate_symbols))
    )
    operation = compile_markets_statement(
        statement,
        context=runtime.context,
        operation="select",
        models=[AssetTable, AlpacaAssetDetailsTable],
        access="read",
    )
    rows = operation_result_rows(execute_markets_operation(operation, context=runtime.context))
    references_by_symbol: dict[str, list[RegisteredAlpacaAssetReference]] = {}
    for row in rows:
        alpaca_asset_id = uuid.UUID(str(row["alpaca_asset_id"]))
        expected_identifier = build_alpaca_unique_identifier(alpaca_asset_id)
        if str(row["unique_identifier"]) != expected_identifier:
            raise ValueError(
                "Registered Alpaca symbol identity disagrees with its parent Asset: "
                f"symbol {row['symbol']}, Asset {row['asset_uid']!s}."
            )
        reference = RegisteredAlpacaAssetReference(
            asset_uid=str(row["asset_uid"]),
            unique_identifier=expected_identifier,
            alpaca_asset_id=alpaca_asset_id,
            symbol=str(row["symbol"]),
        )
        references_by_symbol.setdefault(reference.symbol, []).append(reference)

    resolved: dict[str, RegisteredAlpacaAssetReference] = {}
    for requested_symbol in requested_symbols:
        exact_matches = references_by_symbol.get(requested_symbol, [])
        if len(exact_matches) == 1:
            resolved[requested_symbol] = exact_matches[0]
            continue
        alias_matches = [
            reference
            for candidate in _build_symbol_alias_candidates(requested_symbol)[1:]
            for reference in references_by_symbol.get(candidate, [])
        ]
        if not exact_matches and len(alias_matches) == 1:
            resolved[requested_symbol] = alias_matches[0]
    return resolved


def resolve_alpaca_us_equity_registration_plan(
    plan: AlpacaEquityRegistrationPlan,
    *,
    timeout: float | None = None,
    query_existing_assets_fn: Callable[..., dict[str, Any]] = _query_existing_assets_by_alpaca_id,
    query_registered_symbols_fn: Callable[
        ..., dict[str, RegisteredAlpacaAssetReference]
    ] = _query_registered_assets_by_symbols,
) -> AlpacaEquityRegistrationResolution:
    existing_assets = query_existing_assets_fn(
        [asset.alpaca_asset_id for asset in plan.alpaca_assets],
        timeout=timeout,
    )

    existing_assets_by_symbol: dict[str, str] = {}
    missing_assets: list[AlpacaAssetRecord] = []

    for alpaca_asset in sorted(plan.alpaca_assets, key=lambda value: value.symbol):
        existing_asset = existing_assets.get(str(alpaca_asset.alpaca_asset_id))
        if existing_asset is None:
            missing_assets.append(alpaca_asset)
            continue
        existing_assets_by_symbol[alpaca_asset.symbol] = str(existing_asset.uid)

    existing_off_catalog_assets_by_symbol = query_registered_symbols_fn(
        plan.missing_symbols_from_alpaca,
        timeout=timeout,
    )
    unresolved_symbols = sorted(
        set(plan.missing_symbols_from_alpaca)
        - existing_off_catalog_assets_by_symbol.keys()
    )

    return AlpacaEquityRegistrationResolution(
        plan=plan,
        existing_assets_by_symbol=existing_assets_by_symbol,
        existing_assets_by_alpaca_id={
            alpaca_asset_id: str(asset.uid) for alpaca_asset_id, asset in existing_assets.items()
        },
        existing_off_catalog_assets_by_symbol=existing_off_catalog_assets_by_symbol,
        unresolved_symbols_from_alpaca=unresolved_symbols,
        missing_assets=missing_assets,
    )


def _asset_type_registration_values(alpaca_asset: AlpacaAssetRecord) -> dict[str, Any]:
    from src.assets.alpaca_asset_details import asset_type_from_alpaca_class

    asset_type = asset_type_from_alpaca_class(alpaca_asset.asset_class)
    return {
        "asset_type": asset_type,
        "display_name": asset_type.replace("_", " ").title(),
        "description": (
            "Canonical ms-markets asset family derived from Alpaca "
            f"asset_class={alpaca_asset.asset_class!r}."
        ),
    }


def _alpaca_detail_registration_values(
    alpaca_asset: AlpacaAssetRecord,
    *,
    asset_uid: uuid.UUID | str,
    refreshed_at: dt.datetime,
) -> dict[str, Any]:
    return {
        "asset_uid": asset_uid,
        "alpaca_asset_id": alpaca_asset.alpaca_asset_id,
        "symbol": alpaca_asset.symbol,
        "name": alpaca_asset.name,
        "asset_class": alpaca_asset.asset_class,
        "exchange": alpaca_asset.exchange,
        "status": alpaca_asset.status,
        "tradable": alpaca_asset.tradable,
        "marginable": alpaca_asset.marginable,
        "shortable": alpaca_asset.shortable,
        "easy_to_borrow": alpaca_asset.easy_to_borrow,
        "fractionable": alpaca_asset.fractionable,
        "attributes": alpaca_asset.attributes,
        "raw_payload": alpaca_asset.raw_payload,
        "refreshed_at": refreshed_at,
    }


def _openfigi_detail_registration_values(
    match: OpenFigiMatch,
    *,
    asset_uid: uuid.UUID | str,
) -> dict[str, Any]:
    return {
        "asset_uid": asset_uid,
        "figi": match.figi,
        "ticker": match.ticker,
        "name": match.name,
        "exchange_code": match.exchange_code,
        "security_type": match.security_type,
        "security_type_2": match.security_type_2,
        "security_market_sector": match.security_market_sector,
        "composite": match.composite_figi,
        "share_class": match.share_class_figi,
        "security_description": match.security_description,
    }


def _asset_snapshot_registration_values(
    alpaca_asset: AlpacaAssetRecord,
    match: OpenFigiMatch | None,
    *,
    time_index: dt.datetime,
) -> dict[str, Any]:
    from src.assets.alpaca_asset_details import build_alpaca_unique_identifier

    return {
        "time_index": time_index,
        "asset_identifier": build_alpaca_unique_identifier(alpaca_asset.alpaca_asset_id),
        "name": alpaca_asset.name or (match.name if match else "") or "",
        "ticker": alpaca_asset.symbol,
        "exchange_code": alpaca_asset.exchange or "",
        "asset_ticker_group_id": (match.share_class_figi if match else None) or "",
    }


def _register_alpaca_asset(
    alpaca_asset: AlpacaAssetRecord,
    match: OpenFigiMatch | None = None,
) -> str:
    """Upsert one Alpaca asset; universe-sized callers use the batch implementation."""
    from msm.api.assets import Asset, AssetType, OpenFigiDetails
    from msm.data_nodes.assets import AssetSnapshot

    from src.assets.alpaca_asset_details import (
        build_alpaca_unique_identifier,
        upsert_alpaca_asset_details,
    )

    asset_type_values = _asset_type_registration_values(alpaca_asset)
    AssetType.upsert(**asset_type_values)
    canonical_identifier = build_alpaca_unique_identifier(alpaca_asset.alpaca_asset_id)
    asset = Asset.upsert(
        unique_identifier=canonical_identifier,
        asset_type=asset_type_values["asset_type"],
    )
    refreshed_at = dt.datetime.now(tz=dt.UTC).replace(microsecond=0)
    upsert_alpaca_asset_details(
        asset_uid=asset.uid,
        values=_alpaca_detail_registration_values(
            alpaca_asset,
            asset_uid=asset.uid,
            refreshed_at=refreshed_at,
        ),
    )
    if match is not None:
        OpenFigiDetails.upsert(**_openfigi_detail_registration_values(match, asset_uid=asset.uid))
    AssetSnapshot().set_snapshots(
        _asset_snapshot_registration_values(
            alpaca_asset,
            match,
            time_index=dt.datetime.now(tz=dt.UTC),
        )
    ).run()
    return str(asset.uid)


def _register_alpaca_assets_batch(
    alpaca_assets: Sequence[AlpacaAssetRecord],
    matches_by_symbol: dict[str, OpenFigiMatch],
) -> dict[str, str]:
    """Register one resolved asset batch with fixed-count MetaTable and snapshot operations."""
    if not alpaca_assets:
        return {}

    from msm.api.base import operation_result_rows
    from msm.bootstrap import resolve_runtime
    from msm.data_nodes.assets import AssetSnapshot
    from msm.models import AssetTable, AssetTypeTable, OpenFigiAssetDetailsTable
    from msm.repositories.crud import bulk_upsert_model

    from src.assets.alpaca_asset_details import (
        AlpacaAssetDetailsTable,
        build_alpaca_unique_identifier,
    )

    runtime = resolve_runtime(
        models=[
            AssetTypeTable,
            AssetTable,
            AlpacaAssetDetailsTable,
            OpenFigiAssetDetailsTable,
        ],
        row_model_name="Alpaca asset registration batch",
    )
    context = runtime.context
    asset_type_values_by_key = {
        values["asset_type"]: values
        for values in (_asset_type_registration_values(asset) for asset in alpaca_assets)
    }
    bulk_upsert_model(
        context,
        model=AssetTypeTable,
        values=list(asset_type_values_by_key.values()),
        conflict_columns=("asset_type",),
    )

    asset_values = [
        {
            "unique_identifier": build_alpaca_unique_identifier(asset.alpaca_asset_id),
            "asset_type": _asset_type_registration_values(asset)["asset_type"],
        }
        for asset in alpaca_assets
    ]
    asset_result = bulk_upsert_model(
        context,
        model=AssetTable,
        values=asset_values,
        conflict_columns=("unique_identifier",),
    )
    asset_rows = operation_result_rows(asset_result)
    asset_uids_by_identifier = {
        str(row["unique_identifier"]): str(row["uid"]) for row in asset_rows
    }
    expected_identifiers = {str(values["unique_identifier"]) for values in asset_values}
    missing_identifiers = sorted(expected_identifiers - asset_uids_by_identifier.keys())
    if missing_identifiers:
        raise RuntimeError(
            "Bulk Alpaca Asset upsert returned an incomplete result for: "
            + ", ".join(missing_identifiers)
        )

    refreshed_at = dt.datetime.now(tz=dt.UTC).replace(microsecond=0)
    detail_values = []
    openfigi_values = []
    snapshot_time = dt.datetime.now(tz=dt.UTC)
    snapshot_values = []
    assets_by_symbol: dict[str, str] = {}
    for alpaca_asset in alpaca_assets:
        identifier = build_alpaca_unique_identifier(alpaca_asset.alpaca_asset_id)
        asset_uid = asset_uids_by_identifier[identifier]
        assets_by_symbol[alpaca_asset.symbol] = asset_uid
        detail_values.append(
            _alpaca_detail_registration_values(
                alpaca_asset,
                asset_uid=asset_uid,
                refreshed_at=refreshed_at,
            )
        )
        match = matches_by_symbol.get(alpaca_asset.symbol)
        if match is not None:
            openfigi_values.append(_openfigi_detail_registration_values(match, asset_uid=asset_uid))
        snapshot_values.append(
            _asset_snapshot_registration_values(
                alpaca_asset,
                match,
                time_index=snapshot_time,
            )
        )

    bulk_upsert_model(
        context,
        model=AlpacaAssetDetailsTable,
        values=detail_values,
        conflict_columns=("asset_uid",),
    )
    if openfigi_values:
        bulk_upsert_model(
            context,
            model=OpenFigiAssetDetailsTable,
            values=openfigi_values,
            conflict_columns=("asset_uid",),
        )
    AssetSnapshot().set_snapshots(snapshot_values).run()
    return assets_by_symbol


def register_alpaca_us_equity_assets(
    plan: AlpacaEquityRegistrationPlan | None = None,
    *,
    timeout: float | None = None,
    registration_resolution: AlpacaEquityRegistrationResolution | None = None,
) -> dict[str, dict[str, Any] | list[str]]:
    if registration_resolution is None:
        if plan is None:
            raise ValueError("Either plan or registration_resolution must be provided.")
        registration_resolution = resolve_alpaca_us_equity_registration_plan(
            plan,
            timeout=timeout,
        )

    missing_from_alpaca = sorted(
        set(registration_resolution.unresolved_symbols_from_alpaca)
    )
    if missing_from_alpaca:
        raise ValueError(
            "Registration cannot execute because Alpaca did not resolve these symbols: "
            + ", ".join(missing_from_alpaca)
            + ". No assets were written."
        )

    existing_assets = {
        symbol: asset_uid
        for symbol, asset_uid in registration_resolution.existing_assets_by_symbol.items()
    }
    existing_assets.update(
        {
            symbol: reference.asset_uid
            for symbol, reference in (
                registration_resolution.existing_off_catalog_assets_by_symbol.items()
            )
        }
    )
    created_assets: dict[str, str] = {}

    missing_symbols = {asset.symbol for asset in registration_resolution.missing_assets}
    assets_by_symbol = _register_alpaca_assets_batch(
        registration_resolution.plan.alpaca_assets,
        registration_resolution.plan.matches_by_symbol,
    )
    assets_by_symbol.update(
        {
            symbol: reference.asset_uid
            for symbol, reference in (
                registration_resolution.existing_off_catalog_assets_by_symbol.items()
            )
        }
    )
    created_assets.update(
        {
            symbol: asset_uid
            for symbol, asset_uid in assets_by_symbol.items()
            if symbol in missing_symbols
        }
    )

    return {
        "assets": assets_by_symbol,
        "existing_assets": existing_assets,
        "created_assets": created_assets,
        "openfigi_unmatched_symbols": (registration_resolution.plan.openfigi_unmatched_symbols),
        "not_registered_missing_alpaca_symbols": missing_from_alpaca,
        "warnings_by_symbol": registration_resolution.plan.warnings_by_symbol,
    }
