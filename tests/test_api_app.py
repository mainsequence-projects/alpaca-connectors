from __future__ import annotations

import unittest
from unittest.mock import patch

from api.app.main import app
from api.app.schemas import DiscoveryConfigResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


class ApiAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_schema_visible_api_routes_declare_response_models(self) -> None:
        visible_api_routes = [
            route
            for route in app.routes
            if isinstance(route, APIRoute)
            and route.include_in_schema
            and (route.path == "/health" or route.path.startswith("/v1/"))
        ]
        missing_response_models = [
            f"{','.join(sorted(route.methods or []))} {route.path}"
            for route in visible_api_routes
            if route.response_model is None
        ]

        self.assertEqual(missing_response_models, [])

    def test_api_exposes_only_backend_operations(self) -> None:
        visible_paths = {
            route.path
            for route in app.routes
            if isinstance(route, APIRoute) and route.include_in_schema
        }

        self.assertEqual(
            visible_paths,
            {
                "/health",
                "/v1/assets/registration/execute",
                "/v1/discovery/config",
                "/v1/holdings-categories/execute",
            },
        )

    def test_discovery_config_returns_application_contract(self) -> None:
        mocked_response = DiscoveryConfigResponse(
            supported_component_providers=["ishares"],
            etf_provider_map_normalized={"IVV": "ishares"},
            mag_7_category_symbols=["AAPL"],
            etfs_main_tickers=["IVV"],
        )
        with patch("api.app.main.get_discovery_config", return_value=mocked_response):
            response = self.client.get("/v1/discovery/config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mocked_response.model_dump(mode="json"))

    def test_holdings_category_execute_route_returns_400_on_blocker(self) -> None:
        with patch(
            "api.app.main.execute_holdings_category_sync",
            side_effect=ValueError("blocked"),
        ):
            response = self.client.post(
                "/v1/holdings-categories/execute",
                json={"etf_ticker": "IVV"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "blocked")


if __name__ == "__main__":
    unittest.main()
