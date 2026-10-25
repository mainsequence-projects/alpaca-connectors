"""Pure order-execution logic: the batch contract, Alpaca order translation and ledger rows.

Nothing here reaches Alpaca, the markets runtime or the network; ``services.py`` does. The
vocabulary is the one the Hunch backend consumes: order statuses ``submitted``,
``partially_filled``, ``filled``, ``cancelled``, ``rejected``, ``expired`` and ``failed``.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

Side = Literal["buy", "sell"]

ORDER_SIDE_SIGN: dict[str, int] = {"buy": 1, "sell": -1}
ORDER_STATUSES: tuple[str, ...] = (
    "submitted",
    "partially_filled",
    "filled",
    "cancelled",
    "rejected",
    "expired",
    "failed",
)
TERMINAL_ORDER_STATUSES: frozenset[str] = frozenset(
    {"filled", "cancelled", "rejected", "expired", "failed"}
)
SUPPORTED_ORDER_TYPES: frozenset[str] = frozenset({"market"})
SUPPORTED_TIME_IN_FORCE: frozenset[str] = frozenset({"day"})
QUANTITY_TOLERANCE = 1e-9

# Alpaca order statuses mapped to the contract vocabulary. ``done_for_day`` depends on the fill and
# is handled in :func:`map_alpaca_order_status`.
_ALPACA_STATUS_MAP: dict[str, str] = {
    "new": "submitted",
    "accepted": "submitted",
    "pending_new": "submitted",
    "accepted_for_bidding": "submitted",
    "pending_review": "submitted",
    "held": "submitted",
    "calculated": "submitted",
    "stopped": "submitted",
    "pending_cancel": "submitted",
    "pending_replace": "submitted",
    "partially_filled": "partially_filled",
    "filled": "filled",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "replaced": "cancelled",
    "expired": "expired",
    "rejected": "rejected",
    "suspended": "failed",
}


def _enum_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value.value if hasattr(value, "value") else value)


def _float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _utc(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def is_whole_quantity(quantity: float) -> bool:
    return math.isclose(quantity, round(quantity), abs_tol=QUANTITY_TOLERANCE)


def map_alpaca_order_status(status: Any, *, filled_quantity: float, quantity: float) -> str:
    """Translate an Alpaca order status into the contract vocabulary."""
    normalized = (_enum_str(status) or "").strip().lower()
    if normalized == "done_for_day":
        return "filled" if filled_quantity + QUANTITY_TOLERANCE >= quantity else "expired"
    return _ALPACA_STATUS_MAP.get(normalized, "submitted")


@dataclass(frozen=True, slots=True)
class PreTradeCheck:
    """One failed pre-trade check, reported to the caller as structured data."""

    code: str
    message: str
    client_order_id: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class PreTradeCheckError(ValueError):
    """The batch was refused before any order reached Alpaca.

    The HTTP layer maps it to ``409`` with ``code`` (the single failed check's code, or
    ``pre_trade_checks_failed`` when several failed) and the list of ``checks``.
    """

    def __init__(self, checks: list[PreTradeCheck]) -> None:
        if not checks:
            raise ValueError("PreTradeCheckError needs at least one failed check.")
        self.checks = list(checks)
        super().__init__(" ".join(check.message for check in self.checks))

    @property
    def code(self) -> str:
        return self.checks[0].code if len(self.checks) == 1 else "pre_trade_checks_failed"


@dataclass(frozen=True, slots=True)
class OrderLegSpec:
    """One requested order: the wire contract of ``POST .../orders/actions/submit``."""

    client_order_id: str
    asset_identifier: str
    symbol: str
    side: Side
    quantity: float
    order_type: str = "market"
    time_in_force: str = "day"


@dataclass(frozen=True, slots=True)
class OrderBatchSubmission:
    batch_id: str
    legs: tuple[OrderLegSpec, ...]
    # A ``day`` market order placed while the market is closed is queued by Alpaca for the next
    # open. The default refuses such a batch (``market_closed``); a caller that wants the queue
    # says so explicitly.
    queue_when_closed: bool = False


@dataclass(slots=True)
class OrderRecord:
    """The connector's view of one order in a batch, as stored on the ``OrderManager`` row."""

    client_order_id: str
    asset_identifier: str
    symbol: str
    side: Side
    quantity: float
    status: str = "submitted"
    broker_order_id: str | None = None
    venue_status: str | None = None
    filled_quantity: float = 0.0
    average_fill_price: float | None = None
    submitted_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    # When the order reached its terminal state (filled, cancelled, expired...). Stable once set,
    # so the ``TradesTS`` row it keys is written once.
    terminal_at: dt.datetime | None = None
    message: str | None = None
    order_type: str = "market"
    time_in_force: str = "day"
    alpaca_asset_id: str | None = None
    venue_symbol: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_ORDER_STATUSES

    @property
    def side_sign(self) -> int:
        return ORDER_SIDE_SIGN[self.side]

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("submitted_at", "updated_at", "terminal_at"):
            payload[key] = _iso(payload[key])
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> OrderRecord:
        values = dict(payload)
        for key in ("submitted_at", "updated_at", "terminal_at"):
            values[key] = _utc(values.get(key))
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in values.items() if key in known})

    @classmethod
    def from_leg(cls, leg: OrderLegSpec) -> OrderRecord:
        return cls(
            client_order_id=leg.client_order_id,
            asset_identifier=leg.asset_identifier,
            symbol=leg.symbol,
            side=leg.side,
            quantity=float(leg.quantity),
            order_type=leg.order_type,
            time_in_force=leg.time_in_force,
        )


def _terminal_time(order: Any, status: str) -> dt.datetime | None:
    if status not in TERMINAL_ORDER_STATUSES:
        return None
    for name in ("filled_at", "canceled_at", "expired_at", "failed_at", "updated_at"):
        when = _utc(getattr(order, name, None))
        if when is not None:
            return when
    return None


def order_record_from_alpaca(record: OrderRecord, order: Any) -> OrderRecord:
    """Return ``record`` updated with the state of the Alpaca ``Order`` it was placed as."""
    quantity = _float(getattr(order, "qty", None), record.quantity) or record.quantity
    filled_quantity = _float(getattr(order, "filled_qty", None), 0.0) or 0.0
    status = map_alpaca_order_status(
        getattr(order, "status", None), filled_quantity=filled_quantity, quantity=quantity
    )
    average = _float(getattr(order, "filled_avg_price", None))
    return replace(
        record,
        status=status,
        broker_order_id=str(getattr(order, "id", "") or "") or record.broker_order_id,
        venue_status=_enum_str(getattr(order, "status", None)),
        filled_quantity=filled_quantity,
        average_fill_price=average if filled_quantity > 0 else None,
        submitted_at=_utc(getattr(order, "submitted_at", None)) or record.submitted_at,
        updated_at=_utc(getattr(order, "updated_at", None)) or record.updated_at,
        terminal_at=record.terminal_at or _terminal_time(order, status),
        venue_symbol=str(getattr(order, "symbol", "") or "") or record.venue_symbol,
        alpaca_asset_id=str(getattr(order, "asset_id", "") or "") or record.alpaca_asset_id,
    )


def rejected_record(
    record: OrderRecord, *, message: str, when: dt.datetime, status: str = "rejected"
) -> OrderRecord:
    """Return ``record`` as refused by Alpaca at submission (no broker order exists)."""
    return replace(
        record,
        status=status,
        message=message,
        submitted_at=record.submitted_at or when,
        updated_at=when,
        terminal_at=when,
    )


def batch_status(records: list[OrderRecord]) -> str:
    """The batch outcome in the order vocabulary: ``submitted`` until every order is terminal."""
    if not records:
        return "failed"
    if not all(record.terminal for record in records):
        return "partially_filled" if any(r.filled_quantity > 0 for r in records) else "submitted"
    statuses = {record.status for record in records}
    if statuses == {"filled"}:
        return "filled"
    if any(record.filled_quantity > 0 for record in records):
        return "partially_filled"
    if len(statuses) == 1:
        return statuses.pop()
    return "failed"


def new_fills(before: list[OrderRecord], after: list[OrderRecord]) -> bool:
    """Whether ``after`` carries a fill quantity that ``before`` did not."""
    previous = {record.client_order_id: record.filled_quantity for record in before}
    return any(
        record.filled_quantity > previous.get(record.client_order_id, 0.0) + QUANTITY_TOLERANCE
        for record in after
    )


def parse_alpaca_asset_identifier(asset_identifier: str) -> uuid.UUID | None:
    """The Alpaca asset UUID behind a canonical ``ALPACA::<uuid>`` identifier, or ``None``."""
    from alpaca_connectors.assets.alpaca_asset_details import parse_alpaca_unique_identifier

    try:
        return parse_alpaca_unique_identifier(asset_identifier)
    except (TypeError, ValueError):
        return None


def orders_ledger_rows(
    records: list[OrderRecord], *, account_identifier: str, batch_id: str
) -> list[dict[str, Any]]:
    """One ``OrdersTS`` row per order, keyed by its submission time so re-syncs update in place."""
    rows: list[dict[str, Any]] = []
    for record in records:
        order_time = record.submitted_at or record.updated_at
        if order_time is None:
            continue
        rows.append(
            {
                "order_time": order_time,
                "order_identifier": record.client_order_id,
                "account_identifier": account_identifier,
                "order_manager_identifier": batch_id,
                "asset_identifier": record.asset_identifier,
                "order_remote_id": record.broker_order_id or "",
                "client_order_id": record.client_order_id,
                "order_type": record.order_type,
                "order_side": record.side_sign,
                "quantity": float(record.quantity),
                "status": record.status,
                "filled_quantity": float(record.filled_quantity),
                "filled_price": float(record.average_fill_price or 0.0),
                "expires_time": order_time,
                "limit_price": 0.0,
                "time_in_force": record.time_in_force,
                "comments": record.message or "",
                "venue_metadata": {
                    "venue": "alpaca",
                    "symbol": record.venue_symbol or record.symbol,
                    "venue_status": record.venue_status,
                    "alpaca_asset_id": record.alpaca_asset_id,
                },
            }
        )
    return rows


def trades_ledger_rows(
    records: list[OrderRecord],
    *,
    account_identifier: str,
    batch_id: str,
    cash_asset_identifier: str,
) -> list[dict[str, Any]]:
    """One ``TradesTS`` row per terminal order with a fill: Alpaca reports cumulative fills only."""
    rows: list[dict[str, Any]] = []
    for record in records:
        if not record.terminal or record.filled_quantity <= 0 or record.terminal_at is None:
            continue
        price = float(record.average_fill_price or 0.0)
        rows.append(
            {
                "trade_time": record.terminal_at,
                "trade_identifier": f"{record.client_order_id}:fill",
                "account_identifier": account_identifier,
                "order_identifier": record.client_order_id,
                "asset_identifier": record.asset_identifier,
                "trade_side": record.side_sign,
                "quantity": float(record.filled_quantity),
                "price": price,
                "commission": 0.0,
                "commission_asset_identifier": cash_asset_identifier,
                "settlement_cost": float(record.filled_quantity) * price,
                "settlement_asset_identifier": cash_asset_identifier,
                "comments": batch_id,
                "venue_metadata": {
                    "venue": "alpaca",
                    "symbol": record.venue_symbol or record.symbol,
                    "broker_order_id": record.broker_order_id,
                },
            }
        )
    return rows


__all__ = [
    "ORDER_SIDE_SIGN",
    "ORDER_STATUSES",
    "SUPPORTED_ORDER_TYPES",
    "SUPPORTED_TIME_IN_FORCE",
    "TERMINAL_ORDER_STATUSES",
    "OrderBatchSubmission",
    "OrderLegSpec",
    "OrderRecord",
    "PreTradeCheck",
    "PreTradeCheckError",
    "batch_status",
    "is_whole_quantity",
    "map_alpaca_order_status",
    "new_fills",
    "order_record_from_alpaca",
    "orders_ledger_rows",
    "parse_alpaca_asset_identifier",
    "rejected_record",
    "trades_ledger_rows",
]
