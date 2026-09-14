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
TAU_EXTENSION_PATH = REPO_ROOT / ".tau" / "extensions" / "alpaca_connectors" / "extension.py"
WORKFLOW_PATH = REPO_ROOT / ".mainsequence" / "workflows" / "alpaca-connectors-agent.yaml"


def project_skill_paths() -> list[str]:
    paths: list[str] = []
    for skill_file in sorted(SKILLS_ROOT.rglob("SKILL.md")):
        relative = skill_file.relative_to(REPO_ROOT).as_posix()
        if relative.startswith(
            (
                ".agents/skills/mainsequence/",
                ".agents/skills/ms_markets/",
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
        self.assertIn(".tau/extensions/alpaca_connectors/extension.py", content)
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

    def test_tau_extension_imports_the_project_tool_catalog_without_path_shims(self) -> None:
        source = TAU_EXTENSION_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)

        self.assertNotIn("sys.path", source)
        self.assertIn("from src.agent_tools import PROJECT_AGENT_TOOLS", source)
        setup = next(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "setup"
        )
        setup_source = ast.get_source_segment(source, setup) or ""
        self.assertIn("tau.register_tool", setup_source)
        self.assertIn("PROJECT_AGENT_TOOLS", setup_source)

    def test_agent_workflow_enables_automatic_deployment_with_color_icon(self) -> None:
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

        self.assertEqual(workflow["api_version"], "2.3.0")
        self.assertEqual(workflow["name"], "alpaca-connectors-agent")
        self.assertEqual(len(workflow["resources"]), 1)
        resource = workflow["resources"][0]
        self.assertEqual(resource["kind"], "code_repository_coding_agent")
        specification = resource["spec"]
        self.assertIs(specification["automatic_deployment"], True)
        self.assertIsNone(specification["automatic_redeployment_policy"]["tag_regex"])
        self.assertEqual(
            specification["command_center_icon"],
            {
                "path": ".agents/alpaca-command-center-icon.png",
                "rendering": "color",
            },
        )

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
