from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any

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
    get_alpaca_api_key,
    get_alpaca_secret_key,
    get_figi_market_sector_equity,
    get_figi_security_type_common_stock,
    get_figi_security_type_etp,
    get_figi_security_type_reit,
    get_openfigi_api_key,
)

if TYPE_CHECKING:
    import mainsequence.client as msc

def _load_mainsequence_client() -> Any:
    import mainsequence.client as msc

    return msc


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
    if all(separator not in normalized_symbol for separator in (".", "/", "-")) and len(
        normalized_symbol
    ) >= 3:
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


class AlpacaUsEquity(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    name: str | None = None
    exchange: str | None = None
    asset_class: str
    status: str
    tradable: bool
    marginable: bool
    shortable: bool
    easy_to_borrow: bool
    fractionable: bool
    attributes: list[str] = Field(default_factory=list)

    @classmethod
    def from_trading_asset(cls, asset: AlpacaTradingAsset) -> "AlpacaUsEquity":
        return cls(
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
        )


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
    alpaca_assets: list[AlpacaUsEquity]
    classification_passes: list[AlpacaEquityClassificationPass]
    matches_by_symbol: dict[str, OpenFigiMatch]
    unresolved_symbols: list[str]
    missing_symbols_from_alpaca: list[str] = Field(default_factory=list)
    requested_symbol_aliases: dict[str, str] = Field(default_factory=dict)
    warnings_by_symbol: dict[str, str] = Field(default_factory=dict)

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
            "unresolved_symbols": self.unresolved_symbols,
            "not_registered_missing_figi_symbols": self.unresolved_symbols,
            "warnings_by_symbol": self.warnings_by_symbol,
        }


class AlpacaEquityRegistrationResolution(BaseModel):
    plan: AlpacaEquityRegistrationPlan
    existing_assets_by_symbol: dict[str, int | None]
    existing_assets_by_figi: dict[str, int | None]
    missing_matches: list[OpenFigiMatch]

    def summary(self) -> dict[str, Any]:
        return {
            "existing_assets_by_symbol": self.existing_assets_by_symbol,
            "existing_assets_by_figi": self.existing_assets_by_figi,
            "missing_symbols_to_register": [match.symbol for match in self.missing_matches],
            "missing_figis_to_register": [match.figi for match in self.missing_matches],
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


def build_alpaca_us_equity_trading_client(
) -> TradingClient:
    api_key = get_alpaca_api_key()
    secret_key = get_alpaca_secret_key()
    if not api_key or not secret_key:
        raise RuntimeError(
            "Missing Alpaca credentials in the environment. Set ALPACA_API_KEY/"
            "ALPACA_SECRET_KEY."
        )

    return TradingClient(api_key=api_key, secret_key=secret_key)


def fetch_alpaca_us_equities(
    *,
    trading_client: TradingClient | None = None,
    symbols: Sequence[str] | None = None,
    include_non_tradable: bool = False,
) -> list[AlpacaUsEquity]:
    trading_client = trading_client or build_alpaca_us_equity_trading_client()

    request = GetAssetsRequest(
        status=AssetStatus.ACTIVE,
        asset_class=AssetClass.US_EQUITY,
    )
    assets = trading_client.get_all_assets(request)
    normalized_assets = [
        AlpacaUsEquity.from_trading_asset(asset)
        for asset in assets
        if include_non_tradable or asset.tradable
    ]

    requested_symbols = {symbol.upper() for symbol in symbols or []}
    if requested_symbols:
        normalized_assets = [
            asset for asset in normalized_assets if asset.symbol in requested_symbols
        ]

    return sorted(normalized_assets, key=lambda asset: asset.symbol)


def _openfigi_chunk_size(api_key: str | None) -> int:
    return (
        OPENFIGI_MAX_JOBS_WITH_API_KEY
        if api_key
        else OPENFIGI_MAX_JOBS_WITHOUT_API_KEY
    )


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
        pass_matches, pass_warnings = query_openfigi_fn(
            tickers=remaining_symbols,
            security_type=classification_pass.security_type,
            classification_pass_name=classification_pass.name,
            exchange_code=exchange_code,
            api_key=openfigi_api_key,
            timeout=timeout,
            requests_session=requests_session,
        )
        matches_by_symbol.update(pass_matches)
        warnings_by_symbol.update(pass_warnings)
        for matched_symbol in pass_matches:
            warnings_by_symbol.pop(matched_symbol, None)
        remaining_symbols = [
            symbol for symbol in remaining_symbols if symbol not in pass_matches
        ]

    warnings_by_symbol = {
        symbol: warning
        for symbol, warning in warnings_by_symbol.items()
        if symbol in remaining_symbols
    }

    return AlpacaEquityRegistrationPlan(
        alpaca_assets=list(alpaca_assets),
        classification_passes=list(classification_passes),
        matches_by_symbol=matches_by_symbol,
        unresolved_symbols=remaining_symbols,
        missing_symbols_from_alpaca=sorted(set(missing_symbols_from_alpaca or [])),
        requested_symbol_aliases=dict(sorted((requested_symbol_aliases or {}).items())),
        warnings_by_symbol=warnings_by_symbol,
    )


def build_alpaca_us_equity_registration_plan(
    *,
    trading_client: TradingClient | None = None,
    symbols: Sequence[str] | None = None,
    include_non_tradable: bool = False,
    exchange_code: str = OPENFIGI_DEFAULT_EXCHANGE_CODE,
    timeout: float = OPENFIGI_DEFAULT_TIMEOUT,
    requests_session: requests.Session | None = None,
) -> AlpacaEquityRegistrationPlan:
    available_alpaca_assets = fetch_alpaca_us_equities(
        trading_client=trading_client,
        include_non_tradable=include_non_tradable,
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
    return classify_alpaca_us_equities(
        alpaca_assets,
        exchange_code=exchange_code,
        openfigi_api_key=get_openfigi_api_key(),
        timeout=timeout,
        requests_session=requests_session,
        missing_symbols_from_alpaca=missing_symbols_from_alpaca,
        requested_symbol_aliases=requested_symbol_aliases,
    )


def _query_existing_assets_by_figi(
    figis: Sequence[str],
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    if not figis:
        return {}

    msc = _load_mainsequence_client()
    existing_assets = msc.Asset.query(
        figi__in=sorted(set(figis)),
        per_page=min(500, len(set(figis))),
        timeout=timeout,
    )
    return {asset.figi: asset for asset in existing_assets if asset.figi}


def resolve_alpaca_us_equity_registration_plan(
    plan: AlpacaEquityRegistrationPlan,
    *,
    timeout: float | None = None,
    query_existing_assets_fn: Callable[..., dict[str, Any]] = _query_existing_assets_by_figi,
) -> AlpacaEquityRegistrationResolution:
    existing_assets_by_figi = query_existing_assets_fn(
        [match.figi for match in plan.matches_by_symbol.values()],
        timeout=timeout,
    )

    existing_assets_by_symbol: dict[str, int | None] = {}
    missing_matches: list[OpenFigiMatch] = []

    for symbol, match in sorted(plan.matches_by_symbol.items()):
        existing_asset = existing_assets_by_figi.get(match.figi)
        if existing_asset is None:
            missing_matches.append(match)
            continue
        existing_assets_by_symbol[symbol] = existing_asset.id

    return AlpacaEquityRegistrationResolution(
        plan=plan,
        existing_assets_by_symbol=existing_assets_by_symbol,
        existing_assets_by_figi={
            figi: asset.id for figi, asset in existing_assets_by_figi.items()
        },
        missing_matches=missing_matches,
    )


def register_alpaca_us_equity_assets(
    plan: AlpacaEquityRegistrationPlan | None = None,
    *,
    timeout: float | None = None,
    registration_resolution: AlpacaEquityRegistrationResolution | None = None,
) -> dict[str, dict[str, Any] | list[str]]:
    msc = _load_mainsequence_client()
    if registration_resolution is None:
        if plan is None:
            raise ValueError("Either plan or registration_resolution must be provided.")
        registration_resolution = resolve_alpaca_us_equity_registration_plan(
            plan,
            timeout=timeout,
        )

    existing_assets = {
        symbol: asset_id
        for symbol, asset_id in registration_resolution.existing_assets_by_symbol.items()
    }
    created_assets: dict[str, Any] = {}

    for match in registration_resolution.missing_matches:
        created_assets[match.symbol] = msc.Asset.register_asset_from_figi(
            figi=match.figi,
            timeout=timeout,
        )

    assets_by_symbol: dict[str, Any] = {}
    for symbol, match in registration_resolution.plan.matches_by_symbol.items():
        created_asset = created_assets.get(symbol)
        if created_asset is not None:
            assets_by_symbol[symbol] = created_asset
            continue

        asset_id = registration_resolution.existing_assets_by_symbol.get(symbol)
        if asset_id is not None:
            assets_by_symbol[symbol] = asset_id

    return {
        "assets": assets_by_symbol,
        "existing_assets": existing_assets,
        "created_assets": created_assets,
        "unresolved_symbols": registration_resolution.plan.unresolved_symbols,
        "not_registered_missing_figi_symbols": registration_resolution.plan.unresolved_symbols,
        "not_registered_missing_alpaca_symbols": registration_resolution.plan.missing_symbols_from_alpaca,
        "warnings_by_symbol": registration_resolution.plan.warnings_by_symbol,
    }
