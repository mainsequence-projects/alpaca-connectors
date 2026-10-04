"""The Alpaca Connectors API authenticates every caller through the SDK request identity."""

from __future__ import annotations

import json
import os
import runpy
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from alpaca_connectors.api.app import main as api_main
from alpaca_connectors.api.app.identity import bound_caller_uid, caller_uid
from alpaca_connectors.api.app.main import create_app
from alpaca_connectors.api.app.schemas import ProjectConfigurationResponse
from alpaca_connectors.operations.asset_registration import registration_steps
from mainsequence.server.caller_assertions import ASSERTION_HEADER, CallerAssertionUnavailable
from mainsequence.server.fastapi import install_request_identity

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".mainsequence/workflows/alpaca-connectors-api.yaml"
# The platform launcher and the SDK treat a process with any of these set as hosted.
HOSTED_MARKERS = (
    "APP_NAME",
    "FASTAPI_PUBLIC_BASE_URL",
    "MAINSEQUENCE_CALLER_ASSERTION_ISSUER",
    "MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL",
)
CALLER_UID = "33333333-3333-4333-8333-333333333333"
OTHER_UID = "66666666-6666-4666-8666-666666666666"
SITE_ORIGIN = "https://alpaca.site-dev.main-sequence.app"
REGISTRATION = {
    "uid": "account-uid",
    "account_uid": "account-uid",
    "unique_identifier": "123__ALPACA_PAPER",
    "account_name": "Paper",
    "is_paper": True,
    "account_is_active": True,
    "credential_source": "external",
    "api_key_secret_name": "ALPACA_API_KEY",
    "secret_key_secret_name": "ALPACA_SECRET_KEY",
}
EXTERNAL_CREDENTIALS = {
    "source": "external",
    "api_key_secret_name": "ALPACA_API_KEY",
    "secret_key_secret_name": "ALPACA_SECRET_KEY",
}


def launcher_check(app: FastAPI, *, public_ingress: tuple[tuple[str, str], ...]) -> None:
    """The check the platform's FastAPI launcher runs before it serves an application.

    A copy of PodDeploymentOrchestrator's ``fastapi_auth.require_request_identity`` (3057764).
    The launcher does not import the SDK; it compares the application's declaration with the
    deployment's own mode and ``FASTAPI_PUBLIC_INGRESS`` routes.
    """

    declaration = getattr(app.state, "mainsequence_request_identity", None)
    if not isinstance(declaration, dict) or declaration.get("installed") is not True:
        raise RuntimeError("Application request identity is not installed.")
    hosted = any(os.environ.get(name) for name in HOSTED_MARKERS)
    expected_mode = os.environ.get("MAINSEQUENCE_CALLER_AUTH_MODE") or (
        "assertion" if hosted else "local"
    )
    if (
        expected_mode not in {"assertion", "local"}
        or (hosted and expected_mode != "assertion")
        or declaration.get("mode") != expected_mode
    ):
        raise RuntimeError("Application caller authentication mode conflicts with deployment.")
    if declaration.get("public_ingress") != tuple(public_ingress):
        raise RuntimeError("Application caller public-ingress policy conflicts with deployment.")


def caller_probe(app: FastAPI) -> list[str]:
    """Mount a route that reports the caller the API's identity module resolves."""

    resolved: list[str] = []

    @app.get("/test/caller")
    def caller(request: Request) -> dict[str, str | None]:
        uid = caller_uid()
        resolved.append(uid)
        return {"uid": uid, "authorization": request.headers.get("authorization")}

    return resolved


def anonymous_requests(app: FastAPI) -> list[tuple[str, str]]:
    """One request per method of every route, with placeholder path parameters."""

    return [
        (
            method,
            route.path_format.format(
                **{name: str(uuid.uuid4()) for name in route.param_convertors}
            ),
        )
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in sorted(route.methods or ())
    ]


@pytest.fixture(params=["local", "hosted"])
def callers(request: pytest.FixtureRequest) -> SimpleNamespace:
    """Two distinct verified callers in the requested mode."""

    if request.param == "local":
        local = request.getfixturevalue("local_caller")
        return SimpleNamespace(
            mode="local",
            uid=local.uid,
            headers=lambda: local.headers,
            other_uid=local.other_uid,
            other_headers=lambda: local.other_headers,
        )
    hosted = request.getfixturevalue("hosted_caller")
    return SimpleNamespace(
        mode="hosted",
        uid=hosted.uid,
        headers=hosted.headers,
        other_uid=OTHER_UID,
        other_headers=lambda: hosted.headers(user=OTHER_UID),
    )


# Declaration -----------------------------------------------------------------------------------


def test_local_launch_declares_local_request_identity(clean_identity_environment) -> None:
    app = create_app()

    assert app.state.mainsequence_request_identity == {
        "installed": True,
        "mode": "local",
        "public_ingress": (),
    }
    launcher_check(app, public_ingress=())


def test_hosted_deployment_declares_assertion_request_identity(hosted_caller) -> None:
    app = create_app()

    assert app.state.mainsequence_request_identity == {
        "installed": True,
        "mode": "assertion",
        "public_ingress": (),
    }
    launcher_check(app, public_ingress=())


def test_deployment_markers_select_assertion_mode_without_the_mode_variable(
    hosted_caller, monkeypatch
) -> None:
    monkeypatch.delenv("MAINSEQUENCE_CALLER_AUTH_MODE")

    app = create_app()

    assert app.state.mainsequence_request_identity["mode"] == "assertion"
    launcher_check(app, public_ingress=())


@pytest.mark.parametrize("marker", HOSTED_MARKERS)
def test_a_deployment_marker_without_trust_configuration_fails_creation(
    clean_identity_environment, monkeypatch, marker
) -> None:
    value = OTHER_UID if marker == "APP_NAME" else "https://backend.example.test/"
    monkeypatch.setenv(marker, value)

    with pytest.raises(CallerAssertionUnavailable):
        create_app()


def test_a_hosted_deployment_cannot_select_local_mode(hosted_caller, monkeypatch) -> None:
    monkeypatch.setenv("MAINSEQUENCE_CALLER_AUTH_MODE", "local")

    with pytest.raises(RuntimeError, match="caller authentication mode"):
        create_app()


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("[]", ()),
        (json.dumps([{"method": "GET", "path": "/health"}]), (("GET", "/health"),)),
    ],
)
def test_public_ingress_is_exactly_what_the_deployment_declares(
    hosted_caller, monkeypatch, declared, expected
) -> None:
    monkeypatch.setenv("FASTAPI_PUBLIC_INGRESS", declared)

    app = create_app()

    assert app.state.mainsequence_request_identity["public_ingress"] == expected
    launcher_check(app, public_ingress=expected)
    client = TestClient(app)
    anonymous_health = client.get("/health", headers={"X-Public-Ingress": "1"})
    anonymous_capabilities = client.get(
        "/v1/project-state/capabilities", headers={"X-Public-Ingress": "1"}
    )
    assert anonymous_health.status_code == (200 if expected else 401)
    assert anonymous_capabilities.status_code == 401


def test_the_workflow_declares_no_public_ingress() -> None:
    resources = yaml.safe_load(WORKFLOW.read_text())["resources"]
    api = next(resource for resource in resources if resource["kind"] == "fastapi")

    assert api["spec"]["source_path"] == "api/app/main.py"
    assert not api["spec"].get("public_ingress")


def test_each_created_application_installs_request_identity_exactly_once(
    clean_identity_environment,
) -> None:
    first, second = create_app(), create_app()

    assert first is not second
    for app in (first, second):
        assert app.state.mainsequence_request_identity["installed"] is True
        with pytest.raises(RuntimeError, match="already installed"):
            install_request_identity(app)


def test_the_deployed_entry_point_serves_the_application_with_request_identity() -> None:
    deployed = runpy.run_path(str(ROOT / "api/app/main.py"))["app"]

    assert deployed is api_main.app
    assert deployed.state.mainsequence_request_identity["installed"] is True
    modules = [middleware.cls.__module__ for middleware in deployed.user_middleware]
    assert modules.count("mainsequence.server.fastapi") == 1


def test_cors_wraps_the_request_identity_which_wraps_the_application_middleware(
    clean_identity_environment, monkeypatch
) -> None:
    monkeypatch.setenv("ALPACA_CONNECTORS_CORS_ORIGINS", "https://*.site-dev.main-sequence.app")

    app = create_app()

    # Starlette runs user_middleware in list order, outermost first.
    assert [middleware.cls.__name__ for middleware in app.user_middleware] == [
        "CORSMiddleware",
        "_RequestIdentityMiddleware",
        "BaseHTTPMiddleware",
    ]


# Caller resolution -----------------------------------------------------------------------------


def test_hosted_caller_is_the_subject_of_the_signed_assertion(hosted_caller) -> None:
    app = create_app()
    resolved = caller_probe(app)

    response = TestClient(app).get(
        "/test/caller",
        headers={**hosted_caller.headers(), "X-User-UID": OTHER_UID},
    )

    assert response.status_code == 200
    assert response.json()["uid"] == hosted_caller.uid
    assert resolved == [hosted_caller.uid]


def test_local_caller_is_the_user_of_the_bearer_token(local_caller) -> None:
    app = create_app()
    resolved = caller_probe(app)

    response = TestClient(app).get("/test/caller", headers=local_caller.headers)

    assert response.status_code == 200
    # The SDK removes the token before the application sees the request.
    assert response.json() == {"uid": local_caller.uid, "authorization": None}
    assert resolved == [local_caller.uid]
    assert local_caller.authorizations == [local_caller.headers["Authorization"]]


def test_no_caller_is_bound_outside_a_request() -> None:
    assert bound_caller_uid() is None
    with pytest.raises(HTTPException) as error:
        caller_uid()

    assert error.value.status_code == 401
    assert error.value.detail["code"] == "unauthenticated"


# Rejection -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "uid_header_only",
        "not_a_token",
        "unpublished_key",
        "other_release",
        "other_environment",
        "expired",
        "numeric_user_id",
        "local_bearer_token",
    ],
)
def test_hosted_requests_without_a_valid_assertion_never_reach_a_handler(
    hosted_caller, case
) -> None:
    now = int(time.time())
    if case == "missing":
        headers = {}
    elif case == "uid_header_only":
        headers = {"X-User-UID": hosted_caller.uid}
    elif case == "not_a_token":
        headers = {ASSERTION_HEADER: "not-a-token"}
    elif case == "unpublished_key":
        headers = {ASSERTION_HEADER: hosted_caller.forge()}
    elif case == "other_release":
        headers = hosted_caller.headers(resource_release_uid=OTHER_UID)
    elif case == "other_environment":
        headers = hosted_caller.headers(organization_environment_uid=OTHER_UID)
    elif case == "expired":
        headers = hosted_caller.headers(iat=now - 600, nbf=now - 600, exp=now - 480)
    elif case == "numeric_user_id":
        headers = {**hosted_caller.headers(), "X-User-ID": "7"}
    else:
        headers = {"Authorization": "Bearer local-session-token"}
    app = create_app()
    resolved = caller_probe(app)

    client = TestClient(app)
    statuses = [
        client.get("/test/caller", headers=headers).status_code,
        client.get("/health", headers=headers).status_code,
    ]

    assert statuses == [401, 401]
    assert resolved == []


def test_hosted_requests_get_503_while_caller_keys_are_unavailable(hosted_caller) -> None:
    hosted_caller.keys_available = False
    app = create_app()
    resolved = caller_probe(app)

    response = TestClient(app).get("/test/caller", headers=hosted_caller.headers())

    assert response.status_code == 503
    assert resolved == []


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "uid_header_only",
        "basic_scheme",
        "rejected_token",
        "conflicting_uid_header",
        "numeric_user_id",
    ],
)
def test_local_requests_without_a_valid_bearer_token_never_reach_a_handler(
    local_caller, case
) -> None:
    headers = {
        "missing": {},
        "uid_header_only": {"X-User-UID": local_caller.uid},
        "basic_scheme": {"Authorization": "Basic dGVzdDp0ZXN0"},
        "rejected_token": {"Authorization": "Bearer expired-session-token"},
        "conflicting_uid_header": {**local_caller.headers, "X-User-UID": OTHER_UID},
        "numeric_user_id": {**local_caller.headers, "X-User-ID": "7"},
    }[case]
    app = create_app()
    resolved = caller_probe(app)

    client = TestClient(app)
    statuses = [
        client.get("/test/caller", headers=headers).status_code,
        client.get("/health", headers=headers).status_code,
    ]

    assert statuses == [401, 401]
    assert resolved == []


def test_local_requests_get_503_while_the_backend_cannot_authenticate(
    local_caller, monkeypatch
) -> None:
    app = create_app()
    resolved = caller_probe(app)

    client = TestClient(app)
    local_caller.backend_status = 502
    backend_failing = client.get("/test/caller", headers=local_caller.headers)
    monkeypatch.delenv("MAINSEQUENCE_ENDPOINT")
    backend_unset = client.get("/test/caller", headers=local_caller.headers)

    assert (backend_failing.status_code, backend_unset.status_code) == (503, 503)
    assert resolved == []


@pytest.mark.parametrize("mode", ["local", "hosted"])
def test_no_route_admits_an_anonymous_request(request, mode) -> None:
    request.getfixturevalue("local_caller" if mode == "local" else "hosted_caller")
    app = create_app()
    calls = [*anonymous_requests(app), ("GET", "/openapi.json"), ("GET", "/docs")]

    client = TestClient(app)
    admitted = [
        (method, path)
        for method, path in calls
        if client.request(method, path, headers={"X-Public-Ingress": "1"}).status_code != 401
    ]

    assert len(calls) > 80
    assert admitted == []


# What the caller decides -----------------------------------------------------------------------


def test_account_writes_record_the_verified_caller_as_credential_author(callers) -> None:
    with (
        patch(
            "alpaca_connectors.api.app.routers.accounts.create_account_registration",
            return_value=REGISTRATION,
        ) as create,
        patch(
            "alpaca_connectors.api.app.routers.accounts.update_account",
            return_value=REGISTRATION,
        ) as update,
    ):
        client = TestClient(create_app())
        created = client.post(
            "/v1/accounts",
            json={"environment": "paper", "credentials": EXTERNAL_CREDENTIALS},
            headers=callers.headers(),
        )
        updated = client.patch(
            "/v1/accounts/account-uid",
            json={"credentials": EXTERNAL_CREDENTIALS},
            headers=callers.other_headers(),
        )
        anonymous = client.post(
            "/v1/accounts", json={"environment": "paper", "credentials": EXTERNAL_CREDENTIALS}
        )

    assert (created.status_code, updated.status_code, anonymous.status_code) == (201, 200, 401)
    assert create.call_count == 1
    assert create.call_args.kwargs["actor_user_uid"] == callers.uid
    assert update.call_args.kwargs["actor_user_uid"] == callers.other_uid


def test_registration_operations_belong_to_the_verified_caller(callers) -> None:
    operations: dict[str, SimpleNamespace] = {}

    def create_operation(*, action, request, owner_uid):
        now = datetime.now(UTC)
        operation = SimpleNamespace(
            uid=str(uuid.uuid4()),
            owner_uid=owner_uid,
            action=action,
            status="queued",
            current_step=None,
            steps=registration_steps(action),
            request=request,
            result=None,
            error=None,
            created_at=now,
            started_at=None,
            updated_at=now,
            completed_at=None,
        )
        operations[operation.uid] = operation
        return operation

    with (
        patch(
            "alpaca_connectors.api.app.services.assets.create_asset_registration_operation",
            side_effect=create_operation,
        ),
        patch(
            "alpaca_connectors.api.app.services.assets.get_asset_registration_operation",
            side_effect=operations.get,
        ),
        patch("alpaca_connectors.api.app.routers.assets.run_asset_registration_operation"),
    ):
        client = TestClient(create_app())
        started = client.post(
            "/v1/assets/registration/operations",
            json={"action": "plan", "request": {"account_uid": "account-uid", "symbols": ["AAPL"]}},
            headers=callers.headers(),
        )
        path = f"/v1/assets/registration/operations/{started.json()['operation_uid']}"
        owner_poll = client.get(path, headers=callers.headers())
        other_poll = client.get(path, headers=callers.other_headers())

    assert started.status_code == 202
    assert [operation.owner_uid for operation in operations.values()] == [callers.uid]
    assert owner_poll.status_code == 200
    assert other_poll.status_code == 404


def test_project_configuration_reports_the_verified_caller(callers) -> None:
    with patch(
        "alpaca_connectors.api.app.routers.project_state.get_project_configuration",
        side_effect=lambda *, request_user_uid: ProjectConfigurationResponse(
            supported_component_providers=[],
            migrated_market_data_profiles=[],
            request_user_uid=request_user_uid,
        ),
    ):
        response = TestClient(create_app()).get(
            "/v1/project-state/configuration", headers=callers.headers()
        )

    assert response.status_code == 200
    assert response.json()["request_user_uid"] == callers.uid


def test_discovery_cache_validators_are_scoped_to_the_verified_caller(callers) -> None:
    client = TestClient(create_app())

    first = client.get("/v1/assets/discovery", headers=callers.headers())
    again = client.get("/v1/assets/discovery", headers=callers.headers())
    other = client.get("/v1/assets/discovery", headers=callers.other_headers())

    assert {first.status_code, again.status_code, other.status_code} == {200}
    assert first.headers["etag"] == again.headers["etag"]
    assert first.headers["etag"] != other.headers["etag"]
    assert first.headers["cache-control"] == "private, max-age=0, must-revalidate"


def test_allowed_origins_can_read_identity_rejections_and_preflights_need_no_caller(
    local_caller, monkeypatch
) -> None:
    monkeypatch.setenv("ALPACA_CONNECTORS_CORS_ORIGINS", "https://*.site-dev.main-sequence.app")
    client = TestClient(create_app())

    preflight = client.options(
        "/v1/accounts",
        headers={
            "Origin": SITE_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    anonymous = client.get("/v1/accounts/discovery", headers={"Origin": SITE_ORIGIN})
    authenticated = client.get(
        "/v1/accounts/discovery", headers={"Origin": SITE_ORIGIN, **local_caller.headers}
    )
    foreign = client.get("/v1/accounts/discovery", headers={"Origin": "https://example.test"})

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == SITE_ORIGIN
    assert anonymous.status_code == 401
    assert anonymous.headers["access-control-allow-origin"] == SITE_ORIGIN
    assert authenticated.status_code == 200
    assert authenticated.headers["access-control-allow-origin"] == SITE_ORIGIN
    assert foreign.status_code == 401
    assert "access-control-allow-origin" not in foreign.headers
