from __future__ import annotations

import unittest

from src.agent_tools import PROJECT_AGENT_TOOL_NAMES, PROJECT_AGENT_TOOLS

EXPECTED_TOOL_NAMES = (
    "alpaca_project_state",
    "alpaca_query_assets",
    "alpaca_register_assets",
    "alpaca_query_accounts",
    "alpaca_manage_account",
    "alpaca_query_universes",
    "alpaca_manage_universe",
    "alpaca_query_bar_configurations",
    "alpaca_manage_bar_configuration",
    "alpaca_query_etf_signals",
    "alpaca_manage_etf_signal",
    "alpaca_query_rebalance_configurations",
    "alpaca_manage_rebalance_configuration",
    "alpaca_query_etf_portfolios",
    "alpaca_manage_etf_portfolio",
    "alpaca_get_job_run_status",
)


def contains_reference(value: object) -> bool:
    if isinstance(value, dict):
        return "$ref" in value or any(contains_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_reference(item) for item in value)
    return False


class ProjectAgentToolTests(unittest.TestCase):
    def test_tool_catalog_has_the_reviewed_surface(self) -> None:
        self.assertEqual(PROJECT_AGENT_TOOL_NAMES, EXPECTED_TOOL_NAMES)
        self.assertEqual(len(PROJECT_AGENT_TOOL_NAMES), len(set(PROJECT_AGENT_TOOL_NAMES)))

    def test_every_tool_has_a_self_contained_strict_schema(self) -> None:
        for tool in PROJECT_AGENT_TOOLS:
            with self.subTest(tool=tool.name):
                self.assertTrue(tool.label)
                self.assertTrue(tool.description)
                self.assertIsInstance(tool.parameters, dict)
                self.assertFalse(contains_reference(tool.parameters))
                if "oneOf" in tool.parameters:
                    cases = tool.parameters["oneOf"]
                    self.assertGreaterEqual(len(cases), 2)
                    for case in cases:
                        self.assertIs(case["additionalProperties"], False)
                        self.assertIn("operation", case["required"])
                        self.assertIn("const", case["properties"]["operation"])
                else:
                    self.assertIs(tool.parameters["additionalProperties"], False)

    def test_read_tools_are_parallel_and_mutations_are_sequential(self) -> None:
        for tool in PROJECT_AGENT_TOOLS:
            with self.subTest(tool=tool.name):
                expected = (
                    "parallel"
                    if tool.name.startswith(("alpaca_query_", "alpaca_get_"))
                    or tool.name == "alpaca_project_state"
                    else "sequential"
                )
                self.assertEqual(tool.execution_mode, expected)

    def test_account_tool_does_not_accept_secret_values_or_environment(self) -> None:
        account_tool = next(
            tool for tool in PROJECT_AGENT_TOOLS if tool.name == "alpaca_manage_account"
        )
        serialized = str(account_tool.parameters).lower()
        self.assertNotIn("organization_environment_uid", serialized)
        self.assertNotIn("api_key_value", serialized)
        self.assertNotIn("secret_key_value", serialized)
        self.assertIn("api_key_secret_name", serialized)
        self.assertIn("secret_key_secret_name", serialized)

    def test_delete_cases_require_exact_confirmation_argument(self) -> None:
        for tool in PROJECT_AGENT_TOOLS:
            cases = tool.parameters.get("oneOf", [])
            delete_cases = [
                case
                for case in cases
                if case["properties"]["operation"]["const"].startswith("delete")
            ]
            for case in delete_cases:
                with self.subTest(tool=tool.name, operation=case["properties"]["operation"]):
                    self.assertIn("confirmation", case["required"])
                    self.assertEqual(
                        case["properties"]["confirmation"]["description"],
                        "Exact destructive confirmation text: DELETE <uid>.",
                    )


if __name__ == "__main__":
    unittest.main()
