"""The API serves the Agent's `alpaca` MCP application at /mcp (ADR 0015)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from alpaca_connectors.agent_tools import PROJECT_AGENT_TOOL_NAMES, PROJECT_AGENT_TOOLS

PERSON_UID = "77777777-7777-4777-8777-777777777777"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
SUBMITTED_SECRET = "SECRET-LOOKING-VALUE-9911"
SKILLS_ROOT = Path(__file__).resolve().parents[1] / ".agents/skills/mcp_tools"
SKILL_NAMES = {
    "alpaca-account-workflow",
    "alpaca-asset-registration",
    "alpaca-etf-portfolios",
    "alpaca-etf-weight-signals",
    "alpaca-holdings-category",
    "alpaca-stock-bars",
}


def requester(uid: str = PERSON_UID, **claims: object) -> dict[str, object]:
    """The platform's identity-only statement about the person an Agent's call works for."""
    return {"sub": uid, "is_organization_admin": False, **claims}


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
        assert listed["inputSchema"]["type"] == "object"
        assert listed["inputSchema"] == definition.parameters
        assert listed["annotations"]["readOnlyHint"] is (definition.execution_mode == "parallel")


def test_mcp_preserves_legacy_initialization(local_caller, mcp_client) -> None:
    response = rpc(
        mcp_client,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "alpaca-upgrade-test", "version": "1"},
        },
        local_caller.headers,
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["serverInfo"]["name"] == "alpaca"
    assert "tools" in result["capabilities"]
    assert not result["capabilities"].get("extensions")


def test_modern_mcp_discovery_advertises_the_skills_extension(local_caller, mcp_client) -> None:
    response = rpc(
        mcp_client,
        "server/discover",
        {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {
                    "extensions": {"io.modelcontextprotocol/skills": {}}
                },
            }
        },
        {
            **local_caller.headers,
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "server/discover",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert "io.modelcontextprotocol/skills" in result["capabilities"]["extensions"]


def test_mcp_publishes_only_six_business_skills_with_exact_source_bytes(
    local_caller, mcp_client
) -> None:
    response = rpc(mcp_client, "skills/list", {}, local_caller.headers)
    assert response.status_code == 200
    skills = response.json()["result"]["skills"]
    assert {skill["frontmatter"]["name"] for skill in skills} == SKILL_NAMES
    assert len(skills) == 6
    resources = rpc(mcp_client, "resources/list", {}, local_caller.headers).json()["result"]
    assert {resource["uri"] for resource in resources["resources"]} == {
        f"skill://{name}/SKILL.md" for name in SKILL_NAMES
    }

    for skill in skills:
        name = skill["frontmatter"]["name"]
        uri = f"skill://{name}/SKILL.md"
        payload = (SKILLS_ROOT / name / "SKILL.md").read_bytes()
        assert skill["uri"] == uri
        assert skill["resources"] == [
            {
                "uri": uri,
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        ]
        manifest = rpc(mcp_client, "skills/get", {"uri": uri}, local_caller.headers)
        assert manifest.json()["result"]["skill"] == skill
        content = rpc(mcp_client, "resources/read", {"uri": uri}, local_caller.headers)
        assert content.json()["result"]["contents"][0]["text"].encode("utf-8") == payload


@pytest.mark.parametrize(
    "uri",
    [
        "skill://mainsequence/maintenance/code_repository_maintenance/SKILL.md",
        "skill://mainsequence_platform/agents/code_repository_to_agent/SKILL.md",
        "skill://ms_tau_sdk/tau_project_customization/SKILL.md",
        "skill://metatables/metatables-meta-tables/SKILL.md",
        "skill://alpaca-account-workflow/../../mainsequence/PINNED_FROM.txt",
    ],
)
@pytest.mark.parametrize("method", ["skills/get", "resources/read"])
def test_mcp_does_not_serve_private_skill_files(local_caller, mcp_client, method, uri) -> None:
    response = rpc(mcp_client, method, {"uri": uri}, local_caller.headers)
    assert response.status_code == 200
    assert "error" in response.json()
    assert "result" not in response.json()


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("skills/list", {}),
        ("skills/get", {"uri": "skill://alpaca-account-workflow/SKILL.md"}),
        ("resources/read", {"uri": "skill://alpaca-account-workflow/SKILL.md"}),
    ],
)
def test_mcp_skill_operations_require_authentication(local_caller, mcp_client, method, params):
    assert rpc(mcp_client, method, params, {}).status_code == 401


def test_published_skills_resolve_independently_of_the_working_directory(
    local_caller, monkeypatch, tmp_path
) -> None:
    from alpaca_connectors.api.app.main import create_app

    monkeypatch.chdir(tmp_path)
    with patch("alpaca_connectors.runtime.start_markets_engine"):
        with TestClient(create_app()) as client:
            result = rpc(client, "skills/list", {}, local_caller.headers).json()["result"]
    assert {skill["frontmatter"]["name"] for skill in result["skills"]} == SKILL_NAMES


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


def test_an_agent_call_whose_requester_lists_teams_is_refused(hosted_caller, mcp_client) -> None:
    """The requester statement is identity-only too (ADR-0052); a Team list is never trusted."""
    with patch(
        "alpaca_connectors.api.app.services.account_key_requests.start_registration_key_request",
        side_effect=AssertionError("a refused requester reached the tool"),
    ):
        response = rpc(
            mcp_client,
            "tools/call",
            {
                "name": "alpaca_account_key_request",
                "arguments": {"operation": "start_registration"},
            },
            hosted_caller.headers(requester=requester(team_uids=[])),
        )

    assert response.status_code == 401
