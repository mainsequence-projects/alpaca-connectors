"""Alpaca order execution for authorised applications (ADR 0016).

Submit, sync and cancel market-order batches on a registered account with that account's stored
credentials, exactly as the holdings capture does, and keep the ms-markets execution ledger
(ms-markets ADR 0064): one ``OrderManager`` row per batch, keyed by the caller's ``batch_id``, with
one Markets ``Order`` row per order, both written by ``OrderManager.create_batch`` in one call; and,
in this application's instances, one ``OrdersTS`` state row per order and one ``TradesTS`` row per
filled order, written by ``write_execution`` in one transaction. No order event is recorded: the
state row carries the order's status. After fills, a holdings snapshot is published through the
existing capture.

The preflight and submit do not repeat each other (ADR 0016, decision 3). The preflight runs every
pre-trade check and places nothing. Submit runs only the checks that protect what it places and
records, which Alpaca cannot enforce (``_check_batch`` without ``venue_rules``), and leaves the
account's status, the assets' tradability and fractions, the market hours and the buying power to
Alpaca.

Every provider and runtime collaborator is injectable through :class:`ExecutionDependencies`, so
the whole flow runs in tests against a fake Alpaca client and an in-memory ledger. The Alpaca
Agent has no tool over this module (ADR 0016): it is an HTTP capability for applications.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, Protocol

from alpaca_connectors.execution.orders import (
    SUPPORTED_ORDER_TYPES,
    SUPPORTED_TIME_IN_FORCE,
    OrderBatchSubmission,
    OrderRecord,
    PreTradeCheck,
    PreTradeCheckError,
    batch_status,
    execution_rows,
    is_whole_quantity,
    new_fills,
    order_record_from_alpaca,
    order_rows,
    parse_alpaca_asset_identifier,
    rejected_record,
)
from alpaca_connectors.holdings.account import DEFAULT_CASH_ASSET_IDENTIFIER

logger = logging.getLogger(__name__)

LEDGER_SOURCE = "alpaca-connectors"
PREFLIGHT_CONTRACT = "command-center.bulk_action_preflight@v1"
# Alpaca's own buying-power rule is the authority; this tolerance only absorbs rounding in the
# estimate built from the latest trade prices.
BUYING_POWER_TOLERANCE = 0.01
ALPACA_MESSAGE_LIMIT = 240


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _enum_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value.value if hasattr(value, "value") else value)


def _float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _same_uid(left: Any, right: Any) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return str(left) == str(right)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


# --------------------------------------------------------------------------------------------------
# collaborators
# --------------------------------------------------------------------------------------------------


class ExecutionLedger(Protocol):
    """The ms-markets rows the execution keeps; an in-memory double stands in for tests."""

    def get_batch(self, batch_id: str) -> Any | None: ...

    def list_batches(self, account_uid: str, *, limit: int = 500) -> list[Any]: ...

    def create_batch(
        self,
        *,
        batch_id: str,
        account_uid: str,
        target_time: dt.datetime,
        received_time: dt.datetime,
        status: str,
        metadata: dict[str, Any],
        orders: list[dict[str, Any]],
    ) -> Any: ...

    def update_batch(self, batch_uid: Any, **values: Any) -> None: ...

    def registered_asset_identifiers(self, identifiers: Sequence[str]) -> set[str]: ...

    def ensure_cash_asset(self) -> str: ...

    def write_execution(self, entries: list[dict[str, Any]]) -> None: ...


def _refused(label: str, failed: Sequence[Any]) -> None:
    """Fail when ms-markets lists rows it did not write (it writes the others, issue #128)."""
    if failed:
        for refusal in failed:
            logger.error("Execution %s row not written: %s", label, refusal)
        raise RuntimeError(f"Writing the execution {label} rows failed for {len(failed)} rows.")


class MarketsExecutionLedger:
    """The real ledger: Markets ``OrderManager`` and ``Order`` rows, and this application's order
    states and trades.

    A batch and its orders are written through the Markets API in one call
    (``OrderManager.create_batch``); the orders' state rows and trades are written by
    ``write_execution`` into the ``alpaca_connectors.instances`` tables, in one MetaTables
    transaction with no update execution (ms-markets ADR 0064).
    """

    def get_batch(self, batch_id: str) -> Any | None:
        from msm.api.execution import OrderManager

        return OrderManager.get_by_unique_identifier(batch_id)

    def list_batches(self, account_uid: str, *, limit: int = 500) -> list[Any]:
        from msm.api.execution import OrderManager

        return OrderManager.filter(target_account_uid=uuid.UUID(str(account_uid)), limit=limit)

    def create_batch(
        self,
        *,
        batch_id: str,
        account_uid: str,
        target_time: dt.datetime,
        received_time: dt.datetime,
        status: str,
        metadata: dict[str, Any],
        orders: list[dict[str, Any]],
    ) -> Any:
        from msm.api.execution import OrderManager

        written = OrderManager.create_batch(
            unique_identifier=batch_id,
            target_account_uid=str(account_uid),
            target_time=target_time,
            order_received_time=received_time,
            status=status,
            metadata_json=metadata,
            orders=orders,
        )
        _refused("order", written.failed)
        return written.order_manager

    def update_batch(self, batch_uid: Any, **values: Any) -> None:
        from msm.api.execution import OrderManager

        OrderManager.update(batch_uid, **values)

    def registered_asset_identifiers(self, identifiers: Sequence[str]) -> set[str]:
        from alpaca_connectors.assets.resolution import assets_by_unique_identifiers

        return set(assets_by_unique_identifiers(list(identifiers)))

    def ensure_cash_asset(self) -> str:
        from alpaca_connectors.account.services import ensure_cash_currency_asset

        return ensure_cash_currency_asset(DEFAULT_CASH_ASSET_IDENTIFIER)

    def write_execution(self, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        from msm.time_index.execution import write_execution

        from alpaca_connectors.instances import (
            AlpacaOrderEventsStorage,
            AlpacaOrdersStorage,
            AlpacaTradesStorage,
        )

        written = write_execution(
            entries,
            orders_table=AlpacaOrdersStorage,
            order_events_table=AlpacaOrderEventsStorage,
            trades_table=AlpacaTradesStorage,
        )
        _refused("state and trade", written.failed)


PriceReader = Callable[[Sequence[str]], Mapping[str, float]]


def alpaca_latest_trade_prices(credentials: Any) -> PriceReader:
    """Latest trade prices from Alpaca's data API, for the buying-power estimate."""

    def read(symbols: Sequence[str]) -> Mapping[str, float]:
        from alpaca.data.requests import StockLatestTradeRequest

        from alpaca_connectors.account.credentials import build_alpaca_historical_data_client

        wanted = list(dict.fromkeys(symbol for symbol in symbols if symbol))
        if not wanted:
            return {}
        client = build_alpaca_historical_data_client(credentials=credentials)
        trades = client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=wanted))
        prices: dict[str, float] = {}
        for symbol, trade in dict(trades).items():
            price = _float(getattr(trade, "price", None))
            if price is not None:
                prices[str(symbol)] = price
        return prices

    return read


@dataclass(slots=True)
class ExecutionDependencies:
    """Everything the execution touches outside its own logic."""

    client: Any
    ledger: ExecutionLedger
    price_reader: PriceReader
    capture_holdings: Callable[[], Any]
    # Sizes the account's target and allocates the snapshot a capture just published (ADR 0023);
    # the second argument is the target ``read_target`` answered, or ``UNREAD`` to read it there.
    allocate_holdings: Callable[[Any, Any], Any] | None = None
    # Reads the account's target in force; it runs alongside the capture after fills (issue #46).
    read_target: Callable[[], Any] | None = None
    now: Callable[[], dt.datetime] = _utc_now
    sleep: Callable[[float], None] = time.sleep
    # Sells are placed before buys; when a batch has both, the buys wait this long for the sells to
    # settle so a cash account's buying power reflects them. Alpaca decides the buys after that.
    settle_timeout_seconds: float = 10.0
    poll_interval_seconds: float = 0.5


def load_account_registration(account_uid: str) -> dict[str, Any]:
    """The registered account, or ``LookupError`` (404) when it is not an Alpaca registration."""
    from alpaca_connectors.account.services import get_account_registration
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    registration = get_account_registration(account_uid)
    if registration is None:
        raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
    return registration


def build_dependencies(
    account_uid: str,
    registration: dict[str, Any],
    *,
    client: Any | None = None,
    ledger: ExecutionLedger | None = None,
) -> ExecutionDependencies:
    """Clients from the account's stored credentials, the markets ledger and the holdings capture.

    The capture uses ``registration`` rather than reading it again.
    """
    from alpaca_connectors.account.credentials import (
        build_alpaca_trading_client,
        resolve_registered_alpaca_credentials,
    )
    from alpaca_connectors.holdings.allocation import size_and_allocate
    from alpaca_connectors.holdings.services import capture_alpaca_account_holdings
    from alpaca_connectors.markets_api import account_target_positions

    credentials = resolve_registered_alpaca_credentials(registration)
    trading_client = client or build_alpaca_trading_client(
        credentials=credentials, paper=bool(registration["is_paper"])
    )
    price_reader = alpaca_latest_trade_prices(credentials)
    return ExecutionDependencies(
        client=trading_client,
        ledger=ledger or MarketsExecutionLedger(),
        price_reader=price_reader,
        capture_holdings=lambda: capture_alpaca_account_holdings(
            account_uid, client=trading_client, registration=registration
        ),
        allocate_holdings=lambda capture, target: size_and_allocate(
            account_uid,
            valuation_time=capture.time_index,
            equity=getattr(capture, "equity", None),
            price_reader=price_reader,
            target=target,
        ),
        read_target=lambda: account_target_positions(account_uid),
    )


def _markets_ledger() -> MarketsExecutionLedger:
    """The real ledger, once the Markets engine its reads need is started: the reads that go with
    the registration's must not race its start."""
    from alpaca_connectors.runtime import account_runtime_models, start_markets_engine

    start_markets_engine(models=account_runtime_models())
    return MarketsExecutionLedger()


def _settled(call: Callable[[], Any]) -> Any:
    """``call``'s answer, or the exception it raised: a read made ahead, alongside others, whose
    failure is raised where the read used to be made."""
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - raised where the answer is used
        return exc


def _answer(value: Any) -> Any:
    """A ``_settled`` answer, raising the exception it holds."""
    if isinstance(value, Exception):
        raise value
    return value


def _start(
    account_uid: str,
    batch_id: str,
    deps: ExecutionDependencies | None,
    asset_identifiers: Sequence[str] | None = None,
) -> tuple[dict[str, Any], ExecutionDependencies, Any, Any]:
    """The registration, the order batch and, for a submission, its registered assets.

    None of the three needs another, so they are read together (issue #46); the registration's
    failure is raised first, then the batch's, as when they were read in turn. The registered
    assets answer as ``_settled``: the pre-trade checks raise a failed read where they use it.
    The dependencies are then opened with the registration's keys.
    """
    from alpaca_connectors.markets_api import all_together

    ledger = deps.ledger if deps is not None else _markets_ledger()
    registration, batch, registered = all_together(
        lambda: load_account_registration(account_uid),
        lambda: ledger.get_batch(batch_id),
        lambda: (
            None
            if asset_identifiers is None
            else _settled(lambda: ledger.registered_asset_identifiers(list(asset_identifiers)))
        ),
    )
    deps = deps or build_dependencies(account_uid, registration, ledger=ledger)
    return registration, deps, batch, registered


# --------------------------------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class OrderBatchPreflight:
    batch_id: str
    account_uid: str
    orders: list[OrderRecord]
    blockers: list[PreTradeCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not self.blockers

    def as_payload(self) -> dict[str, Any]:
        return {
            "contract": PREFLIGHT_CONTRACT,
            "allowed": self.allowed,
            "detail": (
                "The batch passes every pre-trade check."
                if self.allowed
                else "One or more pre-trade checks refuse the batch."
            ),
            "matched_count": len(self.orders),
            "blockers": [check.message for check in self.blockers],
            "warnings": list(self.warnings),
            "checks": [check.as_payload() for check in self.blockers],
            "batch_id": self.batch_id,
            "account_uid": self.account_uid,
        }


@dataclass(slots=True)
class OrderBatchResult:
    batch_id: str
    account_uid: str
    status: str
    orders: list[OrderRecord]
    accepted: bool = True
    message: str | None = None
    warnings: list[str] = field(default_factory=list)
    holdings_set_uid: str | None = None
    # The time of that snapshot: an allocation in Markets is addressed by it (ADR 0023).
    holdings_time: dt.datetime | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "account_uid": self.account_uid,
            "status": self.status,
            "accepted": self.accepted,
            "message": self.message,
            "warnings": list(self.warnings),
            "holdings_set_uid": self.holdings_set_uid,
            "holdings_time": self.holdings_time,
            "orders": [record.to_payload() for record in self.orders],
        }


# --------------------------------------------------------------------------------------------------
# pre-trade checks
# --------------------------------------------------------------------------------------------------


def _alpaca_error_message(exc: Exception, default: str) -> str:
    try:
        message = str(exc.message)  # type: ignore[attr-defined]
    except Exception:
        return default
    message = " ".join(message.split())
    return message[:ALPACA_MESSAGE_LIMIT] or default


def _alpaca_status_code(exc: Exception) -> int | None:
    try:
        return exc.status_code  # type: ignore[attr-defined]
    except Exception:
        return None


def _reject_credentials_if_unauthorized(exc: Exception) -> None:
    from alpaca_connectors.account.credentials import AlpacaCredentialsRejectedError

    if _alpaca_status_code(exc) == 401:
        raise AlpacaCredentialsRejectedError() from None


def _validate_legs(submission: OrderBatchSubmission) -> list[PreTradeCheck]:
    checks: list[PreTradeCheck] = []
    seen: set[str] = set()
    for leg in submission.legs:
        if leg.client_order_id in seen:
            checks.append(
                PreTradeCheck(
                    "duplicate_client_order_id",
                    "Every order in a batch needs its own client_order_id.",
                    leg.client_order_id,
                )
            )
        seen.add(leg.client_order_id)
        if leg.order_type not in SUPPORTED_ORDER_TYPES:
            checks.append(
                PreTradeCheck(
                    "unsupported_order_type",
                    "Only market orders are supported.",
                    leg.client_order_id,
                )
            )
        if leg.time_in_force not in SUPPORTED_TIME_IN_FORCE:
            checks.append(
                PreTradeCheck(
                    "unsupported_time_in_force",
                    "Only day orders are supported.",
                    leg.client_order_id,
                )
            )
        if not leg.quantity or leg.quantity <= 0:
            checks.append(
                PreTradeCheck(
                    "invalid_quantity",
                    "The order quantity must be greater than zero.",
                    leg.client_order_id,
                )
            )
    return checks


def _registration_checks(registration: dict[str, Any]) -> list[PreTradeCheck]:
    """This connector's own rule on the account, which Alpaca cannot enforce."""
    if not bool(registration.get("account_is_active", True)):
        return [PreTradeCheck("account_inactive", "The account registration is not active.")]
    return []


def _account_checks(account: Any) -> list[PreTradeCheck]:
    """What Alpaca refuses an order for on the account's own state."""
    checks: list[PreTradeCheck] = []
    status = (_enum_str(getattr(account, "status", None)) or "").upper()
    if status != "ACTIVE":
        checks.append(
            PreTradeCheck(
                "account_not_tradable", f"The Alpaca account status is {status or 'unknown'}."
            )
        )
    for flag, reason in (
        ("trading_blocked", "trading is blocked on the Alpaca account"),
        ("account_blocked", "the Alpaca account is blocked"),
        ("trade_suspended_by_user", "the account holder suspended trading"),
    ):
        if bool(getattr(account, flag, False)):
            checks.append(PreTradeCheck("account_not_tradable", f"Orders are refused: {reason}."))
    return checks


def _live_alpaca_asset(client: Any, asset_id: uuid.UUID) -> Any | None:
    """One Alpaca asset by immutable id, or ``None`` when Alpaca does not know it."""
    from alpaca.common.exceptions import APIError

    try:
        return client.get_asset(str(asset_id))
    except APIError as exc:
        _reject_credentials_if_unauthorized(exc)
        return None


def _readable_identifiers(submission: OrderBatchSubmission) -> dict[str, uuid.UUID]:
    """The batch's Alpaca asset identifiers and their asset ids, in leg order."""
    asset_ids = {
        leg.asset_identifier: parse_alpaca_asset_identifier(leg.asset_identifier)
        for leg in submission.legs
    }
    return {identifier: asset_id for identifier, asset_id in asset_ids.items() if asset_id}


def _resolve_assets(
    deps: ExecutionDependencies,
    submission: OrderBatchSubmission,
    registered: Any = None,
    *,
    venue_rules: bool = True,
) -> tuple[list[OrderRecord], list[PreTradeCheck], list[str]]:
    """Resolve each leg's canonical identifier to the live Alpaca asset, which names the symbol
    the order is placed under, and check the asset is registered here; with ``venue_rules``,
    also that Alpaca would trade it (tradable, fractionable for a fraction).

    The registered set is one markets query, ``registered`` when the caller read it already (as a
    ``_settled`` answer). Alpaca offers no batched asset lookup by id, so the live state is one
    request per distinct asset of the batch, all together (issue #46); the stored
    ``AlpacaAssetDetails`` are registration-time facts and not a pre-trade gate.
    """
    from alpaca_connectors.markets_api import each_together

    checks: list[PreTradeCheck] = []
    warnings: list[str] = []
    records: list[OrderRecord] = []
    readable = _readable_identifiers(submission)
    if registered is None:
        registered = deps.ledger.registered_asset_identifiers(list(readable))
    registered = _answer(registered)
    live_assets: dict[str, Any] = dict(
        zip(
            readable,
            each_together(partial(_live_alpaca_asset, deps.client), readable.values()),
            strict=True,
        )
    )
    for leg in submission.legs:
        record = OrderRecord.from_leg(leg)
        asset_id = readable.get(leg.asset_identifier)
        if asset_id is None:
            checks.append(
                PreTradeCheck(
                    "unknown_asset",
                    "The asset identifier is not an Alpaca asset (ALPACA::<uuid>).",
                    leg.client_order_id,
                )
            )
            records.append(record)
            continue
        if leg.asset_identifier not in registered:
            checks.append(
                PreTradeCheck(
                    "asset_not_registered",
                    "The asset is not registered in this connector; register it first.",
                    leg.client_order_id,
                )
            )
        asset = live_assets.get(leg.asset_identifier)
        if asset is None:
            checks.append(
                PreTradeCheck(
                    "unknown_asset", "Alpaca does not know this asset.", leg.client_order_id
                )
            )
            records.append(record)
            continue
        venue_symbol = str(getattr(asset, "symbol", "") or "").upper() or leg.symbol
        if venue_symbol != leg.symbol.upper():
            warnings.append(
                f"{leg.client_order_id}: Alpaca lists the asset as {venue_symbol}; the order is "
                "placed under that symbol."
            )
        record.venue_symbol = venue_symbol
        record.alpaca_asset_id = str(asset_id)
        if not venue_rules:
            records.append(record)
            continue
        tradable = bool(getattr(asset, "tradable", False))
        asset_status = (_enum_str(getattr(asset, "status", None)) or "active").lower()
        if not tradable or asset_status != "active":
            checks.append(
                PreTradeCheck(
                    "asset_not_tradable",
                    f"{venue_symbol} is not tradable at Alpaca.",
                    leg.client_order_id,
                )
            )
        if not is_whole_quantity(leg.quantity) and not bool(getattr(asset, "fractionable", False)):
            checks.append(
                PreTradeCheck(
                    "fractional_quantity_not_allowed",
                    f"{venue_symbol} trades in whole shares only.",
                    leg.client_order_id,
                )
            )
        records.append(record)
    return records, checks, warnings


def _market_hours_check(
    deps: ExecutionDependencies, submission: OrderBatchSubmission
) -> list[PreTradeCheck]:
    clock = deps.client.get_clock()
    if bool(getattr(clock, "is_open", False)) or submission.queue_when_closed:
        return []
    next_open = getattr(clock, "next_open", None)
    opens = f" It next opens at {next_open.isoformat()}." if next_open is not None else ""
    return [
        PreTradeCheck(
            "market_closed",
            "The market is closed." + opens + " Set queue_when_closed to queue the orders for "
            "the next session.",
        )
    ]


def _buying_power_check(
    deps: ExecutionDependencies, account: Any, records: list[OrderRecord]
) -> tuple[list[PreTradeCheck], list[str]]:
    """Estimate the net cash the buys need against Alpaca's buying power."""
    warnings: list[str] = []
    buys = [record for record in records if record.side == "buy"]
    if not buys:
        return [], warnings
    symbols = [record.venue_symbol or record.symbol for record in records]
    try:
        prices = dict(deps.price_reader(symbols))
    except Exception:
        logger.warning("Latest trade prices are unavailable; buying power is not estimated.")
        prices = {}
    unpriced = [
        record.client_order_id
        for record in buys
        if (record.venue_symbol or record.symbol) not in prices
    ]
    if unpriced:
        warnings.append(
            "Buying power was not estimated for "
            + ", ".join(unpriced)
            + ": no latest trade price. Alpaca decides at submission."
        )
        return [], warnings
    required = sum(
        record.quantity * prices[record.venue_symbol or record.symbol] for record in buys
    ) - sum(
        record.quantity * prices.get(record.venue_symbol or record.symbol, 0.0)
        for record in records
        if record.side == "sell"
    )
    available = _float(getattr(account, "buying_power", None))
    if available is None:
        available = _float(getattr(account, "cash", None))
    if available is None:
        warnings.append("Alpaca reported no buying power; Alpaca decides at submission.")
        return [], warnings
    if required > available + BUYING_POWER_TOLERANCE:
        return [
            PreTradeCheck(
                "insufficient_buying_power",
                f"The buys need about {required:,.2f} net of the sells; the account's buying "
                f"power is {available:,.2f}.",
            )
        ], warnings
    return [], warnings


def _read_account(deps: ExecutionDependencies, registration: dict[str, Any]) -> Any:
    from alpaca_connectors.account.credentials import read_alpaca_account_or_reject

    account = read_alpaca_account_or_reject(deps.client)
    if str(getattr(account, "id", "")) != str(registration["alpaca_account_id"]):
        raise ValueError(
            "The configured Secrets resolve to a different Alpaca account than the registered row."
        )
    return account


def _check_batch(
    deps: ExecutionDependencies,
    registration: dict[str, Any],
    submission: OrderBatchSubmission,
    registered: Any = None,
    *,
    venue_rules: bool = True,
) -> tuple[list[OrderRecord], list[PreTradeCheck], list[str]]:
    """The checks of a batch.

    Without ``venue_rules`` they are submit's: the request, the Alpaca account the keys open, the
    registration's own state, and each asset's identity at Alpaca and registration here. They
    protect what submit places and records, and Alpaca cannot enforce them. ``venue_rules`` adds
    what Alpaca refuses an order for on its own, for the preflight to report before anything is
    placed: the account's status, each asset's tradability and fractions, the market hours and
    the buying power. Submit leaves those to Alpaca, which answers each order it refuses.

    The Alpaca account, the assets and the clock are read together (issue #46); a failed read
    raises in that order. The buying power waits for the account and the assets' venue symbols.
    """
    from alpaca_connectors.markets_api import all_together

    checks = _validate_legs(submission)
    reads = [
        lambda: _read_account(deps, registration),
        lambda: _resolve_assets(deps, submission, registered, venue_rules=venue_rules),
    ]
    if venue_rules:
        reads.append(lambda: _market_hours_check(deps, submission))
    account, (records, asset_checks, warnings), *hours_checks = all_together(*reads)
    checks.extend(_registration_checks(registration))
    if venue_rules:
        checks.extend(_account_checks(account))
    checks.extend(asset_checks)
    for found in hours_checks:
        checks.extend(found)
    if venue_rules and not checks:
        power_checks, power_warnings = _buying_power_check(deps, account, records)
        checks.extend(power_checks)
        warnings.extend(power_warnings)
    return records, checks, warnings


# --------------------------------------------------------------------------------------------------
# Alpaca orders
# --------------------------------------------------------------------------------------------------


def _find_alpaca_order(client: Any, client_order_id: str) -> Any | None:
    from alpaca.common.exceptions import APIError

    try:
        return client.get_order_by_client_id(client_order_id)
    except APIError as exc:
        _reject_credentials_if_unauthorized(exc)
        if _alpaca_status_code(exc) in (404, 422):
            return None
        raise


def _place_order(deps: ExecutionDependencies, record: OrderRecord, now: dt.datetime) -> OrderRecord:
    from alpaca.common.exceptions import APIError
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    # alpaca-py sends ``qty`` as a float; Alpaca reads a whole number as whole shares, and the
    # pre-trade checks have refused fractions on non-fractionable assets.
    request = MarketOrderRequest(
        symbol=record.venue_symbol or record.symbol,
        qty=float(record.quantity),
        side=OrderSide(record.side),
        time_in_force=TimeInForce.DAY,
        client_order_id=record.client_order_id,
    )
    try:
        order = deps.client.submit_order(request)
    except APIError as exc:
        _reject_credentials_if_unauthorized(exc)
        if _alpaca_status_code(exc) == 422:
            existing = _find_alpaca_order(deps.client, record.client_order_id)
            if existing is not None:
                return order_record_from_alpaca(record, existing)
        return rejected_record(
            record, message=_alpaca_error_message(exc, "Alpaca refused the order."), when=now
        )
    return order_record_from_alpaca(record, order)


def _refresh(deps: ExecutionDependencies, records: list[OrderRecord]) -> list[OrderRecord]:
    """Re-read every order Alpaca holds, together (issue #46); orders refused at submission keep
    their state."""
    from alpaca_connectors.markets_api import each_together

    def refreshed(record: OrderRecord) -> OrderRecord:
        if record.terminal and not record.broker_order_id:
            return record
        order = _find_alpaca_order(deps.client, record.client_order_id)
        return order_record_from_alpaca(record, order) if order is not None else record

    return each_together(refreshed, records)


def _wait_for_terminal(
    deps: ExecutionDependencies, records: list[OrderRecord]
) -> list[OrderRecord]:
    waited = 0.0
    current = records
    while True:
        current = _refresh(deps, current)
        if all(record.terminal for record in current) or waited >= deps.settle_timeout_seconds:
            return current
        deps.sleep(deps.poll_interval_seconds)
        waited += deps.poll_interval_seconds


# --------------------------------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------------------------------


def _records_from_batch(batch: Any) -> list[OrderRecord]:
    metadata = getattr(batch, "metadata_json", None) or {}
    return [OrderRecord.from_payload(payload) for payload in metadata.get("orders") or []]


def _require_batch(batch: Any | None, account_uid: str, batch_id: str) -> Any:
    """The batch read for ``batch_id``, or ``LookupError`` when it is not this account's."""
    if batch is None or not _same_uid(batch.target_account_uid, account_uid):
        raise LookupError(f"Order batch {batch_id!s} does not exist for this account.")
    return batch


def _stored_time(value: Any) -> dt.datetime | None:
    """A snapshot time kept in the batch metadata, read back as a datetime."""
    if isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _batch_metadata(
    registration: dict[str, Any],
    records: list[OrderRecord],
    *,
    warnings: list[str],
    now: dt.datetime,
    holdings_set_uid: str | None,
    previous: dict[str, Any] | None,
    holdings_time: dt.datetime | None = None,
) -> dict[str, Any]:
    metadata = dict(previous or {})
    metadata.update(
        {
            "source": LEDGER_SOURCE,
            "venue": "alpaca",
            "account_uid": str(registration["account_uid"]),
            "account_identifier": str(registration["unique_identifier"]),
            "is_paper": bool(registration["is_paper"]),
            "orders": [record.to_payload() for record in records],
            "warnings": list(warnings),
            "last_sync_at": now.isoformat(),
        }
    )
    if holdings_set_uid is not None:
        metadata["holdings_set_uid"] = holdings_set_uid
    if holdings_time is not None:
        metadata["holdings_time"] = holdings_time.isoformat()
    return _json_safe(metadata)


def _publish_after_fills(
    deps: ExecutionDependencies, account_uid: str
) -> tuple[Any | None, list[str]]:
    """Publish a holdings snapshot after fills and allocate it like a sync's (ADR 0023).

    The capture and the target read run together (issue #46). A failure of either step is a
    warning, never an error. Answers the capture (None when it failed, which keeps the batch's
    earlier snapshot) and the warnings.
    """
    from alpaca_connectors.holdings.allocation import UNREAD
    from alpaca_connectors.markets_api import all_together

    read_target = deps.read_target
    capture, target = all_together(
        lambda: _settled(deps.capture_holdings),
        lambda: _settled(read_target) if read_target is not None else UNREAD,
    )
    if isinstance(capture, Exception):
        logger.error(
            "Holdings snapshot after fills failed for account %s",
            account_uid,
            exc_info=capture,
        )
        warning = "The holdings snapshot after the fills could not be published; capture holdings."
        return None, [warning]
    warnings: list[str] = []
    if getattr(capture, "holdings_set_uid", None) and deps.allocate_holdings is not None:
        try:
            deps.allocate_holdings(capture, _answer(target))
        except Exception:
            logger.exception("Fund allocation after fills failed for account %s", account_uid)
            warnings.append(
                "The holdings snapshot after the fills was published but could not be "
                "allocated to the account's funds; sync the account."
            )
    return capture, warnings


def _persist(
    deps: ExecutionDependencies,
    registration: dict[str, Any],
    *,
    batch_id: str,
    batch: Any | None,
    previous: list[OrderRecord],
    records: list[OrderRecord],
    now: dt.datetime,
    warnings: list[str],
    message: str | None = None,
    create_orders: bool = False,
) -> OrderBatchResult:
    """Write the batch, its orders, their states and trades, publish holdings after fills, and
    return the result.

    Two independent pieces run together (issue #46): the ledger, and, after new fills, the
    holdings snapshot with its allocation. The ledger is two Markets requests (ms-markets ADR
    0064): ``create_batch`` writes the batch with its ``Order`` rows, on submit (``create_orders``)
    or for a new batch, alongside the cash asset the fills settle in; then ``write_execution``
    writes the orders' state rows and fills, which key to those rows, in one transaction. The
    batch row's final state is written once both pieces have ended. A failure of the capture or
    of the allocation is a warning on the answer, never an error.
    """
    from alpaca_connectors.markets_api import all_together

    account_uid = str(registration["account_uid"])
    account_identifier = str(registration["unique_identifier"])
    status = batch_status(records)
    terminal = bool(records) and all(record.terminal for record in records)
    previous_metadata = dict(getattr(batch, "metadata_json", None) or {}) if batch else None
    entries = execution_rows(
        records,
        account_identifier=account_identifier,
        batch_id=batch_id,
        cash_asset_identifier=DEFAULT_CASH_ASSET_IDENTIFIER,
    )

    def create_batch() -> Any:
        if batch is not None and not create_orders:
            return batch
        # A batch sent again keeps its own times and status; its existing orders stay as they are.
        target_time = getattr(batch, "target_time", None) or now
        return deps.ledger.create_batch(
            batch_id=batch_id,
            account_uid=account_uid,
            target_time=target_time,
            received_time=getattr(batch, "order_received_time", None) or target_time,
            status=getattr(batch, "status", None) or "submitted",
            metadata=_batch_metadata(
                registration,
                records,
                warnings=warnings,
                now=now,
                holdings_set_uid=None,
                previous=previous_metadata,
            ),
            orders=order_rows(records, account_identifier=account_identifier),
        )

    def write_ledger() -> Any:
        fills = any(entry["trades"] for entry in entries)
        written, *_ = all_together(
            create_batch, *([deps.ledger.ensure_cash_asset] if fills else [])
        )
        deps.ledger.write_execution(entries)
        return written

    fills = new_fills(previous, records)
    batch, published = all_together(
        write_ledger,
        (lambda: _publish_after_fills(deps, account_uid)) if fills else (lambda: None),
    )
    holdings_set_uid = (previous_metadata or {}).get("holdings_set_uid")
    holdings_time = _stored_time((previous_metadata or {}).get("holdings_time"))
    if published is not None:
        capture, fill_warnings = published
        warnings = [*warnings, *fill_warnings]
        if capture is not None:
            holdings_set_uid = str(getattr(capture, "holdings_set_uid", None) or "") or None
            holdings_time = getattr(capture, "time_index", None) if holdings_set_uid else None
    deps.ledger.update_batch(
        batch.uid,
        status=status,
        execution_end=now if terminal else None,
        metadata_json=_batch_metadata(
            registration,
            records,
            warnings=warnings,
            now=now,
            holdings_set_uid=holdings_set_uid,
            previous=previous_metadata,
            holdings_time=holdings_time,
        ),
    )
    return OrderBatchResult(
        batch_id=batch_id,
        account_uid=account_uid,
        status=status,
        orders=records,
        message=message,
        warnings=list(warnings),
        holdings_set_uid=holdings_set_uid,
        holdings_time=holdings_time,
    )


# --------------------------------------------------------------------------------------------------
# public services
# --------------------------------------------------------------------------------------------------


def preflight_order_batch(
    account_uid: str,
    submission: OrderBatchSubmission,
    *,
    deps: ExecutionDependencies | None = None,
) -> OrderBatchPreflight:
    """Run every pre-trade check without placing an order or writing a row."""
    registration, deps, existing, registered = _start(
        account_uid, submission.batch_id, deps, list(_readable_identifiers(submission))
    )
    blockers: list[PreTradeCheck] = []
    if existing is not None and not _same_uid(existing.target_account_uid, account_uid):
        blockers.append(
            PreTradeCheck("batch_id_in_use", "The batch_id already belongs to another account.")
        )
    records, checks, warnings = _check_batch(deps, registration, submission, registered)
    return OrderBatchPreflight(
        batch_id=submission.batch_id,
        account_uid=str(registration["account_uid"]),
        orders=records,
        blockers=[*blockers, *checks],
        warnings=warnings,
    )


def submit_order_batch(
    account_uid: str,
    submission: OrderBatchSubmission,
    *,
    deps: ExecutionDependencies | None = None,
) -> OrderBatchResult:
    """Place a batch of market orders on the account.

    Submit runs only the checks that protect what it places and records (``_check_batch`` without
    ``venue_rules``); what Alpaca refuses on its own is the preflight's to report, and an order
    Alpaca refuses at submission comes back ``rejected`` with Alpaca's reason. Alpaca decides the
    market hours: a day order placed while the market is closed is queued for the next session.

    Idempotent on ``client_order_id``: an order Alpaca already holds under that identifier is
    read back instead of placed again, so a retried submission never doubles a trade. Sells are
    placed before buys. An order Alpaca refuses is returned as ``rejected`` with Alpaca's reason;
    the rest of the batch proceeds. Alpaca takes one order per request, so the orders of one
    step (the lookups, the sells, the buys) go together (issue #46).
    """
    from alpaca_connectors.markets_api import each_together

    registration, deps, batch, registered = _start(
        account_uid, submission.batch_id, deps, list(_readable_identifiers(submission))
    )
    if batch is not None and not _same_uid(batch.target_account_uid, account_uid):
        raise PreTradeCheckError(
            [PreTradeCheck("batch_id_in_use", "The batch_id already belongs to another account.")]
        )
    records, checks, warnings = _check_batch(
        deps, registration, submission, registered, venue_rules=False
    )
    if checks:
        raise PreTradeCheckError(checks)
    now = deps.now()
    previous = _records_from_batch(batch) if batch is not None else []
    # Each order's Markets uid: the one its batch already holds for it on a retry, else a new one.
    known = {record.client_order_id: record.order_uid for record in previous}
    records = [
        replace(record, order_uid=known.get(record.client_order_id) or str(uuid.uuid4()))
        for record in records
    ]
    placed: dict[str, OrderRecord] = {}
    pending: list[OrderRecord] = []
    held = each_together(
        lambda record: _find_alpaca_order(deps.client, record.client_order_id), records
    )
    for record, existing_order in zip(records, held, strict=True):
        if existing_order is not None:
            placed[record.client_order_id] = order_record_from_alpaca(record, existing_order)
        else:
            pending.append(record)
    sells = [record for record in pending if record.side == "sell"]
    buys = [record for record in pending if record.side == "buy"]
    for record in each_together(lambda record: _place_order(deps, record, now), sells):
        placed[record.client_order_id] = record
    if sells and buys:
        for record in _wait_for_terminal(deps, [placed[r.client_order_id] for r in sells]):
            placed[record.client_order_id] = record
    for record in each_together(lambda record: _place_order(deps, record, now), buys):
        placed[record.client_order_id] = record
    final = _refresh(deps, [placed[record.client_order_id] for record in records])
    refused = [record for record in final if record.terminal and not record.broker_order_id]
    message = None
    if refused:
        message = f"{len(refused)} of {len(final)} orders were refused by Alpaca at submission."
    return _persist(
        deps,
        registration,
        batch_id=submission.batch_id,
        batch=batch,
        previous=previous,
        records=final,
        now=now,
        warnings=warnings,
        message=message,
        create_orders=True,
    )


def sync_order_batch(
    account_uid: str,
    batch_id: str,
    *,
    deps: ExecutionDependencies | None = None,
) -> OrderBatchResult:
    """Re-read the batch's orders from Alpaca, record fills and publish holdings after new ones."""
    registration, deps, batch, _ = _start(account_uid, batch_id, deps)
    batch = _require_batch(batch, account_uid, batch_id)
    previous = _records_from_batch(batch)
    records = _refresh(deps, previous)
    return _persist(
        deps,
        registration,
        batch_id=batch_id,
        batch=batch,
        previous=previous,
        records=records,
        now=deps.now(),
        warnings=[],
    )


def cancel_order_batch(
    account_uid: str,
    batch_id: str,
    *,
    deps: ExecutionDependencies | None = None,
) -> OrderBatchResult:
    """Ask Alpaca to cancel every open order of the batch, then record the resulting states.

    Alpaca cancels asynchronously: an order can still read ``submitted`` (``pending_cancel`` at
    Alpaca) right after this call; the next sync settles it. Alpaca cancels one order per
    request, so the batch's cancellations go together (issue #46).
    """
    from alpaca.common.exceptions import APIError

    from alpaca_connectors.markets_api import each_together

    registration, deps, batch, _ = _start(account_uid, batch_id, deps)
    batch = _require_batch(batch, account_uid, batch_id)
    previous = _records_from_batch(batch)
    current = _refresh(deps, previous)

    def cancel(record: OrderRecord) -> str | None:
        """None once Alpaca took the cancellation, else the warning saying why not."""
        try:
            deps.client.cancel_order_by_id(record.broker_order_id)
        except APIError as exc:
            _reject_credentials_if_unauthorized(exc)
            return f"{record.client_order_id}: " + _alpaca_error_message(
                exc, "Alpaca did not accept the cancellation."
            )
        return None

    open_orders = [r for r in current if not r.terminal and r.broker_order_id]
    refusals = each_together(cancel, open_orders)
    warnings = [refusal for refusal in refusals if refusal is not None]
    requested = len(refusals) - len(warnings)
    records = _refresh(deps, current) if requested else current
    return _persist(
        deps,
        registration,
        batch_id=batch_id,
        batch=batch,
        previous=previous,
        records=records,
        now=deps.now(),
        warnings=warnings,
        message=f"Cancellation requested for {requested} orders." if requested else None,
    )


def list_account_orders(
    account_uid: str,
    *,
    batch_id: str | None = None,
    limit: int = 25,
    offset: int = 0,
    ledger: ExecutionLedger | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """The orders recorded for the account, newest batch first, as last synced; no Alpaca call.

    The registration and the batches are read together (issue #46); an unknown account is
    refused first, as when the registration was read alone.
    """
    from alpaca_connectors.markets_api import all_together

    ledger = ledger or _markets_ledger()
    _, found = all_together(
        lambda: load_account_registration(account_uid),
        (lambda: [ledger.get_batch(batch_id)])
        if batch_id is not None
        else (lambda: ledger.list_batches(account_uid)),
    )
    if batch_id is not None:
        batches = [
            batch
            for batch in found
            if batch is not None and _same_uid(batch.target_account_uid, account_uid)
        ]
    else:
        batches = sorted(
            found,
            key=lambda row: (str(row.target_time), str(row.unique_identifier)),
            reverse=True,
        )
    items: list[dict[str, Any]] = []
    for batch in batches:
        for record in _records_from_batch(batch):
            items.append(
                {
                    **record.to_payload(),
                    "batch_id": str(batch.unique_identifier),
                    "batch_status": batch.status,
                }
            )
    return items[offset : offset + limit], len(items)


__all__ = [
    "ExecutionDependencies",
    "ExecutionLedger",
    "MarketsExecutionLedger",
    "OrderBatchPreflight",
    "OrderBatchResult",
    "alpaca_latest_trade_prices",
    "build_dependencies",
    "cancel_order_batch",
    "list_account_orders",
    "load_account_registration",
    "preflight_order_batch",
    "submit_order_batch",
    "sync_order_batch",
]
