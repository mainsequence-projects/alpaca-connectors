"""Set-based reads of Markets registries through the ``msm.api`` row classes.

Markets registries (assets, categories and their memberships, accounts, holdings sets, portfolios)
are reached with ``msm.api`` row calls, which run on the Markets API once the process selected the
remote row backend (``alpaca_connectors.runtime.use_markets_api``; ms-markets ADR 0047). These
helpers keep those reads set-based: a request carries at most ``MARKETS_BATCH_SIZE`` keys, and a
collection is read in pages of that size, never one call per row. Five composite routes go through
the same client: ``register_account`` (ms-markets ADR 0051); for the sync that sizes and
allocates an account's target (ADR 0023, ms-markets ADR 0054), ``account_target_positions``,
``plan_account_virtual_funds`` and ``allocate_account_virtual_funds``; and ``retire_portfolio``
(ms-markets ADR 0049), which ends a provided-weights portfolio its owner deletes (ADR 0024).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

# Largest key list or page one Markets row call carries (the Markets by-uids routes cap at 500).
MARKETS_BATCH_SIZE = 500


def batches(values: Iterable[T], size: int = MARKETS_BATCH_SIZE) -> list[list[T]]:
    """Split ``values`` (deduplicated, order kept) into lists of at most ``size`` items."""
    unique = list(dict.fromkeys(values))
    return [unique[start : start + size] for start in range(0, len(unique), size)]


def read_in_batches(read: Callable[[list[T]], Sequence[Any]], values: Iterable[T]) -> list[Any]:
    """Call ``read`` once per batch of keys and concatenate the rows."""
    return [row for batch in batches(values) for row in read(batch)]


def read_all_pages(read_page: Callable[[int, int], Sequence[Any]], total: int) -> list[Any]:
    """Read ``total`` rows in pages of ``MARKETS_BATCH_SIZE`` (``read_page(limit, offset)``)."""
    offsets = range(0, max(int(total), 0), MARKETS_BATCH_SIZE)
    return [row for offset in offsets for row in read_page(MARKETS_BATCH_SIZE, offset)]


def assets_by_uid(asset_uids: Iterable[Any]) -> dict[str, Any]:
    """Markets ``Asset`` rows keyed by uid string, read in batches."""
    from msm.api.assets import Asset

    uids = [str(uuid.UUID(str(value))) for value in asset_uids if value not in (None, "")]
    rows = read_in_batches(lambda batch: Asset.filter(uid__in=batch, limit=len(batch)), uids)
    return {str(row.uid): row for row in rows}


def assets_by_identifier(unique_identifiers: Iterable[str]) -> dict[str, Any]:
    """Markets ``Asset`` rows keyed by ``unique_identifier``, read in batches."""
    from msm.api.assets import Asset

    identifiers = [str(value) for value in unique_identifiers if str(value or "").strip()]
    rows = read_in_batches(
        lambda batch: Asset.get_many_by_unique_identifier(batch).values(), identifiers
    )
    return {str(row.unique_identifier): row for row in rows}


def openfigi_details_by_asset_uid(asset_uids: Iterable[Any]) -> dict[str, Any]:
    """Markets ``OpenFigiDetails`` rows keyed by asset uid string, read in batches."""
    from msm.api.assets import OpenFigiDetails

    uids = [str(value) for value in asset_uids if value not in (None, "")]
    rows = read_in_batches(
        lambda batch: OpenFigiDetails.filter(asset_uid__in=batch, limit=len(batch)), uids
    )
    return {str(row.asset_uid): row for row in rows}


def category_member_asset_uids(category_uid: Any) -> list[str]:
    """Asset uids of one ``AssetCategory``'s memberships, in asset uid order."""
    from msm.api.assets import AssetCategoryMembership

    total = AssetCategoryMembership.count(category_uid=str(category_uid))
    rows = read_all_pages(
        lambda limit, offset: AssetCategoryMembership.filter(
            category_uid=str(category_uid), order_by="asset_uid", limit=limit, offset=offset
        ),
        total,
    )
    return [str(row.asset_uid) for row in rows]


def register_account(request: Mapping[str, Any]) -> Any:
    """One ``registerAccount`` call (ms-markets ADR 0051): account, assets and holdings at once.

    Markets creates the missing asset types, assets and cash currency asset, writes the asset
    snapshots that changed, creates the account or updates its differing fields, and writes the
    holdings, in one transaction; it writes nothing when nothing changed. The call goes through the
    process's Markets API backend, so it waits for a starting Markets API only as long as the
    caller will (ADR 0020). Returns ms-markets' ``AccountRegistrationResponse``.
    """
    from alpaca_connectors.runtime import use_markets_api

    return use_markets_api().client.register_account(request)


def account_target_positions(account_uid: Any) -> Any:
    """The account's latest target (``getAccountTargetPositions``): its exposure rows.

    A portfolio row states a share of NAV or an amount and carries the portfolio's
    ``unique_identifier``; an account with no target, or a cleared one, answers no position.
    """
    from alpaca_connectors.runtime import use_markets_api

    return use_markets_api().client.account_target_positions(account_uid)


def plan_account_virtual_funds(
    account_uid: Any,
    *,
    position_set_uid: Any,
    valuation_time: Any,
    sized_targets: Sequence[Mapping[str, Any]],
) -> Any:
    """Plan the allocation of the holdings snapshot at ``valuation_time``; writes nothing.

    ``sized_targets`` gives every exposure of the ``PositionSet`` in units. No price is sent. The
    default policy runs, and the answer names the version it ran with.
    """
    from alpaca_connectors.runtime import use_markets_api

    return use_markets_api().client.plan_account_virtual_funds(
        account_uid,
        position_set_uid=position_set_uid,
        valuation_time=valuation_time,
        sized_targets=list(sized_targets),
    )


def allocate_account_virtual_funds(
    account_uid: Any,
    *,
    position_set_uid: Any,
    valuation_time: Any,
    sized_targets: Sequence[Mapping[str, Any]],
    policy: Any,
    plan_digest: str,
) -> Any:
    """Write the allocation a plan described (``allocateAccountVirtualFunds``).

    ``policy`` and ``plan_digest`` are the plan's, so Markets refuses the write when the target,
    its sizing, the holdings or the policy changed since that plan.
    """
    from alpaca_connectors.runtime import use_markets_api

    return use_markets_api().client.allocate_account_virtual_funds(
        account_uid,
        position_set_uid=position_set_uid,
        valuation_time=valuation_time,
        sized_targets=list(sized_targets),
        policy=policy,
        plan_digest=plan_digest,
    )


def retire_portfolio(portfolio_uid: Any) -> Any:
    """Retire a portfolio from now on (``retirePortfolio``, ms-markets ADR 0049).

    Nothing is deleted; retiring a retired portfolio returns it unchanged. Markets refuses it with
    ``MarketsConflictError`` while an account's current target names the portfolio.
    """
    from alpaca_connectors.runtime import use_markets_api

    return use_markets_api().client.retire_portfolio(portfolio_uid)


__all__ = [
    "MARKETS_BATCH_SIZE",
    "account_target_positions",
    "allocate_account_virtual_funds",
    "assets_by_identifier",
    "assets_by_uid",
    "batches",
    "category_member_asset_uids",
    "openfigi_details_by_asset_uid",
    "plan_account_virtual_funds",
    "read_all_pages",
    "read_in_batches",
    "register_account",
    "retire_portfolio",
]
