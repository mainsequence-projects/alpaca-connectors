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
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Literal
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


@pytest.fixture(autouse=True)
def _no_kept_release_access():
    """Each test starts with no release access kept in the process, as a new process does."""
    from mainsequence.client.release_calls import release_runtime_cache

    release_runtime_cache.clear()
    yield
    release_runtime_cache.clear()


def runtime_access(
    state: str,
    *,
    active_revision_uid: str | None = "revision",
    retry_after_ms: int | None = 3000,
    expected_cold_start_seconds: float | None = None,
):
    """The platform's answer about a release that cannot take requests yet, or ever.

    Built with the SDK's own model, so a change to the answer's shape fails here (MetaTables
    0.1.64 began reading ``runtime_presence``).
    """
    from mainsequence.client import ResourceReleaseRuntimeAccess

    return ResourceReleaseRuntimeAccess.model_validate(
        {
            "resource_release_uid": RELEASE_UID,
            "release_kind": "fastapi",
            "scaling": {"expected_cold_start_seconds": expected_cold_start_seconds},
            "runtime_access": {
                "state": state,
                "can_request": False,
                "retry_after_ms": retry_after_ms,
            },
            "runtime_presence": {
                "phase": "starting" if state == "waking" else state,
                "replicas": {"desired": 1, "actual": 0},
                "detail": "The release has no serving replica.",
            },
            "routing": {"state": "not_routable", "active_revision_uid": active_revision_uid},
            "access": None,
        }
    )


class FakeClock:
    """A wait's clock and sleep, so its limit passes at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def module(self) -> SimpleNamespace:
        return SimpleNamespace(monotonic=self.monotonic, sleep=self.sleep, time=time.time)


def platform_answers(monkeypatch: pytest.MonkeyPatch, answer: Callable[[], Any]) -> FakeClock:
    """ms-markets finds the Markets API by UID, and the platform answers ``answer()`` each time.

    Since ms-markets 6.4.0 a Markets call waits for its release in the SDK's own
    ``mainsequence.client.release_calls``, whose clock this fakes.
    """
    from mainsequence.client import ResourceRelease, release_calls

    clock = FakeClock()
    monkeypatch.setattr(release_calls, "time", clock.module())
    monkeypatch.delenv("MARKETS_API_URL", raising=False)
    monkeypatch.setenv("MARKETS_API_RELEASE_UID", RELEASE_UID)
    monkeypatch.setattr(ResourceRelease, "resolve_runtime_access", lambda self, **kwargs: answer())
    return clock


def markets_call_error(monkeypatch: pytest.MonkeyPatch, state: str, **answer: Any) -> Exception:
    """What a Markets API call raises when the platform answers ``state`` for its release."""
    from msm.client import MarketsClient

    platform_answers(monkeypatch, lambda: runtime_access(state, **answer))
    with pytest.raises(Exception) as raised:
        MarketsClient.from_environment().request("GET", "/health")
    return raised.value


def metatables_token_refresh_error(
    monkeypatch: pytest.MonkeyPatch, state: str, *, active_revision_uid: str | None = "revision"
) -> Exception:
    """What a MetaTables call raises when its release token is refreshed while ``state``.

    ``metatables.client.transport`` refreshes the token with ``endpoint._release_access`` and raises the
    SDK's ``AuthError`` from the discovery error.
    """
    from mainsequence.client.utils import AuthError
    from metatables.client import endpoint

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


def test_a_markets_api_call_that_gives_up_while_it_wakes_is_a_start(monkeypatch) -> None:
    assert is_dependency_starting(markets_call_error(monkeypatch, "waking"))


def test_a_markets_api_that_cannot_take_requests_is_a_failure(monkeypatch) -> None:
    # The platform's rule (ADR 0041): an answer without a retry hint is final.
    assert not is_dependency_starting(
        markets_call_error(monkeypatch, "failed", retry_after_ms=None)
    )


def test_a_metatables_token_refresh_that_gives_up_while_it_wakes_is_a_start(monkeypatch) -> None:
    assert is_dependency_starting(metatables_token_refresh_error(monkeypatch, "waking"))


def test_a_metatables_api_that_is_unavailable_or_not_deployed_is_a_failure(monkeypatch) -> None:
    assert not is_dependency_starting(metatables_token_refresh_error(monkeypatch, "failed"))
    assert not is_dependency_starting(
        metatables_token_refresh_error(monkeypatch, "failed", active_revision_uid=None)
    )


def test_the_sdks_own_start_errors_are_starts() -> None:
    import mainsequence.client as sdk_client

    starts = (
        "ReleaseReadinessTimeoutError",
        "ReleaseDeadlineTooShortError",
        "ReleaseColdStartTimeoutError",
    )
    # A release with no active revision (mainsequence 9.0.46) is a failure, not a start.
    failures = ("ReleaseUnavailableError", "ReleaseNotDeployedError", "ReleaseRequestTimeoutError")

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
    # Outside a request no caller has set a limit.
    assert dependencies.caller_remaining_seconds() is None

    monkeypatch.setattr("mainsequence.client.remaining_request_seconds", lambda: 7.5)
    assert dependencies.caller_remaining_seconds() == 7.5


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
    """The backend's client, ms-markets' own, gives up on a waking Markets API in the SDK's wait."""
    platform_answers(monkeypatch, lambda: runtime_access("waking"))
    backend = MarketsApiBackend()

    with pytest.raises(Exception) as raised:
        backend.call("asset", "filter", {})

    assert is_dependency_starting(raised.value)


# --- A Job waits out a start (decision 5) ------------------------------------------------------


def markets_api_that_wakes_after(monkeypatch: pytest.MonkeyPatch, seconds: float) -> FakeClock:
    """The platform answers ``waking`` until ``seconds`` pass on the SDK's clock, then ready.

    That is how every scheduled dispatcher run of 2026-11-12 and 2026-11-13 met the Markets API.
    The platform expects a 20 s cold start, so the SDK's own limit for a call is 40 s.
    """
    from mainsequence.client import ResourceReleaseRuntimeAccess

    ready = ResourceReleaseRuntimeAccess.model_validate(
        {
            "resource_release_uid": RELEASE_UID,
            "release_kind": "fastapi",
            "runtime_access": {"state": "ready", "can_request": True, "retry_after_ms": None},
            "runtime_presence": {
                "phase": "serving",
                "replicas": {"desired": 1, "actual": 1},
                "detail": "The release serves.",
            },
            "routing": {"state": "routable", "active_revision_uid": "revision"},
            "access": {"mode": "token", "rpc_url": "https://markets.example.test", "token": "t"},
        }
    )
    clock: FakeClock

    def answer():
        if clock.now >= seconds:
            return ready
        return runtime_access("waking", expected_cold_start_seconds=20)

    clock = platform_answers(monkeypatch, answer)
    return clock


def test_a_markets_call_alone_gives_up_on_a_markets_api_asleep_overnight(monkeypatch) -> None:
    from msm.client import MarketsClient

    markets_api_that_wakes_after(monkeypatch, 60)

    with pytest.raises(Exception, match="still starting after 40s") as raised:
        MarketsClient.from_environment().request("GET", "/health")
    assert is_dependency_starting(raised.value)


def test_a_job_waits_out_a_markets_api_asleep_overnight(monkeypatch) -> None:
    clock = markets_api_that_wakes_after(monkeypatch, 60)

    client = dependencies.markets_client(discovery_timeout=dependencies.JOB_WAKE_SECONDS)

    assert client.release_uid == RELEASE_UID
    assert 60 <= clock.now < dependencies.JOB_WAKE_SECONDS


def test_a_job_still_fails_on_a_markets_api_that_never_wakes(monkeypatch) -> None:
    clock = markets_api_that_wakes_after(monkeypatch, float("inf"))

    with pytest.raises(Exception, match="still starting after 300s") as raised:
        dependencies.markets_client(discovery_timeout=dependencies.JOB_WAKE_SECONDS)
    assert is_dependency_starting(raised.value)
    assert clock.now == dependencies.JOB_WAKE_SECONDS


def test_the_schema_gate_waits_out_both_releases_for_a_job(monkeypatch) -> None:
    from alpaca_connectors.migrations import schema_gate
    from metatables.client import endpoint

    markets_waits: list[float] = []
    metatables_waits: list[float] = []

    def markets_client(*, discovery_timeout: float):
        markets_waits.append(discovery_timeout)
        version = schema_gate.required_markets_version()
        return SimpleNamespace(request=lambda method, path: {"version": version})

    def read_applied(migration) -> tuple[str, ...]:
        metatables_waits.append(endpoint.discovery_budget())
        return (schema_gate.code_head_revision(schema_gate.script_directory(migration)),)

    monkeypatch.setattr(dependencies, "markets_client", markets_client)

    state = schema_gate.schema_state(read_applied=read_applied)

    assert not state.behind
    assert markets_waits == [dependencies.JOB_WAKE_SECONDS]
    assert metatables_waits == [dependencies.JOB_WAKE_SECONDS]
    # The longer wait ends with the gate; nothing else in the process inherits it.
    assert endpoint.discovery_budget() == endpoint.DISCOVERY_TIMEOUT


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

    from mainsequence.client import ResourceRelease

    monkeypatch.setenv("APP_NAME", RELEASE_UID)
    asked, released, woken = threading.Event(), threading.Event(), threading.Event()
    client = SimpleNamespace(release_uid=RELEASE_UID)

    def from_environment(cls):
        asked.set()
        released.wait(5)
        return client

    def resolve_runtime_access(self, **kwargs):
        assert self.uid == RELEASE_UID
        woken.set()
        return runtime_access("waking")

    monkeypatch.setattr("msm.client.MarketsClient.from_environment", classmethod(from_environment))
    monkeypatch.setattr(ResourceRelease, "resolve_runtime_access", resolve_runtime_access)

    started = time.monotonic()
    prewarm_dependencies()
    assert time.monotonic() - started < 1
    assert asked.wait(2)

    backend = active_remote_backend()
    assert isinstance(backend, MarketsApiBackend)
    released.set()
    assert backend.client is client
    # Finding the release no longer asks for its access (ms-markets 6.4.0); the prewarm asks once.
    assert woken.wait(2)


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
            lambda monkeypatch: markets_call_error(monkeypatch, "waking"),
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
    """Through the SDK's request identity, which reads the caller's deadline."""
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
    with patch(
        CREATE_ACCOUNT, side_effect=markets_call_error(monkeypatch, "failed", retry_after_ms=None)
    ):
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
