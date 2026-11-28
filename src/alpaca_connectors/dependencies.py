"""The Main Sequence releases this connector works through, and their starts (ADR 0020).

The connector reaches two releases that scale to zero:

- **The Markets API**, which ms-markets finds in the caller's Environment on the first Markets row
  call (``msm.client``, ADR 0018). Since ms-markets 6.4.0 finding it only names the release, and
  each call goes through the SDK's ``ResourceRelease.call``. That asks the platform for the
  release's runtime access, which starts a release that is asleep, and waits for it within the
  caller's deadline, else the SDK's own limit.
- **The MetaTables API**, which ``metatables.client.endpoint`` finds when the runtime attaches this
  project's tables. It asks for the release's runtime access and polls until the release serves
  or its own limit (30 s) ends.

What this module does with them:

- On the API's startup, ``prewarm_dependencies`` finds the Markets API on its own thread and asks
  once for its runtime access, so a caller that woke this API finds the Markets API starting as
  well. The runtime attachment that follows starts, and waits for, the MetaTables API. Nothing is
  started outside a hosted release.
- A Markets row call waits for the discovery only as long as its request's caller will still wait,
  then raises ``DependencyStarting``; the discovery goes on, so the caller's retry finds the
  Markets API.
- ``is_dependency_starting`` tells a start from a failure. The API answers a start as
  ``503 dependency_starting`` with ``Retry-After`` (``alpaca_connectors.api.app.errors``).
- A Job has no caller to answer, so it waits out a start instead: the schema gate, the first
  thing each scheduled launcher does, waits up to ``JOB_WAKE_SECONDS`` for both releases (ADR
  0020, decision 5). A release asleep since the previous night can take longer than the SDK's
  own limit, which is twice the cold start the platform expects.
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
# How long a Job waits for each release that is waking (ADR 0020, decision 5). The libraries' own
# 30 s ended every scheduled dispatcher run while the Markets API was still waking.
JOB_WAKE_SECONDS = 300.0
# How ms-markets (`msm.client.discovery`) and MetaTables (`metatables.client.endpoint`) end the message of
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

    The SDK's request identity reads the caller's ``Mainsequence-Request-Timeout-Ms`` (its ADR
    0041); ``mainsequence.client.remaining_request_seconds`` is public from mainsequence 9.0.46.
    """
    from mainsequence.client import remaining_request_seconds

    return remaining_request_seconds()


def is_dependency_starting(exc: BaseException) -> bool:
    """Whether ``exc``, or an error it was raised from, is a release that is still starting.

    That is ``DependencyStarting``, the SDK's readiness, deadline and cold-start errors (ADR 0041),
    which every Markets API call raises since ms-markets 6.4.0, and a MetaTables discovery that
    gave up while the platform still reported the release waking. A release that is unavailable,
    missing or ambiguous is a failure.
    """
    from metatables.client.endpoint import ApiResolutionError

    starting = (DependencyStarting, *_sdk_starting_errors())
    return any(
        isinstance(error, starting)
        or (isinstance(error, ApiResolutionError) and _STILL_WAKING in str(error))
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
    from mainsequence.client import (
        ReleaseColdStartTimeoutError,
        ReleaseDeadlineTooShortError,
        ReleaseReadinessTimeoutError,
    )

    return (
        ReleaseReadinessTimeoutError,
        ReleaseDeadlineTooShortError,
        ReleaseColdStartTimeoutError,
    )


class MarketsApiBackend(LazyRemoteBackend):
    """ms-markets' lazy Markets API backend, found on its own thread and waited for by deadline.

    ``start`` runs the factory (ms-markets' ``MarketsClient.from_environment``) on a daemon thread,
    outside any request, one discovery at a time; a discovery that failed is started again by the
    next call. ``client`` waits for it at most as long as the request's caller will still wait.
    Since ms-markets 6.4.0 the discovery only finds the release; each call then waits for a
    release that is starting inside the SDK's ``ResourceRelease.call``, within the same deadline.
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


def markets_client(*, discovery_timeout: float) -> Any:
    """A Markets API client whose release serves, waiting up to ``discovery_timeout`` for it.

    ms-markets' ``MarketsClient.from_environment`` finds the release (the ``MARKETS_API_URL`` and
    ``MARKETS_API_RELEASE_UID`` overrides, else discovery in the caller's Environment) without
    asking for its access, and its first call waits for a release that is starting only for the
    SDK's own limit. So for a release this waits first with the SDK's ``wait_for_runtime_access``,
    which keeps the access the client's calls then reuse. A ``MARKETS_API_URL`` is called as it is.
    """
    from msm.client import MarketsClient

    from mainsequence.client import ResourceRelease

    client = MarketsClient.from_environment()
    if client.release_uid is not None:
        ResourceRelease.by_uid(client.release_uid).wait_for_runtime_access(
            wait_timeout_seconds=discovery_timeout
        )
    return client


def prewarm_dependencies() -> None:
    """Start the Markets API without waiting for it, on a hosted release only.

    Finding the Markets API runs on its own thread; asking the platform once for its runtime
    access then starts a release that is asleep, so a caller that woke this API does not find
    the Markets API asleep. This never blocks or fails startup: a failure is logged and left to
    the first row call, which finds the release again and waits for it.
    """
    if not any(os.environ.get(name) for name in _HOSTED_MARKERS):
        return
    try:
        from alpaca_connectors.runtime import use_markets_api

        backend = use_markets_api()
        if isinstance(backend, MarketsApiBackend):
            threading.Thread(
                target=_wake_markets_api,
                args=(backend.start(),),
                name="markets-api-wake",
                daemon=True,
            ).start()
    except Exception:
        logger.warning("Could not start finding the Markets API at startup.", exc_info=True)


def _wake_markets_api(discovery: Future[Any]) -> None:
    """Ask the platform once for the found release's runtime access, which starts it."""
    if discovery.exception() is not None:  # already logged by the discovery
        return
    release_uid = getattr(discovery.result(), "release_uid", None)
    if release_uid is None:
        return
    try:
        from mainsequence.client import ResourceRelease

        ResourceRelease.by_uid(release_uid).resolve_runtime_access()
    except Exception:
        logger.warning("Could not start the Markets API at startup.", exc_info=True)


__all__ = [
    "ANSWER_MARGIN_SECONDS",
    "JOB_WAKE_SECONDS",
    "STARTING_RETRY_SECONDS",
    "DependencyStarting",
    "MarketsApiBackend",
    "caller_remaining_seconds",
    "is_dependency_starting",
    "markets_client",
    "prewarm_dependencies",
]
