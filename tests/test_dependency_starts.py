"""Releases this connector works through that are starting, and provider payloads it cannot read.

ADR 0020 (issue #18). Nothing here reaches the platform, Alpaca or a key: the platform's answers
are fakes, and ms-markets' and MetaTables' own discovery code gives up on them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
from types import SimpleNamespace
from typing import Literal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from alpaca_connectors import dependencies
from alpaca_connectors.api.app.errors import (
    DEPENDENCY_STARTING,
    PROVIDER_PAYLOAD_UNREADABLE,
    api_http_error,
)
from alpaca_connectors.dependencies import (
    DependencyStarting,
    MarketsApiBackend,
    is_dependency_starting,
    prewarm_dependencies,
)

RELEASE_UID = "88888888-8888-4888-8888-888888888888"
CREATE_ACCOUNT = "alpaca_connectors.api.app.routers.accounts.create_account_registration"
REGISTRATION = {
    "environment": "paper",
    "credentials": {
        "source": "external",
        "api_key_secret_name": "ALPACA_API_KEY",
        "secret_key_secret_name": "ALPACA_SECRET_KEY",
    },
}


def runtime_access(state: str, *, active_revision_uid: str | None = "revision") -> SimpleNamespace:
    """The platform's answer about a release that cannot take requests yet, or ever."""
    return SimpleNamespace(
        resource_release_uid=RELEASE_UID,
        release_kind="fastapi",
        runtime_access=SimpleNamespace(state=state, can_request=False, retry_after_ms=3000),
        routing=SimpleNamespace(state="not_routable", active_revision_uid=active_revision_uid),
        access=None,
    )


class FakeClock:
    """A discovery's clock and sleep, so its 30 s limit passes at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def module(self) -> SimpleNamespace:
        return SimpleNamespace(monotonic=self.monotonic, sleep=self.sleep)


def platform_answers(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    """ms-markets finds the Markets API by UID, and the platform answers ``state`` every time."""
    from msm.client import discovery

    from mainsequence.client import ResourceRelease

    monkeypatch.setattr(discovery, "time", FakeClock().module())
    monkeypatch.delenv("MARKETS_API_URL", raising=False)
    monkeypatch.setenv("MARKETS_API_RELEASE_UID", RELEASE_UID)
    monkeypatch.setattr(
        ResourceRelease, "resolve_runtime_access", lambda self, **kwargs: runtime_access(state)
    )


def markets_discovery_error(monkeypatch: pytest.MonkeyPatch, state: str) -> Exception:
    """What ms-markets raises when it gives up on a Markets API the platform answers ``state`` for."""
    from msm.client import discovery

    platform_answers(monkeypatch, state)
    with pytest.raises(discovery.MarketsApiResolutionError) as raised:
        discovery.resolve_markets_api_endpoint()
    return raised.value


def metatables_token_refresh_error(
    monkeypatch: pytest.MonkeyPatch, state: str, *, active_revision_uid: str | None = "revision"
) -> Exception:
    """What a MetaTables call raises when its release token is refreshed while ``state``.

    ``metatables.transport`` refreshes the token with ``endpoint._release_access`` and raises the
    SDK's ``AuthError`` from the discovery error.
    """
    from mainsequence.client.utils import AuthError
    from metatables import endpoint

    monkeypatch.setattr(endpoint, "time", FakeClock().module())
    release = SimpleNamespace(
        uid=RELEASE_UID,
        resolve_runtime_access=lambda **kwargs: runtime_access(
            state, active_revision_uid=active_revision_uid
        ),
    )
    with pytest.raises(endpoint.ApiResolutionError) as raised:
        endpoint._release_access(release)
    try:
        raise AuthError(str(raised.value)) from raised.value
    except AuthError as wrapped:
        return wrapped


# --- What is a start ---------------------------------------------------------------------------


def test_a_markets_api_discovery_that_gives_up_while_it_wakes_is_a_start(monkeypatch) -> None:
    assert is_dependency_starting(markets_discovery_error(monkeypatch, "waking"))


def test_a_markets_api_that_cannot_take_requests_is_a_failure(monkeypatch) -> None:
    assert not is_dependency_starting(markets_discovery_error(monkeypatch, "failed"))


def test_a_metatables_token_refresh_that_gives_up_while_it_wakes_is_a_start(monkeypatch) -> None:
    assert is_dependency_starting(metatables_token_refresh_error(monkeypatch, "waking"))


def test_a_metatables_api_that_is_unavailable_or_not_deployed_is_a_failure(monkeypatch) -> None:
    assert not is_dependency_starting(metatables_token_refresh_error(monkeypatch, "failed"))
    assert not is_dependency_starting(
        metatables_token_refresh_error(monkeypatch, "failed", active_revision_uid=None)
    )


class ReleaseCallStandIn(Exception):
    """The SDK's release-call errors (mainsequence 9.0.39, ADR 0041), for an SDK without them."""

    def __init__(self, message: str, **facts: object) -> None:
        super().__init__(message)


def test_the_sdks_own_start_errors_are_starts(monkeypatch) -> None:
    import mainsequence.client as sdk_client

    starts = (
        "ReleaseReadinessTimeoutError",
        "ReleaseDeadlineTooShortError",
        "ReleaseColdStartTimeoutError",
    )
    failures = ("ReleaseUnavailableError", "ReleaseRequestTimeoutError")
    for name in (*starts, *failures):
        if not hasattr(sdk_client, name):
            monkeypatch.setattr(
                sdk_client, name, type(name, (ReleaseCallStandIn,), {}), raising=False
            )

    def error(name: str) -> Exception:
        return getattr(sdk_client, name)("The release ...", resource_release_uid=RELEASE_UID)

    assert all(is_dependency_starting(error(name)) for name in starts)
    assert not any(is_dependency_starting(error(name)) for name in failures)
    assert not is_dependency_starting(TimeoutError("a slow answer"))


def test_a_start_is_found_through_the_errors_raised_from_it() -> None:
    try:
        try:
            raise DependencyStarting()
        except DependencyStarting as start:
            raise RuntimeError("the service failed") from start
    except RuntimeError as wrapped:
        assert is_dependency_starting(wrapped)

    try:
        try:
            raise DependencyStarting()
        except DependencyStarting:
            raise RuntimeError("unrelated") from None
    except RuntimeError as suppressed:
        assert not is_dependency_starting(suppressed)


def test_the_caller_deadline_comes_from_the_sdk_request_identity(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "mainsequence._request_deadline",
        SimpleNamespace(remaining_request_seconds=lambda: 7.5),
    )
    assert dependencies.caller_remaining_seconds() == 7.5

    # mainsequence before 9.0.39 reads no deadline: no request has a limit.
    monkeypatch.setitem(sys.modules, "mainsequence._request_deadline", None)
    assert dependencies.caller_remaining_seconds() is None


# --- Waiting for the Markets API ---------------------------------------------------------------


def test_a_row_call_waits_for_the_markets_api_only_as_long_as_its_caller(monkeypatch) -> None:
    released = threading.Event()
    client = SimpleNamespace(run_row_operation=lambda *args: {"rows": []})
    discoveries: list[float] = []

    def find_markets_api():
        discoveries.append(time.monotonic())
        released.wait(5)
        return client

    backend = MarketsApiBackend(find_markets_api)
    monkeypatch.setattr(
        dependencies,
        "caller_remaining_seconds",
        lambda: dependencies.ANSWER_MARGIN_SECONDS + 0.05,
    )
    asked = time.monotonic()
    with pytest.raises(DependencyStarting):
        backend.call("asset", "filter", {})
    assert time.monotonic() - asked < 2

    # The discovery went on; the caller's retry finds the Markets API it started.
    released.set()
    assert backend.call("asset", "filter", {}) == {"rows": []}
    assert len(discoveries) == 1


def test_a_caller_without_a_deadline_waits_for_the_discovery(monkeypatch) -> None:
    client = object()
    monkeypatch.setattr(dependencies, "caller_remaining_seconds", lambda: None)
    backend = MarketsApiBackend(lambda: time.sleep(0.05) or client)

    assert backend.client is client
    assert backend.client is client


def test_a_failed_discovery_is_started_again_by_the_next_call() -> None:
    client = object()
    answers: list[object] = [RuntimeError("no answer"), client]

    def find_markets_api():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    backend = MarketsApiBackend(find_markets_api)
    with pytest.raises(RuntimeError, match="no answer"):
        backend.client
    assert backend.client is client


def test_a_row_call_while_the_markets_api_wakes_is_a_start(monkeypatch) -> None:
    """ms-markets' own discovery, run by the backend, gives up on a waking Markets API."""
    platform_answers(monkeypatch, "waking")
    backend = MarketsApiBackend()

    with pytest.raises(Exception) as raised:
        backend.call("asset", "filter", {})

    assert is_dependency_starting(raised.value)


# --- Starting the Markets API with the API -----------------------------------------------------


def test_prewarm_does_nothing_outside_a_hosted_release(
    clean_identity_environment, monkeypatch
) -> None:
    from msm.api.remote import active_remote_backend

    monkeypatch.setattr(
        "msm.client.MarketsClient.from_environment",
        classmethod(lambda cls: pytest.fail("there is no platform to ask")),
    )

    prewarm_dependencies()

    assert active_remote_backend() is None


def test_prewarm_starts_the_markets_api_without_waiting_for_it(
    clean_identity_environment, monkeypatch
) -> None:
    from msm.api.remote import active_remote_backend

    monkeypatch.setenv("APP_NAME", RELEASE_UID)
    asked, released, client = threading.Event(), threading.Event(), object()

    def from_environment(cls):
        asked.set()
        released.wait(5)
        return client

    monkeypatch.setattr("msm.client.MarketsClient.from_environment", classmethod(from_environment))

    started = time.monotonic()
    prewarm_dependencies()
    assert time.monotonic() - started < 1
    assert asked.wait(2)

    backend = active_remote_backend()
    assert isinstance(backend, MarketsApiBackend)
    released.set()
    assert backend.client is client


def test_prewarm_never_fails_startup(clean_identity_environment, monkeypatch) -> None:
    from msm.api.remote import active_remote_backend

    monkeypatch.setenv("APP_NAME", RELEASE_UID)
    discoveries: list[int] = []

    def from_environment(cls):
        discoveries.append(1)
        raise RuntimeError("the platform did not answer")

    monkeypatch.setattr("msm.client.MarketsClient.from_environment", classmethod(from_environment))

    prewarm_dependencies()
    backend = active_remote_backend()
    assert isinstance(backend._discovery.exception(timeout=2), RuntimeError)
    with pytest.raises(RuntimeError):
        backend.client
    assert len(discoveries) == 2  # the first row call asked again

    def unavailable():
        raise RuntimeError("no backend")

    monkeypatch.setattr("alpaca_connectors.runtime.use_markets_api", unavailable)
    prewarm_dependencies()


def test_the_api_starts_the_markets_api_before_attaching_its_runtime(
    clean_identity_environment,
) -> None:
    from alpaca_connectors.api.app.main import create_app

    steps: list[str] = []
    with (
        patch(
            "alpaca_connectors.dependencies.prewarm_dependencies",
            lambda: steps.append("start the Markets API"),
        ),
        patch(
            "alpaca_connectors.runtime.start_markets_engine",
            lambda: steps.append("attach the runtime"),
        ),
        TestClient(create_app()),
    ):
        pass

    assert steps == ["start the Markets API", "attach the runtime"]


# --- What a caller is answered ----------------------------------------------------------------


@pytest.mark.parametrize(
    "start",
    [
        pytest.param(lambda monkeypatch: DependencyStarting(), id="past-the-caller-deadline"),
        pytest.param(
            lambda monkeypatch: markets_discovery_error(monkeypatch, "waking"),
            id="markets-api-waking",
        ),
        pytest.param(
            lambda monkeypatch: metatables_token_refresh_error(monkeypatch, "waking"),
            id="metatables-api-waking",
        ),
    ],
)
def test_registration_answers_dependency_starting_while_a_release_starts(
    api_client, monkeypatch, start
) -> None:
    with patch(CREATE_ACCOUNT, side_effect=start(monkeypatch)):
        response = api_client.post("/v1/accounts", json=REGISTRATION)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(dependencies.STARTING_RETRY_SECONDS)
    assert response.json()["detail"] == {
        "code": "dependency_starting",
        "message": DEPENDENCY_STARTING,
        "retryable": True,
    }
    for internal in ("Markets", "MetaTables", "release", "waking", RELEASE_UID, "Error"):
        assert internal not in response.text


def test_the_callers_deadline_bounds_the_wait_for_the_markets_api(api_client) -> None:
    """Through the SDK's request identity, which reads the deadline from mainsequence 9.0.39."""
    pytest.importorskip(
        "mainsequence._request_deadline", reason="this SDK reads no caller deadline"
    )
    from msm.api.remote import use_remote_backend

    released = threading.Event()
    backend = MarketsApiBackend(lambda: released.wait(10) and object())
    use_remote_backend(backend)
    try:
        with patch(CREATE_ACCOUNT, side_effect=lambda *args, **kwargs: backend.client):
            asked = time.monotonic()
            response = api_client.post(
                "/v1/accounts",
                json=REGISTRATION,
                headers={"Mainsequence-Request-Timeout-Ms": "1500"},
            )
            answered = time.monotonic() - asked
    finally:
        released.set()

    assert answered < 5
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "dependency_starting"


def test_a_release_that_cannot_start_is_not_reported_as_starting(api_client, monkeypatch) -> None:
    with patch(CREATE_ACCOUNT, side_effect=markets_discovery_error(monkeypatch, "failed")):
        response = api_client.post("/v1/accounts", json=REGISTRATION)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "dependency_unavailable"
    assert "Retry-After" not in response.headers


def test_an_unhandled_start_is_answered_as_dependency_starting() -> None:
    from alpaca_connectors.api.app.main import sanitized_unhandled_error

    starting = asyncio.run(sanitized_unhandled_error(None, DependencyStarting()))
    assert starting.status_code == 503
    assert starting.headers["Retry-After"] == str(dependencies.STARTING_RETRY_SECONDS)
    assert json.loads(starting.body)["detail"]["code"] == "dependency_starting"

    failure = asyncio.run(sanitized_unhandled_error(None, RuntimeError("private context")))
    assert failure.status_code == 503
    assert json.loads(failure.body)["detail"]["code"] == "dependency_unavailable"
    assert "Retry-After" not in failure.headers


def test_an_account_sync_that_meets_a_start_records_it_as_retryable() -> None:
    from alpaca_connectors.api.app.services.account_sync import sync_error

    error = sync_error(DependencyStarting())

    assert (error.code, error.message, error.retryable) == (
        "dependency_starting",
        DEPENDENCY_STARTING,
        True,
    )


# --- Provider payloads ---------------------------------------------------------------------------


class ProviderAsset(BaseModel):
    symbol: str
    exchange: Literal["NYSE", "NASDAQ"]


def unreadable_provider_payload() -> ValidationError:
    try:
        ProviderAsset.model_validate({"symbol": "QQQX", "exchange": "NEWEXCHANGE"})
    except ValidationError as exc:
        return exc
    raise AssertionError("the payload was read")


def test_an_unreadable_provider_payload_is_a_provider_failure(api_client, caplog) -> None:
    with (
        caplog.at_level(logging.WARNING, logger="alpaca_connectors.api.app.errors"),
        patch(CREATE_ACCOUNT, side_effect=unreadable_provider_payload()),
    ):
        response = api_client.post("/v1/accounts", json=REGISTRATION)

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "provider_failure",
        "message": PROVIDER_PAYLOAD_UNREADABLE,
        "retryable": False,
    }
    for internal in ("NEWEXCHANGE", "QQQX", "exchange", "ProviderAsset", "validation"):
        assert internal not in response.text
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "ProviderAsset" in logged and "exchange" in logged
    assert "NEWEXCHANGE" not in logged


def test_a_conflict_the_connector_raises_itself_stays_409() -> None:
    error = api_http_error(ValueError("The account is registered already."))

    assert error.status_code == 409
    assert error.detail["code"] == "action_conflict"
    assert error.detail["message"] == "The account is registered already."
