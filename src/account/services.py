"""Authenticated Alpaca account registration + holdings snapshot for ms-markets.

`register_alpaca_account(...)` is the single entrypoint. It:

1. reads the Alpaca account (`get_account`), configuration, and positions;
2. upserts the ms-markets `Account` keyed on a stable `unique_identifier`;
3. upserts the project-owned `AlpacaAccountDetails` (static metadata) sidecar row;
4. writes a timestamped `AlpacaAccountBalancesStorage` row (the Alpaca financials);
5. resolves-or-registers every held equity (+ a USD cash asset) and snapshots holdings into
   `AccountHoldingsStorage` via `AccountHoldings` / `AccountHoldingsSet`.

The pure data-shaping helpers (`build_account_detail_values`, `build_account_balance_values`,
`build_holdings_rows`) take plain inputs so they are unit-testable without a backend; the
ms-markets writes are lazy-imported and only happen inside `register_alpaca_account`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
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
from src.settings import get_alpaca_api_key, get_alpaca_secret_key

# Default unique_identifier for the account's USD cash position.
DEFAULT_CASH_ASSET_IDENTIFIER = "USD"
US_EQUITY_ASSET_CLASS = "us_equity"

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


def build_holdings_rows(
    *,
    positions: Sequence[Any],
    cash: Any,
    resolve_symbol: Callable[[str], str | None],
    currency_identifier: str | None = None,
    equity_only: bool = True,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Build account-holdings position rows from Alpaca positions + cash.

    References **existing** assets only — ``resolve_symbol`` returns a registered
    ``Asset.unique_identifier`` (FIGI) or ``None``; it never creates assets. The cash row is added
    only when ``currency_identifier`` is a pre-existing currency asset (pass ``None`` to skip cash).

    Returns ``(rows, unresolved_symbols, skipped_non_equity_symbols)``. Each row is a holdings
    payload: ``asset_identifier``, positive ``quantity``, ``direction`` (+1 long / -1 short), and
    provider economics in ``extra_details`` (``AccountHoldingsStorage`` has no price/value columns).
    """
    rows: list[dict[str, Any]] = []
    unresolved: list[str] = []
    skipped_non_equity: list[str] = []

    for position in positions:
        symbol = getattr(position, "symbol", None)
        asset_class = _enum_str(getattr(position, "asset_class", None))
        if equity_only and asset_class != US_EQUITY_ASSET_CLASS:
            if symbol:
                skipped_non_equity.append(symbol)
            continue

        identifier = resolve_symbol(symbol) if symbol else None
        if not identifier:
            if symbol:
                unresolved.append(symbol)
            continue

        quantity = _to_decimal(getattr(position, "qty", None)) or Decimal(0)
        side = _enum_str(getattr(position, "side", None))
        direction = -1 if (side == "short" or quantity < 0) else 1
        rows.append(
            {
                "asset_identifier": identifier,
                "quantity": abs(quantity),
                "direction": direction,
                "extra_details": {
                    "symbol": symbol,
                    "asset_class": asset_class,
                    "exchange": _enum_str(getattr(position, "exchange", None)),
                    "avg_entry_price": getattr(position, "avg_entry_price", None),
                    "market_value": getattr(position, "market_value", None),
                    "cost_basis": getattr(position, "cost_basis", None),
                    "unrealized_pl": getattr(position, "unrealized_pl", None),
                    "current_price": getattr(position, "current_price", None),
                    "alpaca_asset_id": str(getattr(position, "asset_id", "")) or None,
                },
            }
        )

    cash_amount = _to_decimal(cash)
    if currency_identifier and cash_amount is not None and cash_amount != 0:
        rows.append(
            {
                "asset_identifier": currency_identifier,
                "quantity": abs(cash_amount),
                "direction": 1 if cash_amount >= 0 else -1,
                "extra_details": {"kind": "cash"},
            }
        )

    return rows, sorted(set(unresolved)), sorted(set(skipped_non_equity))


# --------------------------------------------------------------------------------------------------
# authenticated Alpaca read
# --------------------------------------------------------------------------------------------------
def build_alpaca_trading_client(*, api_key: str, secret_key: str, paper: bool) -> Any:
    """Build an alpaca-py ``TradingClient`` (paper selects the base URL)."""
    from alpaca.trading.client import TradingClient

    if not api_key or not secret_key:
        raise RuntimeError(
            "Missing Alpaca credentials. Set ALPACA_API_KEY/ALPACA_SECRET_KEY in the environment "
            "or as MainSequence secrets."
        )
    return TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)


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
def _cash_asset_exists(identifier: str) -> bool:
    """Return True if a currency ``Asset`` with this ``unique_identifier`` already exists.

    The account flow **references** an existing currency asset (e.g. the shared ``USD`` row); it
    never creates one. If absent, the cash holding is skipped (and reported), not manufactured.
    """
    from msm.api.assets import Asset

    return Asset.get_by_unique_identifier(identifier) is not None


def _make_symbol_resolver(*, register_missing: bool) -> Callable[[str], str | None]:
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
    api_key: str | None = None,
    secret_key: str | None = None,
    paper: bool = True,
    account_name: str | None = None,
    snapshot_time: dt.datetime | None = None,
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
    resolved_api_key = api_key or get_alpaca_api_key()
    resolved_secret_key = secret_key or get_alpaca_secret_key()

    # Attach the account runtime (re-entrant per process). Lazy import keeps this module offline.
    from msm.api.accounts import Account, AccountHoldingsSet
    from msm.data_nodes.accounts import AccountHoldings
    from msm.repositories.crud import upsert_model
    from msm.services import build_account_holdings_frame

    from src.runtime import account_runtime_models, start_markets_engine

    runtime = start_markets_engine(models=account_runtime_models())
    context = runtime.context

    trading_client = client or build_alpaca_trading_client(
        api_key=resolved_api_key, secret_key=resolved_secret_key, paper=paper
    )
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
        key_fingerprint=api_key_fingerprint(resolved_api_key),
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

    # (4) holdings snapshot: positions + cash -> the ms-markets AccountHoldingsStorage.
    # Equities auto-register only when FIGI-backed; the FIGI-less USD cash asset is referenced, not created.
    resolver = symbol_resolver or _make_symbol_resolver(register_missing=register_missing_assets)
    cash_identifier = cash_asset_identifier if _cash_asset_exists(cash_asset_identifier) else None
    rows, unresolved_symbols, skipped_non_equity = build_holdings_rows(
        positions=snapshot.positions,
        cash=getattr(account_model, "cash", None),
        resolve_symbol=resolver,
        currency_identifier=cash_identifier,
    )

    holdings_written = 0
    if rows:
        holdings_set = AccountHoldingsSet.upsert(account_uid=account.uid, time_index=when)
        holdings_frame = build_account_holdings_frame(
            holdings_date=when,
            account_uid=account.uid,
            holdings_set_uid=holdings_set.uid,
            positions=rows,
        )
        holdings_node = AccountHoldings(config=AccountHoldings.default_config())
        holdings_node.set_frame(holdings_frame)
        holdings_error, _ = holdings_node.run(debug_mode=True, force_update=True)
        if holdings_error:
            raise RuntimeError(f"Account holdings snapshot failed for {unique_identifier!r}.")
        holdings_written = len(rows)

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
    api_key: str | None = None,
    secret_key: str | None = None,
    paper: bool = True,
    cash_asset_identifier: str = DEFAULT_CASH_ASSET_IDENTIFIER,
    client: Any | None = None,
    symbol_resolver: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Read-only dry run: resolve the account + holdings without writing anything.

    Attaches the runtime (for read-only symbol resolution) and uses ``register_missing=False`` so no
    assets/account/holdings are written. Returns a summary for ``--plan-only``.
    """
    resolved_api_key = api_key or get_alpaca_api_key()
    resolved_secret_key = secret_key or get_alpaca_secret_key()

    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())

    trading_client = client or build_alpaca_trading_client(
        api_key=resolved_api_key, secret_key=resolved_secret_key, paper=paper
    )
    snapshot = read_alpaca_account(trading_client)
    account_model = snapshot.account
    unique_identifier = build_account_unique_identifier(
        account_number=str(getattr(account_model, "account_number", "")), is_paper=paper
    )
    resolver = symbol_resolver or _make_symbol_resolver(register_missing=False)
    cash_identifier = cash_asset_identifier if _cash_asset_exists(cash_asset_identifier) else None
    rows, unresolved_symbols, skipped_non_equity = build_holdings_rows(
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
        "equity": getattr(account_model, "equity", None),
        "cash": getattr(account_model, "cash", None),
        "would_write_holdings": len(rows),
        "unresolved_symbols": unresolved_symbols,
        "skipped_non_equity_symbols": skipped_non_equity,
    }


__all__ = [
    "AlpacaAccountRegistrationResult",
    "AlpacaAccountSnapshot",
    "build_account_balance_values",
    "build_account_detail_values",
    "build_alpaca_trading_client",
    "build_holdings_rows",
    "plan_alpaca_account",
    "read_alpaca_account",
    "register_alpaca_account",
]
