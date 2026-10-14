"""Exercise the installed TAU host with the Agent's declared tool restrictions.

Backend/model I/O is mocked; tool assembly and skill path validation use the real SDK.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest
import yaml
from ms_tau_sdk.backend.models import MCPApplication, ProviderControl, ProviderCredential
from ms_tau_sdk.resources.loader import tau_resource_paths
from ms_tau_sdk.runtime.manager import RUNTIME_CAPABILITIES, SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings
from ms_tau_sdk.tools.skill_read import create_skill_read_tool
from tau_coding.skills import load_skills_with_diagnostics
from tau_coding.tools import ToolInputError

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".mainsequence/workflows/alpaca-connectors-api.yaml"


def restricted_settings(tmp_path: Path) -> TauSDKSettings:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    agent = next(item for item in workflow["resources"] if item["kind"] == "harness_agent")
    env = {item["name"]: item["value"] for item in agent["spec"]["env_vars"]}
    with patch.dict(os.environ, env, clear=True):
        return TauSDKSettings(_env_file=None, workspace=REPO_ROOT, tau_state_home=tmp_path)


def test_hosted_catalog_preserves_alpaca_but_excludes_generic_tools(tmp_path: Path) -> None:
    settings = restricted_settings(tmp_path)
    assert settings.exclude_base_tools is True
    assert settings.exclude_mainsequence_mcp is True
    assert settings.local_mode is False

    bootstrap = SimpleNamespace(
        runtime_capabilities=RUNTIME_CAPABILITIES,
        session=SimpleNamespace(model_extra={}, harness="tau", active_provider="openai"),
        lease=SimpleNamespace(lease_token="test-lease-not-a-credential"),
        resume_snapshot=None,
        history=SimpleNamespace(model_extra={}, has_more=False, entries=[], next_sequence=0),
        provider_credentials={},
        provider_control=ProviderControl(
            schema_version=1,
            catalog_digest="sha256:" + "0" * 64,
            provider="openai",
            model={
                "model": "test-model",
                "api": "openai-responses",
                "input": ["text"],
                "reasoning": True,
                "thinking_levels": ["medium"],
            },
        ),
        mcp_applications=[
            MCPApplication(name="alpaca", resource_release_uid="test-alpaca-release")
        ],
    )
    backend = Mock(
        bootstrap_tau_runtime=AsyncMock(return_value=bootstrap),
        release_runtime_lease=AsyncMock(),
        provider_credential_from_hydration=Mock(return_value=ProviderCredential(provider="openai")),
    )
    providers = Mock()
    providers.for_session_credential.return_value = SimpleNamespace(
        provider=Mock(), name="openai", model="test-model", thinking_level="medium"
    )

    class CatalogCaptured(Exception):
        """Stop at the model-session boundary without opening a provider connection."""

    async def inspect_catalog(config):
        assert {tool.name for tool in config.tools} == {
            "read",
            "alpaca__list_tools",
            "alpaca__call_tool",
            "alpaca__list_resources",
            "alpaca__read_resource",
            "task_request_input",
            "task_request_authorization",
        }
        assert config.append_system_prompt is None
        raise CatalogCaptured

    # Mock external/session lifetime boundaries, not the SDK's actual tool assembly.
    with (
        patch("ms_tau_sdk.runtime.manager.register_runtime_platform"),
        patch("ms_tau_sdk.runtime.manager.CodingSession.load", side_effect=inspect_catalog),
        patch("ms_tau_sdk.runtime.manager.create_coding_tools") as coding_tools,
        patch.object(SessionRuntimeManager, "_get_mcp_client", new_callable=AsyncMock) as mcp,
    ):
        manager = SessionRuntimeManager(settings=settings, backend=backend, providers=providers)
        with pytest.raises(CatalogCaptured):
            anyio.run(manager.get, "test-session")
        coding_tools.assert_not_called()
        mcp.assert_not_awaited()


def test_local_reader_does_not_expose_application_or_private_development_files(tmp_path: Path):
    skills, diagnostics = load_skills_with_diagnostics(
        tau_resource_paths(REPO_ROOT, state_home=tmp_path)
    )
    assert not diagnostics
    # TAU 2.0.14 serves application skills through its MCP resource tools, not the local index.
    # Their real HTTP discovery and verified reads are covered in test_agent_mcp_skills.py.
    assert skills == []
    reader = create_skill_read_tool(cwd=REPO_ROOT, skills=lambda: skills)

    async def read_paths():
        # Do not use a real credential file as the denial probe.
        with pytest.raises(ToolInputError, match="outside the available skills"):
            await reader.execute_fn("read-other", {"path": str(REPO_ROOT / "pyproject.toml")})
        with pytest.raises(ToolInputError, match="outside the available skills"):
            await reader.execute_fn(
                "read-local-business",
                {
                    "path": str(
                        REPO_ROOT / ".agents/skills/mcp_tools/alpaca-account-workflow/SKILL.md"
                    )
                },
            )
        with pytest.raises(ToolInputError, match="outside the available skills"):
            await reader.execute_fn(
                "read-private",
                {
                    "path": str(
                        REPO_ROOT / ".agents/skills/ms_tau_sdk/tau_project_customization/SKILL.md"
                    )
                },
            )

    anyio.run(read_paths)
