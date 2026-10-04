"""Callers for the Alpaca Connectors API's request identity.

`local_caller` is a local launch: a direct Bearer token that the SDK checks against
`MAINSEQUENCE_ENDPOINT/api/v1/users/me/`, served here by a loopback stand-in for the backend.
`hosted_caller` is a hosted deployment: the environment the platform gives the FastAPI release of
this API, and caller assertions signed by a test Ed25519 key. Only key discovery is replaced; the
SDK verifier still reads its issuer, key URL, release and Environment from the environment.
`api_client` calls the deployed application as the local caller.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from mainsequence.server.caller_assertions import (
    ASSERTION_HEADER,
    ASSERTION_TYPE,
    CallerAssertionVerifier,
)

CALLER_UID = "33333333-3333-4333-8333-333333333333"
OTHER_CALLER_UID = "66666666-6666-4666-8666-666666666666"
LOCAL_TOKEN = "local-session-token"
OTHER_LOCAL_TOKEN = "other-local-session-token"
RELEASE_UID = "44444444-4444-4444-8444-444444444444"
ENVIRONMENT_UID = "55555555-5555-4555-8555-555555555555"
ISSUER = "https://backend.example.test"
KEY_ID = "test-caller-key"

# Any of these makes the SDK and the platform launcher treat the process as hosted.
HOSTED_MARKERS = (
    "APP_NAME",
    "FASTAPI_PUBLIC_BASE_URL",
    "MAINSEQUENCE_CALLER_ASSERTION_ISSUER",
    "MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL",
)
IDENTITY_ENVIRONMENT = (
    *HOSTED_MARKERS,
    "MAINSEQUENCE_CALLER_AUTH_MODE",
    "MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID",
    "FASTAPI_PUBLIC_INGRESS",
)
# What the backend sets on the FastAPI deployment of this API, whose workflow declaration has no
# public_ingress (tdag-django run_knative_fastapi and caller_runtime_environment).
HOSTED_ENVIRONMENT = {
    "FASTAPI_PUBLIC_BASE_URL": "https://alpaca-connectors-api.example.test",
    "FASTAPI_PUBLIC_INGRESS": "[]",
    "APP_NAME": RELEASE_UID,
    "MAINSEQUENCE_CALLER_AUTH_MODE": "assertion",
    "MAINSEQUENCE_CALLER_ASSERTION_ISSUER": ISSUER,
    "MAINSEQUENCE_CALLER_ASSERTION_JWKS_URL": ISSUER + "/fastapi/caller-keys/",
    "MAINSEQUENCE_ORGANIZATION_ENVIRONMENT_UID": ENVIRONMENT_UID,
}

# The SDK reads these when an application is built. Clear them before any test module imports the
# API, so the module's application is built in local mode whatever the shell exports. A test that
# simulates a deployment sets them itself and builds its own application.
for _name in IDENTITY_ENVIRONMENT:
    os.environ.pop(_name, None)


@pytest.fixture
def clean_identity_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from a local process: no deployment markers, no backend, no public routes."""

    for name in (*IDENTITY_ENVIRONMENT, "MAINSEQUENCE_ENDPOINT", "ALPACA_CONNECTORS_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)


@dataclass
class LocalCaller:
    uid: str
    headers: dict[str, str]
    other_uid: str = OTHER_CALLER_UID
    other_headers: dict[str, str] = field(
        default_factory=lambda: {"Authorization": f"Bearer {OTHER_LOCAL_TOKEN}"}
    )
    backend_status: int = 200
    authorizations: list[str | None] = field(default_factory=list)


class _UsersMe(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        caller: LocalCaller = self.server.caller  # type: ignore[attr-defined]
        authorization = self.headers.get("Authorization")
        caller.authorizations.append(authorization)
        users = {
            f"Bearer {LOCAL_TOKEN}": caller.uid,
            f"Bearer {OTHER_LOCAL_TOKEN}": caller.other_uid,
        }
        if self.path != "/api/v1/users/me/":
            status, body = 404, {"detail": "Not found."}
        elif caller.backend_status != 200:
            status, body = caller.backend_status, {"detail": "Backend status for the test."}
        elif authorization not in users:
            status, body = 401, {"detail": "Given token not valid for any token type"}
        else:
            status, body = 200, {"uid": users[authorization], "username": "operator"}
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        pass


@pytest.fixture
def local_caller(monkeypatch: pytest.MonkeyPatch, clean_identity_environment: None):
    """Serve `/api/v1/users/me/` for the SDK's local request identity.

    Without deployment markers the SDK checks the caller's direct Bearer token against
    `MAINSEQUENCE_ENDPOINT/api/v1/users/me/`. This backend knows two tokens and two users.
    """

    caller = LocalCaller(uid=CALLER_UID, headers={"Authorization": f"Bearer {LOCAL_TOKEN}"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UsersMe)
    server.caller = caller  # type: ignore[attr-defined]
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    monkeypatch.setenv("MAINSEQUENCE_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    try:
        yield caller
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def api_client(local_caller: LocalCaller) -> TestClient:
    """Call the deployed application as the caller the SDK's local request identity accepts."""

    from alpaca_connectors.api.app.main import app

    return TestClient(app, headers=local_caller.headers)


@dataclass
class HostedCaller:
    uid: str
    sign: Callable[..., str]
    forge: Callable[..., str]
    keys_available: bool = True

    def headers(self, **claims: object) -> dict[str, str]:
        return {ASSERTION_HEADER: self.sign(**claims)}


def _assertion(key: ed25519.Ed25519PrivateKey, user: str, **changes: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": f"urn:mainsequence:fapi:{RELEASE_UID}",
        "sub": user,
        "resource_release_uid": RELEASE_UID,
        "organization_environment_uid": ENVIRONMENT_UID,
        "iat": now,
        "nbf": now,
        "exp": now + 120,
    }
    claims.update(changes)
    return jwt.encode(
        claims,
        key,
        algorithm="EdDSA",
        headers={"kid": KEY_ID, "typ": ASSERTION_TYPE},
    )


@pytest.fixture
def hosted_caller(monkeypatch: pytest.MonkeyPatch, clean_identity_environment: None):
    for name, value in HOSTED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    platform_key = ed25519.Ed25519PrivateKey.generate()
    forger_key = ed25519.Ed25519PrivateKey.generate()
    public = jwt.algorithms.OKPAlgorithm.to_jwk(platform_key.public_key(), as_dict=True)
    published = {
        "keys": [
            {
                "kid": KEY_ID,
                "alg": "EdDSA",
                "use": "sig",
                **{name: public[name] for name in ("kty", "crv", "x")},
            }
        ]
    }
    caller = HostedCaller(
        uid=CALLER_UID,
        sign=lambda user=CALLER_UID, **claims: _assertion(platform_key, user, **claims),
        forge=lambda user=CALLER_UID, **claims: _assertion(forger_key, user, **claims),
    )

    def discover_keys() -> dict[str, object]:
        if not caller.keys_available:
            raise ConnectionError("caller key discovery is unavailable")
        return published

    from_environment = CallerAssertionVerifier.from_environment.__func__
    monkeypatch.setattr(
        CallerAssertionVerifier,
        "from_environment",
        classmethod(lambda cls: from_environment(cls, fetch_jwks=discover_keys)),
    )
    return caller
