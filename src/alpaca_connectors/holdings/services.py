"""Canonical Alpaca account-holdings capture and query services.

Holdings are Markets data this application publishes into and reads back through the Markets API
(ms-markets ADR 0047): one ``registerAccount`` call (ms-markets ADR 0051) writes a snapshot together
with the account and any held asset Markets does not know yet, ``AccountHoldingsSnapshots.get``/
``get_many`` read snapshots, and ``AccountHoldingsSet.filter``/``count`` select holdings sets. No
statement reads the Markets holdings tables.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from alpaca_connectors.holdings.account import (
    DEFAULT_CASH_ASSET_IDENTIFIER,
    build_account_holdings_rows,
)


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


class AccountRegistrationConflictError(ValueError):
    """Markets refused an account registration as a conflict (409); it wrote nothing.

    The message is the connector's own: Markets' detail, which names stored assets and types, is
    kept only as the cause.
    """

    ASSET_TYPE_MESSAGE = (
        "An asset this account holds is already registered in Main Sequence Markets with a "
        "different asset type, so Markets refused the registration. Nothing was written."
    )
    MESSAGE = (
        "Main Sequence Markets refused the account registration because it conflicts with what "
        "Markets stores. Nothing was written."
    )

    @classmethod
    def from_markets(cls, exc: Exception) -> AccountRegistrationConflictError:
        # ms-markets ADR 0051: "Existing assets keep their asset_type; ...". With ``overwrite``
        # a registration's holdings never conflict, so this is the conflict expected here.
        asset_type = "asset_type" in str(exc)
        return cls(cls.ASSET_TYPE_MESSAGE if asset_type else cls.MESSAGE)


@dataclass(frozen=True, slots=True)
class AccountHoldingsAssetScope:
    """Assets resolved from one recent, immutable account-holdings snapshot."""

    account_uid: str
    holdings_set_uid: str
    snapshot_time: dt.datetime
    asset_uids: list[str]
    asset_identifiers: list[str]


@dataclass(frozen=True, slots=True)
class AccountRegistrationHoldings:
    """One account's complete holdings, ready for a ``registerAccount`` call (ADR 0051).

    ``positions`` are the holdings rows; ``assets`` the Alpaca catalog record of every held asset;
    ``described_asset_ids`` the held Alpaca asset ids this project's details table already has.
    """

    positions: list[dict[str, Any]]
    assets: list[Any]
    described_asset_ids: frozenset[str]
    cash_currency: str


def resolve_account_registration_holdings(
    *,
    trading_client: Any,
    positions: list[Any],
    cash: Any,
    cash_asset_identifier: str = DEFAULT_CASH_ASSET_IDENTIFIER,
) -> AccountRegistrationHoldings:
    """Resolve every non-zero position to its Alpaca asset by UUID, writing nothing.

    One unfiltered Alpaca catalog request resolves the held assets and one read of this project's
    details table finds those it already describes; Markets is not called. A position the catalog
    does not resolve raises ``AccountHoldingsRegistryError``, before anything is written.
    """
    from alpaca_connectors.account.services import resolve_alpaca_asset_records_for_positions
    from alpaca_connectors.assets.alpaca_asset_details import build_alpaca_unique_identifier
    from alpaca_connectors.assets.alpaca_us_equities import alpaca_asset_ids_with_details

    cash_currency = str(cash_asset_identifier).strip().upper()
    if not cash_currency:
        raise ValueError("A cash currency Asset identifier is required.")
    records_by_position_id = resolve_alpaca_asset_records_for_positions(
        trading_client=trading_client,
        positions=positions,
    )
    rows, unresolved_symbols = build_account_holdings_rows(
        positions=positions,
        cash=cash,
        resolve_asset=lambda position: (
            build_alpaca_unique_identifier(records_by_position_id[id(position)].alpaca_asset_id)
            if id(position) in records_by_position_id
            else None
        ),
        currency_identifier=cash_currency,
    )
    if unresolved_symbols:
        raise AccountHoldingsRegistryError(unresolved_symbols=unresolved_symbols)
    held = {row["asset_identifier"] for row in rows}
    records = {
        str(record.alpaca_asset_id): record
        for record in records_by_position_id.values()
        if build_alpaca_unique_identifier(record.alpaca_asset_id) in held
    }
    return AccountRegistrationHoldings(
        positions=rows,
        assets=list(records.values()),
        described_asset_ids=frozenset(alpaca_asset_ids_with_details(records.keys())),
        cash_currency=cash_currency,
    )


def register_account_holdings(
    *,
    account: Mapping[str, Any],
    holdings: AccountRegistrationHoldings,
    holdings_date: dt.datetime,
) -> Any:
    """Register the account and its holdings snapshot in one Markets call (ADR 0051).

    ``account`` is ms-markets' registration account: ``unique_identifier`` and ``account_name``,
    plus the flags to set. Markets creates the held assets and the cash currency asset it does not
    know, writes their changed snapshots and the holdings, and replaces a snapshot at the same
    ``holdings_date``, as a holdings publish always did; a repeat with the same holdings writes
    nothing. Then this project's details row is written for each held asset that has none. A
    Markets conflict (409) raises ``AccountRegistrationConflictError``. Returns ms-markets'
    ``AccountRegistrationResponse``.
    """
    from msm.api.remote import MarketsConflictError

    from alpaca_connectors.assets.alpaca_us_equities import (
        account_registration_asset,
        write_alpaca_asset_details,
    )
    from alpaca_connectors.markets_api import register_account

    try:
        response = register_account(
            {
                "account": dict(account),
                "assets": [account_registration_asset(record) for record in holdings.assets],
                "cash_currency": holdings.cash_currency,
                "holdings_date": holdings_date,
                "overwrite": True,
                "positions": holdings.positions,
            }
        )
    except MarketsConflictError as exc:
        raise AccountRegistrationConflictError.from_markets(exc) from exc
    write_alpaca_asset_details(
        [
            record
            for record in holdings.assets
            if str(record.alpaca_asset_id) not in holdings.described_asset_ids
        ],
        asset_uids_by_identifier=response.asset_uids,
    )
    return response


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


# Snapshots one ``AccountHoldingsSnapshots.get_many`` call reads when a listing spans many sets.
HOLDINGS_SNAPSHOTS_PER_READ = 50


def _utc(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _holdings_row(
    row: dict[str, Any], *, account_uid: str, holdings_set_uid: str
) -> dict[str, Any]:
    """An ``AccountHoldingsStorage``-shaped row from a Markets holdings snapshot row."""
    quantity = row.get("quantity")
    return {
        "time_index": _utc(row.get("time_index")),
        "account_uid": account_uid,
        "asset_identifier": str(row.get("asset_identifier") or ""),
        "holdings_set_uid": holdings_set_uid,
        "is_trade_snapshot": False,
        "quantity": None if quantity in (None, "") else float(quantity),
        "direction": int(row.get("direction") or 1),
        "target_trade_time": _utc(row.get("target_trade_time")),
        "extra_details": dict(row.get("extra_details") or {}),
    }


def _snapshot_rows(account_uid: str, snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not snapshot or not snapshot.get("holdings_set_uid"):
        return []
    holdings_set_uid = str(snapshot["holdings_set_uid"])
    return [
        _holdings_row(row, account_uid=account_uid, holdings_set_uid=holdings_set_uid)
        for row in snapshot.get("holdings") or []
    ]


def _holdings_sets(
    account_uid: uuid.UUID | str,
    *,
    as_of: dt.datetime | None = None,
    since: dt.datetime | None = None,
    limit: int | None = None,
) -> list[Any]:
    """Holdings sets of one account, newest first, bounded by ``since <= time_index <= as_of``."""
    from msm.api.accounts import AccountHoldingsSet

    from alpaca_connectors.markets_api import read_all_pages

    filters: dict[str, Any] = {"account_uid": str(account_uid)}
    if as_of is not None:
        filters["time_index__lte"] = as_of
    if since is not None:
        filters["time_index__gte"] = since
    order_by = ["-time_index", "-uid"]
    if limit is not None:
        return AccountHoldingsSet.filter(**filters, order_by=order_by, limit=limit)
    total = AccountHoldingsSet.count(**filters)
    return read_all_pages(
        lambda page_limit, offset: AccountHoldingsSet.filter(
            **filters, order_by=order_by, limit=page_limit, offset=offset
        ),
        total,
    )


def _rows_of_sets(account_uid: uuid.UUID | str, holdings_sets: list[Any]) -> list[dict[str, Any]]:
    """Every holdings row of ``holdings_sets``, read in ``get_many`` calls of 50 snapshots."""
    from msm.api.accounts import AccountHoldingsSnapshots

    from alpaca_connectors.markets_api import batches

    account = str(account_uid)
    wanted = {str(holdings_set.uid) for holdings_set in holdings_sets}
    dates = [holdings_set.time_index for holdings_set in holdings_sets]
    snapshots = [
        snapshot
        for batch in batches(dates, HOLDINGS_SNAPSHOTS_PER_READ)
        for snapshot in AccountHoldingsSnapshots.get_many(
            account_uid=account, holdings_dates=batch, limit=len(batch)
        )
        or []
    ]
    return [
        row
        for snapshot in snapshots
        if str(snapshot.get("holdings_set_uid")) in wanted
        for row in _snapshot_rows(account, snapshot)
    ]


def resolve_recent_account_holdings_assets(
    account_uid: uuid.UUID | str,
    *,
    as_of: dt.datetime | None = None,
    max_age: dt.timedelta = dt.timedelta(days=30),
) -> AccountHoldingsAssetScope:
    """Resolve the newest stored account snapshot in the inclusive trailing window.

    This is intentionally read-only. It never contacts Alpaca and never captures a new holdings
    snapshot. The exact ``AccountHoldingsSet`` selected here is then used to read its holdings
    rows, so simultaneous newer captures cannot mix positions into this resolution.
    """
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    if max_age <= dt.timedelta(0):
        raise ValueError("max_age must be positive.")
    boundary = as_of or dt.datetime.now(dt.timezone.utc)
    if boundary.tzinfo is None:
        raise ValueError("as_of must be timezone-aware.")
    boundary = boundary.astimezone(dt.timezone.utc)
    start_markets_engine(models=account_runtime_models())
    holdings_sets = _holdings_sets(account_uid, as_of=boundary, since=boundary - max_age, limit=1)
    if not holdings_sets:
        raise LookupError(
            f"Account {account_uid!s} has no stored holdings snapshot in the trailing "
            f"{max_age.days}-day window. Capture holdings first."
        )
    holdings_set = holdings_sets[0]
    holdings_rows = _rows_of_sets(account_uid, [holdings_set])
    identifiers = held_asset_identifiers_from_snapshot_rows(holdings_rows)
    if not identifiers:
        raise ValueError(
            f"The latest eligible holdings snapshot for account {account_uid!s} has no "
            "non-cash positions."
        )
    from alpaca_connectors.assets.resolution import assets_by_unique_identifiers

    assets_by_identifier = assets_by_unique_identifiers(identifiers)
    missing = [identifier for identifier in identifiers if identifier not in assets_by_identifier]
    if missing:
        raise LookupError(
            f"The selected holdings snapshot references unregistered assets: {sorted(missing)!r}."
        )
    asset_uids = [str(assets_by_identifier[identifier].uid) for identifier in identifiers]
    return AccountHoldingsAssetScope(
        account_uid=str(account_uid),
        holdings_set_uid=str(holdings_set.uid),
        snapshot_time=_utc(holdings_set.time_index),
        asset_uids=asset_uids,
        asset_identifiers=identifiers,
    )


def plan_alpaca_account_holdings(
    account_uid: uuid.UUID | str,
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """Read and translate current positions without writing assets or a holdings snapshot."""
    from alpaca_connectors.account.services import (
        build_registered_account_client,
        cash_asset_exists,
        get_account_registration,
        prepare_alpaca_position_assets,
        read_alpaca_account,
    )
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    registration = get_account_registration(account_uid)
    if registration is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    active_client = client or build_registered_account_client(registration)
    snapshot = read_alpaca_account(active_client)
    if str(getattr(snapshot.account, "id", "")) != str(registration["alpaca_account_id"]):
        raise ValueError(
            "The stored keys resolve to a different Alpaca account than the registered row."
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


def capture_alpaca_account_holdings(
    account_uid: uuid.UUID | str,
    *,
    snapshot_time: dt.datetime | None = None,
    client: Any | None = None,
) -> AccountHoldingsCaptureResult:
    """Publish a complete snapshot of the account's current Alpaca holdings.

    Sync work only: one read of the registration, the Alpaca reads, and one ``registerAccount``
    call at the new ``holdings_date`` that leaves the account as it is and creates only the held
    assets Markets does not know yet. Assets Markets already has are neither registered again nor
    given a snapshot that did not change. An account that holds nothing, not even cash, publishes
    no snapshot.
    """
    from alpaca_connectors.account.services import (
        build_registered_account_client,
        get_account_registration,
        read_alpaca_account,
    )
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    registration = get_account_registration(account_uid)
    if registration is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    active_client = client or build_registered_account_client(registration)
    snapshot = read_alpaca_account(active_client)
    if str(getattr(snapshot.account, "id", "")) != str(registration["alpaca_account_id"]):
        raise ValueError(
            "The stored keys resolve to a different Alpaca account than the registered row."
        )
    when = snapshot_time or dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    holdings = resolve_account_registration_holdings(
        trading_client=active_client,
        positions=snapshot.positions,
        cash=getattr(snapshot.account, "cash", None),
    )
    if not holdings.positions:
        return AccountHoldingsCaptureResult(
            account_uid=str(account_uid),
            holdings_set_uid=None,
            time_index=when,
            holdings_rows=0,
        )
    # The flags are left out, so Markets keeps them; the name is the one just read.
    response = register_account_holdings(
        account={
            "unique_identifier": str(registration["unique_identifier"]),
            "account_name": str(
                registration.get("account_name") or registration["unique_identifier"]
            ),
        },
        holdings=holdings,
        holdings_date=when,
    )
    return AccountHoldingsCaptureResult(
        account_uid=str(account_uid),
        holdings_set_uid=str(response.holdings_set_uid),
        time_index=when,
        holdings_rows=len(holdings.positions),
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
    """Return bounded canonical holdings observations for one account.

    The holdings sets in scope come from ``AccountHoldingsSet`` and their rows from
    ``AccountHoldingsSnapshots`` (one call per 50 snapshots). Markets offers no paged, filtered
    read of holdings rows across sets, so a listing over every set reads all of the account's
    snapshots up to ``as_of`` and pages them here.
    """
    from msm.api.accounts import AccountHoldingsSet

    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    descending = ordering.startswith("-")
    ordering_key = ordering.removeprefix("-")
    if ordering_key not in {"time_index", "asset_identifier"}:
        raise ValueError(f"Unsupported holdings ordering {ordering!r}.")
    start_markets_engine(models=account_runtime_models())
    if holdings_set_uid is not None:
        holdings_sets = AccountHoldingsSet.filter(
            account_uid=str(account_uid), uid=str(holdings_set_uid), limit=1
        )
        if as_of is not None:
            holdings_sets = [item for item in holdings_sets if _utc(item.time_index) <= _utc(as_of)]
    elif latest_only:
        holdings_sets = _holdings_sets(account_uid, as_of=as_of, limit=1)
    else:
        holdings_sets = _holdings_sets(account_uid, as_of=as_of)
    rows = _rows_of_sets(account_uid, holdings_sets)
    if asset_identifiers:
        wanted = set(asset_identifiers)
        rows = [row for row in rows if row["asset_identifier"] in wanted]
    # Secondary order as the compiled query had it: newest time first, then identifier.
    rows.sort(key=lambda row: row["asset_identifier"])
    rows.sort(key=lambda row: row["time_index"], reverse=True)
    rows.sort(key=lambda row: row[ordering_key], reverse=descending)
    total = len(rows)
    page = rows[offset : offset + limit]
    from alpaca_connectors.assets.resolution import assets_by_unique_identifiers

    identifiers = list(dict.fromkeys(str(row["asset_identifier"]) for row in page))
    assets_by_identifier = assets_by_unique_identifiers(identifiers)
    for row in page:
        identifier = str(row["asset_identifier"])
        asset = assets_by_identifier.get(identifier)
        row["asset_uid"] = str(asset.uid) if asset else None
    return page, total


def get_account_holdings_snapshot(
    account_uid: uuid.UUID | str,
    holdings_set_uid: uuid.UUID | str,
) -> dict[str, Any] | None:
    """Return one immutable holdings set and its canonical position rows."""
    from msm.api.accounts import AccountHoldingsSet

    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

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
    "AccountRegistrationConflictError",
    "AccountRegistrationHoldings",
    "capture_alpaca_account_holdings",
    "get_account_holdings_snapshot",
    "held_asset_identifiers_from_snapshot_rows",
    "list_account_holdings",
    "plan_alpaca_account_holdings",
    "register_account_holdings",
    "resolve_account_registration_holdings",
    "resolve_recent_account_holdings_assets",
]
