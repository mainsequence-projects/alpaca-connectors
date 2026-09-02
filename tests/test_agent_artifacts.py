from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
AGENT_CARD_PATH = REPO_ROOT / ".agents" / "agent_card.json"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def custom_skill_ids() -> list[str]:
    skill_ids: list[str] = []
    for skill_file in sorted(SKILLS_ROOT.rglob("SKILL.md")):
        relative_parent = skill_file.parent.relative_to(SKILLS_ROOT).as_posix()
        if relative_parent.startswith(("mainsequence/", "ms_markets/")):
            continue
        skill_ids.append(relative_parent.replace("/", "."))
    return skill_ids


class AgentArtifactsTests(unittest.TestCase):
    def test_agents_md_has_real_project_specific_instructions(self) -> None:
        content = AGENTS_PATH.read_text(encoding="utf-8")
        self.assertNotIn("HERE SHOULD BE THE PROJECT-SPECIFIC ACTIONS", content)
        self.assertIn("Project-to-agent boundary:", content)
        self.assertIn("alpaca-connectors asset register", content)

    def test_agent_card_exists_and_matches_project_metadata(self) -> None:
        card = json.loads(AGENT_CARD_PATH.read_text(encoding="utf-8"))
        pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(card["name"], "Alpaca Connection Manager")
        self.assertEqual(card["version"], pyproject["project"]["version"])

    def test_agent_card_skills_match_custom_skill_files(self) -> None:
        card = json.loads(AGENT_CARD_PATH.read_text(encoding="utf-8"))
        declared_ids = sorted(skill["id"] for skill in card["skills"])
        self.assertEqual(declared_ids, sorted(custom_skill_ids()))

    def test_agent_card_uses_empty_tags(self) -> None:
        card = json.loads(AGENT_CARD_PATH.read_text(encoding="utf-8"))
        for skill in card["skills"]:
            self.assertEqual(skill["tags"], [])


if __name__ == "__main__":
    unittest.main()
