"""Account sync answered as an observable operation (issue #14).

A sync runs inside the request: it captures the holdings, sizes the account's target into units
and allocates the snapshot to the account's funds (ADR 0022). It is recorded as one durable
`AccountSyncOperation`, so the route answers 200 with a terminal operation and
`GET /v1/operations/{operation_uid}` answers the same operation to its owner afterwards.
"""

from __future__ import annotations

import uuid
from typing import Any

from msm.api.http import OperationError
from pydantic import ValidationError

from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.holdings.allocation import (
    FundAllocationResult,
    allocate_sized_target,
    registered_account_price_reader,
    size_account_target,
)
from alpaca_connectors.operations.account_sync import (
    ALLOCATE_STEP,
    CAPTURE_STEP,
    SIZE_STEP,
    AccountSyncOperation,
    fail_account_sync_operation,
    get_account_sync_operation,
    start_account_sync_operation,
    succeed_account_sync_operation,
)

from ..errors import api_http_error
from ..schemas import AccountSyncOperationResponse, AccountSyncResult
from .holdings import capture_snapshot as capture_holdings

SYNC_FAILED = OperationError(
    code="account_sync_failed",
    message="The account sync could not be completed.",
    retryable=True,
)


def operation_response(operation: AccountSyncOperation) -> AccountSyncOperationResponse:
    return AccountSyncOperationResponse(
        operation_uid=str(operation.uid),
        action=operation.action,
        status=operation.status,
        current_step=operation.current_step,
        steps=[step.model_dump(mode="json") for step in operation.steps],
        request=operation.request,
        result=operation.result,
        error=operation.error.model_dump(mode="json") if operation.error else None,
        created_at=operation.created_at,
        started_at=operation.started_at,
        updated_at=operation.updated_at,
        completed_at=operation.completed_at,
    )


def sync_error(exc: Exception) -> OperationError:
    """The public error of a capture that failed, as the account routes would answer it.

    The API's error mapping already keeps exception internals and key values out of its
    messages; any detail without that shape becomes the generic retryable failure.
    """
    detail = api_http_error(exc).detail
    if isinstance(detail, dict):
        try:
            return OperationError(
                code=detail["code"],
                message=detail["message"],
                retryable=bool(detail.get("retryable", False)),
            )
        except (KeyError, ValidationError):
            pass
    return SYNC_FAILED


def sync_result(
    captured: dict[str, Any], allocation: FundAllocationResult | None = None
) -> AccountSyncResult:
    allocation = allocation or FundAllocationResult()
    return AccountSyncResult(
        account_uid=str(captured["account_uid"]),
        holdings_set_uid=(
            str(captured["holdings_set_uid"])
            if captured.get("holdings_set_uid") is not None
            else None
        ),
        time_index=captured["time_index"],
        holdings_rows=int(captured["holdings_rows"]),
        position_set_uid=allocation.position_set_uid,
        allocation_applied=allocation.allocation_applied,
        lines=list(allocation.lines),
        extra={"unresolved_symbols": list(captured.get("unresolved_symbols") or [])},
    )


def sync_account(account_uid: str, *, owner: AccountOwner) -> AccountSyncOperationResponse:
    """Capture the holdings, size the target, allocate, and answer the terminal operation.

    A step that fails is the operation's ``failed`` outcome at that step, not an HTTP error; only
    a failure to record the operation itself propagates. A snapshot published before a later step
    failed stays published. An account with no target, or a capture that published no snapshot,
    ends after the capture with the two other steps skipped.
    """
    operation = start_account_sync_operation(account_uid=account_uid, owner=owner)
    step = CAPTURE_STEP
    try:
        captured = capture_holdings(account_uid)
        allocation = None
        if captured.get("holdings_set_uid") is not None:
            step = SIZE_STEP
            sized = size_account_target(
                account_uid,
                valuation_time=captured["time_index"],
                equity=captured.get("equity"),
                price_reader=registered_account_price_reader(account_uid),
            )
            if sized is not None:
                step = ALLOCATE_STEP
                allocation = allocate_sized_target(account_uid, sized)
        result = sync_result(captured, allocation)
    except Exception as exc:
        return operation_response(
            fail_account_sync_operation(operation, error=sync_error(exc), step=step)
        )
    return operation_response(
        succeed_account_sync_operation(
            operation,
            result=result.model_dump(mode="json"),
            skipped=() if allocation is not None else (SIZE_STEP, ALLOCATE_STEP),
        )
    )


def get_sync_operation(
    operation_uid: str,
    *,
    owner: AccountOwner,
) -> AccountSyncOperationResponse | None:
    """Return the operation to the owner it ran for; anyone else gets None, as for a missing one."""
    try:
        uid = uuid.UUID(operation_uid)
    except ValueError:
        return None
    operation = get_account_sync_operation(uid)
    if operation is None or operation.owner != owner:
        return None
    return operation_response(operation)


__all__ = ["get_sync_operation", "operation_response", "sync_account", "sync_error"]
