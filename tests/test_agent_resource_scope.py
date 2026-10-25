"""What the deployed Alpaca Agent reads from this repository (ms-tau-sdk ADR 0025).

The Agent is not a coding agent, so its workflow does not admit the repository's development
resources and the runtime reads only `.tau/`: `.tau/SYSTEM.md` and the six skills under
`.tau/skills`, the same folder the API publishes over its MCP. The root `AGENTS.md` and the development skills under
`.agents/skills` are for the coding agents that develop the repository. The session is built the
way the SDK builds it, through `tau_resource_paths`, which also installs the SDK's context filter;
Tau's raw resource paths or context discovery would bypass that filter.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import anyio
import pytest
import yaml
from ms_tau_sdk.resources.context_compat import EXCLUDED_CONTEXT_MESSAGE
from ms_tau_sdk.resources.loader import tau_resource_paths
from ms_tau_sdk.settings import TauSDKSettings
from ms_tau_sdk.tools.skill_read import create_skill_read_tool
from tau_ai.fake import FakeProvider
from tau_coding import CodingSession, CodingSessionConfig
from tau_coding.resources import ResourceError

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".mainsequence/workflows/alpaca-connectors-api.yaml"
SYSTEM_PROMPT_PATH = REPO_ROOT / ".tau/SYSTEM.md"
ROOT_AGENTS_PATH = REPO_ROOT / "AGENTS.md"
EXPECTED_SKILLS = {
    "alpaca-account-workflow",
    "alpaca-asset-registration",
    "alpaca-etf-portfolios",
    "alpaca-etf-weight-signals",
    "alpaca-holdings-category",
    "alpaca-stock-bars",
}
SYNCED_DEVELOPMENT_SKILL = (
    REPO_ROOT / ".agents/skills/ms_tau_sdk/tau_project_customization/SKILL.md"
)


class _MemoryStorage:
    def __init__(self) -> None:
        self.entries = []

    async def read_all(self):
        return list(self.entries)

    async def append(self, entry):
        self.entries.append(entry)


def deployed_settings(tmp_path: Path) -> TauSDKSettings:
    """The SDK settings the hosted Agent runs with: its workflow `env_vars` and nothing else."""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    agent = next(item for item in workflow["resources"] if item["kind"] == "harness_agent")
    env = {item["name"]: item["value"] for item in agent["spec"]["env_vars"]}
    with patch.dict(os.environ, env, clear=True):
        return TauSDKSettings(_env_file=None, workspace=REPO_ROOT, tau_state_home=tmp_path)


def test_the_deployed_agent_reads_only_tau(tmp_path: Path) -> None:
    settings = deployed_settings(tmp_path)
    assert settings.include_development_resources is False
    assert SYNCED_DEVELOPMENT_SKILL.is_file()

    async def run() -> None:
        session = await CodingSession.load(
            CodingSessionConfig(
                provider=FakeProvider([]),
                provider_name="fake",
                model="fake-model",
                storage=_MemoryStorage(),
                cwd=REPO_ROOT,
                tools=[create_skill_read_tool(cwd=REPO_ROOT, skills=lambda: session.skills)],
                resource_paths=tau_resource_paths(
                    REPO_ROOT,
                    state_home=tmp_path / "state",
                    include_development_resources=settings.include_development_resources,
                ),
                project_extensions_enabled=False,
                trust_override="approve",
            )
        )
        try:
            # Exactly the six `.tau/skills`, none of the 40-odd synced development skills.
            assert {skill.name for skill in session.skills} == EXPECTED_SKILLS
            assert {Path(skill.path).resolve() for skill in session.skills} == {
                (REPO_ROOT / ".tau/skills" / name / "SKILL.md").resolve()
                for name in EXPECTED_SKILLS
            }

            # `.tau/SYSTEM.md` is the prompt, and no AGENTS.md reaches it.
            assert [Path(path).resolve() for path in session.system_prompt_files] == [
                SYSTEM_PROMPT_PATH.resolve()
            ]
            assert not session.context_files
            prompt = session.system_prompt
            assert prompt.startswith(SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()[:60])
            for line in ROOT_AGENTS_PATH.read_text(encoding="utf-8").splitlines():
                if len(line) > 40:
                    assert line not in prompt
            excluded = {
                Path(diagnostic.path).resolve()
                for diagnostic in session.resource_diagnostics
                if diagnostic.kind == "context" and diagnostic.message == EXCLUDED_CONTEXT_MESSAGE
            }
            assert ROOT_AGENTS_PATH.resolve() in excluded

            # The skill-scoped read serves the Agent's skills and refuses development skills.
            read = next(tool for tool in session.tools if tool.name == "read")
            with pytest.raises(Exception, match="outside the available skills"):
                await read.execute("read-development", {"path": str(SYNCED_DEVELOPMENT_SKILL)})
            served = await read.execute(
                "read-agent-skill", {"path": ".tau/skills/alpaca-account-workflow/SKILL.md"}
            )
            assert "name: alpaca-account-workflow" in served.content[0].text

            # `/skill:<name>` from chat expands only the Agent's own skills.
            with pytest.raises(ResourceError, match="Unknown skill: tau_project_customization"):
                session.expand_prompt_text("/skill:tau_project_customization")
            expanded = session.expand_prompt_text("/skill:alpaca-account-workflow")
            assert expanded != "/skill:alpaca-account-workflow"
        finally:
            await session.aclose()

    anyio.run(run)
