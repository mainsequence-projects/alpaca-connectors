"""The Alpaca Agent runs on its own `.tau/SYSTEM.md`, not on Tau's packaged general prompt.

Tau replaces its packaged prompt with a project's `.tau/SYSTEM.md`, so the runtime role and
guardrails live in that file. The deployed Agent reads only `.tau/` (ms-tau-sdk ADR 0025) and gets
no project context file; `test_agent_resource_scope.py` pins that on a session the SDK builds.
This uses the SDK's resource paths with Tau's own resource discovery and prompt builder.
"""

from __future__ import annotations

from pathlib import Path

from ms_tau_sdk.resources.loader import resource_root, tau_resource_paths
from tau_coding.resources import discover_system_prompt_resources
from tau_coding.system_prompt import BuildSystemPromptOptions, build_system_prompt

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PROMPT_PATH = REPO_ROOT / ".tau" / "SYSTEM.md"


def test_the_runtime_replaces_the_packaged_prompt_with_the_alpaca_prompt(tmp_path: Path) -> None:
    plain_project = tmp_path / "plain"
    plain_project.mkdir()
    packaged = discover_system_prompt_resources(
        tau_resource_paths(plain_project, state_home=tmp_path)
    )
    assert packaged.custom_prompt_path == resource_root() / "SYSTEM.md"

    resources = discover_system_prompt_resources(tau_resource_paths(REPO_ROOT, state_home=tmp_path))
    assert resources.custom_prompt_path == SYSTEM_PROMPT_PATH
    # No context file: the SDK keeps the root AGENTS.md out (test_agent_resource_scope.py).
    prompt = build_system_prompt(
        BuildSystemPromptOptions(cwd=REPO_ROOT, tools=(), custom_prompt=resources.custom_prompt)
    )
    flat = " ".join(prompt.split())
    assert "You are Alpaca Agent" in flat
    assert "Do not introduce yourself as a Tau coding agent" in flat
    assert packaged.custom_prompt not in prompt


def test_the_prompt_keeps_the_agents_runtime_guardrails() -> None:
    flat = " ".join(SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").split())

    # Alpaca operations go only through the application's MCP tools; skills come from its MCP.
    for name in (
        "alpaca__list_tools",
        "alpaca__call_tool",
        "alpaca__list_resources",
        "alpaca__read_resource",
        "alpaca_project_state",
        "alpaca_get_job_run_status",
    ):
        assert f"`{name}`" in flat
    assert "You have no shell, file, SQL or general platform tools" in flat
    # Keys are entered on the site for a key request, never in the conversation (ADR 0015).
    assert "Never ask for, accept, repeat or store Alpaca API key values" in flat
    assert "`alpaca_account_key_request` with `start_registration` or `start_rotation`" in flat
    # Identity and the Environment never come from chat.
    assert "with that person's effective permissions" in flat
    assert "Never take an identity, user UID, permission, token or Environment from chat" in flat
    # Destructive and uncertain writes.
    assert "exact confirmation `DELETE <uid>`" in flat
    assert "read the affected record or operation status before doing anything else" in flat
    assert "never replay a write whose outcome is unknown" in flat
    # Refusals are explained, and secrets never leave the runtime.
    assert "explain plainly" in flat
    assert "Never reveal runtime credentials, access tokens, Secret values or other secrets" in flat
