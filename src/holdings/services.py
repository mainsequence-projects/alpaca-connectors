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


class AccountHoldingsRegistryError(ValueError):
    """Raised before publication when a complete registered holdings set cannot be built."""

    def __init__(
        self,
        *,
        unresolved_symbols: list[str],
    ) -> None:
        self.unresolved_symbols = sorted(set(unresolved_symbols))
        details: list[str] = []
        if self.unresolved_symbols:
            details.append(
                "Unresolved or unregistered assets: " + ", ".join(self.unresolved_symbols) + "."
            )
        super().__init__(
            "Account holdings require every non-zero position to reference a registered Main "
            "Sequence Asset. " + " ".join(details) + " No holdings snapshot was written."
        )


@dataclass(frozen=True, slots=True)
class AccountHoldingsAssetScope:
    """Assets resolved from one recent, immutable account-holdings snapshot."""

    account_uid: str
    holdings_set_uid: str
    snapshot_time: dt.datetime
    asset_uids: list[str]
    asset_identifiers: list[str]


def resolve_complete_account_holdings_rows(
    *,
    positions: list[Any],
    cash: Any,
    asset_resolver,
    cash_asset_identifier: str = DEFAULT_CASH_ASSET_IDENTIFIER,
) -> list[dict[str, Any]]:
    """Ensure canonical cash identity and resolve holdings before snapshot publication."""
    from src.account.services import ensure_cash_currency_asset

    cash_identifier = ensure_cash_currency_asset(cash_asset_identifier)
    rows, unresolved_symbols = build_account_holdings_rows(
        positions=positions,
        cash=cash,
        resolve_asset=asset_resolver,
        currency_identifier=cash_identifier,
    )
    if unresolved_symbols:
        raise AccountHoldingsRegistryError(unresolved_symbols=unresolved_symbols)
    return rows


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


def _latest_holdings_set_uid_statement(
    account_uid: uuid.UUID | str,
    *,
    as_of: dt.datetime | None = None,
):
    """Select the newest immutable holdings-set UID for one account."""
    from msm.models.accounts.core import AccountHoldingsSetTable
    from sqlalchemy import select

    statement = select(AccountHoldingsSetTable.uid).where(
        AccountHoldingsSetTable.account_uid == account_uid
    )
    if as_of is not None:
        statement = statement.where(AccountHoldingsSetTable.time_index <= as_of)
    return statement.order_by(
        AccountHoldingsSetTable.time_index.desc(),
        AccountHoldingsSetTable.uid.desc(),
    ).limit(1)


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
    from src.assets.resolution import assets_by_unique_identifiers

    assets_by_identifier = assets_by_unique_identifiers(identifiers)
    missing = [identifier for identifier in identifiers if identifier not in assets_by_identifier]
    if missing:
        raise LookupError(
            f"The selected holdings snapshot references unregistered assets: {sorted(missing)!r}."
        )
    asset_uids = [str(assets_by_identifier[identifier].uid) for identifier in identifiers]
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
        prepare_alpaca_position_assets,
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
    cash_identifier = DEFAULT_CASH_ASSET_IDENTIFIER
    cash_asset_identifiers_to_ensure = (
        [] if cash_asset_exists(cash_identifier) else [cash_identifier]
    )
    identifiers_by_position_id, registration_resolution = prepare_alpaca_position_assets(
        trading_client=active_client,
        positions=snapshot.positions,
        register_missing=False,
    )

    rows, unresolved_symbols = build_account_holdings_rows(
        positions=snapshot.positions,
        cash=getattr(snapshot.account, "cash", None),
        resolve_asset=lambda position: identifiers_by_position_id.get(id(position)),
        currency_identifier=cash_identifier,
    )
    blockers: list[str] = []
    registerable_asset_ids = sorted(
        str(asset.alpaca_asset_id) for asset in registration_resolution.missing_assets
    )
    return {
        "allowed": not blockers,
        "account_uid": str(account_uid),
        "would_write_holdings": len(rows),
        "unresolved_symbols": unresolved_symbols,
        "alpaca_asset_ids_to_register": registerable_asset_ids,
        "cash_asset_identifiers_to_ensure": cash_asset_identifiers_to_ensure,
        "blockers": blockers,
        "warnings": [
            *(
                ["Execution will register missing held assets by immutable Alpaca asset UUID."]
                if registerable_asset_ids
                else []
            ),
            *(
                [f"Execution will ensure the canonical cash currency Asset {cash_identifier!r}."]
                if cash_asset_identifiers_to_ensure
                else []
            ),
        ],
    }


def publish_account_holdings_snapshot(
    *,
    account_uid: uuid.UUID | str,
    positions: list[Any],
    cash: Any,
    snapshot_time: dt.datetime,
    asset_resolver,
    cash_asset_identifier: str = DEFAULT_CASH_ASSET_IDENTIFIER,
) -> AccountHoldingsCaptureResult:
    """Resolve a complete registered snapshot, then publish it into ms-markets primitives."""
    rows = resolve_complete_account_holdings_rows(
        positions=positions,
        cash=cash,
        asset_resolver=asset_resolver,
        cash_asset_identifier=cash_asset_identifier,
    )
    return publish_resolved_account_holdings_snapshot(
        account_uid=account_uid,
        rows=rows,
        snapshot_time=snapshot_time,
    )


def publish_resolved_account_holdings_snapshot(
    *,
    account_uid: uuid.UUID | str,
    rows: list[dict[str, Any]],
    snapshot_time: dt.datetime,
) -> AccountHoldingsCaptureResult:
    """Publish rows that have already passed complete registered-asset resolution."""
    from msm.api.accounts import AccountHoldingsSet
    from msm.data_nodes.accounts import AccountHoldings
    from msm.services import build_account_holdings_frame

    if not rows:
        return AccountHoldingsCaptureResult(
            account_uid=str(account_uid),
            holdings_set_uid=None,
            time_index=snapshot_time,
            holdings_rows=0,
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
    holdings_error, _ = holdings_node.run()
    if holdings_error:
        raise RuntimeError(f"Account holdings capture failed for account {account_uid!s}.")
    return AccountHoldingsCaptureResult(
        account_uid=str(account_uid),
        holdings_set_uid=str(holdings_set.uid),
        time_index=snapshot_time,
        holdings_rows=len(rows),
    )


def capture_alpaca_account_holdings(
    account_uid: uuid.UUID | str,
    *,
    snapshot_time: dt.datetime | None = None,
    client: Any | None = None,
) -> AccountHoldingsCaptureResult:
    """Register every missing held Alpaca asset by UUID, then publish a complete snapshot."""
    from src.account.services import (
        build_registered_account_client,
        get_account_registration,
        prepare_alpaca_position_assets,
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
    identifiers_by_position_id, _ = prepare_alpaca_position_assets(
        trading_client=active_client,
        positions=snapshot.positions,
        register_missing=True,
    )
    return publish_account_holdings_snapshot(
        account_uid=account_uid,
        positions=snapshot.positions,
        cash=getattr(snapshot.account, "cash", None),
        snapshot_time=when,
        asset_resolver=lambda position: identifiers_by_position_id.get(id(position)),
    )


def list_account_holdings(
    account_uid: uuid.UUID | str,
    *,
    limit: int = 25,
    offset: int = 0,
    as_of: dt.datetime | None = None,
    holdings_set_uid: uuid.UUID | str | None = None,
    latest_only: bool = False,
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
    elif latest_only:
        latest_set_uid = _latest_holdings_set_uid_statement(
            account_uid,
            as_of=as_of,
        ).scalar_subquery()
        statement = statement.where(AccountHoldingsStorage.holdings_set_uid == latest_set_uid)
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
    from src.assets.resolution import assets_by_unique_identifiers

    identifiers = list(dict.fromkeys(str(row["asset_identifier"]) for row in rows))
    assets_by_identifier = assets_by_unique_identifiers(identifiers)
    for row in rows:
        identifier = str(row["asset_identifier"])
        asset = assets_by_identifier.get(identifier)
        row["asset_uid"] = str(asset.uid) if asset else None
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
    "AccountHoldingsRegistryError",
    "capture_alpaca_account_holdings",
    "get_account_holdings_snapshot",
    "held_asset_identifiers_from_snapshot_rows",
    "list_account_holdings",
    "plan_alpaca_account_holdings",
    "publish_account_holdings_snapshot",
    "publish_resolved_account_holdings_snapshot",
    "resolve_complete_account_holdings_rows",
    "resolve_recent_account_holdings_assets",
]
