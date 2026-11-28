"""A caller's trace context goes through the API to the Markets API (Main Sequence ADR 0042).

The SDK's request identity makes a request's ``traceparent`` and ``baggage`` its OpenTelemetry
context, and since ms-markets 6.4.0 each Markets call is sent inside the SDK's
``ResourceRelease.call``, which names the current span as the call's parent and carries the
baggage. What this application owns is everything in between: its routes, the MCP tools' worker
thread and the calls it makes together on a thread pool. Nothing here reaches the platform or a
Markets API: the platform's grant is faked, and the Markets API is an ``httpx.MockTransport``
behind ms-markets' own client.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry import baggage
from opentelemetry import context as otel_context
from opentelemetry.propagate import extract

MARKETS_RELEASE_UID = "77777777-7777-4777-8777-777777777777"
MARKETS_URL = "https://markets.example.test"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
CALLER_SPAN_ID = "00f067aa0ba902b7"
TRACE_HEADERS = {
    "traceparent": f"00-{TRACE_ID}-{CALLER_SPAN_ID}-01",
    "baggage": "mainsequence.advisor.person=p-17",
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
TRACEPARENT = re.compile(r"^00-(?P<trace>[0-9a-f]{32})-(?P<span>[0-9a-f]{16})-[0-9a-f]{2}$")


def granted_access():
    """The platform's answer for a Markets API that serves, built with the SDK's own model."""
    from mainsequence.client import ResourceReleaseRuntimeAccess

    return ResourceReleaseRuntimeAccess.model_validate(
        {
            "resource_release_uid": MARKETS_RELEASE_UID,
            "release_kind": "fastapi",
            "runtime_access": {"state": "ready", "can_request": True, "retry_after_ms": None},
            "runtime_presence": {
                "phase": "serving",
                "replicas": {"desired": 1, "actual": 1},
                "detail": "The release serves.",
            },
            "routing": {"state": "routable", "active_revision_uid": "revision"},
            "access": {"mode": "token", "rpc_url": MARKETS_URL, "token": "markets-token"},
        }
    )


@pytest.fixture
def markets(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """The Markets API as ms-markets' client reaches a release; returns the requests it was sent.

    Every row call is answered with no row, as for an Asset the registry does not hold.
    """
    from msm.api.remote import use_remote_backend
    from msm.client import MarketsClient

    from alpaca_connectors.dependencies import MarketsApiBackend
    from mainsequence.client import ResourceRelease
    from mainsequence.client.release_calls import release_runtime_cache

    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"result": None})

    monkeypatch.setattr(
        ResourceRelease, "resolve_runtime_access", lambda self, **kwargs: granted_access()
    )
    client = MarketsClient(
        release_uid=MARKETS_RELEASE_UID,
        session=httpx.Client(transport=httpx.MockTransport(answer)),
    )
    release_runtime_cache.clear()
    use_remote_backend(MarketsApiBackend(lambda: client))
    with patch("alpaca_connectors.runtime.start_markets_engine"):
        yield sent
    release_runtime_cache.clear()


def assert_in_the_callers_trace(request: httpx.Request) -> None:
    """The Markets call is a span of the caller's trace, not the caller's span, with its baggage."""
    assert str(request.url).startswith(MARKETS_URL)
    traceparent = TRACEPARENT.match(request.headers.get("traceparent", ""))
    assert traceparent is not None
    assert traceparent["trace"] == TRACE_ID
    assert traceparent["span"] != CALLER_SPAN_ID
    assert "mainsequence.advisor.person=p-17" in request.headers.get("baggage", "")


def test_a_rest_request_passes_its_trace_to_the_markets_api(api_client, markets) -> None:
    response = api_client.get(f"/v1/assets/{uuid.uuid4()}", headers=TRACE_HEADERS)

    assert response.status_code == 404
    assert len(markets) == 1
    assert_in_the_callers_trace(markets[0])


def test_a_request_without_trace_context_starts_its_own_trace(api_client, markets) -> None:
    api_client.get(f"/v1/assets/{uuid.uuid4()}")

    traceparent = TRACEPARENT.match(markets[0].headers.get("traceparent", ""))
    assert traceparent is not None
    assert traceparent["trace"] != TRACE_ID
    assert "mainsequence.advisor.person" not in markets[0].headers.get("baggage", "")


def call_query_assets(headers: dict[str, str]) -> httpx.Response:
    """``alpaca_query_assets`` over the API's MCP, with the lifespan's Markets API start replaced."""
    from alpaca_connectors.api.app.main import create_app

    with (
        patch("alpaca_connectors.dependencies.prewarm_dependencies"),
        TestClient(create_app()) as client,
    ):
        return client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "alpaca_query_assets",
                    "arguments": {"operation": "get", "asset_uid": str(uuid.uuid4())},
                },
            },
            headers={**MCP_HEADERS, **headers, **TRACE_HEADERS},
        )


def test_an_mcp_tool_passes_its_callers_trace_to_the_markets_api(local_caller, markets) -> None:
    """A tool runs on anyio's worker thread, which keeps the request's context."""
    response = call_query_assets(local_caller.headers)

    assert response.status_code == 200
    assert json.loads(response.json()["result"]["content"][0]["text"])["code"] == "not_found"
    assert len(markets) == 1
    assert_in_the_callers_trace(markets[0])


def test_a_hosted_caller_passes_its_trace_to_the_markets_api(hosted_caller, markets) -> None:
    """As in a deployment: the platform's signed assertion, here over REST and over MCP."""
    from alpaca_connectors.api.app.main import create_app

    rest = TestClient(create_app()).get(
        f"/v1/assets/{uuid.uuid4()}", headers={**hosted_caller.headers(), **TRACE_HEADERS}
    )
    mcp = call_query_assets(hosted_caller.headers())

    assert rest.status_code == 404
    assert mcp.status_code == 200
    assert len(markets) == 2
    for request in markets:
        assert_in_the_callers_trace(request)


@pytest.fixture
def callers_context() -> Iterator[None]:
    """The context the request identity attaches for ``TRACE_HEADERS``, outside a request."""
    token = otel_context.attach(extract(TRACE_HEADERS))
    try:
        yield
    finally:
        otel_context.detach(token)


def test_markets_calls_made_together_stay_in_the_callers_trace(markets, callers_context) -> None:
    from msm.api.assets import Asset

    from alpaca_connectors.markets_api import all_together

    all_together(*(lambda: Asset.get_by_uid(str(uuid.uuid4())) for _ in range(3)))

    assert len(markets) == 3
    for request in markets:
        assert_in_the_callers_trace(request)


def test_alpaca_asset_reads_made_together_keep_the_callers_context(callers_context) -> None:
    """Alpaca is no Main Sequence release and is sent no trace; the reads' own log lines are."""
    from alpaca_connectors.assets.alpaca_us_equities import read_alpaca_asset_records

    seen: list[object] = []

    class TradingClient:
        def get(self, path: str) -> None:
            seen.append(baggage.get_baggage("mainsequence.advisor.person"))
            return None

    read_alpaca_asset_records(TradingClient(), ["AAPL", "MSFT", "NVDA"])

    assert seen == ["p-17", "p-17", "p-17"]
