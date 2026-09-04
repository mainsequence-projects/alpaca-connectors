"""Alpaca account registration and refresh services for ms-markets.

All public operations accept Main Sequence Secret names, never credential values. Account
registration always resolves/registers held assets and creates the initial holdings snapshot;
later holdings captures remain an independent lifecycle operation.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from src.account import (
    api_key_fingerprint,
    build_account_unique_identifier,
)
from src.account.alpaca_account_details import (
    ACCOUNT_BALANCE_NUMERIC_COLUMNS,
    AlpacaAccountDetails,
)
from src.account.credentials import (
    AlpacaSecretNames,
    build_alpaca_trading_client,
    resolve_alpaca_credentials,
)
from src.holdings import DEFAULT_CASH_ASSET_IDENTIFIER, build_account_holdings_rows

# Detail-table boolean/string/int metadata fields read straight off the TradeAccount (typed).
_ACCOUNT_FLAG_FIELDS = (
    "pattern_day_trader",
    "shorting_enabled",
    "trading_blocked",
    "transfers_blocked",
    "account_blocked",
    "trade_suspended_by_user",
)
_ACCOUNT_CONFIG_FIELDS = (
    "dtbp_check",
    "pdt_check",
    "fractional_trading",
    "max_margin_multiplier",
    "no_shorting",
    "suspend_trade",
    "trade_confirm_email",
    "max_options_trading_level",
)


@dataclass(frozen=True)
class AlpacaAccountSnapshot:
    """Bundled Alpaca account read used by the registration flow."""

    account: Any  # alpaca.trading.models.TradeAccount
    configuration: Any | None  # AccountConfiguration | None
    positions: list[Any]  # list[Position]
    raw_account: dict[str, Any] | None  # full raw GET /v2/account dict (has the docs-only fields)


@dataclass(frozen=True)
class AlpacaAccountRegistrationResult:
    account_unique_identifier: str
    account_uid: str
    is_paper: bool
    detail_table: str
    holdings_rows: int
    unresolved_symbols: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------------
# value helpers (pure)
# --------------------------------------------------------------------------------------------------
def _enum_str(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _raw_or_attr(raw: dict[str, Any] | None, account: Any, name: str) -> Any:
    """Prefer the raw account dict (has the live-only fields), fall back to the typed model."""
    if raw is not None and name in raw:
        return raw.get(name)
    return getattr(account, name, None)


def build_account_balance_values(
    *,
    account: Any,
    raw_account: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map a TradeAccount (+ raw dict) to the current numeric financial column values.

    Prefers the raw account dict (it carries the six live-API-only fields), falling back to the
    typed model. Returns the ``ACCOUNT_BALANCE_NUMERIC_COLUMNS`` plus ``daytrade_count`` /
    ``balance_asof`` — all written onto the ``AlpacaAccountDetails`` row.
    """
    values: dict[str, Any] = {}
    for column in ACCOUNT_BALANCE_NUMERIC_COLUMNS:
        values[column] = _to_decimal(_raw_or_attr(raw_account, account, column))
    values["daytrade_count"] = _to_int(_raw_or_attr(raw_account, account, "daytrade_count"))
    values["balance_asof"] = _raw_or_attr(raw_account, account, "balance_asof")
    return values


def build_account_detail_values(
    *,
    account: Any,
    configuration: Any | None,
    unique_identifier: str,
    key_fingerprint: str,
    api_key_secret_name: str,
    secret_key_secret_name: str,
    is_paper: bool,
    snapshot_time: dt.datetime | None = None,
    raw_account: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map a TradeAccount + AccountConfiguration to ``AlpacaAccountDetails`` column values.

    Includes the static metadata, the account configuration, **and** the current financials
    (cash/equity/buying-power/margin/…) — the latter are refreshed on each register, not stored as a
    time series. Per-position data is written separately as account holdings.
    """
    values: dict[str, Any] = {
        "account_unique_identifier": unique_identifier,
        "alpaca_account_id": str(getattr(account, "id", "")),
        "account_number": getattr(account, "account_number", None),
        "api_key_fingerprint": key_fingerprint,
        "api_key_secret_name": api_key_secret_name,
        "secret_key_secret_name": secret_key_secret_name,
        "is_paper": is_paper,
        "snapshot_time": snapshot_time,
        "status": _enum_str(getattr(account, "status", None)),
        "crypto_status": _enum_str(getattr(account, "crypto_status", None)),
        "currency": getattr(account, "currency", None),
        "multiplier": getattr(account, "multiplier", None),
        "options_approved_level": _to_int(getattr(account, "options_approved_level", None)),
        "options_trading_level": _to_int(getattr(account, "options_trading_level", None)),
        "created_at": getattr(account, "created_at", None),
    }
    for flag in _ACCOUNT_FLAG_FIELDS:
        values[flag] = getattr(account, flag, None)
    for cfg_field in _ACCOUNT_CONFIG_FIELDS:
        raw_value = getattr(configuration, cfg_field, None) if configuration is not None else None
        if cfg_field in ("dtbp_check", "pdt_check", "trade_confirm_email"):
            values[cfg_field] = _enum_str(raw_value)
        elif cfg_field == "max_options_trading_level":
            values[cfg_field] = _to_int(raw_value)
        else:
            values[cfg_field] = raw_value

    # current financials onto the same row
    values.update(build_account_balance_values(account=account, raw_account=raw_account))

    values["raw_account_payload"] = {
        "account": raw_account,
        "configuration": (
            configuration.model_dump(mode="json") if hasattr(configuration, "model_dump") else None
        ),
    }
    return values


# --------------------------------------------------------------------------------------------------
# authenticated Alpaca read
# --------------------------------------------------------------------------------------------------
def _safe_raw_get(client: Any, path: str) -> dict[str, Any] | None:
    try:
        result = client.get(path)
    except Exception:
        return None
    return result if isinstance(result, dict) else None


def read_alpaca_account(
    client: Any,
    *,
    include_positions: bool = True,
) -> AlpacaAccountSnapshot:
    """Fetch account state, optionally including the independently managed positions state."""
    account = client.get_account()
    try:
        configuration = client.get_account_configurations()
    except Exception:
        configuration = None
    positions = list(client.get_all_positions()) if include_positions else []
    raw_account = _safe_raw_get(client, "/account")
    return AlpacaAccountSnapshot(
        account=account,
        configuration=configuration,
        positions=positions,
        raw_account=raw_account,
    )


# --------------------------------------------------------------------------------------------------
# asset resolve-or-register (real default; injectable for tests)
# --------------------------------------------------------------------------------------------------
def cash_asset_exists(identifier: str) -> bool:
    """Return whether the canonical cash-currency ``Asset`` already exists."""
    from msm.api.assets import Asset

    return Asset.get_by_unique_identifier(identifier) is not None


def ensure_cash_currency_asset(identifier: str = DEFAULT_CASH_ASSET_IDENTIFIER) -> str:
    """Idempotently ensure one canonical ms-markets currency Asset.

    Cash is an account balance denominated in a shared currency, not an Alpaca catalog asset.
    Consequently this writes only the built-in currency ``AssetType`` and ``Asset`` rows: it never
    creates an ``AlpacaAssetDetails`` or ``CurrencySpot`` row.
    """
    from msm.api.assets import Asset, AssetType
    from msm.constants import ASSET_TYPE_CURRENCY, ASSET_TYPE_CURRENCY_DEFINITION

    currency_identifier = str(identifier).strip().upper()
    if not currency_identifier:
        raise ValueError("A cash currency Asset identifier is required.")
    AssetType.upsert(**ASSET_TYPE_CURRENCY_DEFINITION.as_payload())
    asset = Asset.upsert(
        unique_identifier=currency_identifier,
        asset_type=ASSET_TYPE_CURRENCY,
    )
    return str(asset.unique_identifier)


def canonical_identifier_for_position(position: Any) -> str | None:
    """Return the Alpaca UUID carried by a position.

    This is the canonical catalog identity for non-crypto assets. Alpaca crypto positions can carry
    a position UUID that differs from the Asset catalog UUID, so crypto must be resolved through
    :func:`resolve_alpaca_asset_record_for_position` before building an Asset identifier.
    """
    from src.assets.alpaca_asset_details import build_alpaca_unique_identifier

    asset_id = getattr(position, "asset_id", None) or getattr(position, "id", None)
    if asset_id is None:
        return None
    try:
        return build_alpaca_unique_identifier(asset_id)
    except (TypeError, ValueError):
        return None


def _normalized_alpaca_symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace("/", "")


def resolve_alpaca_asset_record_for_position(
    *,
    trading_client: Any,
    position: Any,
):
    """Return the authoritative Alpaca Asset catalog record for one held position."""
    from src.assets.alpaca_us_equities import AlpacaAssetRecord

    held_symbol = str(getattr(position, "symbol", "") or "unknown")
    asset_class = (_enum_str(getattr(position, "asset_class", None)) or "").lower()
    lookup_key = (
        held_symbol
        if asset_class in {"crypto", "crypto_perp"}
        else str(getattr(position, "asset_id", None) or getattr(position, "id", None))
    )
    provider_asset = trading_client.get_asset(lookup_key)
    alpaca_asset = AlpacaAssetRecord.from_trading_asset(provider_asset)
    _validate_alpaca_asset_record_for_position(position=position, alpaca_asset=alpaca_asset)
    return alpaca_asset


def _validate_alpaca_asset_record_for_position(*, position: Any, alpaca_asset: Any) -> None:
    """Validate one locally matched provider catalog row against a held position."""
    from src.assets.alpaca_asset_details import normalize_alpaca_asset_id

    held_asset_id = normalize_alpaca_asset_id(
        getattr(position, "asset_id", None) or getattr(position, "id", None)
    )
    held_symbol = str(getattr(position, "symbol", "") or "unknown")
    asset_class = (_enum_str(getattr(position, "asset_class", None)) or "").lower()
    is_crypto = asset_class in {"crypto", "crypto_perp"}

    if is_crypto:
        if _normalized_alpaca_symbol(alpaca_asset.symbol) != _normalized_alpaca_symbol(held_symbol):
            raise ValueError(
                "Alpaca asset identity mismatch for held crypto position "
                f"{held_symbol!r} with position UUID {held_asset_id}: the asset catalog returned "
                f"{alpaca_asset.symbol!r} with UUID {alpaca_asset.alpaca_asset_id}."
            )
    elif alpaca_asset.alpaca_asset_id != held_asset_id:
        raise ValueError(
            "Alpaca asset identity mismatch for held position "
            f"{held_symbol!r}: the position references asset UUID {held_asset_id}, but the "
            f"asset catalog returned {alpaca_asset.symbol!r} with UUID "
            f"{alpaca_asset.alpaca_asset_id}."
        )


def resolve_alpaca_asset_records_for_positions(
    *,
    trading_client: Any,
    positions: list[Any],
) -> dict[int, Any]:
    """Resolve every held position from one unfiltered Alpaca catalog request."""
    from src.assets.alpaca_asset_details import normalize_alpaca_asset_id
    from src.assets.alpaca_us_equities import AlpacaAssetRecord

    positions_with_identity = [
        position
        for position in positions
        if canonical_identifier_for_position(position) is not None
    ]
    if not positions_with_identity:
        return {}

    provider_assets = trading_client.get_all_assets()
    catalog = [AlpacaAssetRecord.from_trading_asset(asset) for asset in provider_assets]
    records_by_id = {str(asset.alpaca_asset_id): asset for asset in catalog}
    records_by_symbol = {_normalized_alpaca_symbol(asset.symbol): asset for asset in catalog}
    resolved: dict[int, Any] = {}
    for position in positions_with_identity:
        held_asset_id = normalize_alpaca_asset_id(
            getattr(position, "asset_id", None) or getattr(position, "id", None)
        )
        held_symbol = str(getattr(position, "symbol", "") or "")
        asset_class = (_enum_str(getattr(position, "asset_class", None)) or "").lower()
        if asset_class in {"crypto", "crypto_perp"}:
            alpaca_asset = records_by_symbol.get(_normalized_alpaca_symbol(held_symbol))
        else:
            alpaca_asset = records_by_id.get(str(held_asset_id))
            if alpaca_asset is None:
                alpaca_asset = records_by_symbol.get(_normalized_alpaca_symbol(held_symbol))
        if alpaca_asset is None:
            continue
        _validate_alpaca_asset_record_for_position(
            position=position,
            alpaca_asset=alpaca_asset,
        )
        resolved[id(position)] = alpaca_asset
    return resolved


def prepare_alpaca_position_assets(
    *,
    trading_client: Any,
    positions: list[Any],
    register_missing: bool,
) -> tuple[dict[int, str], Any]:
    """Plan or bulk-register one position set and return canonical identifiers."""
    from src.assets.alpaca_asset_details import build_alpaca_unique_identifier
    from src.assets.alpaca_us_equities import (
        AlpacaEquityRegistrationPlan,
        register_alpaca_us_equity_assets,
        resolve_alpaca_us_equity_registration_plan,
    )

    records_by_position_id = resolve_alpaca_asset_records_for_positions(
        trading_client=trading_client,
        positions=positions,
    )
    unique_records = {
        str(record.alpaca_asset_id): record for record in records_by_position_id.values()
    }
    plan = AlpacaEquityRegistrationPlan(
        alpaca_assets=list(unique_records.values()),
        classification_passes=[],
        matches_by_symbol={},
        openfigi_enrichment_skipped=True,
    )
    resolution = resolve_alpaca_us_equity_registration_plan(plan)
    if register_missing and plan.alpaca_assets:
        register_alpaca_us_equity_assets(registration_resolution=resolution)
    identifiers_by_position_id = {
        position_id: build_alpaca_unique_identifier(record.alpaca_asset_id)
        for position_id, record in records_by_position_id.items()
    }
    return identifiers_by_position_id, resolution


def make_position_resolver(
    *,
    trading_client: Any,
    register_missing: bool,
) -> Callable[[Any], str | None]:
    """Resolve/register positions by immutable Alpaca asset UUID, never by ticker or FIGI."""
    from msm.api.assets import Asset

    from src.assets.alpaca_asset_details import (
        alpaca_details_for_asset_uid,
        build_alpaca_unique_identifier,
    )

    def resolve(position: Any) -> str | None:
        if canonical_identifier_for_position(position) is None:
            return None

        from src.assets.alpaca_us_equities import (
            AlpacaEquityRegistrationPlan,
            classify_alpaca_us_equities,
            register_alpaca_us_equity_assets,
            resolve_alpaca_us_equity_registration_plan,
        )

        alpaca_asset = resolve_alpaca_asset_record_for_position(
            trading_client=trading_client,
            position=position,
        )
        canonical_identifier = build_alpaca_unique_identifier(alpaca_asset.alpaca_asset_id)
        existing = Asset.get_by_unique_identifier(canonical_identifier)
        if existing is not None and alpaca_details_for_asset_uid(existing.uid) is not None:
            return canonical_identifier
        if not register_missing:
            return None
        if alpaca_asset.asset_class == "us_equity":
            plan = classify_alpaca_us_equities([alpaca_asset])
        else:
            plan = AlpacaEquityRegistrationPlan(
                alpaca_assets=[alpaca_asset],
                classification_passes=[],
                matches_by_symbol={},
                openfigi_unmatched_symbols=[],
            )
        resolution = resolve_alpaca_us_equity_registration_plan(plan)
        register_alpaca_us_equity_assets(registration_resolution=resolution)
        return canonical_identifier

    return resolve


# --------------------------------------------------------------------------------------------------
# registration entrypoint
# --------------------------------------------------------------------------------------------------
def register_alpaca_account(
    *,
    api_key_secret_name: str,
    secret_key_secret_name: str,
    paper: bool = True,
    account_name: str | None = None,
    snapshot_time: dt.datetime | None = None,
    cash_asset_identifier: str = DEFAULT_CASH_ASSET_IDENTIFIER,
    client: Any | None = None,
    asset_resolver: Callable[[Any], str | None] | None = None,
) -> AlpacaAccountRegistrationResult:
    """Register the Alpaca account and its mandatory initial holdings snapshot.

    Every non-zero position is first resolved or registered by immutable Alpaca asset UUID. A
    holdings registry failure blocks both the Account and holdings writes.
    """
    secret_names = AlpacaSecretNames(
        api_key_secret_name=api_key_secret_name,
        secret_key_secret_name=secret_key_secret_name,
    )
    credentials = resolve_alpaca_credentials(secret_names)

    # Attach the account runtime (re-entrant per process). Lazy import keeps this module offline.
    from msm.api.accounts import Account
    from msm.repositories.crud import upsert_model

    from src.runtime import account_runtime_models, start_markets_engine

    runtime = start_markets_engine(models=account_runtime_models())
    context = runtime.context

    trading_client = client or build_alpaca_trading_client(credentials=credentials, paper=paper)
    snapshot = read_alpaca_account(trading_client)
    account_model = snapshot.account

    when = snapshot_time or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    unique_identifier = build_account_unique_identifier(
        account_number=str(getattr(account_model, "account_number", "")), is_paper=paper
    )
    status = (_enum_str(getattr(account_model, "status", None)) or "").upper()

    from src.holdings.services import resolve_complete_account_holdings_rows

    active_asset_resolver = asset_resolver
    if active_asset_resolver is None:
        identifiers_by_position_id, _ = prepare_alpaca_position_assets(
            trading_client=trading_client,
            positions=snapshot.positions,
            register_missing=True,
        )
        active_asset_resolver = lambda position: identifiers_by_position_id.get(id(position))

    resolved_holdings_rows = resolve_complete_account_holdings_rows(
        positions=snapshot.positions,
        cash=getattr(account_model, "cash", None),
        asset_resolver=active_asset_resolver,
        cash_asset_identifier=cash_asset_identifier,
    )

    account = Account.upsert(
        unique_identifier=unique_identifier,
        account_name=account_name or unique_identifier,
        is_paper=paper,
        account_is_active=(status == "ACTIVE"),
    )

    # (3) detail sidecar row: static metadata + current financials (cash/equity/buying-power/...).
    detail_values = build_account_detail_values(
        account=account_model,
        configuration=snapshot.configuration,
        unique_identifier=unique_identifier,
        key_fingerprint=api_key_fingerprint(credentials.api_key),
        api_key_secret_name=secret_names.api_key_secret_name,
        secret_key_secret_name=secret_names.secret_key_secret_name,
        is_paper=paper,
        snapshot_time=when,
        raw_account=snapshot.raw_account,
    )
    upsert_model(
        context,
        model=AlpacaAccountDetails,
        values={"account_uid": account.uid, **detail_values},
        conflict_columns=("account_uid",),
    )

    from src.holdings.services import publish_resolved_account_holdings_snapshot

    capture_result = publish_resolved_account_holdings_snapshot(
        account_uid=account.uid,
        rows=resolved_holdings_rows,
        snapshot_time=when,
    )

    return AlpacaAccountRegistrationResult(
        account_unique_identifier=unique_identifier,
        account_uid=str(account.uid),
        is_paper=paper,
        detail_table=AlpacaAccountDetails.__metatable_identifier__,
        holdings_rows=capture_result.holdings_rows,
        unresolved_symbols=capture_result.unresolved_symbols,
    )


def plan_alpaca_account(
    *,
    api_key_secret_name: str,
    secret_key_secret_name: str,
    paper: bool = True,
    cash_asset_identifier: str = DEFAULT_CASH_ASSET_IDENTIFIER,
    client: Any | None = None,
    asset_resolver: Callable[[Any], str | None] | None = None,
) -> dict[str, Any]:
    """Read-only dry run: resolve the account + holdings without writing anything.

    Attaches the runtime for read-only identity checks. Missing provider-native assets are reported
    as planned registrations; no assets, account, or holdings are written.
    """
    secret_names = AlpacaSecretNames(
        api_key_secret_name=api_key_secret_name,
        secret_key_secret_name=secret_key_secret_name,
    )
    credentials = resolve_alpaca_credentials(secret_names)

    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())

    trading_client = client or build_alpaca_trading_client(credentials=credentials, paper=paper)
    snapshot = read_alpaca_account(trading_client)
    account_model = snapshot.account
    unique_identifier = build_account_unique_identifier(
        account_number=str(getattr(account_model, "account_number", "")), is_paper=paper
    )
    registration_resolution = None
    active_asset_resolver = asset_resolver
    if active_asset_resolver is None:
        identifiers_by_position_id, registration_resolution = prepare_alpaca_position_assets(
            trading_client=trading_client,
            positions=snapshot.positions,
            register_missing=False,
        )
        active_asset_resolver = lambda position: identifiers_by_position_id.get(id(position))

    cash_identifier = str(cash_asset_identifier).strip().upper()
    cash_asset_identifiers_to_ensure = (
        [] if cash_asset_exists(cash_identifier) else [cash_identifier]
    )
    rows, unresolved_symbols = build_account_holdings_rows(
        positions=snapshot.positions,
        cash=getattr(account_model, "cash", None),
        resolve_asset=active_asset_resolver,
        currency_identifier=cash_identifier,
    )
    assets_to_register = sorted(
        str(asset.alpaca_asset_id)
        for asset in (
            registration_resolution.missing_assets if registration_resolution is not None else []
        )
    )
    return {
        "account_unique_identifier": unique_identifier,
        "account_number": getattr(account_model, "account_number", None),
        "status": _enum_str(getattr(account_model, "status", None)),
        "is_paper": paper,
        "api_key_secret_name": secret_names.api_key_secret_name,
        "secret_key_secret_name": secret_names.secret_key_secret_name,
        "equity": getattr(account_model, "equity", None),
        "cash": getattr(account_model, "cash", None),
        "would_write_holdings": len(rows),
        "unresolved_symbols": unresolved_symbols,
        "alpaca_asset_ids_to_register": assets_to_register,
        "cash_asset_identifiers_to_ensure": cash_asset_identifiers_to_ensure,
    }


def get_account_registration(account_uid: str) -> dict[str, Any] | None:
    """Return the ms-markets Account merged with its Alpaca detail row."""
    from msm.api.accounts import Account
    from msm.api.base import operation_result_rows
    from msm.repositories.crud import get_model_by_uid

    from src.runtime import account_runtime_models, start_markets_engine

    runtime = start_markets_engine(models=account_runtime_models())
    account = Account.get_by_uid(account_uid)
    detail_result = get_model_by_uid(
        runtime.context,
        model=AlpacaAccountDetails,
        uid=account_uid,
    )
    detail_rows = operation_result_rows(detail_result)
    if account is None or not detail_rows:
        return None
    return {
        **account.model_dump(mode="json"),
        **detail_rows[0],
        "uid": str(account.uid),
        "account_uid": str(account.uid),
    }


def list_account_registrations(
    *,
    limit: int = 25,
    offset: int = 0,
    search: str | None = None,
    active: bool | None = None,
    is_paper: bool | None = None,
    ordering: str = "account_name",
) -> tuple[list[dict[str, Any]], int]:
    """Return paginated Alpaca registrations, excluding unrelated ms-markets Accounts."""
    from msm.api.accounts import Account
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, or_, select

    from src.runtime import account_runtime_models, start_markets_engine

    runtime = start_markets_engine(models=account_runtime_models())
    account_model = Account.__table__
    detail_columns = [
        column.label(column.key)
        for column in AlpacaAccountDetails.__table__.columns
        if column.key != "account_uid"
    ]
    statement = (
        select(
            account_model.uid.label("uid"),
            account_model.uid.label("account_uid"),
            account_model.unique_identifier,
            account_model.account_name,
            account_model.is_paper,
            account_model.account_is_active,
            account_model.account_group_uid,
            account_model.holdings_data_node_uid,
            account_model.metadata_json,
            *detail_columns,
        )
        .select_from(AlpacaAccountDetails)
        .join(account_model, AlpacaAccountDetails.account_uid == account_model.uid)
    )
    if active is not None:
        statement = statement.where(account_model.account_is_active == active)
    if is_paper is not None:
        statement = statement.where(account_model.is_paper == is_paper)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(
                account_model.account_name.ilike(pattern),
                account_model.unique_identifier.ilike(pattern),
            )
        )
    count_statement = select(func.count().label("count")).select_from(statement.subquery())
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    ordering_columns = {
        "account_name": account_model.account_name,
        "unique_identifier": account_model.unique_identifier,
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported account ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    page_statement = statement.order_by(ordering_expression, account_model.uid.asc())
    page_statement = page_statement.limit(limit).offset(offset)
    page_operation = compile_markets_statement(
        page_statement,
        context=runtime.context,
        operation="select",
        models=[AlpacaAccountDetails, account_model],
        access="read",
    )
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
        models=[AlpacaAccountDetails, account_model],
        access="read",
    )
    registrations = operation_result_rows(
        execute_markets_operation(page_operation, context=runtime.context)
    )
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    total = int(count_rows[0].get("count", 0)) if count_rows else 0
    return registrations, total


def update_account_registration(
    account_uid: str,
    *,
    account_name: str | None = None,
    api_key_secret_name: str | None = None,
    secret_key_secret_name: str | None = None,
    account_is_active: bool | None = None,
) -> dict[str, Any]:
    """Update mutable account registration fields; paper/live identity is immutable."""
    from msm.api.accounts import Account
    from msm.repositories.crud import update_model

    from src.runtime import account_runtime_models, start_markets_engine

    current = get_account_registration(account_uid)
    if current is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    runtime = start_markets_engine(models=account_runtime_models())
    account_values = {
        key: value
        for key, value in {
            "account_name": account_name,
            "account_is_active": account_is_active,
        }.items()
        if value is not None
    }
    if account_values:
        Account.update(account_uid, account_values)
    detail_values: dict[str, Any] = {}
    if api_key_secret_name is not None or secret_key_secret_name is not None:
        names = AlpacaSecretNames(
            api_key_secret_name=api_key_secret_name or str(current["api_key_secret_name"]),
            secret_key_secret_name=secret_key_secret_name or str(current["secret_key_secret_name"]),
        )
        credentials = resolve_alpaca_credentials(names)
        candidate_client = build_alpaca_trading_client(
            credentials=credentials,
            paper=bool(current["is_paper"]),
        )
        candidate_account = candidate_client.get_account()
        if str(getattr(candidate_account, "id", "")) != str(current["alpaca_account_id"]):
            raise ValueError(
                "The configured Secrets resolve to a different Alpaca account than the "
                "registered row."
            )
        detail_values.update(
            api_key_secret_name=names.api_key_secret_name,
            secret_key_secret_name=names.secret_key_secret_name,
            api_key_fingerprint=api_key_fingerprint(credentials.api_key),
        )
    if detail_values:
        update_model(
            runtime.context,
            model=AlpacaAccountDetails,
            uid=account_uid,
            values=detail_values,
        )
    updated = get_account_registration(account_uid)
    if updated is None:
        raise RuntimeError("Updated account registration could not be read back.")
    return updated


def remove_account_registration(account_uid: str) -> dict[str, Any]:
    """Remove the Alpaca detail binding and deactivate Account; holdings history is retained."""
    from msm.api.accounts import Account, AccountHoldingsSet
    from msm.repositories.crud import delete_model

    from src.runtime import account_runtime_models, start_markets_engine

    current = get_account_registration(account_uid)
    if current is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    from src.operations.signal_job_configurations import signal_job_configurations_for_account

    signal_configurations = signal_job_configurations_for_account(account_uid)
    if signal_configurations:
        dependencies = ", ".join(
            f"{configuration.name} ({configuration.uid!s})"
            for configuration in signal_configurations
        )
        raise ValueError(
            f"Alpaca account registration {account_uid!s} is referenced by signal Job "
            f"configuration(s): {dependencies}. Delete or change those configurations first."
        )
    runtime = start_markets_engine(models=account_runtime_models())
    holdings_sets = AccountHoldingsSet.filter(account_uid=account_uid, limit=500)
    delete_model(runtime.context, model=AlpacaAccountDetails, uid=account_uid)
    Account.update(account_uid, account_is_active=False)
    return {
        "account_uid": str(account_uid),
        "registration_removed": True,
        "account_deactivated": True,
        "retained_holdings_sets": len(holdings_sets),
    }


def build_registered_account_client(registration: dict[str, Any]):
    names = AlpacaSecretNames(
        api_key_secret_name=str(registration["api_key_secret_name"]),
        secret_key_secret_name=str(registration["secret_key_secret_name"]),
    )
    credentials = resolve_alpaca_credentials(names)
    return build_alpaca_trading_client(
        credentials=credentials,
        paper=bool(registration["is_paper"]),
    )


def refresh_alpaca_account(
    account_uid: str,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """Refresh account metadata and balances without creating a holdings snapshot."""
    from msm.api.accounts import Account
    from msm.repositories.crud import update_model

    from src.runtime import account_runtime_models, start_markets_engine

    registration = get_account_registration(account_uid)
    if registration is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    active_client = client or build_registered_account_client(registration)
    snapshot = read_alpaca_account(active_client, include_positions=False)
    if str(getattr(snapshot.account, "id", "")) != str(registration["alpaca_account_id"]):
        raise ValueError(
            "The configured Secrets resolve to a different Alpaca account than the registered row."
        )
    runtime = start_markets_engine(models=account_runtime_models())
    status = (_enum_str(getattr(snapshot.account, "status", None)) or "").upper()
    Account.update(account_uid, account_is_active=(status == "ACTIVE"))
    values = build_account_detail_values(
        account=snapshot.account,
        configuration=snapshot.configuration,
        unique_identifier=str(registration["unique_identifier"]),
        key_fingerprint=str(registration["api_key_fingerprint"]),
        api_key_secret_name=str(registration["api_key_secret_name"]),
        secret_key_secret_name=str(registration["secret_key_secret_name"]),
        is_paper=bool(registration["is_paper"]),
        snapshot_time=dt.datetime.now(dt.timezone.utc).replace(microsecond=0),
        raw_account=snapshot.raw_account,
    )
    update_model(
        runtime.context,
        model=AlpacaAccountDetails,
        uid=account_uid,
        values=values,
    )
    refreshed = get_account_registration(account_uid)
    if refreshed is None:
        raise RuntimeError("Refreshed account registration could not be read back.")
    return refreshed


__all__ = [
    "AlpacaAccountRegistrationResult",
    "AlpacaAccountSnapshot",
    "build_account_balance_values",
    "build_account_detail_values",
    "build_alpaca_trading_client",
    "build_registered_account_client",
    "canonical_identifier_for_position",
    "cash_asset_exists",
    "ensure_cash_currency_asset",
    "get_account_registration",
    "list_account_registrations",
    "make_position_resolver",
    "plan_alpaca_account",
    "prepare_alpaca_position_assets",
    "read_alpaca_account",
    "refresh_alpaca_account",
    "register_alpaca_account",
    "remove_account_registration",
    "resolve_alpaca_asset_record_for_position",
    "resolve_alpaca_asset_records_for_positions",
    "update_account_registration",
]
