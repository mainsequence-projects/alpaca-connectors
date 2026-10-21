from __future__ import annotations

import ast
import json
import re
import struct
import tomllib
import unittest
from pathlib import Path

import jsonschema
import yaml

from alpaca_connectors.agent_tools import PROJECT_AGENT_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
MCP_SKILLS_ROOT = SKILLS_ROOT / "mcp_tools"
AGENT_CARD_PATH = REPO_ROOT / ".agents" / "agent_card.json"
ICON_PATH = REPO_ROOT / ".agents" / "alpaca-command-center-icon.png"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
MCP_SERVER_PATH = REPO_ROOT / "src" / "alpaca_connectors" / "api" / "app" / "mcp_server.py"
WORKFLOW_PATH = REPO_ROOT / ".mainsequence" / "workflows" / "alpaca-connectors-api.yaml"
OLD_AGENT_WORKFLOW_PATH = REPO_ROOT / ".mainsequence" / "workflows" / "alpaca-connectors-agent.yaml"
AGENT_ENTRY_PATH = REPO_ROOT / "api" / "agent" / "main.py"


def project_skill_paths() -> list[str]:
    paths: list[str] = []
    for skill_file in sorted(SKILLS_ROOT.rglob("SKILL.md")):
        relative = skill_file.relative_to(REPO_ROOT).as_posix()
        if relative.startswith(
            (
                ".agents/skills/mainsequence/",
                ".agents/skills/mainsequence_platform/",
                ".agents/skills/metatables/",
                ".agents/skills/ms_markets/",
                ".agents/skills/ms_tau_sdk/",
            )
        ):
            continue
        paths.append(relative)
    return paths


class AgentArtifactsTests(unittest.TestCase):
    def test_agents_md_has_repository_specific_agent_instructions(self) -> None:
        content = AGENTS_PATH.read_text(encoding="utf-8")
        self.assertNotIn("HERE SHOULD BE THE PROJECT-SPECIFIC ACTIONS", content)
        self.assertIn("## CodeRepository-Specific Instructions", content)
        self.assertIn("Project-to-agent boundary:", content)
        self.assertIn("alpaca_connectors.api.app.mcp_server", content)

    def test_agents_md_is_a_short_developer_file_the_agent_also_reads(self) -> None:
        # TAU adds AGENTS.md to every Agent session, so it holds only developer rules; the runtime
        # prompt is .tau/SYSTEM.md, and the operator manual lives in docs/.
        content = AGENTS_PATH.read_text(encoding="utf-8")
        self.assertLess(len(content.splitlines()), 80)
        for retired in (
            "dual-mandate agent",
            "mainsequence-agent-scaffold",
            "code-repository update AGENTS.md",
            "api_surfaces",
        ):
            self.assertNotIn(retired, content)
        self.assertIn("Its runtime instructions are `.tau/SYSTEM.md`", content)
        self.assertTrue((REPO_ROOT / ".tau" / "SYSTEM.md").is_file())

        guide = REPO_ROOT / "docs" / "operations" / "operator-guide.md"
        self.assertIn("docs/operations/operator-guide.md", content)
        moved = guide.read_text(encoding="utf-8")
        for heading in (
            "## Supported Operator Surfaces",
            "## Asset Registration",
            "## Asset Universes And Holdings Categories",
            "## Price Updates From Alpaca",
            "## Operational Notes",
        ):
            self.assertIn(heading, moved)
        self.assertIn("alpaca-connectors asset register", moved)
        mkdocs = yaml.safe_load((REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8"))
        self.assertIn("operations/operator-guide.md", str(mkdocs["nav"]))

    def test_agent_card_is_source_metadata_not_a_runtime_card(self) -> None:
        card = json.loads(AGENT_CARD_PATH.read_text(encoding="utf-8"))
        pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(card["name"], "Alpaca Agent")
        self.assertEqual(card["version"], pyproject["project"]["version"])
        for runtime_field in (
            "supportedInterfaces",
            "securitySchemes",
            "securityRequirements",
            "defaultInputModes",
            "defaultOutputModes",
        ):
            self.assertNotIn(runtime_field, card)

        extensions = card["capabilities"]["extensions"]
        self.assertEqual(len(extensions), 1)
        response_kind = extensions[0]["params"]
        self.assertEqual(response_kind["supportedResponseKinds"], ["message"])
        self.assertEqual(response_kind["defaultResponseKind"], "message")

    def test_agent_card_declares_every_repository_owned_skill(self) -> None:
        card = json.loads(AGENT_CARD_PATH.read_text(encoding="utf-8"))
        declared_paths = sorted(skill["path"] for skill in card["skills"])
        self.assertEqual(declared_paths, project_skill_paths())

        skill_ids = [skill["id"] for skill in card["skills"]]
        self.assertEqual(len(skill_ids), len(set(skill_ids)))
        for skill in card["skills"]:
            self.assertRegex(skill["id"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
            self.assertTrue(skill["name"])
            self.assertTrue(skill["description"])
            self.assertTrue(skill["tags"])
            self.assertTrue(skill["examples"])
            self.assertTrue((REPO_ROOT / skill["path"]).is_file())

    def test_business_skills_have_one_copy_in_the_selected_mcp_root(self) -> None:
        paths = project_skill_paths()
        self.assertEqual(len(paths), 6)
        for path in paths:
            skill = REPO_ROOT / path
            self.assertEqual(skill.parent.parent, MCP_SKILLS_ROOT)
            self.assertFalse(skill.is_symlink())
            self.assertFalse(skill.parent.is_symlink())
            frontmatter = yaml.safe_load(skill.read_text().split("---", 2)[1])
            self.assertEqual(frontmatter["name"], skill.parent.name)
            self.assertTrue(frontmatter["description"])
        self.assertFalse((REPO_ROOT / ".tau" / "skills").exists())

    def test_skill_call_examples_match_the_actual_tool_schemas(self) -> None:
        catalog = {tool.name: tool.parameters for tool in PROJECT_AGENT_TOOLS}
        for path in project_skill_paths():
            content = (REPO_ROOT / path).read_text(encoding="utf-8")
            examples = re.findall(r"```json\s*\n(.*?)\n```", content, re.DOTALL)
            self.assertTrue(examples, path)
            for example in examples:
                with self.subTest(skill=path, example=example):
                    call = json.loads(example)
                    self.assertEqual(set(call), {"tool", "arguments"})
                    self.assertIn(call["tool"], catalog)
                    jsonschema.validate(call["arguments"], catalog[call["tool"]])

    def test_the_agent_gets_project_tools_only_from_the_api_mcp(self) -> None:
        # No in-process Tau tools: every project tool runs in the API, as the person it serves.
        self.assertFalse((REPO_ROOT / ".tau" / "extensions").exists())
        self.assertFalse(OLD_AGENT_WORKFLOW_PATH.exists())
        source = MCP_SERVER_PATH.read_text(encoding="utf-8")
        self.assertIn("from alpaca_connectors.agent_tools import PROJECT_AGENT_TOOLS", source)
        self.assertNotIn("sys.path", source)

    def test_agent_workflow_declares_a_harness_agent_that_acts_for_its_requester(self) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

        self.assertEqual(workflow["api_version"], "3.0.0")
        resources = {resource["key"]: resource for resource in workflow["resources"]}
        agents = [item for item in resources.values() if item["kind"] == "harness_agent"]
        self.assertEqual(len(agents), 1)
        resource = agents[0]
        # `code_repository_coding_agent` is a retired kind the platform rejects.
        self.assertEqual(resource["key"], "alpaca-connectors-agent")
        self.assertIs(resource["acts_for_requester"], True)
        specification = resource["spec"]
        self.assertEqual(specification["llm_provider"], "openai-codex")
        self.assertEqual(specification["llm_model"], "gpt-5.6-luna")
        self.assertEqual(specification["llm_thinking"], "medium")
        # The same file's API serves the MCP; its key is the Agent's tool prefix.
        self.assertEqual(specification["mcp_applications"], [{"resource": "alpaca"}])
        self.assertRegex("alpaca", r"^[a-z][a-z0-9_]{0,39}$")
        self.assertEqual(resources["alpaca"]["kind"], "fastapi")
        self.assertIs(resources["alpaca"]["spec"]["mcp_enabled"], True)
        self.assertEqual(
            specification["source_path"], AGENT_ENTRY_PATH.relative_to(REPO_ROOT).as_posix()
        )
        self.assertIs(specification["automatic_deployment"], True)
        self.assertEqual(
            specification["automatic_redeployment"], {"enabled": True, "tag_regex": None}
        )
        for retired in ("cpu_limit", "memory_limit"):
            self.assertNotIn(retired, specification)

    def test_agent_icon_and_poses_fit_the_platform_icon_limits(self) -> None:
        # The platform refuses an agent icon over 128 x 128 px or 64 KiB (`agent_icon_invalid`)
        # and keeps its default robot, while the deployment still succeeds.
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        agent = next(item for item in workflow["resources"] if item["kind"] == "harness_agent")
        specification = agent["spec"]
        animations = specification["command_center_animations"]
        paths = [specification["command_center_icon"]["path"]] + [
            pose["path"] for pose in animations["poses"].values()
        ]
        self.assertEqual(set(animations["poses"]), {"thinking", "working", "trading", "error"})
        self.assertEqual(animations["tools"], [{"application": "alpaca", "pose": "trading"}])
        for path in paths:
            with self.subTest(icon=path):
                payload = (REPO_ROOT / path).read_bytes()
                self.assertLessEqual(len(payload), 64 * 1024)
                self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
                width, height = struct.unpack(">II", payload[16:24])
                self.assertLessEqual((width, height), (128, 128))
                self.assertEqual(width, height)

    def test_the_api_and_the_agent_declare_user_access_to_each_other(self) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        resources = {resource["key"]: resource for resource in workflow["resources"]}

        # The API runs this branch's Jobs, reads MetaTables, and finds the Agent it returns to
        # the site; the Agent calls the API's MCP and reads no tables itself. `branch: editor`
        # stays only for the bars Job of alpaca-bars-update.yaml, which no `resources` entry of
        # this file can name.
        self.assertEqual(
            resources["alpaca"]["access"],
            {
                "branch": "editor",
                "branches": [{"repository": "MetaTables", "policy": "reader"}],
                "resources": [
                    {"key": "alpaca-connectors-agent", "policy": "user"},
                    {"key": "alpaca-etf-signal", "policy": "runner"},
                    {"key": "alpaca-etf-portfolio", "policy": "runner"},
                    {"key": "alpaca-etf-signal-dispatcher", "policy": "viewer"},
                    {"key": "alpaca-etf-portfolio-dispatcher", "policy": "viewer"},
                ],
            },
        )
        self.assertEqual(
            resources["alpaca-connectors-agent"]["access"],
            {"resources": [{"key": "alpaca", "policy": "user"}]},
        )

    def test_agent_workflow_disables_generic_tool_sources(self) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        agent = next(item for item in workflow["resources"] if item["kind"] == "harness_agent")
        env = {item["name"]: item["value"] for item in agent["spec"]["env_vars"]}
        self.assertEqual(env["TAU_EXCLUDE_BASE_TOOLS"], "true")
        self.assertEqual(env["TAU_EXCLUDE_MAINSEQUENCE_MCP"], "true")
        self.assertEqual(agent["spec"]["mcp_applications"], [{"resource": "alpaca"}])

    def test_agent_entry_point_serves_the_object_create_app_returns(self) -> None:
        tree = ast.parse(AGENT_ENTRY_PATH.read_text(encoding="utf-8"))
        imports = [
            (node.module, alias.name)
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        ]
        self.assertEqual(imports, [("ms_tau_sdk", "create_app")])
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and [target.id for target in node.targets if isinstance(target, ast.Name)] == ["app"]
        )
        self.assertIsInstance(assignment.value, ast.Call)
        self.assertEqual(assignment.value.func.id, "create_app")
        self.assertEqual(assignment.value.args, [])

        dependencies = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"][
            "dependencies"
        ]
        self.assertTrue(any(item.startswith("ms-tau-sdk") for item in dependencies))

    def test_agent_icon_is_a_supported_rgba_png(self) -> None:
        payload = ICON_PATH.read_bytes()
        self.assertLessEqual(len(payload), 512 * 1024)
        self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
        ihdr_length = struct.unpack(">I", payload[8:12])[0]
        self.assertEqual(ihdr_length, 13)
        self.assertEqual(payload[12:16], b"IHDR")
        width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[16:26])
        self.assertEqual((width, height), (512, 512))
        self.assertEqual(bit_depth, 8)
        self.assertEqual(color_type, 6)


if __name__ == "__main__":
    unittest.main()
