"""Canonical Alpaca account-holdings capture and query services."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from msm.api.base import operation_result_rows

from src.holdings.account import DEFAULT_CASH_ASSET_IDENTIFIER, build_account_holdings_rows


@dataclass(frozen=True, slots=True)
class AccountHoldingsCaptureResult:
    account_uid: str
    holdings_set_uid: str | None
    time_index: dt.datetime
    holdings_rows: int
    unresolved_symbols: list[str] = field(default_factory=list)
    skipped_non_equity_symbols: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AccountHoldingsAssetScope:
    """Assets resolved from one recent, immutable account-holdings snapshot."""

    account_uid: str
    holdings_set_uid: str
    snapshot_time: dt.datetime
    asset_uids: list[str]
    asset_identifiers: list[str]


def held_asset_identifiers_from_snapshot_rows(rows: list[dict[str, Any]]) -> list[str]:
    """Return distinct non-cash assets with a non-zero quantity from one snapshot."""
    identifiers: set[str] = set()
    for row in rows:
        identifier = str(row.get("asset_identifier") or "").strip()
        details = row.get("extra_details") or {}
        is_cash = identifier == DEFAULT_CASH_ASSET_IDENTIFIER or (
            isinstance(details, dict) and details.get("kind") == "cash"
        )
        quantity = row.get("quantity")
        if not identifier or is_cash or quantity is None or float(quantity) == 0:
            continue
        identifiers.add(identifier)
    return sorted(identifiers)


def _recent_holdings_set_statement(
    account_uid: uuid.UUID | str,
    *,
    boundary: dt.datetime,
    max_age: dt.timedelta,
):
    from msm.models.accounts.core import AccountHoldingsSetTable
    from sqlalchemy import select

    cutoff = boundary - max_age
    return (
        select(AccountHoldingsSetTable)
        .where(
            AccountHoldingsSetTable.account_uid == account_uid,
            AccountHoldingsSetTable.time_index >= cutoff,
            AccountHoldingsSetTable.time_index <= boundary,
        )
        .order_by(AccountHoldingsSetTable.time_index.desc(), AccountHoldingsSetTable.uid.desc())
        .limit(1)
    )


def resolve_recent_account_holdings_assets(
    account_uid: uuid.UUID | str,
    *,
    as_of: dt.datetime | None = None,
    max_age: dt.timedelta = dt.timedelta(days=30),
) -> AccountHoldingsAssetScope:
    """Resolve the newest stored account snapshot in the inclusive trailing window.

    This is intentionally read-only. It never contacts Alpaca and never captures a new holdings
    snapshot. The exact ``AccountHoldingsSet`` selected here is then used to query holdings rows,
    so simultaneous newer captures cannot mix positions into this resolution.
    """
    from msm.api.assets import Asset
    from msm.data_nodes.accounts.storage import AccountHoldingsStorage
    from msm.models.accounts.core import AccountHoldingsSetTable
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from src.runtime import account_runtime_models, start_markets_engine

    if max_age <= dt.timedelta(0):
        raise ValueError("max_age must be positive.")
    boundary = as_of or dt.datetime.now(dt.timezone.utc)
    if boundary.tzinfo is None:
        raise ValueError("as_of must be timezone-aware.")
    boundary = boundary.astimezone(dt.timezone.utc)
    runtime = start_markets_engine(models=account_runtime_models())
    set_statement = _recent_holdings_set_statement(
        account_uid,
        boundary=boundary,
        max_age=max_age,
    )
    set_operation = compile_markets_statement(
        set_statement,
        context=runtime.context,
        operation="select",
        models=[AccountHoldingsSetTable],
        access="read",
    )
    set_rows = operation_result_rows(
        execute_markets_operation(set_operation, context=runtime.context)
    )
    if not set_rows:
        raise LookupError(
            f"Account {account_uid!s} has no stored holdings snapshot in the trailing "
            f"{max_age.days}-day window. Capture holdings first."
        )
    holdings_set = set_rows[0]
    holdings_set_uid = holdings_set["uid"]
    holdings_statement = (
        select(AccountHoldingsStorage)
        .where(
            AccountHoldingsStorage.account_uid == account_uid,
            AccountHoldingsStorage.holdings_set_uid == holdings_set_uid,
        )
        .order_by(AccountHoldingsStorage.asset_identifier.asc())
    )
    holdings_operation = compile_markets_statement(
        holdings_statement,
        context=runtime.context,
        operation="select",
        models=[AccountHoldingsStorage],
        access="read",
    )
    holdings_rows = operation_result_rows(
        execute_markets_operation(holdings_operation, context=runtime.context)
    )
    identifiers = held_asset_identifiers_from_snapshot_rows(holdings_rows)
    if not identifiers:
        raise ValueError(
            f"The latest eligible holdings snapshot for account {account_uid!s} has no "
            "non-cash positions."
        )
    asset_uids: list[str] = []
    missing: list[str] = []
    for identifier in identifiers:
        asset = Asset.get_by_unique_identifier(identifier)
        if asset is None:
            missing.append(identifier)
        else:
            asset_uids.append(str(asset.uid))
    if missing:
        raise LookupError(
            f"The selected holdings snapshot references unregistered assets: {sorted(missing)!r}."
        )
    return AccountHoldingsAssetScope(
        account_uid=str(account_uid),
        holdings_set_uid=str(holdings_set_uid),
        snapshot_time=holdings_set["time_index"],
        asset_uids=asset_uids,
        asset_identifiers=identifiers,
    )


def plan_alpaca_account_holdings(
    account_uid: uuid.UUID | str,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """Read and translate current positions without writing assets or a holdings snapshot."""
    from src.account.services import (
        build_registered_account_client,
        cash_asset_exists,
        get_account_registration,
        make_symbol_resolver,
        read_alpaca_account,
    )
    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    registration = get_account_registration(account_uid)
    if registration is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    active_client = client or build_registered_account_client(registration)
    snapshot = read_alpaca_account(active_client)
    if str(getattr(snapshot.account, "id", "")) != str(registration["alpaca_account_id"]):
        raise ValueError(
            "The configured Secrets resolve to a different Alpaca account than the registered row."
        )
    cash_identifier = (
        DEFAULT_CASH_ASSET_IDENTIFIER if cash_asset_exists(DEFAULT_CASH_ASSET_IDENTIFIER) else None
    )
    rows, unresolved_symbols, skipped_non_equity = build_account_holdings_rows(
        positions=snapshot.positions,
        cash=getattr(snapshot.account, "cash", None),
        resolve_symbol=make_symbol_resolver(register_missing=False),
        currency_identifier=cash_identifier,
    )
    return {
        "allowed": True,
        "account_uid": str(account_uid),
        "would_write_holdings": len(rows),
        "unresolved_symbols": unresolved_symbols,
        "skipped_non_equity_symbols": skipped_non_equity,
        "warnings": (
            ["Unregistered equity symbols will require registration during execution."]
            if unresolved_symbols
            else []
        ),
    }


def publish_account_holdings_snapshot(
    *,
    account_uid: uuid.UUID | str,
    positions: list[Any],
    cash: Any,
    snapshot_time: dt.datetime,
    symbol_resolver,
    cash_asset_identifier: str = DEFAULT_CASH_ASSET_IDENTIFIER,
) -> AccountHoldingsCaptureResult:
    """Translate and publish an already-read Alpaca snapshot into ms-markets primitives."""
    from msm.api.accounts import AccountHoldingsSet
    from msm.data_nodes.accounts import AccountHoldings
    from msm.services import build_account_holdings_frame

    from src.account.services import cash_asset_exists

    cash_identifier = cash_asset_identifier if cash_asset_exists(cash_asset_identifier) else None
    rows, unresolved_symbols, skipped_non_equity = build_account_holdings_rows(
        positions=positions,
        cash=cash,
        resolve_symbol=symbol_resolver,
        currency_identifier=cash_identifier,
    )
    if not rows:
        return AccountHoldingsCaptureResult(
            account_uid=str(account_uid),
            holdings_set_uid=None,
            time_index=snapshot_time,
            holdings_rows=0,
            unresolved_symbols=unresolved_symbols,
            skipped_non_equity_symbols=skipped_non_equity,
        )

    holdings_set = AccountHoldingsSet.upsert(
        account_uid=account_uid,
        time_index=snapshot_time,
    )
    holdings_frame = build_account_holdings_frame(
        holdings_date=snapshot_time,
        account_uid=account_uid,
        holdings_set_uid=holdings_set.uid,
        positions=rows,
    )
    holdings_node = AccountHoldings(config=AccountHoldings.default_config())
    holdings_node.set_frame(holdings_frame)
    holdings_error, _ = holdings_node.run(debug_mode=True, force_update=True)
    if holdings_error:
        raise RuntimeError(f"Account holdings capture failed for account {account_uid!s}.")
    return AccountHoldingsCaptureResult(
        account_uid=str(account_uid),
        holdings_set_uid=str(holdings_set.uid),
        time_index=snapshot_time,
        holdings_rows=len(rows),
        unresolved_symbols=unresolved_symbols,
        skipped_non_equity_symbols=skipped_non_equity,
    )


def capture_alpaca_account_holdings(
    account_uid: uuid.UUID | str,
    *,
    snapshot_time: dt.datetime | None = None,
    register_missing_assets: bool = True,
    client: Any | None = None,
) -> AccountHoldingsCaptureResult:
    """Read a registered Alpaca account using its stored Secret names and publish a snapshot."""
    from src.account.services import (
        build_registered_account_client,
        get_account_registration,
        make_symbol_resolver,
        read_alpaca_account,
    )
    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    registration = get_account_registration(account_uid)
    if registration is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    active_client = client or build_registered_account_client(registration)
    snapshot = read_alpaca_account(active_client)
    if str(getattr(snapshot.account, "id", "")) != str(registration["alpaca_account_id"]):
        raise ValueError(
            "The configured Secrets resolve to a different Alpaca account than the registered row."
        )
    when = snapshot_time or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    return publish_account_holdings_snapshot(
        account_uid=account_uid,
        positions=snapshot.positions,
        cash=getattr(snapshot.account, "cash", None),
        snapshot_time=when,
        symbol_resolver=make_symbol_resolver(register_missing=register_missing_assets),
    )


def list_account_holdings(
    account_uid: uuid.UUID | str,
    *,
    limit: int = 25,
    offset: int = 0,
    as_of: dt.datetime | None = None,
    holdings_set_uid: uuid.UUID | str | None = None,
    asset_identifiers: list[str] | None = None,
    ordering: str = "-time_index",
) -> tuple[list[dict[str, Any]], int]:
    """Return bounded canonical holdings observations for one account."""
    from msm.data_nodes.accounts.storage import AccountHoldingsStorage
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import func, select

    from src.runtime import account_runtime_models, start_markets_engine

    runtime = start_markets_engine(models=account_runtime_models())
    statement = select(AccountHoldingsStorage).where(
        AccountHoldingsStorage.account_uid == account_uid
    )
    if as_of is not None:
        statement = statement.where(AccountHoldingsStorage.time_index <= as_of)
    if holdings_set_uid is not None:
        statement = statement.where(AccountHoldingsStorage.holdings_set_uid == holdings_set_uid)
    if asset_identifiers:
        statement = statement.where(AccountHoldingsStorage.asset_identifier.in_(asset_identifiers))
    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    ordering_columns = {
        "time_index": AccountHoldingsStorage.time_index,
        "asset_identifier": AccountHoldingsStorage.asset_identifier,
    }
    if ordering_key not in ordering_columns:
        raise ValueError(f"Unsupported holdings ordering {ordering!r}.")
    ordering_column = ordering_columns[ordering_key]
    ordering_expression = ordering_column.desc() if descending else ordering_column.asc()
    statement = statement.order_by(
        ordering_expression,
        AccountHoldingsStorage.time_index.desc(),
        AccountHoldingsStorage.asset_identifier.asc(),
    )
    count_statement = select(func.count().label("count")).select_from(statement.subquery())
    page_operation = compile_markets_statement(
        statement.limit(limit).offset(offset),
        context=runtime.context,
        operation="select",
        models=[AccountHoldingsStorage],
        access="read",
    )
    count_operation = compile_markets_statement(
        count_statement,
        context=runtime.context,
        operation="select",
        models=[AccountHoldingsStorage],
        access="read",
    )
    rows = operation_result_rows(execute_markets_operation(page_operation, context=runtime.context))
    count_rows = operation_result_rows(
        execute_markets_operation(count_operation, context=runtime.context)
    )
    total = int(count_rows[0].get("count", 0)) if count_rows else 0
    from msm.api.assets import Asset

    asset_uid_by_identifier: dict[str, str | None] = {}
    for row in rows:
        identifier = str(row["asset_identifier"])
        if identifier not in asset_uid_by_identifier:
            asset = Asset.get_by_unique_identifier(identifier)
            asset_uid_by_identifier[identifier] = str(asset.uid) if asset else None
        row["asset_uid"] = asset_uid_by_identifier[identifier]
    return rows, total


def get_account_holdings_snapshot(
    account_uid: uuid.UUID | str,
    holdings_set_uid: uuid.UUID | str,
) -> dict[str, Any] | None:
    """Return one immutable holdings set and its canonical position rows."""
    from msm.api.accounts import AccountHoldingsSet

    from src.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    holdings_set = AccountHoldingsSet.get_by_uid(holdings_set_uid)
    if holdings_set is None or str(holdings_set.account_uid) != str(account_uid):
        return None
    positions, _ = list_account_holdings(
        account_uid,
        holdings_set_uid=holdings_set_uid,
        limit=10_000,
    )
    return {
        "uid": str(holdings_set.uid),
        "account_uid": str(holdings_set.account_uid),
        "time_index": holdings_set.time_index,
        "positions": positions,
    }


__all__ = [
    "AccountHoldingsAssetScope",
    "AccountHoldingsCaptureResult",
    "capture_alpaca_account_holdings",
    "get_account_holdings_snapshot",
    "held_asset_identifiers_from_snapshot_rows",
    "list_account_holdings",
    "plan_alpaca_account_holdings",
    "publish_account_holdings_snapshot",
    "resolve_recent_account_holdings_assets",
]
