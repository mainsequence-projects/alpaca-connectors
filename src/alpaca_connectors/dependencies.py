"""The Main Sequence releases this connector works through, and their starts (ADR 0020).

The connector reaches two releases that scale to zero: the Markets API, which ms-markets finds in
the caller's Environment on the first Markets row call (``msm.client``, ADR 0018), and the
MetaTables API, which ``metatables.endpoint`` finds when the runtime attaches this project's
tables. Each library asks the platform for the release's runtime access, which also starts a
release that is asleep, and polls until it serves or its own limit (30 s) ends.

- On the API's startup, ``prewarm_dependencies`` starts finding the Markets API on its own thread,
  so a caller that woke this API finds the Markets API starting as well. The runtime attachment
  that follows starts, and waits for, the MetaTables API. Nothing is started outside a hosted
  release.
- A Markets row call waits for that discovery only as long as its request's caller will still
  wait, then raises ``DependencyStarting``; the discovery goes on, so the caller's retry finds the
  Markets API. Without a caller deadline it waits for ms-markets' own limit.
- ``is_dependency_starting`` tells a start from a failure. The API answers a start as
  ``503 dependency_starting`` with ``Retry-After`` (``alpaca_connectors.api.app.errors``).
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from typing import Any

from msm.client import LazyRemoteBackend

logger = logging.getLogger(__name__)

# Seconds a caller is told to wait before it sends the request again (`Retry-After`).
STARTING_RETRY_SECONDS = 5
# Kept from the caller's deadline, so the answer reaches the caller before it gives up.
ANSWER_MARGIN_SECONDS = 1.0
# How ms-markets (`msm.client.discovery`) and MetaTables (`metatables.endpoint`) end the message of
# a discovery that gave up while the platform still reported the release as starting.
_STILL_WAKING = "last state: waking"
# The deployment markers by which the SDK's request identity knows a hosted release.
_HOSTED_MARKERS = (
    "APP_NAME",
    "FASTAPI_PUBLIC_BASE_URL",
    "MAINSEQUENCE_CALLER_ASSERTION_ISSUER",
    "MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL",
)


class DependencyStarting(RuntimeError):
    """A release this connector works through is still starting."""

    def __init__(self, message: str = "A release this connector works through is starting."):
        super().__init__(message)


def caller_remaining_seconds() -> float | None:
    """Seconds the current request's caller will still wait; ``None`` when it set no limit.

    The SDK's request identity reads the caller's ``Mainsequence-Request-Timeout-Ms`` from
    mainsequence 9.0.39 (its ADR 0041); with an earlier SDK no request has a limit.
    """
    try:
        from mainsequence._request_deadline import remaining_request_seconds
    except ImportError:
        return None
    return remaining_request_seconds()


def is_dependency_starting(exc: BaseException) -> bool:
    """Whether ``exc``, or an error it was raised from, is a release that is still starting.

    That is ``DependencyStarting``, the SDK's readiness, deadline and cold-start errors (ADR 0041),
    and an ms-markets or MetaTables discovery that gave up while the platform still reported the
    release waking. A release that is unavailable, missing or ambiguous is a failure.
    """
    starting = (DependencyStarting, *_sdk_starting_errors())
    discovery = _discovery_errors()
    return any(
        isinstance(error, starting)
        or (isinstance(error, discovery) and _STILL_WAKING in str(error))
        for error in _causes(exc)
    )


def _causes(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    error: BaseException | None = exc
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)


def _sdk_starting_errors() -> tuple[type[BaseException], ...]:
    try:
        from mainsequence.client import (
            ReleaseColdStartTimeoutError,
            ReleaseDeadlineTooShortError,
            ReleaseReadinessTimeoutError,
        )
    except ImportError:  # mainsequence before 9.0.39 raises none of them
        return ()
    return (
        ReleaseReadinessTimeoutError,
        ReleaseDeadlineTooShortError,
        ReleaseColdStartTimeoutError,
    )


def _discovery_errors() -> tuple[type[BaseException], ...]:
    from msm.client.discovery import MarketsApiResolutionError

    from metatables.endpoint import ApiResolutionError

    return (MarketsApiResolutionError, ApiResolutionError)


class MarketsApiBackend(LazyRemoteBackend):
    """ms-markets' lazy Markets API backend, found on its own thread and waited for by deadline.

    ``start`` runs the factory (ms-markets' ``MarketsClient.from_environment``) on a daemon thread,
    outside any request, one discovery at a time; a discovery that failed is started again by the
    next call. ``client`` waits for it at most as long as the request's caller will still wait.
    """

    def __init__(self, factory: Callable[[], Any] | None = None) -> None:
        super().__init__(factory)
        self._discovery: Future[Any] | None = None

    def start(self) -> Future[Any]:
        """The current discovery of the Markets API, started unless one runs or succeeded."""
        with self._lock:
            discovery = self._discovery
            if discovery is None or (discovery.done() and discovery.exception() is not None):
                discovery = self._discovery = Future()
                threading.Thread(
                    target=self._discover,
                    args=(discovery,),
                    name="markets-api-discovery",
                    daemon=True,
                ).start()
            return discovery

    def _discover(self, discovery: Future[Any]) -> None:
        try:
            client = self._factory()
        except BaseException as exc:  # every outcome belongs to the callers waiting for it
            logger.warning("Finding the Markets API failed (%s).", type(exc).__name__)
            discovery.set_exception(exc)
        else:
            discovery.set_result(client)

    @property
    def client(self) -> Any:
        discovery = self.start()
        remaining = caller_remaining_seconds()
        wait = None if remaining is None else max(0.0, remaining - ANSWER_MARGIN_SECONDS)
        try:
            # Waits without raising the discovery's own error, which `result` raises below.
            discovery.exception(timeout=wait)
        except TimeoutError:
            raise DependencyStarting() from None
        return discovery.result()


def prewarm_dependencies() -> None:
    """Start finding the Markets API without waiting for it, on a hosted release only.

    Asking the platform for the Markets API's runtime access starts it, so a caller that woke this
    API does not find the Markets API asleep. This never blocks or fails startup: the discovery
    runs on its own thread, and a failure is logged and left to the first row call to retry.
    """
    if not any(os.environ.get(name) for name in _HOSTED_MARKERS):
        return
    try:
        from alpaca_connectors.runtime import use_markets_api

        backend = use_markets_api()
        if isinstance(backend, MarketsApiBackend):
            backend.start()
    except Exception:
        logger.warning("Could not start finding the Markets API at startup.", exc_info=True)


__all__ = [
    "ANSWER_MARGIN_SECONDS",
    "STARTING_RETRY_SECONDS",
    "DependencyStarting",
    "MarketsApiBackend",
    "caller_remaining_seconds",
    "is_dependency_starting",
    "prewarm_dependencies",
]
