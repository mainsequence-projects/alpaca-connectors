"""Alpaca account registration and refresh services for ms-markets.

All public operations accept Main Sequence Secret names, never credential values. Account
registration and holdings capture are separate lifecycle operations; callers may explicitly
request one initial holdings capture while registering.
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
    skipped_non_equity_symbols: list[str] = field(default_factory=list)


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


def read_alpaca_account(client: Any) -> AlpacaAccountSnapshot:
    """Fetch account + configuration + positions, plus the raw account dict (live-only fields)."""
    account = client.get_account()
    try:
        configuration = client.get_account_configurations()
    except Exception:
        configuration = None
    positions = list(client.get_all_positions())
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
    """Return True if a currency ``Asset`` with this ``unique_identifier`` already exists.

    The account flow **references** an existing currency asset (e.g. the shared ``USD`` row); it
    never creates one. If absent, the cash holding is skipped (and reported), not manufactured.
    """
    from msm.api.assets import Asset

    return Asset.get_by_unique_identifier(identifier) is not None


def make_symbol_resolver(*, register_missing: bool) -> Callable[[str], str | None]:
    """Resolve an equity symbol to its ms-markets ``Asset.unique_identifier`` (FIGI).

    Uses the project's OpenFIGI-backed resolution. When ``register_missing`` is set, a held equity
    that is not yet registered is created through the **strict FIGI** registration path — which only
    creates an asset when the symbol resolves to a FIGI; FIGI-less / custom symbols are never
    created, just reported. This keeps a registered account's holdings current without forcing the
    user to pre-register every held equity (an account snapshot must be able to add a FIGI-backed
    asset it holds). Returns ``None`` for ambiguous, FIGI-less, or — when ``register_missing`` is
    False (e.g. ``--plan-only``) — simply unregistered symbols.
    """
    from src.assets.resolution import assets_for_ticker

    def resolve(symbol: str) -> str | None:
        assets = assets_for_ticker(symbol)
        if len(assets) == 1:
            return assets[0].unique_identifier
        if len(assets) > 1:
            return None
        if not register_missing:
            return None
        from src.assets.alpaca_us_equities import (
            build_alpaca_us_equity_registration_plan,
            register_alpaca_us_equity_assets,
        )

        # Strict FIGI registration: only creates the asset if the symbol resolves to a FIGI.
        plan = build_alpaca_us_equity_registration_plan(symbols=[symbol])
        register_alpaca_us_equity_assets(plan=plan)
        assets = assets_for_ticker(symbol)
        return assets[0].unique_identifier if len(assets) == 1 else None

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
    capture_initial_holdings: bool = False,
    register_missing_assets: bool = True,
    cash_asset_identifier: str = DEFAULT_CASH_ASSET_IDENTIFIER,
    client: Any | None = None,
    symbol_resolver: Callable[[str], str | None] | None = None,
) -> AlpacaAccountRegistrationResult:
    """Register the Alpaca account into ms-markets and snapshot its balances + holdings.

    Held equities are resolved to a registered ``Asset.unique_identifier`` (FIGI); when
    ``register_missing_assets`` is set (default) a held equity that resolves to a FIGI but is not yet
    registered is auto-registered through the strict FIGI path, so the account snapshot stays current
    without forcing manual pre-registration. FIGI-less symbols and the FIGI-less ``USD`` cash asset
    are never created — cash references a pre-existing currency asset (else the cash row is skipped).
    ``client`` / ``symbol_resolver`` are injectable for testing.
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

    holdings_written = 0
    unresolved_symbols: list[str] = []
    skipped_non_equity: list[str] = []
    if capture_initial_holdings:
        from src.holdings.services import publish_account_holdings_snapshot

        capture_result = publish_account_holdings_snapshot(
            account_uid=account.uid,
            positions=snapshot.positions,
            cash=getattr(account_model, "cash", None),
            snapshot_time=when,
            symbol_resolver=symbol_resolver
            or make_symbol_resolver(register_missing=register_missing_assets),
            cash_asset_identifier=cash_asset_identifier,
        )
        holdings_written = capture_result.holdings_rows
        unresolved_symbols = capture_result.unresolved_symbols
        skipped_non_equity = capture_result.skipped_non_equity_symbols

    return AlpacaAccountRegistrationResult(
        account_unique_identifier=unique_identifier,
        account_uid=str(account.uid),
        is_paper=paper,
        detail_table=AlpacaAccountDetails.__metatable_identifier__,
        holdings_rows=holdings_written,
        unresolved_symbols=unresolved_symbols,
        skipped_non_equity_symbols=skipped_non_equity,
    )


def plan_alpaca_account(
    *,
    api_key_secret_name: str,
    secret_key_secret_name: str,
    paper: bool = True,
    cash_asset_identifier: str = DEFAULT_CASH_ASSET_IDENTIFIER,
    client: Any | None = None,
    symbol_resolver: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Read-only dry run: resolve the account + holdings without writing anything.

    Attaches the runtime (for read-only symbol resolution) and uses ``register_missing=False`` so no
    assets/account/holdings are written. Returns a summary for ``--plan-only``.
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
    resolver = symbol_resolver or make_symbol_resolver(register_missing=False)
    cash_identifier = cash_asset_identifier if cash_asset_exists(cash_asset_identifier) else None
    rows, unresolved_symbols, skipped_non_equity = build_account_holdings_rows(
        positions=snapshot.positions,
        cash=getattr(account_model, "cash", None),
        resolve_symbol=resolver,
        currency_identifier=cash_identifier,
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
        "skipped_non_equity_symbols": skipped_non_equity,
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
    snapshot = read_alpaca_account(active_client)
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
    "cash_asset_exists",
    "get_account_registration",
    "list_account_registrations",
    "make_symbol_resolver",
    "plan_alpaca_account",
    "read_alpaca_account",
    "refresh_alpaca_account",
    "register_alpaca_account",
    "remove_account_registration",
    "update_account_registration",
]
