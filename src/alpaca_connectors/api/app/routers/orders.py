"""Order execution on a registered account (ADR 0016).

The contract the Hunch backend targets: submit a batch of market orders keyed by the caller's
``batch_id``, sync or cancel it, and list what was recorded. Every route needs a verified caller
like the rest of the API; whose account an order may be placed on is the calling application's
decision, as it is for the holdings capture.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query
from msm.api.http import BulkActionPreflightResponse

from ..errors import api_http_error
from ..schemas import (
    OrderBatchActionRequest,
    OrderBatchResponse,
    OrderBatchSubmitRequest,
    ResourceCollection,
)
from ..services.common import validate_page_window
from ..services.orders import (
    cancel_orders,
    list_orders,
    preflight_orders,
    submit_orders,
    sync_orders,
)

router = APIRouter(prefix="/v1/accounts/{account_uid}/orders", tags=["Execution"])


@router.get(
    "",
    response_model=ResourceCollection,
    summary="Recorded orders",
    description=(
        "The orders recorded for the account as of their last sync, newest batch first; "
        "`batch_id` narrows the list to one batch. Reads the ledger only."
    ),
)
def orders_list(
    account_uid: str,
    batch_id: str | None = Query(default=None, min_length=1, max_length=255),
    limit: int = Query(default=25, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
) -> ResourceCollection:
    validate_page_window(limit=limit, offset=offset)
    try:
        return list_orders(account_uid, batch_id=batch_id, limit=limit, offset=offset)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/actions/submit/preflight",
    response_model=BulkActionPreflightResponse,
    summary="Pre-trade checks",
    description="Run every pre-trade check of a submission without placing an order.",
)
def orders_submit_preflight(
    account_uid: str, request: OrderBatchSubmitRequest = Body(...)
) -> dict[str, Any]:
    try:
        return preflight_orders(account_uid, request)
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/actions/submit",
    response_model=OrderBatchResponse,
    summary="Submit a batch of market orders",
    description=(
        "Place the batch on the account with its stored credentials after the pre-trade checks "
        "pass (409 with the failed `checks` otherwise). Idempotent on `client_order_id`."
    ),
)
def orders_submit(
    account_uid: str, request: OrderBatchSubmitRequest = Body(...)
) -> OrderBatchResponse:
    try:
        return OrderBatchResponse.model_validate(submit_orders(account_uid, request))
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/actions/sync",
    response_model=OrderBatchResponse,
    summary="Sync a batch",
    description="Re-read the batch's orders from Alpaca, record fills and publish holdings.",
)
def orders_sync(
    account_uid: str, request: OrderBatchActionRequest = Body(...)
) -> OrderBatchResponse:
    try:
        return OrderBatchResponse.model_validate(sync_orders(account_uid, request.batch_id))
    except Exception as exc:
        raise api_http_error(exc) from exc


@router.post(
    "/actions/cancel",
    response_model=OrderBatchResponse,
    summary="Cancel a batch",
    description="Ask Alpaca to cancel every open order of the batch and record the states.",
)
def orders_cancel(
    account_uid: str, request: OrderBatchActionRequest = Body(...)
) -> OrderBatchResponse:
    try:
        return OrderBatchResponse.model_validate(cancel_orders(account_uid, request.batch_id))
    except Exception as exc:
        raise api_http_error(exc) from exc
