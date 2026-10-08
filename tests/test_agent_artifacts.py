from __future__ import annotations

import ast
import json
import struct
import tomllib
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
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
        self.assertIn("alpaca-connectors asset register", content)

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

    def test_the_agent_gets_project_tools_only_from_the_api_mcp(self) -> None:
        # No in-process Tau tools: every project tool runs in the API, as the person it serves.
        self.assertFalse((REPO_ROOT / ".tau" / "extensions").exists())
        self.assertFalse(OLD_AGENT_WORKFLOW_PATH.exists())
        source = MCP_SERVER_PATH.read_text(encoding="utf-8")
        self.assertIn("from alpaca_connectors.agent_tools import PROJECT_AGENT_TOOLS", source)
        self.assertNotIn("sys.path", source)

    def test_agent_workflow_declares_a_harness_agent_that_acts_for_its_requester(self) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

        self.assertEqual(workflow["api_version"], "2.3.0")
        resources = {resource["key"]: resource for resource in workflow["resources"]}
        agents = [item for item in resources.values() if item["kind"] == "harness_agent"]
        self.assertEqual(len(agents), 1)
        resource = agents[0]
        # `code_repository_coding_agent` is a retired kind the platform rejects.
        self.assertEqual(resource["key"], "alpaca-connectors-agent")
        self.assertIs(resource["acts_for_requester"], True)
        specification = resource["spec"]
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
        for retired in ("command_center_icon", "cpu_limit", "memory_limit"):
            self.assertNotIn(retired, specification)

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
