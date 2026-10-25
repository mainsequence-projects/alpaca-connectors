"""Use the installed TAU application adapter against the real local Alpaca MCP over HTTP.

Only the platform's release-access grant and model/provider boundary are mocked. Identity,
transport negotiation, skill discovery, digest verification and resource confinement are real.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anyio
import httpx
import pytest
import uvicorn
from ms_tau_sdk.backend.mcp import MCPConnectionClient
from ms_tau_sdk.backend.models import MCPApplication
from ms_tau_sdk.settings import TauSDKSettings
from ms_tau_sdk.tools.mcp_applications import application_connector, create_mcp_application_tools

from alpaca_connectors.agent_tools import PROJECT_AGENT_TOOL_NAMES

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / ".tau/skills"
SKILL_URIS = {f"skill://{path.parent.name}/SKILL.md" for path in SKILLS_ROOT.glob("*/SKILL.md")}


@pytest.fixture
def alpaca_mcp_http(local_caller):
    from alpaca_connectors.api.app.main import create_app

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        with patch("alpaca_connectors.runtime.start_markets_engine"):
            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(), loop="asyncio", ws="none", log_level="warning", access_log=False
                )
            )
            thread = threading.Thread(
                target=server.run, kwargs={"sockets": [listener]}, daemon=True
            )
            thread.start()
            try:
                deadline = time.monotonic() + 5
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert server.started, "The test MCP HTTP server did not start."
                yield f"http://127.0.0.1:{port}"
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                assert not thread.is_alive(), "The test MCP HTTP server did not stop."


def application_tools(tmp_path):
    settings = TauSDKSettings(
        _env_file=None,
        workspace=REPO_ROOT,
        tau_state_home=tmp_path,
        exclude_base_tools=True,
        exclude_mainsequence_mcp=True,
    )
    return {
        tool.name: tool
        for tool in create_mcp_application_tools(
            [MCPApplication(name="alpaca", resource_release_uid="test-alpaca-release")],
            connect=application_connector(settings),
            tool_timeout_seconds=5,
            open_timeout_seconds=5,
        )
    }


def test_tau_discovers_and_verifies_all_six_published_skills(
    local_caller, alpaca_mcp_http, tmp_path
):
    tools = application_tools(tmp_path)
    access = SimpleNamespace(
        rpc_url=httpx.URL(alpaca_mcp_http),
        token=local_caller.headers["Authorization"].removeprefix("Bearer "),
        renew=AsyncMock(),
    )

    async def exercise():
        catalog = await tools["alpaca__list_tools"].execute_fn("list-tools", {})
        assert catalog.details["is_error"] is False
        assert tuple(catalog.details["tools"]) == PROJECT_AGENT_TOOL_NAMES
        assert "alpaca__list_resources" in catalog.content[0].text

        listing = await tools["alpaca__list_resources"].execute_fn("list-skills", {})
        assert listing.details["is_error"] is False
        assert set(listing.details["skills"]) == SKILL_URIS
        assert len(listing.details["skills"]) == 6
        assert listing.details["resources"] == []

        for uri in sorted(SKILL_URIS):
            result = await tools["alpaca__read_resource"].execute_fn("read-skill", {"uri": uri})
            assert result.details == {
                "application": "alpaca",
                "is_error": False,
                "uri": uri,
                "skill": uri,
                "verified": True,
            }
            name = uri.removeprefix("skill://").split("/")[0]
            assert result.content[1].text.encode() == (SKILLS_ROOT / name / "SKILL.md").read_bytes()

        for uri, failure in (
            ("skill://ms_tau_sdk/tau_project_customization/SKILL.md", "not_published"),
            (
                "skill://mainsequence/maintenance/code_repository_maintenance/SKILL.md",
                "not_published",
            ),
            ("skill://alpaca-account-workflow/../../pyproject.toml", "not_listed"),
        ):
            denied = await tools["alpaca__read_resource"].execute_fn("read-private", {"uri": uri})
            assert denied.details["is_error"] is True
            assert denied.details["failure"] == failure

    with patch(
        "ms_tau_sdk.tools.mcp_applications._turn_application_access",
        new=AsyncMock(return_value=access),
    ):
        anyio.run(exercise)
    assert local_caller.authorizations
    assert set(local_caller.authorizations) == {local_caller.headers["Authorization"]}


def test_tau_never_returns_skill_content_with_a_mismatched_digest(
    local_caller, alpaca_mcp_http, tmp_path
):
    tools = application_tools(tmp_path)
    access = SimpleNamespace(
        rpc_url=httpx.URL(alpaca_mcp_http),
        token=local_caller.headers["Authorization"].removeprefix("Bearer "),
        renew=AsyncMock(),
    )
    read_resource = MCPConnectionClient.read_resource
    unverified_text = "Unverified replacement instructions must not enter model context."

    async def altered_read(client, uri):
        response = await read_resource(client, uri)
        response.contents[0].text = unverified_text
        return response

    async def exercise():
        result = await tools["alpaca__read_resource"].execute_fn(
            "read-altered", {"uri": "skill://alpaca-account-workflow/SKILL.md"}
        )
        assert result.details["is_error"] is True
        assert result.details["failure"] == "unverified"
        assert unverified_text not in str(result)

    with (
        patch(
            "ms_tau_sdk.tools.mcp_applications._turn_application_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(MCPConnectionClient, "read_resource", new=altered_read),
    ):
        anyio.run(exercise)


def test_application_calls_do_not_reuse_another_callers_access(
    local_caller, alpaca_mcp_http, tmp_path
):
    from alpaca_connectors.api.app.identity import acting_user_uid

    tools = application_tools(tmp_path)
    access = SimpleNamespace(
        rpc_url=httpx.URL(alpaca_mcp_http),
        token=local_caller.headers["Authorization"].removeprefix("Bearer "),
        renew=AsyncMock(),
    )

    async def exercise():
        for uid, headers in (
            (local_caller.uid, local_caller.headers),
            (local_caller.other_uid, local_caller.other_headers),
        ):
            access.token = headers["Authorization"].removeprefix("Bearer ")
            result = await tools["alpaca__call_tool"].execute_fn(
                "read-state", {"tool": "alpaca_project_state", "arguments": {}}
            )
            assert result.details["is_error"] is False
            assert json.loads(result.content[0].text)["configuration"]["request_user_uid"] == uid

    with (
        patch(
            "ms_tau_sdk.tools.mcp_applications._turn_application_access",
            new=AsyncMock(return_value=access),
        ),
        patch(
            "alpaca_connectors.api.app.services.project_state.get_project_configuration",
            side_effect=lambda: {"request_user_uid": acting_user_uid()},
        ),
    ):
        anyio.run(exercise)
    assert set(local_caller.authorizations) == {
        local_caller.headers["Authorization"],
        local_caller.other_headers["Authorization"],
    }
