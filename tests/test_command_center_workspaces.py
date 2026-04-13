from __future__ import annotations

import unittest

from src.command_center.workspaces import (
    APP_COMPONENT_WIDGET_ID,
    REGISTER_ASSET_BY_TICKER_OPERATION_KEY,
    REGISTER_ASSET_BY_TICKER_ROUTE_PATH,
    build_alpaca_assets_registry_workspace_update,
    build_register_asset_by_ticker_app_component_widget,
)


class CommandCenterWorkspaceBuilderTests(unittest.TestCase):
    def test_build_register_asset_widget(self) -> None:
        widget = build_register_asset_by_ticker_app_component_widget(
            fastapi_release_id=91,
        )

        self.assertEqual(widget["widgetId"], APP_COMPONENT_WIDGET_ID)
        self.assertEqual(widget["props"]["apiTargetMode"], "main-sequence-resource-release")
        self.assertEqual(widget["props"]["mainSequenceResourceRelease"]["releaseId"], 91)
        self.assertEqual(widget["props"]["method"], "post")
        self.assertEqual(widget["props"]["path"], REGISTER_ASSET_BY_TICKER_ROUTE_PATH)
        self.assertEqual(widget["props"]["requestButtonLabel"], "Register")
        self.assertEqual(
            widget["props"]["bindingSpec"]["operationKey"],
            REGISTER_ASSET_BY_TICKER_OPERATION_KEY,
        )
        self.assertEqual(
            widget["props"]["requestInputMap"]["operationKey"],
            REGISTER_ASSET_BY_TICKER_OPERATION_KEY,
        )
        self.assertEqual(
            widget["props"]["bindingSpec"]["requestForm"]["bodyMode"],
            "generated",
        )
        self.assertIn("response:$", {
            port["id"] for port in widget["props"]["bindingSpec"]["responsePorts"]
        })
        self.assertIn("response:status", {
            port["id"] for port in widget["props"]["bindingSpec"]["responsePorts"]
        })

    def test_build_workspace_update(self) -> None:
        workspace = build_alpaca_assets_registry_workspace_update(
            fastapi_release_id=91,
        )

        self.assertEqual(workspace["title"], "Alpaca Assets Registry")
        self.assertEqual(workspace["layoutKind"], "custom")
        self.assertEqual(len(workspace["widgets"]), 1)
        self.assertEqual(workspace["widgets"][0]["widgetId"], APP_COMPONENT_WIDGET_ID)


if __name__ == "__main__":
    unittest.main()
