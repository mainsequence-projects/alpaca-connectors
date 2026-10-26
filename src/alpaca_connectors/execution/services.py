"""Alpaca order execution for authorised applications (ADR 0016).

Submit, sync and cancel market-order batches on a registered account with that account's stored
credentials, exactly as the holdings capture does, and keep the ms-markets execution ledger: one
``OrderManager`` row per batch, keyed by the caller's ``batch_id``; one ``OrdersTS`` row per order;
one ``TradesTS`` row per filled order. ``OrderEventsTS`` is not written, because its asset-index
contract is defective in ms-markets 2.2.12. After fills, a holdings snapshot is published through
the existing capture.

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
from dataclasses import dataclass, field
from typing import Any, Protocol

from alpaca_connectors.execution.orders import (
    SUPPORTED_ORDER_TYPES,
    SUPPORTED_TIME_IN_FORCE,
    OrderBatchSubmission,
    OrderRecord,
    PreTradeCheck,
    PreTradeCheckError,
    batch_status,
    is_whole_quantity,
    new_fills,
    order_record_from_alpaca,
    orders_ledger_rows,
    parse_alpaca_asset_identifier,
    rejected_record,
    trades_ledger_rows,
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
        status: str,
        metadata: dict[str, Any],
    ) -> Any: ...

    def update_batch(self, batch_uid: Any, **values: Any) -> None: ...

    def registered_asset_identifiers(self, identifiers: Sequence[str]) -> set[str]: ...

    def ensure_cash_asset(self) -> str: ...

    def write_orders(self, rows: list[dict[str, Any]]) -> None: ...

    def write_trades(self, rows: list[dict[str, Any]]) -> None: ...


def _run_execution_node(node_class: Any, rows: list[dict[str, Any]], label: str) -> None:
    if not rows:
        return
    import pandas as pd

    node = node_class(config=node_class.default_config())
    node.set_frame(pd.DataFrame(rows))
    error, _ = node.run()
    if error:
        raise RuntimeError(f"Writing the execution {label} failed.")


class MarketsExecutionLedger:
    """The real ledger: ``OrderManager`` rows and the ``OrdersTS`` / ``TradesTS`` DataNodes."""

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
        status: str,
        metadata: dict[str, Any],
    ) -> Any:
        from msm.api.execution import OrderManager

        return OrderManager.create_batch(
            unique_identifier=batch_id,
            target_account_uid=str(account_uid),
            target_time=target_time,
            order_received_time=target_time,
            status=status,
            metadata_json=metadata,
        )

    def update_batch(self, batch_uid: Any, **values: Any) -> None:
        from msm.api.execution import OrderManager

        OrderManager.update(batch_uid, **values)

    def registered_asset_identifiers(self, identifiers: Sequence[str]) -> set[str]:
        from alpaca_connectors.assets.resolution import assets_by_unique_identifiers

        return set(assets_by_unique_identifiers(list(identifiers)))

    def ensure_cash_asset(self) -> str:
        from alpaca_connectors.account.services import ensure_cash_currency_asset

        return ensure_cash_currency_asset(DEFAULT_CASH_ASSET_IDENTIFIER)

    def write_orders(self, rows: list[dict[str, Any]]) -> None:
        from msm.time_index.execution import Orders

        _run_execution_node(Orders, rows, "orders")

    def write_trades(self, rows: list[dict[str, Any]]) -> None:
        from msm.time_index.execution import Trades

        _run_execution_node(Trades, rows, "trades")


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
    account_uid: str, registration: dict[str, Any], *, client: Any | None = None
) -> ExecutionDependencies:
    """Clients from the account's stored credentials, the markets ledger and the holdings capture."""
    from alpaca_connectors.account.credentials import (
        build_alpaca_trading_client,
        resolve_registered_alpaca_credentials,
    )
    from alpaca_connectors.holdings.services import capture_alpaca_account_holdings

    credentials = resolve_registered_alpaca_credentials(registration)
    trading_client = client or build_alpaca_trading_client(
        credentials=credentials, paper=bool(registration["is_paper"])
    )
    return ExecutionDependencies(
        client=trading_client,
        ledger=MarketsExecutionLedger(),
        price_reader=alpaca_latest_trade_prices(credentials),
        capture_holdings=lambda: capture_alpaca_account_holdings(
            account_uid, client=trading_client
        ),
    )


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

    def as_payload(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "account_uid": self.account_uid,
            "status": self.status,
            "accepted": self.accepted,
            "message": self.message,
            "warnings": list(self.warnings),
            "holdings_set_uid": self.holdings_set_uid,
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


def _account_checks(registration: dict[str, Any], account: Any) -> list[PreTradeCheck]:
    checks: list[PreTradeCheck] = []
    if not bool(registration.get("account_is_active", True)):
        checks.append(PreTradeCheck("account_inactive", "The account registration is not active."))
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


def _resolve_assets(
    deps: ExecutionDependencies, submission: OrderBatchSubmission
) -> tuple[list[OrderRecord], list[PreTradeCheck], list[str]]:
    """Resolve each leg's canonical identifier to the live Alpaca asset and check it is tradable.

    The registered set is one markets query. Alpaca offers no batched asset lookup by id, so the
    live state is one request per distinct asset of the batch; the stored ``AlpacaAssetDetails``
    are registration-time facts and not a pre-trade gate.
    """
    checks: list[PreTradeCheck] = []
    warnings: list[str] = []
    records: list[OrderRecord] = []
    asset_ids: dict[str, uuid.UUID | None] = {
        leg.asset_identifier: parse_alpaca_asset_identifier(leg.asset_identifier)
        for leg in submission.legs
    }
    registered = deps.ledger.registered_asset_identifiers(
        [identifier for identifier, asset_id in asset_ids.items() if asset_id is not None]
    )
    live_assets: dict[str, Any] = {
        identifier: _live_alpaca_asset(deps.client, asset_id)
        for identifier, asset_id in asset_ids.items()
        if asset_id is not None
    }
    for leg in submission.legs:
        record = OrderRecord.from_leg(leg)
        asset_id = asset_ids[leg.asset_identifier]
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
    deps: ExecutionDependencies, registration: dict[str, Any], submission: OrderBatchSubmission
) -> tuple[list[OrderRecord], list[PreTradeCheck], list[str]]:
    checks = _validate_legs(submission)
    account = _read_account(deps, registration)
    checks.extend(_account_checks(registration, account))
    records, asset_checks, warnings = _resolve_assets(deps, submission)
    checks.extend(asset_checks)
    checks.extend(_market_hours_check(deps, submission))
    if not checks:
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
    """Re-read every order Alpaca holds; orders refused at submission keep their state."""
    refreshed: list[OrderRecord] = []
    for record in records:
        if record.terminal and not record.broker_order_id:
            refreshed.append(record)
            continue
        order = _find_alpaca_order(deps.client, record.client_order_id)
        refreshed.append(order_record_from_alpaca(record, order) if order is not None else record)
    return refreshed


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


def _require_batch(ledger: ExecutionLedger, account_uid: str, batch_id: str) -> Any:
    batch = ledger.get_batch(batch_id)
    if batch is None or not _same_uid(batch.target_account_uid, account_uid):
        raise LookupError(f"Order batch {batch_id!s} does not exist for this account.")
    return batch


def _batch_metadata(
    registration: dict[str, Any],
    records: list[OrderRecord],
    *,
    warnings: list[str],
    now: dt.datetime,
    holdings_set_uid: str | None,
    previous: dict[str, Any] | None,
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
    return _json_safe(metadata)


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
) -> OrderBatchResult:
    """Write the batch, order and trade rows, publish holdings after fills, return the result."""
    account_uid = str(registration["account_uid"])
    account_identifier = str(registration["unique_identifier"])
    status = batch_status(records)
    terminal = bool(records) and all(record.terminal for record in records)
    previous_metadata = dict(getattr(batch, "metadata_json", None) or {}) if batch else None
    if batch is None:
        batch = deps.ledger.create_batch(
            batch_id=batch_id,
            account_uid=account_uid,
            target_time=now,
            status="submitted",
            metadata=_batch_metadata(
                registration,
                records,
                warnings=warnings,
                now=now,
                holdings_set_uid=None,
                previous=None,
            ),
        )
    deps.ledger.write_orders(
        orders_ledger_rows(records, account_identifier=account_identifier, batch_id=batch_id)
    )
    trade_rows = trades_ledger_rows(
        records,
        account_identifier=account_identifier,
        batch_id=batch_id,
        cash_asset_identifier=DEFAULT_CASH_ASSET_IDENTIFIER,
    )
    if trade_rows:
        deps.ledger.ensure_cash_asset()
        deps.ledger.write_trades(trade_rows)
    holdings_set_uid = (previous_metadata or {}).get("holdings_set_uid")
    if new_fills(previous, records):
        try:
            capture = deps.capture_holdings()
            holdings_set_uid = str(getattr(capture, "holdings_set_uid", None) or "") or None
        except Exception:
            logger.exception("Holdings snapshot after fills failed for account %s", account_uid)
            warnings = [
                *warnings,
                "The holdings snapshot after the fills could not be published; capture holdings.",
            ]
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
    registration = load_account_registration(account_uid)
    deps = deps or build_dependencies(account_uid, registration)
    existing = deps.ledger.get_batch(submission.batch_id)
    blockers: list[PreTradeCheck] = []
    if existing is not None and not _same_uid(existing.target_account_uid, account_uid):
        blockers.append(
            PreTradeCheck("batch_id_in_use", "The batch_id already belongs to another account.")
        )
    records, checks, warnings = _check_batch(deps, registration, submission)
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
    """Place a batch of market orders on the account after the pre-trade checks pass.

    Idempotent on ``client_order_id``: an order Alpaca already holds under that identifier is
    read back instead of placed again, so a retried submission never doubles a trade. Sells are
    placed before buys. An order Alpaca refuses is returned as ``rejected`` with Alpaca's reason;
    the rest of the batch proceeds.
    """
    registration = load_account_registration(account_uid)
    deps = deps or build_dependencies(account_uid, registration)
    batch = deps.ledger.get_batch(submission.batch_id)
    if batch is not None and not _same_uid(batch.target_account_uid, account_uid):
        raise PreTradeCheckError(
            [PreTradeCheck("batch_id_in_use", "The batch_id already belongs to another account.")]
        )
    records, checks, warnings = _check_batch(deps, registration, submission)
    if checks:
        raise PreTradeCheckError(checks)
    now = deps.now()
    previous = _records_from_batch(batch) if batch is not None else []
    placed: dict[str, OrderRecord] = {}
    pending: list[OrderRecord] = []
    for record in records:
        existing_order = _find_alpaca_order(deps.client, record.client_order_id)
        if existing_order is not None:
            placed[record.client_order_id] = order_record_from_alpaca(record, existing_order)
        else:
            pending.append(record)
    sells = [record for record in pending if record.side == "sell"]
    buys = [record for record in pending if record.side == "buy"]
    for record in sells:
        placed[record.client_order_id] = _place_order(deps, record, now)
    if sells and buys:
        for record in _wait_for_terminal(deps, [placed[r.client_order_id] for r in sells]):
            placed[record.client_order_id] = record
    for record in buys:
        placed[record.client_order_id] = _place_order(deps, record, now)
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
    )


def sync_order_batch(
    account_uid: str,
    batch_id: str,
    *,
    deps: ExecutionDependencies | None = None,
) -> OrderBatchResult:
    """Re-read the batch's orders from Alpaca, record fills and publish holdings after new ones."""
    registration = load_account_registration(account_uid)
    deps = deps or build_dependencies(account_uid, registration)
    batch = _require_batch(deps.ledger, account_uid, batch_id)
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
    Alpaca) right after this call; the next sync settles it.
    """
    from alpaca.common.exceptions import APIError

    registration = load_account_registration(account_uid)
    deps = deps or build_dependencies(account_uid, registration)
    batch = _require_batch(deps.ledger, account_uid, batch_id)
    previous = _records_from_batch(batch)
    current = _refresh(deps, previous)
    warnings: list[str] = []
    requested = 0
    for record in current:
        if record.terminal or not record.broker_order_id:
            continue
        try:
            deps.client.cancel_order_by_id(record.broker_order_id)
            requested += 1
        except APIError as exc:
            _reject_credentials_if_unauthorized(exc)
            warnings.append(
                f"{record.client_order_id}: "
                + _alpaca_error_message(exc, "Alpaca did not accept the cancellation.")
            )
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
    """The orders recorded for the account, newest batch first, as last synced; no Alpaca call."""
    load_account_registration(account_uid)
    ledger = ledger or MarketsExecutionLedger()
    if batch_id is not None:
        batch = ledger.get_batch(batch_id)
        batches = (
            [batch]
            if batch is not None and _same_uid(batch.target_account_uid, account_uid)
            else []
        )
    else:
        batches = sorted(
            ledger.list_batches(account_uid),
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
