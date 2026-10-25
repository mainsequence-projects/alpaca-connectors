"""API shaping for the order-execution services (ADR 0016)."""

from __future__ import annotations

from typing import Any

from alpaca_connectors.execution import (
    OrderBatchSubmission,
    OrderLegSpec,
    cancel_order_batch,
    list_account_orders,
    preflight_order_batch,
    submit_order_batch,
    sync_order_batch,
)

from ..schemas import OrderBatchSubmitRequest
from .common import collection_response


def _submission(request: OrderBatchSubmitRequest) -> OrderBatchSubmission:
    return OrderBatchSubmission(
        batch_id=request.batch_id,
        legs=tuple(
            OrderLegSpec(
                client_order_id=leg.client_order_id,
                asset_identifier=leg.asset_identifier,
                symbol=leg.symbol,
                side=leg.side,
                quantity=float(leg.quantity),
                order_type=leg.order_type,
                time_in_force=leg.time_in_force,
            )
            for leg in request.orders
        ),
        queue_when_closed=request.queue_when_closed,
    )


def preflight_orders(account_uid: str, request: OrderBatchSubmitRequest) -> dict[str, Any]:
    return preflight_order_batch(account_uid, _submission(request)).as_payload()


def submit_orders(account_uid: str, request: OrderBatchSubmitRequest) -> dict[str, Any]:
    return submit_order_batch(account_uid, _submission(request)).as_payload()


def sync_orders(account_uid: str, batch_id: str) -> dict[str, Any]:
    return sync_order_batch(account_uid, batch_id).as_payload()


def cancel_orders(account_uid: str, batch_id: str) -> dict[str, Any]:
    return cancel_order_batch(account_uid, batch_id).as_payload()


def list_orders(account_uid: str, *, batch_id: str | None, limit: int, offset: int):
    items, total = list_account_orders(account_uid, batch_id=batch_id, limit=limit, offset=offset)
    return collection_response(items=items, total=total, limit=limit, offset=offset)


__all__ = ["cancel_orders", "list_orders", "preflight_orders", "submit_orders", "sync_orders"]
