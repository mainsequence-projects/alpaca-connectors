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
``portfolio_delete_blockers`` and ``delete_portfolio``, with their set-based forms
``portfolios_delete_blockers`` and ``delete_portfolios`` (issue #46). The preflights and the bulk
portfolio delete have no typed client method in ms-markets 4.0.1, so they call their routes
through the same client's ``request``. Retiring a portfolio and listing the accounts whose current
target names it have only one-item routes there.

Signal weights, portfolio weights and portfolio values are published to Markets and read back
from it through the ms-markets recipes (ADR 0031, ms-markets ADR 0060), only by the composites of
the last section: ``publish_signal_weights``, ``signal_bounds``, ``latest_signal_observations``,
``portfolio_values``, ``portfolio_weights_as_of``, ``run_portfolio``, ``end_signals`` and
``delete_private_series``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
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

    Its weights and values, which are Markets' own rows (ADR 0031), go with it. This
    application's private rows that reference it (rebalance state) refuse the delete with
    ``RESTRICT``, so ``delete_private_series`` deletes them first.
    """
    from msm.api.portfolios import Portfolio

    return Portfolio.delete(portfolio_uid)


# ------------------------------------------------------------------------------------------------
# Published series (ADR 0031, ms-markets ADR 0060)
#
# Signal weights, portfolio weights and portfolio values are Markets' own rows: this application
# publishes them and reads them back through the ms-markets recipes (``msm_portfolios.recipes``),
# never through a table of its own. Every recipe is imported here, inside the function that calls
# it, and every answer is translated here into this module's shapes, so a signature or an answer
# that differs from what a docstring assumes changes this module only. Each docstring states what
# this application needs from its recipe: the arguments, the answer and the refusals.
# ------------------------------------------------------------------------------------------------


def _get(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _utc(value: Any) -> dt.datetime | None:
    """A timezone-aware time in UTC, or None. A naive time is refused: Markets sends UTC."""
    if value is None:
        return None
    if not isinstance(value, dt.datetime):
        text = str(value).strip()
        if not text:
            return None
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("Markets returned a series time without a timezone.")
    return value.astimezone(dt.UTC)


class SignalWeightsRefused(Exception):
    """Markets refused a publication of signal weights; nothing was written.

    ``reason`` is ``not_after_last`` (an observation at or before the signal's last stored one,
    or a different row for a stored time), ``unknown_asset`` (an asset Markets has not
    registered) or ``invalid`` (any other value the series cannot store). The caller answers it
    in its own words; Markets' message, which can quote values, is kept only as the cause.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def publish_signal_weights(
    signal_uid: str,
    observations: Mapping[dt.datetime, Mapping[str, float]],
    *,
    signal_description: str,
    description: str | None,
) -> None:
    """Publish one or more observations of one signal (recipe ``publish_signal_weights``).

    Assumed call: ``publish_signal_weights(signal_uid=str, observations={UTC datetime:
    {asset_identifier: weight}}, signal_description=str, description=str | None)``. The
    observations are validated here first: timezone-aware UTC times, oldest first, finite
    weights, registered Alpaca assets. Markets creates the signal's ``SignalMetadata`` with
    ``signal_description`` on its first publication (recording this application as its manager,
    ms-markets ADR 0060 decision 4), writes an asset dropped since the last observation at 0,
    refuses an observation at or before the last stored one, and writes nothing when it refuses.
    Repeating a publication writes nothing.

    Its answer (the count, the assets and the first and last time) is not read: the caller
    answers what it validated and sent, which is what Markets wrote.

    Refusals, raised as ``SignalWeightsRefused`` with Markets' error as the cause:

    - ``MarketsConflictError`` (409): ``not_after_last``, answered ``409
      observation_not_after_last``. The status decides, never the wording.
    - ``LookupError`` (404): ``unknown_asset``, answered ``422 unknown_asset``.
    - ``ValueError`` or ``TypeError`` (400, 422): ``invalid``, answered ``422
      invalid_observation``.

    Any other error (``MarketsAccessError`` when Markets does not record this application as the
    signal's manager, an unavailable Markets API) propagates unchanged.
    """
    from msm.api.remote import MarketsConflictError
    from msm_portfolios.recipes import publish_signal_weights as recipe

    try:
        recipe(
            signal_uid=str(signal_uid),
            observations={time: dict(weights) for time, weights in observations.items()},
            signal_description=signal_description,
            description=description,
        )
    except MarketsConflictError as exc:
        raise SignalWeightsRefused("not_after_last") from exc
    except LookupError as exc:
        raise SignalWeightsRefused("unknown_asset") from exc
    except (TypeError, ValueError) as exc:
        raise SignalWeightsRefused("invalid") from exc


@dataclass(frozen=True, slots=True)
class SignalObservationBounds:
    """The first and last stored observation times of one signal; None before its first."""

    signal_uid: str
    first_observation_at: dt.datetime | None
    last_observation_at: dt.datetime | None


def signal_bounds(signal_uids: Iterable[str]) -> dict[str, SignalObservationBounds]:
    """First and last observation of several signals (recipe ``signal_history``).

    Assumed call: ``signal_history(signal_uids=[str, ...])``, once per ``MARKETS_BATCH_SIZE``
    signals. Assumed answer: a mapping of ``signal_uid`` to an item with
    ``first_observation_at`` and ``last_observation_at``; a signal with no observation is absent
    or has both None. Every signal asked for is answered here, with no bounds when Markets has
    none. Refusals propagate.
    """
    from msm_portfolios.recipes import signal_history

    values = list(dict.fromkeys(str(uid) for uid in signal_uids))
    bounds = {
        uid: SignalObservationBounds(uid, first_observation_at=None, last_observation_at=None)
        for uid in values
    }
    for batch in batches(values):
        answer = signal_history(signal_uids=batch) or {}
        for uid in batch:
            item = answer.get(uid)
            if item is None:
                continue
            bounds[uid] = SignalObservationBounds(
                signal_uid=uid,
                first_observation_at=_utc(_get(item, "first_observation_at")),
                last_observation_at=_utc(_get(item, "last_observation_at")),
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

    Assumed call: ``signal_history(signal_uids=[signal_uid], latest=limit)``. An observation is
    one distinct time and is never cut short by its number of assets. Assumed answer:
    ``answer[signal_uid].matrix``, a pandas ``DataFrame`` with one row per observation time
    (timezone-aware, any order) and one column per asset identifier. A weight missing at a time
    (an asset before its first observation) is NaN and is answered here as 0, as an asset the
    signal leaves out is held at zero. No matrix, or an absent signal, is no observation.
    Refusals propagate.
    """
    import math

    from msm_portfolios.recipes import signal_history

    uid = str(signal_uid)
    item = (signal_history(signal_uids=[uid], latest=int(limit)) or {}).get(uid)
    matrix = _get(item, "matrix") if item is not None else None
    if matrix is None or len(matrix.index) == 0:
        return LatestSignalObservations(signal_uid=uid, time_indexes=(), weights={})
    frame = matrix.sort_index()
    time_indexes = tuple(_utc(value.to_pydatetime()) for value in frame.index)
    weights: dict[str, tuple[float, ...]] = {}
    for column in frame.columns:
        values = []
        for value in frame[column].tolist():
            number = math.nan if value is None else float(value)
            values.append(0.0 if math.isnan(number) else number)
        weights[str(column)] = tuple(values)
    return LatestSignalObservations(signal_uid=uid, time_indexes=time_indexes, weights=weights)


@dataclass(frozen=True, slots=True)
class PortfolioValuesPage:
    """A portfolio's latest values, newest first, and how many it has in all."""

    rows: list[dict[str, Any]]
    total_count: int


def portfolio_values(portfolio_identifier: str, *, limit: int) -> PortfolioValuesPage:
    """A portfolio's latest ``limit`` values, newest first (recipe ``portfolio_history``).

    Assumed call: ``portfolio_history(portfolio_identifier=str, limit=int)``, by the
    portfolio's ``unique_identifier``. Assumed answer: ``rows``, newest first, each with
    ``time_index``, ``close``, ``return``, ``calculated_close`` and ``close_time``; and
    ``total_count``, every value the portfolio has. Answered here as rows with ``period_return``
    for ``return``. A portfolio with no value answers no row. Refusals propagate.
    """
    from msm_portfolios.recipes import portfolio_history

    answer = portfolio_history(portfolio_identifier=str(portfolio_identifier), limit=int(limit))
    rows = [
        {
            "time_index": _get(row, "time_index"),
            "close": _get(row, "close"),
            "period_return": _get(row, "return"),
            "calculated_close": _get(row, "calculated_close"),
            "close_time": _get(row, "close_time"),
        }
        for row in (_get(answer, "rows") or [])
    ]
    return PortfolioValuesPage(rows=rows, total_count=int(_get(answer, "total_count") or 0))


def portfolio_weights_as_of(
    portfolio_identifiers: Iterable[str], *, at: dt.datetime
) -> dict[str, dict[str, float]]:
    """Each portfolio's latest weights at or before ``at`` (recipe ``weights_as_of``).

    Assumed call: ``weights_as_of(portfolio_identifiers=[str, ...], at=datetime)``, once per
    ``MARKETS_BATCH_SIZE`` portfolios, by ``unique_identifier``. Assumed answer: a mapping of
    portfolio identifier to a mapping of asset identifier to weight; a portfolio with no weights
    by then is absent or empty, and is absent here. A null weight is left out. Refusals
    propagate.
    """
    from msm_portfolios.recipes import weights_as_of

    weights: dict[str, dict[str, float]] = {}
    for batch in batches(str(identifier) for identifier in portfolio_identifiers):
        answer = weights_as_of(portfolio_identifiers=batch, at=at) or {}
        for identifier, legs in answer.items():
            values = {
                str(asset): float(weight)
                for asset, weight in (legs or {}).items()
                if weight is not None
            }
            if values:
                weights[str(identifier)] = values
    return weights


@dataclass(frozen=True, slots=True)
class PortfolioRunAnswer:
    """What a portfolio run wrote, as Markets answered it."""

    portfolio_uid: str
    portfolio_unique_identifier: str
    # Rows written per stage: prices, calendar_events, rebalance, weights, values.
    written: dict[str, int]
    first_rebalance: bool | None


def run_portfolio(
    *,
    portfolio_configuration: Any,
    portfolio_description: str,
    unique_identifier: str,
    calendar_uid: Any,
    signal_uid: str,
    start_at: dt.datetime,
    calendar_events: Any,
    rebalance_output_table: Any,
) -> PortfolioRunAnswer:
    """Run one portfolio's chain in order and publish it (recipe ``run_portfolio``).

    Assumed call: ``run_portfolio(portfolio_configuration=PortfolioConfiguration,
    portfolio_description=str, unique_identifier=str, calendar_uid=..., signal_uid=str,
    start_at=datetime, calendar_events=PortfolioCalendarEvents | None,
    rebalance_output_table=<private rebalance-state table>)``.

    This application supplies what is its own: the ms-markets ``PortfolioConfiguration`` it
    built (its interpolated prices as the valuation source, the rebalance strategy, the
    ``ApplicationWeights`` signal, valuation and fees), the calendar-events node bound to its
    private table (``None`` for ``immediate_signal``), its private rebalance-state table, the
    ``ALPACA_PORTFOLIO__<hex>`` identifier and the calendar. Markets runs, in order, prices,
    calendar events, rebalance, weights and values, scoped to ``signal_uid`` and starting at
    ``start_at`` (the signal's first observation); creates or updates the ``Portfolio`` row of
    ``unique_identifier`` on ``calendar_uid``; publishes the weights and values to its own
    series; and continues a stopped run from what it holds. Nothing is registered or reconciled.

    Assumed answer: ``portfolio`` (the row, with ``uid`` and ``unique_identifier``), ``written``
    (rows per stage: ``prices``, ``calendar_events``, ``rebalance``, ``weights``, ``values``) and
    ``first_rebalance`` (whether a first rebalance has happened). A stage that fails raises, and
    the error propagates.
    """
    from msm_portfolios.recipes import run_portfolio as recipe

    answer = recipe(
        portfolio_configuration=portfolio_configuration,
        portfolio_description=portfolio_description,
        unique_identifier=unique_identifier,
        calendar_uid=calendar_uid,
        signal_uid=str(signal_uid),
        start_at=start_at,
        calendar_events=calendar_events,
        rebalance_output_table=rebalance_output_table,
    )
    portfolio = _get(answer, "portfolio")
    first_rebalance = _get(answer, "first_rebalance")
    written = _get(answer, "written") or {}
    return PortfolioRunAnswer(
        portfolio_uid=str(_get(portfolio, "uid")),
        portfolio_unique_identifier=str(_get(portfolio, "unique_identifier") or unique_identifier),
        written={str(stage): int(rows or 0) for stage, rows in written.items()},
        first_rebalance=None if first_rebalance is None else bool(first_rebalance),
    )


def end_signals(signal_uids: Iterable[str]) -> dict[str, Any]:
    """Delete signals' weights and metadata in Markets (recipe ``end_signal``).

    Their ``SignalMetadata`` rows are read in one ``filter`` per batch (issue #46), so a signal
    Markets never registered (nothing was ever published to it) costs no call. Assumed call, once
    per registered signal: ``end_signal(signal_uid=str)``, which deletes the signal's weights and
    its ``SignalMetadata`` in one Markets call. Markets refuses with ``MarketsConflictError``
    while a ``Portfolio`` follows the signal; the refusal propagates. Answers each registered
    signal's result.
    """
    from msm_portfolios.api.market_metadata import SignalMetadata
    from msm_portfolios.recipes import end_signal

    values = list(dict.fromkeys(str(uid) for uid in signal_uids))
    rows = read_in_batches(
        lambda batch: SignalMetadata.filter(signal_uid__in=batch, limit=len(batch)), values
    )
    return {str(row.signal_uid): end_signal(signal_uid=str(row.signal_uid)) for row in rows}


def delete_private_series(*, tables: Sequence[Any], portfolio_identifiers: Iterable[str]) -> None:
    """Delete this application's private rows of portfolios (recipe ``delete_private_series``).

    Assumed call: ``delete_private_series(tables=[<private table>, ...],
    portfolio_identifiers=[str, ...])``, once per ``MARKETS_BATCH_SIZE`` portfolios. ms-markets
    deletes the rows of each given table whose ``portfolio_identifier`` is one of them, in the
    order their keys demand, with this process's own access: the tables are this application's,
    and Markets never reads them. It runs before the ``Portfolio`` rows are deleted, which those
    rows' ``RESTRICT`` keys would refuse. Refusals propagate.
    """
    from msm_portfolios.recipes import delete_private_series as recipe

    for batch in batches(str(identifier) for identifier in portfolio_identifiers):
        recipe(tables=list(tables), portfolio_identifiers=batch)


__all__ = [
    "MARKETS_BATCH_SIZE",
    "LatestSignalObservations",
    "PortfolioRunAnswer",
    "PortfolioValuesPage",
    "SignalObservationBounds",
    "SignalWeightsRefused",
    "account_delete_preflight",
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
    "plan_account_virtual_funds",
    "portfolio_delete_blockers",
    "portfolio_values",
    "portfolio_weights_as_of",
    "portfolios_delete_blockers",
    "publish_signal_weights",
    "read_all_pages",
    "read_in_batches",
    "register_account",
    "retire_portfolio",
    "run_portfolio",
    "signal_bounds",
    "write_in_batches",
]
