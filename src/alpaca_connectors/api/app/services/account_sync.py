"""Account sync answered as an observable operation (issue #14).

A sync runs inside the request: it captures the holdings, sizes the account's target into units
and allocates the snapshot to the account's funds (ADR 0023). It is recorded as one durable
`AccountSyncOperation`, so the route answers 200 with a terminal operation and
`GET /v1/operations/{operation_uid}` answers the same operation to its owner afterwards.
"""

from __future__ import annotations

import datetime as dt
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
    StepTimes,
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


def _now() -> dt.datetime:
    """The time a sync step starts or ends, to the microsecond: a step can take under a second."""
    return dt.datetime.now(dt.UTC)


def _log_timings(account_uid: str, status: str, times: StepTimes, alpaca: float) -> None:
    logger.info(
        "account sync %s account=%s %s alpaca_seconds=%.2f",
        status,
        account_uid,
        " ".join(
            f"{step}_seconds={(completed - started).total_seconds():.2f}"
            for step, (started, completed) in times.items()
        ),
        alpaca,
    )


def sync_account(
    account_uid: str,
    *,
    owner: AccountOwner,
    registration: Mapping[str, Any] | None = None,
) -> AccountSyncOperationResponse:
    """Capture the holdings, size the target, allocate, and answer the terminal operation.

    The registration and the keys are read once and serve the capture and the sizing's latest
    trades; ``registration`` is the one the route read for its 404, not read again. Reads that do
    not depend on each other run together (issue #46): the operation's record with the keys, and
    the account's target with the capture. A step that fails is the operation's ``failed``
    outcome at that step, not an HTTP error; only a failure to record the operation itself
    propagates. A snapshot published before a
    later step failed stays published. An account with no target, or a capture that published no
    snapshot, ends after the capture with the two other steps skipped. Each step's own start and
    end are recorded on the operation's steps, and logged with the time spent in Alpaca in one
    line, when the sync ends (issue #46).
    """
    from alpaca_connectors.markets_api import all_together

    if registration is None:
        registration = get_account_registration(account_uid)
    operation, credentials = all_together(
        lambda: start_account_sync_operation(account_uid=account_uid, owner=owner),
        lambda: _credentials_or_error(registration),
    )
    step = CAPTURE_STEP
    alpaca = _AlpacaTime()
    times: dict[str, tuple[dt.datetime, dt.datetime]] = {}
    started = _now()

    def finish_step(next_step: str) -> None:
        nonlocal step, started
        now = _now()
        times[step] = (started, now)
        step, started = next_step, now

    try:
        if isinstance(credentials, Exception):
            raise credentials
        client = _TimedClient(
            build_alpaca_trading_client(
                credentials=credentials, paper=bool(registration["is_paper"])
            ),
            alpaca,
        )
        captured, target = all_together(
            lambda: capture_holdings(account_uid, registration=registration, client=client),
            lambda: _target_or_error(account_uid),
        )
        allocation = None
        if captured.get("holdings_set_uid") is not None:
            finish_step(SIZE_STEP)
            if isinstance(target, Exception):
                raise target
            sized = size_account_target(
                account_uid,
                valuation_time=captured["time_index"],
                equity=captured.get("equity"),
                price_reader=alpaca.timed(account_price_reader(credentials)),
                target=target,
            )
            if sized is not None:
                finish_step(ALLOCATE_STEP)
                allocation = allocate_sized_target(account_uid, sized)
        result = sync_result(captured, allocation)
    except Exception as exc:
        finish_step(step)
        _log_timings(account_uid, "failed", times, alpaca.seconds)
        return operation_response(
            fail_account_sync_operation(operation, error=sync_error(exc), step=step, times=times)
        )
    finish_step(step)
    _log_timings(account_uid, "succeeded", times, alpaca.seconds)
    return operation_response(
        succeed_account_sync_operation(
            operation,
            result=result.model_dump(mode="json"),
            skipped=() if allocation is not None else (SIZE_STEP, ALLOCATE_STEP),
            times=times,
        )
    )


def _credentials_or_error(registration: Mapping[str, Any] | None) -> Any:
    """The account's keys, or the error that reading them raised, which the sync records as its
    capture step's failure once its operation exists."""
    try:
        if registration is None:
            raise LookupError("Alpaca account registration does not exist.")
        return resolve_registered_alpaca_credentials(registration)
    except Exception as exc:  # noqa: BLE001 - recorded as the operation's failure
        return exc


def _target_or_error(account_uid: str) -> Any:
    """The account's target in force, read alongside the capture; the error, as Markets refused
    it, is raised at the size step, so a capture that publishes nothing never meets it."""
    from alpaca_connectors.holdings.allocation import markets_refusal
    from alpaca_connectors.markets_api import account_target_positions

    try:
        return account_target_positions(account_uid)
    except Exception as exc:  # noqa: BLE001 - raised at the size step
        return markets_refusal(exc)


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
