"""Set-based reads of Markets registries through the ``msm.api`` row classes.

Markets registries (assets, categories and their memberships, accounts, holdings sets, portfolios)
are reached with ``msm.api`` row calls, which run on the Markets API once the process selected the
remote row backend (``alpaca_connectors.runtime.use_markets_api``; ms-markets ADR 0047). These
helpers keep those reads set-based: a request carries at most ``MARKETS_BATCH_SIZE`` keys, and a
collection is read in pages of that size, never one call per row. Composite routes go through
the same client: ``register_account`` (ms-markets ADR 0051); for the sync that sizes and
allocates an account's target (ADR 0023, ms-markets ADR 0054), ``account_target_positions``
and ``allocate_account_virtual_funds``; ``retire_portfolios``
(ms-markets ADR 0049), which ends a portfolio its owner deletes (ADR 0024, ADR 0027); and the
calls of an account delete and a signal delete (ADR 0028): ``current_target_account_uids``,
``clear_account_target``, ``account_delete_preflight``, ``delete_account``,
``portfolio_delete_blockers`` and ``delete_portfolio``, with their set-based forms
``portfolios_delete_blockers`` and ``delete_portfolios`` (issue #46), and ``account_order_uids``,
the account's ``Order`` rows (ms-markets ADR 0064) whose states, events and trades this
application deletes first. The preflights and the bulk
portfolio delete have no typed client method in ms-markets 4.0.1, so they call their routes
through the same client's ``request``. Since ms-markets 5.3.0 several portfolios are retired, and
several signals ended, in one call (MainSequenceMarkets#124, #126). Listing the accounts whose
current target names a portfolio stays a one-item route (#125: the account delete's own
``delete_portfolios`` option answers that); ``each_together`` makes those calls together instead
of one after another.

Signal weights, portfolio weights and portfolio values are published to Markets and read back
from it through the ms-markets recipes (ADR 0031, ms-markets ADR 0060), only by the composites of
the last section: ``publish_signal_weights``, ``signal_bounds``, ``latest_signal_observations``,
``portfolio_values``, ``portfolio_weights_as_of``, ``run_portfolio``, ``run_portfolios``,
``end_signals`` and
``delete_private_series``.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")
R = TypeVar("R")

# Largest key list or page one Markets row call carries (the Markets by-uids routes cap at 500).
MARKETS_BATCH_SIZE = 500
# Most one-item Markets calls ``each_together`` has in flight at once.
MARKETS_CONCURRENT_CALLS = 8


def all_together(*calls: Callable[[], Any]) -> list[Any]:
    """Run calls that do not depend on each other together: independent reads of MetaTables
    and of Markets that would otherwise wait for each other.

    At most ``MARKETS_CONCURRENT_CALLS`` are in flight at once. Each runs in a copy of the
    caller's context, so it acts as the caller's own calls do. Answers in the order of
    ``calls``; every call runs, and the first failure in that order is raised once all have
    ended.
    """
    if len(calls) < 2:
        return [call() for call in calls]
    with ThreadPoolExecutor(max_workers=min(MARKETS_CONCURRENT_CALLS, len(calls))) as pool:
        futures = [pool.submit(contextvars.copy_context().run, call) for call in calls]
    return [future.result() for future in futures]


def each_together(call: Callable[[T], R], values: Iterable[T]) -> list[R]:
    """``call`` once per value, together, for a Markets route that takes one item at a time.

    As ``all_together``: at most ``MARKETS_CONCURRENT_CALLS`` in flight, each in a copy of the
    caller's context, answers in the order of ``values``, and the first failure raised once
    every call has ended.
    """
    items = list(values)
    return all_together(*(lambda item=item: call(item) for item in items))


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


def account_order_uids(account_identifier: str) -> list[uuid.UUID]:
    """The uids of the account's Markets ``Order`` rows (ms-markets ADR 0064), read in pages.

    The account's order states and events in this application's instances name their order by
    this uid only.
    """
    from msm.api import Order

    uids: list[uuid.UUID] = []
    while True:
        page = Order.filter(
            account_identifier=str(account_identifier),
            order_by="uid",
            limit=MARKETS_BATCH_SIZE,
            offset=len(uids),
        )
        uids.extend(row.uid for row in page)
        if len(page) < MARKETS_BATCH_SIZE:
            return uids


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


def allocate_account_virtual_funds(
    account_uid: Any,
    *,
    position_set_uid: Any,
    valuation_time: Any,
    sized_targets: Sequence[Mapping[str, Any]],
) -> Any:
    """Plan and write the allocation of the holdings snapshot at ``valuation_time`` in one call
    (``allocateAccountVirtualFunds``).

    ``sized_targets`` gives every exposure of the ``PositionSet`` in units. No price is sent. The
    default policy runs; Markets plans from these inputs and writes in one transaction.
    """
    from alpaca_connectors.runtime import use_markets_api

    return use_markets_api().client.allocate_account_virtual_funds(
        account_uid,
        position_set_uid=position_set_uid,
        valuation_time=valuation_time,
        sized_targets=list(sized_targets),
    )


@dataclass(frozen=True, slots=True)
class Retirements:
    """What retiring several portfolios did, by canonical portfolio uid."""

    # Retired by this call or already retired: the ``Portfolio`` as Markets answered it.
    retired: dict[str, Any]
    # Markets has no such portfolio.
    missing: set[str]
    # Not retired, with Markets' reason: an account's current target names it, or it changed
    # while it was being retired.
    refused: dict[str, str]


def retire_portfolios(portfolio_uids: Iterable[Any]) -> Retirements:
    """Retire portfolios from now on: one ``bulkRetirePortfolios`` per batch (ms-markets ADR
    0049, MainSequenceMarkets#124).

    Nothing is deleted; retiring a retired portfolio returns it unchanged. Markets retires every
    selected portfolio no account's current target names, in one update, and lists each other
    one in ``failed`` with the reason, which never stops the others.
    """
    from alpaca_connectors.runtime import use_markets_api

    client = use_markets_api().client
    retired: dict[str, Any] = {}
    missing: set[str] = set()
    refused: dict[str, str] = {}
    for batch in batches(str(uuid.UUID(str(uid))) for uid in portfolio_uids):
        answer = client.bulk_retire_portfolios(batch)
        for portfolio in answer.retired:
            retired[str(uuid.UUID(str(portfolio.uid)))] = portfolio
        for failure in answer.failed:
            uid = str(uuid.UUID(str(failure.uid)))
            if failure.detail == f"Portfolio {uid} was not found.":
                missing.add(uid)
            else:
                refused[uid] = failure.detail
    return Retirements(retired=retired, missing=missing, refused=refused)


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

    ms-markets' preflight (``preflightBulkDeletePortfolios``, ms-markets 4.2.0) takes the
    explicit selection and answers ``portfolios``, each with its ``uid`` and its own
    ``blockers``, and ``blockers`` for the whole request: each portfolio's, prefixed with
    ``Portfolio '<unique_identifier>'``, ``Portfolio <uid> was not found.`` for a missing one,
    and those of the delete's own table impact. Each portfolio gets its own blockers and its
    not-found; any other blocker of the request blocks every portfolio of it.
    """
    from msm.client.client import API_PREFIX

    # ms-markets names each portfolio by its canonical uid.
    uids = list(dict.fromkeys(str(uuid.UUID(str(uid))) for uid in portfolio_uids))
    blockers: dict[str, list[str]] = {uid: [] for uid in uids}
    for batch in batches(uids):
        preflight = (
            _client().request(
                "POST",
                f"{API_PREFIX}/portfolio/bulk-delete/preflight/",
                json=_explicit_selections(batch),
            )
            or {}
        )
        labels = []
        for item in preflight.get("portfolios") or []:
            uid = str(uuid.UUID(str(item["uid"])))
            labels.append(f"Portfolio {item.get('unique_identifier')!r}: ")
            if uid in blockers:
                blockers[uid].extend(str(blocker) for blocker in item.get("blockers") or [])
        for blocker in preflight.get("blockers") or []:
            text = str(blocker)
            if any(text.startswith(label) for label in labels):
                continue
            missing = [uid for uid in batch if text == f"Portfolio {uid} was not found."]
            for uid in missing or batch:
                blockers[uid].append(text)
    return blockers


@dataclass(frozen=True, slots=True)
class PortfolioDeletes:
    """What deleting several portfolios did, by canonical portfolio uid."""

    deleted: list[str]
    # Not deleted, with Markets' reason: missing, referenced, or not the caller's to delete.
    failed: dict[str, str]


def delete_portfolios(portfolio_uids: Iterable[Any]) -> PortfolioDeletes:
    """Delete several portfolios nothing references: one ``bulkDeletePortfolios`` per batch.

    The set-based form of ``delete_portfolio`` (issue #46). Since ms-markets 5.3.0
    (MainSequenceMarkets#127) it deletes every portfolio that may go in one transaction, its own
    weights and values with each, and lists each other one in ``failed`` with the reason instead
    of refusing the request.
    """
    from msm.client.client import API_PREFIX

    deleted: list[str] = []
    failed: dict[str, str] = {}
    for batch in batches(str(uuid.UUID(str(uid))) for uid in portfolio_uids):
        answer = (
            _client().request(
                "POST", f"{API_PREFIX}/portfolio/bulk-delete/", json=_explicit_selections(batch)
            )
            or {}
        )
        deleted.extend(str(uuid.UUID(str(item["uid"]))) for item in answer.get("deleted") or [])
        for item in answer.get("failed") or []:
            failed[str(uuid.UUID(str(item["uid"])))] = str(item.get("detail") or "")
    return PortfolioDeletes(deleted=deleted, failed=failed)


def delete_portfolio(portfolio_uid: Any) -> dict[str, Any]:
    """Delete a portfolio nothing references (``DELETE /portfolio/{uid}/``, ms-markets ADR 0049).

    Its datasets in Markets, its weights and values among them (ADR 0031), go with it in one
    transaction. ``Portfolio.delete`` deletes nothing since ms-markets 4.2.0, so the route is
    called. This application's private rows that reference it (rebalance state) refuse the
    delete with ``RESTRICT``, so ``delete_private_series`` deletes them first. Markets refuses a
    referenced or missing portfolio (``MarketsConflictError``, ``LookupError``).
    """
    from msm.client.client import API_PREFIX

    return _client().request("DELETE", f"{API_PREFIX}/portfolio/{uuid.UUID(str(portfolio_uid))}/")


# ------------------------------------------------------------------------------------------------
# Published series (ADR 0031, ms-markets ADR 0060, ms-markets 4.2.0)
#
# Signal weights, portfolio weights and portfolio values are Markets' own rows: this application
# publishes them and reads them back through the recipes of ``msm_portfolios.recipes``, never
# through a table of its own. Each recipe is imported here, inside the function that calls it,
# and each answer is translated here into this module's shapes, so a change of a recipe's
# signature or answer changes this module only. Each docstring names the call, its answer and its
# refusals, and how they become this application's answers.
# ------------------------------------------------------------------------------------------------


def _utc(value: Any) -> dt.datetime | None:
    """A timezone-aware time in UTC, or None for a missing one (``None``, ``NaT``)."""
    import pandas as pd

    if value is None or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if not isinstance(value, dt.datetime):
        text = str(value).strip()
        if not text:
            return None
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("Markets returned a series time without a timezone.")
    return value.astimezone(dt.UTC)


def _number(value: Any) -> float | None:
    """A float, or None for a missing one (``None``, ``NaN``)."""
    import math

    if value is None:
        return None
    number = float(value)
    return None if math.isnan(number) else number


class SignalWeightsRefused(Exception):
    """Markets refused a publication of signal weights; nothing was written.

    ``reason`` is ``unknown_asset`` (an asset Markets has not registered) or ``refused`` (any
    other refusal of the observations: a time at or before the signal's last stored one, a
    different row at a stored time, a value the series cannot store). ms-markets refuses all of
    those with ``ValueError`` (or ``TypeError``), so the caller tells a time conflict apart by
    the signal's stored last time, never by the wording. Markets' message, which can quote
    values, is kept only as the cause.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def publish_signal_weights(
    signal_uid: str,
    observations: Mapping[dt.datetime, Mapping[str, float]],
    *,
    description: str | None,
) -> None:
    """Publish one or more observations of one signal (``recipes.publish_signal_weights``).

    Calls ``publish_signal_weights(signal_uid, {UTC datetime: {asset_identifier: weight}},
    description=description)``. Markets creates the signal's ``SignalMetadata`` with
    ``description`` on its first publication (its manager is the caller, ms-markets ADR 0060
    decision 4) and updates the description when it differs, writes an asset dropped since the
    last observation at 0, and refuses the whole call, writing nothing, for an observation at or
    before the last stored one that differs from it. Its ``SignalPublication`` answer is not
    read: the caller answers what it validated and sent.

    Refusals: ``LookupError`` (an unknown asset, ``services/series.py``
    ``_require_registered_assets``) is ``SignalWeightsRefused("unknown_asset")``; ``ValueError``
    and ``TypeError`` (``MarketsConflictError`` included) are ``SignalWeightsRefused("refused")``.
    ``PermissionError`` (``MarketsAccessError``: this application is not the signal's manager)
    and any other error propagate unchanged.
    """
    from msm_portfolios.recipes import publish_signal_weights as recipe

    try:
        recipe(
            str(signal_uid),
            {time: dict(weights) for time, weights in observations.items()},
            description=description,
        )
    except PermissionError:
        raise
    except LookupError as exc:
        raise SignalWeightsRefused("unknown_asset") from exc
    except (TypeError, ValueError) as exc:
        raise SignalWeightsRefused("refused") from exc


@dataclass(frozen=True, slots=True)
class SignalObservationBounds:
    """The first and last stored observation times of one signal; None before its first."""

    signal_uid: str
    first_observation_at: dt.datetime | None
    last_observation_at: dt.datetime | None


def signal_bounds(signal_uids: Iterable[str]) -> dict[str, SignalObservationBounds]:
    """First and last observation of several signals (``recipes.signal_history``).

    Calls ``signal_history(batch)`` once per ``MARKETS_BATCH_SIZE`` signals; its answer's
    ``bounds`` maps each signal to a ``SeriesBounds`` (``first_time``, ``last_time``,
    ``row_count``). Every signal asked for is answered, with no bounds when Markets has none.
    Refusals propagate.
    """
    from msm_portfolios.recipes import signal_history

    values = list(dict.fromkeys(str(uid) for uid in signal_uids))
    bounds = {
        uid: SignalObservationBounds(uid, first_observation_at=None, last_observation_at=None)
        for uid in values
    }
    for batch in batches(values):
        for uid, item in signal_history(batch).bounds.items():
            if str(uid) in bounds:
                bounds[str(uid)] = SignalObservationBounds(
                    signal_uid=str(uid),
                    first_observation_at=_utc(item.first_time),
                    last_observation_at=_utc(item.last_time),
                )
    return bounds


@dataclass(frozen=True, slots=True)
class LatestSignalObservations:
    """A signal's latest observations: times oldest first, each asset's weights aligned to them."""

    signal_uid: str
    time_indexes: tuple[dt.datetime, ...]
    weights: dict[str, tuple[float, ...]]


def latest_signal_observations(signal_uid: str, *, limit: int) -> LatestSignalObservations:
    """The latest ``limit`` observations of one signal, every asset of each (``signal_history``).

    Calls ``signal_history([signal_uid], latest=limit)``; its answer's ``latest[signal_uid]`` is a
    time (rows, UTC) by asset (columns) ``DataFrame``, oldest first, empty when the signal has
    no observation. An observation is one distinct time and is never cut short by its number of
    assets. A weight missing at a time (an asset before its first observation) is NaN and is
    answered here as 0, as an asset the signal leaves out is held at zero. Refusals propagate.
    """
    from msm_portfolios.recipes import signal_history

    uid = str(signal_uid)
    frame = signal_history([uid], latest=int(limit)).latest.get(uid)
    if frame is None or frame.empty:
        return LatestSignalObservations(signal_uid=uid, time_indexes=(), weights={})
    frame = frame.sort_index()
    return LatestSignalObservations(
        signal_uid=uid,
        time_indexes=tuple(_utc(value) for value in frame.index),
        weights={
            str(asset): tuple(_number(value) or 0.0 for value in frame[asset].tolist())
            for asset in frame.columns
        },
    )


@dataclass(frozen=True, slots=True)
class PortfolioValuesPage:
    """A portfolio's latest values, newest first, and how many it has in all."""

    rows: list[dict[str, Any]]
    total_count: int


def portfolio_values(portfolio_identifier: str, *, limit: int) -> PortfolioValuesPage:
    """A portfolio's latest ``limit`` values, newest first (``recipes.portfolio_history``).

    Calls ``portfolio_history(portfolio_identifier, limit=limit)``, by the portfolio's
    ``unique_identifier``. Its answer's ``values`` is a ``DataFrame`` newest first, indexed by
    ``time_index`` and ``portfolio_identifier``, with ``close``, ``return``,
    ``calculated_close`` and ``close_time``; ``count`` is every value the portfolio has. Answered
    here as rows with ``period_return`` for ``return`` and None for a missing value. Refusals
    propagate.
    """
    from msm_portfolios.recipes import portfolio_history

    answer = portfolio_history(str(portfolio_identifier), limit=int(limit))
    frame = answer.values.reset_index() if not answer.values.empty else None
    rows = [
        {
            "time_index": _utc(row.get("time_index")),
            "close": _number(row.get("close")),
            "period_return": _number(row.get("return")),
            "calculated_close": _number(row.get("calculated_close")),
            "close_time": _utc(row.get("close_time")),
        }
        for row in ([] if frame is None else frame.to_dict(orient="records"))
    ]
    return PortfolioValuesPage(rows=rows, total_count=int(answer.count or 0))


def portfolio_weights_as_of(
    portfolio_identifiers: Iterable[str], *, at: dt.datetime
) -> dict[str, dict[str, float]]:
    """Each portfolio's latest weights at or before ``at`` (``recipes.weights_as_of``).

    Calls ``weights_as_of(batch, at=at)`` once per ``MARKETS_BATCH_SIZE`` portfolios, by
    ``unique_identifier``. Its answer is a ``DataFrame`` indexed by ``time_index``,
    ``portfolio_identifier`` and ``asset_identifier`` with a ``weight`` column; a portfolio with
    no weights by then has no rows and is absent here. A missing weight is left out. Refusals
    propagate.
    """
    from msm_portfolios.recipes import weights_as_of

    weights: dict[str, dict[str, float]] = {}
    for batch in batches(str(identifier) for identifier in portfolio_identifiers):
        frame = weights_as_of(batch, at=at)
        if frame is None or frame.empty:
            continue
        for row in frame.reset_index().to_dict(orient="records"):
            weight = _number(row.get("weight"))
            if weight is not None:
                legs = weights.setdefault(str(row["portfolio_identifier"]), {})
                legs[str(row["asset_identifier"])] = weight
    return weights


def portfolio_weights_recorded(portfolio_identifier: str) -> bool:
    """Whether Markets holds any weights of the portfolio (``PortfolioWeightsSeries.bounds``)."""
    from msm_portfolios.api import PortfolioWeightsSeries

    (bounds,) = PortfolioWeightsSeries.bounds([str(portfolio_identifier)])
    return int(bounds.row_count or 0) > 0


def publish_portfolio_weights(portfolio_identifier: str, rows: list[dict[str, Any]]) -> Any:
    """Publish fixed weights of one portfolio (``PortfolioWeightsSeries.publish``).

    Each row has ``time_index``, ``portfolio_identifier``, ``asset_identifier`` and ``weight``.
    The lifecycle check publishes its fixed-weight portfolios this way (ADR 0031); Markets'
    answer is returned as it is. Refusals propagate.
    """
    from msm_portfolios.api import PortfolioWeightsSeries

    return PortfolioWeightsSeries.publish(str(portfolio_identifier), rows)


def portfolio_series_bounds(
    portfolio_identifiers: Iterable[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Each portfolio's weights and values bounds, keyed by ``unique_identifier``.

    Calls ``PortfolioWeightsSeries.bounds`` and ``PortfolioValuesSeries.bounds`` once each; every
    bound carries ``row_count`` and ``first_time``. Refusals propagate.
    """
    from msm_portfolios.api import PortfolioValuesSeries, PortfolioWeightsSeries

    identifiers = [str(identifier) for identifier in portfolio_identifiers]
    weights = {str(bounds.key): bounds for bounds in PortfolioWeightsSeries.bounds(identifiers)}
    values = {str(bounds.key): bounds for bounds in PortfolioValuesSeries.bounds(identifiers)}
    return weights, values


@dataclass(frozen=True, slots=True)
class PortfolioRunAnswer:
    """What a portfolio run wrote, as Markets answered it."""

    portfolio_uid: str
    portfolio_unique_identifier: str
    # Rows published per series: ``weights`` and ``values``.
    written: dict[str, int]
    first_rebalance: bool


class _ConfigurationPortfolio:
    """The ``portfolio_resolver`` ``run_portfolio`` asks for the run's ``Portfolio`` row.

    ms-markets calls ``get_or_create_from_configuration_hash`` with the configuration's hash
    (``time_index/portfolio_identity.py``); this application's portfolio is identified by its
    configuration UID, not by that hash, so the row is ``Portfolio.upsert`` of the given
    ``unique_identifier`` on the given calendar, created when it does not exist.
    """

    def __init__(self, unique_identifier: str, calendar_uid: Any) -> None:
        self.unique_identifier = unique_identifier
        self.calendar_uid = calendar_uid

    def get_or_create_from_configuration_hash(self, **_: Any) -> Any:
        from msm.api.portfolios import Portfolio

        return Portfolio.upsert(
            unique_identifier=self.unique_identifier, calendar_uid=self.calendar_uid
        )


def run_portfolio(
    portfolio_configuration: Any,
    *,
    unique_identifier: str,
    calendar_uid: Any,
    portfolio_description: str,
    rebalance_output_table: Any,
    event_ledger_output_table: Any,
) -> PortfolioRunAnswer:
    """Run one portfolio's chain and publish it (``recipes.run_portfolio``).

    Calls ``run_portfolio(portfolio_configuration, rebalance_output_table=...,
    event_ledger_output_table=..., portfolio_resolver=..., portfolio_description=...)``.
    ms-markets refuses, with ``ValueError``, a portfolio its ``portfolio_preflight`` finds not
    ready; runs the ledger's graph in one MetaTables invocation: the valuation source (this
    application's interpolated prices) and the rebalance strategy's declared inputs (calendar
    events), then the rebalance state and the ledger (both into this application's private
    tables); publishes the weights and the values the ledger projects (ms-markets ADR 0065);
    reads the signal's weights by its ``signal_uid`` only; and records the signal on the
    ``Portfolio`` row. The row itself is this application's ``unique_identifier`` on
    ``calendar_uid`` (``_ConfigurationPortfolio``).

    Its ``PortfolioRun`` answer gives the ``portfolio`` row, the ``SeriesPublication`` of the
    weights and of the values (None when nothing was new) and ``first_rebalance`` (this run made
    the first). Refusals propagate.
    """
    from msm_portfolios.recipes import run_portfolio as recipe

    answer = recipe(
        portfolio_configuration,
        rebalance_output_table=rebalance_output_table,
        event_ledger_output_table=event_ledger_output_table,
        portfolio_resolver=_ConfigurationPortfolio(unique_identifier, calendar_uid),
        portfolio_description=portfolio_description,
    )
    return _answer(answer, unique_identifier)


def _answer(run: Any, unique_identifier: str) -> PortfolioRunAnswer:
    return PortfolioRunAnswer(
        portfolio_uid=str(run.portfolio.uid),
        portfolio_unique_identifier=str(run.portfolio.unique_identifier or unique_identifier),
        written={
            "weights": 0 if run.weights is None else int(run.weights.written),
            "values": 0 if run.values is None else int(run.values.written),
        },
        first_rebalance=bool(run.first_rebalance),
    )


@dataclass(frozen=True, slots=True)
class PortfolioRunEntry:
    """One portfolio of a ``run_portfolios`` call: its configuration and its ``Portfolio`` row."""

    portfolio_configuration: Any
    unique_identifier: str
    calendar_uid: Any


class _ConfigurationPortfolios:
    """The one ``portfolio_resolver`` ``run_portfolios`` asks for each run's ``Portfolio`` row.

    ms-markets passes each configuration's hash (``time_index/portfolio_identity.py``); each hash
    names one entry, whose row is ``Portfolio.upsert`` of its ``unique_identifier`` on its
    ``calendar_uid``, as ``_ConfigurationPortfolio`` does for one run.
    """

    def __init__(self, by_hash: Mapping[str, PortfolioRunEntry]) -> None:
        self._by_hash = dict(by_hash)

    def get_or_create_from_configuration_hash(
        self, *, portfolio_configuration_hash: str, **_: Any
    ) -> Any:
        from msm.api.portfolios import Portfolio

        entry = self._by_hash.get(str(portfolio_configuration_hash))
        if entry is None:
            raise LookupError("The run asked for a portfolio it was not given.")
        return Portfolio.upsert(
            unique_identifier=entry.unique_identifier, calendar_uid=entry.calendar_uid
        )


def run_portfolios(
    entries: Sequence[PortfolioRunEntry],
    *,
    rebalance_output_table: Any,
    event_ledger_output_table: Any,
) -> list[PortfolioRunAnswer | BaseException]:
    """Run several portfolios in one MetaTables invocation and publish each (``recipes.run_portfolios``).

    Calls ``run_portfolios(configurations, rebalance_output_table=...,
    event_ledger_output_table=..., portfolio_resolver=...)``: every portfolio's ledger is a head
    of one joined graph, so a node several of them share runs once, and the run never waits for
    another process. Each portfolio is answered in the order given, with its
    ``PortfolioRunAnswer`` or with what stopped it: its preflight's refusal, its own node's error,
    a dependency's or its publication's. One portfolio's failure never stops the others.

    Two entries whose configurations hash the same ask the resolver the same question, so they
    go in separate calls; every other entry shares the first.
    """
    from msm_portfolios.recipes import run_portfolios as recipe
    from msm_portfolios.time_index.portfolio_identity import compute_portfolio_configuration_hash

    hashes = [compute_portfolio_configuration_hash(e.portfolio_configuration) for e in entries]
    rounds: list[dict[str, int]] = []
    for position, configuration_hash in enumerate(hashes):
        target = next((r for r in rounds if configuration_hash not in r), None)
        if target is None:
            target = {}
            rounds.append(target)
        target[configuration_hash] = position
    answers: list[PortfolioRunAnswer | BaseException | None] = [None] * len(entries)
    for positions in rounds:
        ordered = list(positions.values())
        runs = recipe(
            [entries[position].portfolio_configuration for position in ordered],
            rebalance_output_table=rebalance_output_table,
            event_ledger_output_table=event_ledger_output_table,
            portfolio_resolver=_ConfigurationPortfolios(
                {hashes[position]: entries[position] for position in ordered}
            ),
        )
        for failure in runs.failed:
            answers[ordered[failure.position]] = failure.error
        by_identifier = {str(run.portfolio.unique_identifier): run for run in runs.ran}
        for position in ordered:
            if answers[position] is not None:
                continue
            identifier = entries[position].unique_identifier
            run = by_identifier.get(identifier)
            answers[position] = (
                _answer(run, identifier)
                if run is not None
                else RuntimeError("The run answered nothing for this portfolio.")
            )
    return answers  # type: ignore[return-value]


class SignalsNotEnded(RuntimeError):
    """Markets refused to end some signals; the others were ended. ``refused`` gives each
    refused signal's reason: its manager is another, or a ``Portfolio`` still names it."""

    def __init__(self, refused: Mapping[str, str]) -> None:
        self.refused = dict(refused)
        super().__init__("Markets did not end these signals: " + "; ".join(self.refused.values()))


def end_signals(signal_uids: Iterable[str]) -> dict[str, Any]:
    """Delete signals' weights and metadata in Markets: one ``recipes.end_signals`` call per
    batch (MainSequenceMarkets#126).

    Markets ends every signal that may go in one transaction and lists each other one in
    ``failed`` with the reason. A signal Markets never registered (nothing was ever published to
    it) has nothing to end and is left out. Any other refusal, from a signal another manager
    manages or a ``Portfolio`` still names, raises ``SignalsNotEnded`` once the others are ended.
    Answers each ended signal's result, by signal uid.
    """
    from msm_portfolios.recipes import end_signals as end_many

    ended: dict[str, Any] = {}
    refused: dict[str, str] = {}
    for batch in batches(str(uid) for uid in signal_uids):
        answer = end_many(batch) or {}
        for item in answer.get("ended") or []:
            ended[str(item["signal_uid"])] = item
        for item in answer.get("failed") or []:
            uid, detail = str(item["signal_uid"]), str(item["detail"])
            if detail != f"No SignalMetadata row has signal_uid {uid!r}.":
                refused[uid] = detail
    if refused:
        raise SignalsNotEnded(refused)
    return ended


def delete_private_series(*, tables: Sequence[Any], portfolio_identifiers: Iterable[str]) -> None:
    """Delete this application's private rows of portfolios (``recipes.delete_private_series``).

    Calls ``delete_private_series(tables, portfolios=batch)`` once per ``MARKETS_BATCH_SIZE``
    portfolios. ms-markets deletes, in one transaction per call and on this process's own runtime
    (never the Markets API), the rows of each table whose ``portfolio_identifier`` is one of
    them, a table before the tables it references. It runs before the ``Portfolio`` rows are
    deleted, which those rows' ``RESTRICT`` keys would refuse. Refusals propagate.
    """
    from msm_portfolios.recipes import delete_private_series as recipe

    for batch in batches(str(identifier) for identifier in portfolio_identifiers):
        recipe(list(tables), portfolios=batch)


__all__ = [
    "MARKETS_BATCH_SIZE",
    "LatestSignalObservations",
    "PortfolioRunAnswer",
    "PortfolioValuesPage",
    "SignalObservationBounds",
    "SignalWeightsRefused",
    "account_delete_preflight",
    "account_order_uids",
    "account_target_positions",
    "allocate_account_virtual_funds",
    "assets_by_identifier",
    "assets_by_uid",
    "batches",
    "category_member_asset_uids",
    "clear_account_target",
    "current_target_account_uids",
    "delete_account",
    "delete_portfolio",
    "delete_portfolios",
    "delete_private_series",
    "end_signals",
    "latest_signal_observations",
    "openfigi_details_by_asset_uid",
    "portfolio_delete_blockers",
    "portfolio_values",
    "portfolio_weights_as_of",
    "portfolios_delete_blockers",
    "publish_signal_weights",
    "read_all_pages",
    "read_in_batches",
    "register_account",
    "PortfolioDeletes",
    "Retirements",
    "SignalsNotEnded",
    "retire_portfolios",
    "portfolio_weights_recorded",
    "PortfolioRunEntry",
    "run_portfolio",
    "run_portfolios",
    "signal_bounds",
    "write_in_batches",
]
