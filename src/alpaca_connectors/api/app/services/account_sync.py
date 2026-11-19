"""Account sync answered as an observable operation (issue #14).

A sync runs inside the request: it captures the holdings, sizes the account's target into units
and allocates the snapshot to the account's funds (ADR 0023). It is recorded as one durable
`AccountSyncOperation`, so the route answers 200 with a terminal operation and
`GET /v1/operations/{operation_uid}` answers the same operation to its owner afterwards.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any

from msm.api.http import OperationError
from pydantic import ValidationError

from alpaca_connectors.account.credentials import (
    build_alpaca_trading_client,
    resolve_registered_alpaca_credentials,
)
from alpaca_connectors.account.ownership import AccountOwner
from alpaca_connectors.account.services import get_account_registration
from alpaca_connectors.holdings import capture_alpaca_account_holdings
from alpaca_connectors.holdings.allocation import (
    FundAllocationResult,
    account_price_reader,
    allocate_sized_target,
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

logger = logging.getLogger(__name__)

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


class _AlpacaTime:
    """Seconds spent in Alpaca calls during one sync, for the sync's timing log line."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def timed(self, call: Callable[..., Any]) -> Callable[..., Any]:
        def run(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                return call(*args, **kwargs)
            finally:
                self.seconds += time.monotonic() - started

        return run


class _TimedClient:
    """An Alpaca trading client whose calls add to ``_AlpacaTime``."""

    def __init__(self, client: Any, alpaca: _AlpacaTime) -> None:
        self._client = client
        self._alpaca = alpaca

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._client, name)
        return self._alpaca.timed(value) if callable(value) else value


def capture_holdings(
    account_uid: str, *, registration: Mapping[str, Any], client: Any
) -> dict[str, Any]:
    """The capture with Alpaca's reported equity, from the registration and client in hand."""
    return asdict(
        capture_alpaca_account_holdings(account_uid, registration=registration, client=client)
    )


def _log_timings(account_uid: str, status: str, steps: dict[str, float], alpaca: float) -> None:
    logger.info(
        "account sync %s account=%s %s alpaca_seconds=%.2f",
        status,
        account_uid,
        " ".join(f"{step}_seconds={seconds:.2f}" for step, seconds in steps.items()),
        alpaca,
    )


def sync_account(account_uid: str, *, owner: AccountOwner) -> AccountSyncOperationResponse:
    """Capture the holdings, size the target, allocate, and answer the terminal operation.

    The registration and the keys are read once and serve the capture and the sizing's latest
    trades. A step that fails is the operation's ``failed`` outcome at that step, not an HTTP
    error; only a failure to record the operation itself propagates. A snapshot published before a
    later step failed stays published. An account with no target, or a capture that published no
    snapshot, ends after the capture with the two other steps skipped. Each step's duration and
    the time spent in Alpaca are logged in one line when the sync ends.
    """
    operation = start_account_sync_operation(account_uid=account_uid, owner=owner)
    step = CAPTURE_STEP
    alpaca = _AlpacaTime()
    durations: dict[str, float] = {}
    started = time.monotonic()

    def finish_step(next_step: str) -> None:
        nonlocal step, started
        now = time.monotonic()
        durations[step] = now - started
        step, started = next_step, now

    try:
        registration = get_account_registration(account_uid)
        if registration is None:
            raise LookupError(f"Alpaca account registration {account_uid!s} does not exist.")
        credentials = resolve_registered_alpaca_credentials(registration)
        client = _TimedClient(
            build_alpaca_trading_client(
                credentials=credentials, paper=bool(registration["is_paper"])
            ),
            alpaca,
        )
        captured = capture_holdings(account_uid, registration=registration, client=client)
        allocation = None
        if captured.get("holdings_set_uid") is not None:
            finish_step(SIZE_STEP)
            sized = size_account_target(
                account_uid,
                valuation_time=captured["time_index"],
                equity=captured.get("equity"),
                price_reader=alpaca.timed(account_price_reader(credentials)),
            )
            if sized is not None:
                finish_step(ALLOCATE_STEP)
                allocation = allocate_sized_target(account_uid, sized)
        result = sync_result(captured, allocation)
    except Exception as exc:
        finish_step(step)
        _log_timings(account_uid, "failed", durations, alpaca.seconds)
        return operation_response(
            fail_account_sync_operation(operation, error=sync_error(exc), step=step)
        )
    finish_step(step)
    _log_timings(account_uid, "succeeded", durations, alpaca.seconds)
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
