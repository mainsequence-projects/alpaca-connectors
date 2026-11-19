"""Set-based reads of Markets registries through the ``msm.api`` row classes.

Markets registries (assets, categories and their memberships, accounts, holdings sets, portfolios)
are reached with ``msm.api`` row calls, which run on the Markets API once the process selected the
remote row backend (``alpaca_connectors.runtime.use_markets_api``; ms-markets ADR 0047). These
helpers keep those reads set-based: a request carries at most ``MARKETS_BATCH_SIZE`` keys, and a
collection is read in pages of that size, never one call per row. Composite routes go through
the same client: ``register_account`` (ms-markets ADR 0051); for the sync that sizes and
allocates an account's target (ADR 0023, ms-markets ADR 0054), ``account_target_positions``,
``plan_account_virtual_funds`` and ``allocate_account_virtual_funds``; ``retire_portfolio``
(ms-markets ADR 0049), which ends a portfolio its owner deletes (ADR 0024, ADR 0027); and the
calls of an account delete and a signal delete (ADR 0028): ``current_target_account_uids``,
``clear_account_target``, ``account_delete_preflight``, ``delete_account``,
``portfolio_delete_blockers``, ``delete_portfolio`` and ``delete_signal_metadata``, with their
set-based forms ``portfolios_delete_blockers``, ``delete_portfolios`` and
``delete_signals_metadata`` (issue #46). The preflights, the bulk portfolio delete and the signal
delete have no typed client method in ms-markets 4.0.1, so they call their routes through the
same client's ``request``. Retiring a portfolio, listing the accounts whose current target names
it, unbinding a signal and deleting a signal have only one-item routes there.
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


def write_in_batches(write: Callable[[list[T]], Any], values: Iterable[T]) -> None:
    """Call ``write`` once per batch of keys: one statement or request per batch, never per key."""
    for batch in batches(values):
        write(batch)


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


def _client() -> Any:
    from alpaca_connectors.runtime import use_markets_api

    return use_markets_api().client


def _explicit_selection(uid: Any) -> dict[str, Any]:
    return _explicit_selections([uid])


def _explicit_selections(uids: Sequence[Any]) -> dict[str, Any]:
    return {"selection": {"mode": "explicit", "uids": [str(uid) for uid in uids]}, "options": {}}


def current_target_account_uids(portfolio_uid: Any) -> list[str]:
    """Accounts whose current target names the portfolio (``listPortfolioAccountTargets``).

    Only current targets: an account that named it earlier, or cleared its target, is not
    listed. Read in pages of ``MARKETS_BATCH_SIZE``.
    """
    client = _client()
    accounts: list[str] = []
    offset = 0
    while True:
        page = client.portfolio_account_targets(
            portfolio_uid, limit=MARKETS_BATCH_SIZE, offset=offset
        )
        accounts.extend(str(uuid.UUID(str(item.account_uid))) for item in page.items)
        if not page.page_info.has_next_page:
            return accounts
        offset += MARKETS_BATCH_SIZE


def clear_account_target(account_uid: Any, *, at: Any) -> Any:
    """Clear the account's current target at ``at``: an empty snapshot (ms-markets ADR 0049)."""
    return _client().clear_account_target_positions(account_uid, target_positions_date=at)


def account_delete_preflight(account_uid: Any) -> dict[str, Any]:
    """What would refuse deleting the account, from ms-markets' preflight (its ADR 0050).

    ``other_namespace_restricts`` are only warnings there: Markets cannot count another
    application's rows, so the delete can still be refused by them.
    """
    from msm.client.client import API_PREFIX

    return _client().request(
        "POST",
        f"{API_PREFIX}/account/bulk-delete/preflight/",
        json=_explicit_selection(account_uid),
    )


def delete_account(account_uid: Any) -> dict[str, Any] | None:
    """Delete the account with its whole history in one transaction (``Account.delete``).

    Another application's ``CASCADE`` keys go with it and its ``RESTRICT`` keys refuse it
    (``MarketsConflictError``). ``None`` when the account does not exist.
    """
    from msm.api.accounts import Account

    return Account.delete(account_uid)


def portfolio_delete_blockers(portfolio_uid: Any) -> list[str]:
    """Why the portfolio cannot be deleted: target rows or virtual funds of any account's history.

    From ms-markets' portfolio delete preflight, which changes nothing.
    """
    return portfolios_delete_blockers([portfolio_uid])[str(uuid.UUID(str(portfolio_uid)))]


def portfolios_delete_blockers(portfolio_uids: Iterable[Any]) -> dict[str, list[str]]:
    """``portfolio_delete_blockers`` of several portfolios: one preflight per batch (issue #46).

    ms-markets' preflight (``preflightBulkDeletePortfolios``) takes the explicit selection and
    prefixes each portfolio's blocker with ``Portfolio <uid>``, the uid as sent, so each blocker
    goes to its portfolio. A blocker that names none (the delete's own table impact) blocks
    every portfolio of its request.
    """
    from msm.client.client import API_PREFIX

    # ms-markets names each portfolio by its canonical uid.
    uids = list(dict.fromkeys(str(uuid.UUID(str(uid))) for uid in portfolio_uids))
    blockers: dict[str, list[str]] = {uid: [] for uid in uids}
    for batch in batches(uids):
        preflight = _client().request(
            "POST",
            f"{API_PREFIX}/portfolio/bulk-delete/preflight/",
            json=_explicit_selections(batch),
        )
        for blocker in (preflight or {}).get("blockers") or []:
            text = str(blocker)
            named = [uid for uid in batch if text.startswith(f"Portfolio {uid}")]
            for uid in named or batch:
                blockers[uid].append(text)
    return blockers


def delete_portfolios(portfolio_uids: Iterable[Any]) -> list[dict[str, Any]]:
    """Delete several portfolios nothing references: one ``bulkDeletePortfolios`` per batch.

    The set-based form of ``delete_portfolio`` (issue #46). ms-markets runs its preflight again
    and refuses the whole request (``MarketsConflictError``) when one of them is referenced or
    missing; its own weights and values go with each.
    """
    from msm.client.client import API_PREFIX

    return [
        _client().request(
            "POST", f"{API_PREFIX}/portfolio/bulk-delete/", json=_explicit_selections(batch)
        )
        for batch in batches(str(uid) for uid in portfolio_uids)
    ]


def delete_portfolio(portfolio_uid: Any) -> dict[str, Any]:
    """Delete a portfolio nothing references (``deletePortfolio``, ms-markets ADR 0049).

    Its core weights and values go with it. Rows in this application's own instances refuse
    the delete with ``RESTRICT``, so their deletion comes first.
    """
    from msm.api.portfolios import Portfolio

    return Portfolio.delete(portfolio_uid)


def delete_signal_metadata(signal_uid: str) -> dict[str, Any] | None:
    """Unbind the signal from its dataset and delete its ``SignalMetadata`` row.

    ms-markets refuses while a ``Portfolio`` follows the signal or an instance holds its
    observations. ``None`` when the signal is not registered.
    """
    return delete_signals_metadata([signal_uid]).get(str(signal_uid))


def delete_signals_metadata(signal_uids: Iterable[str]) -> dict[str, Any]:
    """``delete_signal_metadata`` of several signals; answers each registered signal's result.

    Their ``SignalMetadata`` rows are read in one ``filter`` per batch (issue #46). The unbinding
    (``unregister_signal``) and the delete (``DELETE /portfolio-signal/{uid}/``) are ms-markets'
    one-signal routes, so they run once per registered signal.
    """
    from msm.client.client import API_PREFIX
    from msm_portfolios.api.datasets import PortfolioDatasets
    from msm_portfolios.api.market_metadata import SignalMetadata

    values = list(dict.fromkeys(str(uid) for uid in signal_uids))
    rows = read_in_batches(
        lambda batch: SignalMetadata.filter(signal_uid__in=batch, limit=len(batch)), values
    )
    deleted: dict[str, Any] = {}
    for row in rows:
        PortfolioDatasets.unregister_signal(signal_uid=str(row.signal_uid))
        deleted[str(row.signal_uid)] = _client().request(
            "DELETE", f"{API_PREFIX}/portfolio-signal/{row.uid}/"
        )
    return deleted


__all__ = [
    "MARKETS_BATCH_SIZE",
    "account_delete_preflight",
    "clear_account_target",
    "current_target_account_uids",
    "delete_account",
    "delete_portfolio",
    "delete_portfolios",
    "delete_signal_metadata",
    "delete_signals_metadata",
    "portfolio_delete_blockers",
    "portfolios_delete_blockers",
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
    "write_in_batches",
    "register_account",
    "retire_portfolio",
]
