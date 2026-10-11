"""The API serves the Agent's `alpaca` MCP application at /mcp (ADR 0015)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from alpaca_connectors.agent_tools import PROJECT_AGENT_TOOL_NAMES, PROJECT_AGENT_TOOLS

PERSON_UID = "77777777-7777-4777-8777-777777777777"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
SUBMITTED_SECRET = "SECRET-LOOKING-VALUE-9911"


def requester(uid: str = PERSON_UID) -> dict[str, object]:
    """The platform's nested statement about the person an Agent's call works for."""
    return {"sub": uid, "team_uids": [], "is_organization_admin": False}


@pytest.fixture
def mcp_client() -> Iterator[TestClient]:
    """The application with its lifespan running, which starts the MCP session manager."""
    from alpaca_connectors.api.app.main import create_app

    with patch("alpaca_connectors.runtime.start_markets_engine"):
        with TestClient(create_app()) as client:
            yield client


def rpc(client: TestClient, method: str, params: dict, headers: dict[str, str]):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={**MCP_HEADERS, **headers},
    )


def call_tool(client: TestClient, name: str, arguments: dict, headers: dict[str, str]):
    response = rpc(client, "tools/call", {"name": name, "arguments": arguments}, headers)
    assert response.status_code == 200
    result = response.json()["result"]
    return result, json.loads(result["content"][0]["text"])


def test_create_app_installs_mcp_inside_the_request_identity(clean_identity_environment) -> None:
    from alpaca_connectors.api.app.main import create_app

    app = create_app()

    assert app.state.mainsequence_request_identity["installed"] is True
    assert app.state.mainsequence_mcp["path"] == "/mcp"
    assert app.state.mainsequence_mcp["stateless"] is True


def test_mcp_lists_the_project_tool_catalog(local_caller, mcp_client) -> None:
    response = rpc(mcp_client, "tools/list", {}, local_caller.headers)

    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert tuple(tool["name"] for tool in tools) == PROJECT_AGENT_TOOL_NAMES
    by_name = {tool["name"]: tool for tool in tools}
    for definition in PROJECT_AGENT_TOOLS:
        listed = by_name[definition.name]
        assert listed["inputSchema"] == definition.parameters
        assert listed["annotations"]["readOnlyHint"] is (definition.execution_mode == "parallel")


def test_mcp_refuses_a_request_without_an_authenticated_caller(local_caller, mcp_client) -> None:
    assert rpc(mcp_client, "tools/list", {}, {}).status_code == 401


def test_key_requests_belong_to_the_person_an_agent_call_works_for(hosted_caller, mcp_client):
    created: list[dict] = []

    def start(**kwargs):
        created.append(kwargs)
        return {"key_request": {"uid": "request-uid"}, "site_url": None, "instructions": "..."}

    with patch(
        "alpaca_connectors.api.app.services.account_key_requests.start_registration_key_request",
        side_effect=start,
    ):
        # The Agent is the caller; the platform names the person as the requester.
        agent_call, _ = call_tool(
            mcp_client,
            "alpaca_account_key_request",
            {"operation": "start_registration", "environment": "live", "account_name": "Main"},
            hosted_caller.headers(requester=requester()),
        )
        # A person calling directly is both the caller and the acting user.
        direct_call, _ = call_tool(
            mcp_client,
            "alpaca_account_key_request",
            {"operation": "start_registration"},
            hosted_caller.headers(),
        )

    assert agent_call["isError"] is False and direct_call["isError"] is False
    assert created == [
        {"requester_uid": PERSON_UID, "environment": "live", "account_name": "Main"},
        {"requester_uid": hosted_caller.uid, "environment": "paper", "account_name": None},
    ]


def test_mcp_tool_errors_never_echo_the_arguments(local_caller, mcp_client) -> None:
    schema_error, schema_body = call_tool(
        mcp_client,
        "alpaca_account_key_request",
        {"operation": "start_registration", "environment": SUBMITTED_SECRET},
        local_caller.headers,
    )
    handler_error, handler_body = call_tool(
        mcp_client,
        "alpaca_manage_account",
        {
            "operation": "create",
            "request": {
                "environment": "paper",
                "api_key_secret_name": SUBMITTED_SECRET,
                "secret_key_secret_name": "",
            },
        },
        local_caller.headers,
    )

    assert schema_error["isError"] is True
    assert schema_body["code"] == "invalid_request"
    assert handler_error["isError"] is True
    for result in (schema_error, handler_error):
        assert SUBMITTED_SECRET not in json.dumps(result)


@pytest.mark.parametrize("tool_name", ["alpaca_place_order", SUBMITTED_SECRET])
def test_mcp_reports_an_unknown_tool_without_echoing_its_name(
    local_caller, mcp_client, tool_name
) -> None:
    result, body = call_tool(mcp_client, tool_name, {}, local_caller.headers)

    assert result["isError"] is True
    assert body["code"] == "unknown_tool"
    assert tool_name not in json.dumps(result)
    assert body["retryable"] is False


def test_no_mcp_tool_accepts_alpaca_key_values() -> None:
    def property_names(schema: object) -> set[str]:
        if isinstance(schema, dict):
            names = set(schema.get("properties", {})) if "properties" in schema else set()
            return names.union(*(property_names(value) for value in schema.values()))
        if isinstance(schema, list):
            return set().union(*(property_names(item) for item in schema))
        return set()

    names = set().union(*(property_names(tool.parameters) for tool in PROJECT_AGENT_TOOLS))
    assert not {"api_key", "secret_key", "credentials"} & names
