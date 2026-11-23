"""Size an account's target and allocate one holdings snapshot to its funds (ADR 0023).

ms-markets states the account target as exposure and reads no price (ms-markets ADR 0054). After
a holdings snapshot is published at time ``T``, the connector reads the account's target, sizes
every exposure into units with this sync's prices, and asks Markets to assign the snapshot to
the account's funds. Markets records each owner's target, what it was assigned and the delta.
The connector writes no target and stores no line.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from alpaca_connectors.holdings.sizing import (
    AssetTerms,
    Exposure,
    FundAllocationError,
    size_targets,
)

logger = logging.getLogger(__name__)

PriceReader = Callable[[Sequence[str]], Mapping[str, float]]

# The Markets refusals of the target read, the plan and the allocation, in the connector's own
# words. Markets' detail can name stored rows, so it is kept only as the cause.
MARKETS_REFUSALS: dict[str, str] = {
    "not_found": (
        "Markets has no target or no holdings snapshot at the sync's time for this account."
    ),
    "target_after_snapshot": (
        "The account target was written after this holdings snapshot; sync again."
    ),
    "invalid_sized_targets": "Markets refused the sized targets of this sync.",
    "plan_changed": "The target or the holdings changed while the sync ran; sync again.",
    "allocation_exists": "This holdings snapshot already has a different allocation.",
    "policy_version_mismatch": "The allocation policy changed while the sync ran; sync again.",
    "portfolio_retired": "The account target names a retired portfolio.",
}
RETRYABLE_REFUSALS = frozenset({"target_after_snapshot", "plan_changed", "policy_version_mismatch"})
_REFUSAL_CODE = re.compile(r"""['"]code['"]\s*:\s*['"]([a-z_]+)['"]""")


@dataclass(frozen=True, slots=True)
class SizedAccountTarget:
    """The account's target in force and its exposures in units, for one snapshot time."""

    position_set_uid: str
    valuation_time: dt.datetime
    sized_targets: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class FundAllocationResult:
    """What a sync's allocation left in Markets, as Markets answered it."""

    position_set_uid: str | None = None
    allocation_applied: bool = False
    lines: list[dict[str, Any]] = field(default_factory=list)


def _get(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, Mapping) else getattr(row, name, None)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def markets_refusal(exc: Exception) -> Exception:
    """The public error of a Markets refusal, or ``exc`` itself when it is not one."""
    if isinstance(exc, FundAllocationError):
        return exc
    match = _REFUSAL_CODE.search(str(exc))
    code = match.group(1) if match else None
    if code not in MARKETS_REFUSALS:
        if not isinstance(exc, LookupError):
            return exc
        code = "not_found"
    error = FundAllocationError(code, MARKETS_REFUSALS[code])
    error.__cause__ = exc
    return error


def account_exposures(positions: Sequence[Any]) -> list[Exposure]:
    """The rows of an account target that have to be sized.

    A portfolio row states a share of NAV (``weight_notional_exposure``); an account target states
    no amount since ms-markets 5.3.0 (its ADR 0061). An asset row stated as a share is sized too;
    one stated as ``single_asset_quantity`` is already in units and is left out.
    """
    exposures: list[Exposure] = []
    for row in positions:
        target_type = str(_get(row, "target_type"))
        weight = _number(_get(row, "weight_notional_exposure"))
        if weight is None:
            if target_type == "asset" and _get(row, "single_asset_quantity") not in (None, ""):
                continue
            raise FundAllocationError(
                "invalid_account_target",
                "A row of the account target states no share of NAV.",
            )
        reference = _get(row, "portfolio" if target_type == "portfolio" else "asset")
        identifier = str(_get(reference, "unique_identifier") or "").strip()
        if not identifier:
            raise FundAllocationError(
                "invalid_account_target",
                "A row of the account target names a portfolio or an asset Markets cannot read.",
            )
        exposures.append(
            Exposure(
                target_type="portfolio" if target_type == "portfolio" else "asset",
                target_uid=str(_get(row, "target_uid")),
                identifier=identifier,
                weight=weight,
            )
        )
    return exposures


def read_portfolio_weights(
    portfolio_identifiers: Sequence[str], *, at: dt.datetime
) -> dict[str, dict[str, float]]:
    """Each portfolio's latest published weights at or before ``at``, by asset identifier.

    Markets' published series (ADR 0031), read with the ms-markets recipe ``weights_as_of`` for
    all the portfolios at once. A portfolio with no weights by then is absent from the answer.
    """
    from alpaca_connectors.markets_api import portfolio_weights_as_of

    identifiers = list(dict.fromkeys(identifier for identifier in portfolio_identifiers))
    if not identifiers:
        return {}
    return portfolio_weights_as_of(identifiers, at=at)


def read_alpaca_details(asset_uids: Sequence[str]) -> dict[str, tuple[str, bool]]:
    """Alpaca symbol and ``fractionable`` flag per asset uid, read in batches.

    Only the two columns the sizing needs are selected, so a detail row is never validated as a
    whole for them.
    """
    from msm.api.base import operation_result_rows
    from msm.repositories.base import compile_markets_statement, execute_markets_operation
    from sqlalchemy import select

    from alpaca_connectors.assets.alpaca_asset_details import AlpacaAssetDetailsTable
    from alpaca_connectors.markets_api import read_in_batches
    from alpaca_connectors.runtime import start_markets_engine

    uids = [str(uid) for uid in asset_uids]
    if not uids:
        return {}
    runtime = start_markets_engine()
    rows = AlpacaAssetDetailsTable.__table__.c

    def read(batch: list[str]) -> list[dict[str, Any]]:
        statement = select(
            rows.asset_uid.label("asset_uid"),
            rows.symbol.label("symbol"),
            rows.fractionable.label("fractionable"),
        ).where(rows.asset_uid.in_([uuid.UUID(uid) for uid in batch]))
        operation = compile_markets_statement(
            statement, context=runtime.context, operation="select"
        )
        return operation_result_rows(execute_markets_operation(operation, context=runtime.context))

    return {
        str(uuid.UUID(str(row["asset_uid"]))): (str(row["symbol"]), bool(row["fractionable"]))
        for row in read_in_batches(read, uids)
    }


def read_asset_terms(
    asset_identifiers: Sequence[str], *, price_reader: PriceReader
) -> dict[str, AssetTerms]:
    """Uid, latest trade and ``fractionable`` flag of each registered Alpaca asset.

    Two reads: this application's Alpaca details by canonical identifier (the identifier is
    ``ALPACA::<alpaca_asset_id>``, so no Markets read is needed), and one latest-trade request for
    their symbols. An asset the details table does not have is absent from the answer.
    """
    from alpaca_connectors.assets.resolution import alpaca_details_by_identifier

    details = alpaca_details_by_identifier(asset_identifiers)
    prices = price_reader(sorted({str(row["symbol"]) for row in details.values()}))
    return {
        identifier: AssetTerms(
            asset_uid=row["asset_uid"],
            price=_number(prices.get(str(row["symbol"]))),
            fractionable=bool(row["fractionable"]),
        )
        for identifier, row in details.items()
    }


def size_account_target(
    account_uid: Any,
    *,
    valuation_time: dt.datetime,
    equity: float | None,
    price_reader: PriceReader,
) -> SizedAccountTarget | None:
    """Read the account's target in force and size every exposure of it into units.

    Returns None when the account has no target, so there is nothing to allocate. The latest
    target is the one in force at a snapshot just captured; Markets refuses one written after it.
    """
    from alpaca_connectors.markets_api import account_target_positions

    try:
        target = account_target_positions(account_uid)
    except Exception as exc:
        raise markets_refusal(exc) from exc
    position_set_uid = _get(target, "position_set_uid") if target is not None else None
    positions = list(_get(target, "positions") or []) if target is not None else []
    if not position_set_uid or not positions:
        return None
    exposures = account_exposures(positions)
    portfolio_identifiers = [e.identifier for e in exposures if e.target_type == "portfolio"]
    weights = read_portfolio_weights(portfolio_identifiers, at=valuation_time)
    missing = [identifier for identifier in portfolio_identifiers if not weights.get(identifier)]
    if missing:
        from alpaca_connectors.portfolios.rebalance_schedule import missing_weights_message

        # Issue #41: say when the portfolio's first rebalance is, not only that it has no weights.
        message = missing_weights_message(missing[0], at=valuation_time)
        if message is not None:
            raise FundAllocationError("portfolio_weights_missing", message)
    asset_identifiers = sorted(
        {e.identifier for e in exposures if e.target_type == "asset"}
        | {identifier for legs in weights.values() for identifier in legs}
    )
    assets = (
        read_asset_terms(asset_identifiers, price_reader=price_reader) if asset_identifiers else {}
    )
    return SizedAccountTarget(
        position_set_uid=str(position_set_uid),
        valuation_time=valuation_time,
        sized_targets=size_targets(exposures, equity=equity, weights=weights, assets=assets),
    )


def _line(line: Any) -> dict[str, Any]:
    return {
        "owner": _get(line, "owner"),
        "target_portfolio_uid": _get(line, "target_portfolio_uid"),
        "asset_uid": _get(line, "asset_uid"),
        "asset_identifier": _get(line, "asset_identifier"),
        "target": _get(line, "target"),
        "assigned": _get(line, "assigned"),
        "delta": _get(line, "delta"),
    }


def allocate_sized_target(account_uid: Any, sized: SizedAccountTarget) -> FundAllocationResult:
    """Plan the snapshot against the sized target, then allocate with the plan's digest.

    A shortfall is not refused: the allocation is written and its deltas recorded. The same sync
    again writes nothing (``already_applied``). A plan Markets would not apply, such as one with
    a retired portfolio, fails here.
    """
    from alpaca_connectors.markets_api import (
        allocate_account_virtual_funds,
        plan_account_virtual_funds,
    )

    request = {
        "position_set_uid": sized.position_set_uid,
        "valuation_time": sized.valuation_time,
        "sized_targets": sized.sized_targets,
    }
    try:
        plan = plan_account_virtual_funds(account_uid, **request)
        blockers = list(_get(plan, "apply_blockers") or [])
        if blockers:
            code = str(_get(blockers[0], "code") or "portfolio_retired")
            raise FundAllocationError(
                code if code in MARKETS_REFUSALS else "portfolio_retired",
                MARKETS_REFUSALS.get(code, MARKETS_REFUSALS["portfolio_retired"]),
            )
        allocation = allocate_account_virtual_funds(
            account_uid,
            **request,
            policy=_get(plan, "policy"),
            plan_digest=str(_get(plan, "plan_digest")),
        )
    except Exception as exc:
        raise markets_refusal(exc) from exc
    return FundAllocationResult(
        position_set_uid=sized.position_set_uid,
        allocation_applied=bool(_get(allocation, "applied")),
        lines=[_line(line) for line in _get(allocation, "lines") or []],
    )


def account_price_reader(credentials: Any) -> PriceReader:
    """Latest trades read with keys the caller already opened."""
    from alpaca_connectors.execution.services import alpaca_latest_trade_prices

    return alpaca_latest_trade_prices(credentials)


def registered_account_price_reader(account_uid: Any) -> PriceReader:
    """Latest trades read with the account's stored keys, resolved on the first read."""

    def read(symbols: Sequence[str]) -> Mapping[str, float]:
        from alpaca_connectors.account.credentials import resolve_registered_alpaca_credentials
        from alpaca_connectors.account.services import get_account_registration
        from alpaca_connectors.execution.services import alpaca_latest_trade_prices

        registration = get_account_registration(account_uid)
        if registration is None:
            raise LookupError("The Alpaca account registration does not exist.")
        credentials = resolve_registered_alpaca_credentials(registration)
        return alpaca_latest_trade_prices(credentials)(symbols)

    return read


def size_and_allocate(
    account_uid: Any,
    *,
    valuation_time: dt.datetime,
    equity: float | None,
    price_reader: PriceReader | None = None,
) -> FundAllocationResult:
    """Both steps for one published snapshot; an account with no target allocates nothing."""
    sized = size_account_target(
        account_uid,
        valuation_time=valuation_time,
        equity=equity,
        price_reader=price_reader or registered_account_price_reader(account_uid),
    )
    if sized is None:
        return FundAllocationResult()
    return allocate_sized_target(account_uid, sized)


__all__ = [
    "MARKETS_REFUSALS",
    "RETRYABLE_REFUSALS",
    "FundAllocationResult",
    "SizedAccountTarget",
    "account_exposures",
    "account_price_reader",
    "allocate_sized_target",
    "markets_refusal",
    "read_alpaca_details",
    "read_asset_terms",
    "read_portfolio_weights",
    "registered_account_price_reader",
    "size_account_target",
    "size_and_allocate",
]
