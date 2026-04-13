from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app.main import app


class ApiAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_registration_plan_route(self) -> None:
        mocked_response = {
            "request": {
                "symbols": ["NVDA"],
                "seed_tickers": None,
                "component_provider": None,
                "include_non_tradable": False,
                "timeout": 30.0,
            },
            "plan_summary": {"alpaca_asset_count": 1},
            "resolution_summary": {"missing_symbols_to_register": ["NVDA"]},
            "can_register": True,
            "unresolved_symbols": [],
            "missing_symbols_from_alpaca": [],
            "missing_symbols_to_register": ["NVDA"],
            "warnings_by_symbol": {},
        }
        with patch("api.app.main.build_asset_registration_discovery", return_value=mocked_response):
            response = self.client.post(
                "/v1/assets/registration/plan",
                json={"symbols": ["NVDA"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["missing_symbols_to_register"], ["NVDA"])

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
