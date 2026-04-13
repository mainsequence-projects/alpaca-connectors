from __future__ import annotations
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app.main import app
from api.app.schemas import AssetRegistrationByTickerResponse
from api.app.services import _build_table_source_response


class ApiAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_registration_plan_route(self) -> None:
        mocked_response = _build_table_source_response(
            label="Asset Registration Plan",
            rows=[
                {
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
            ],
        )
        with patch("api.app.main.build_asset_registration_discovery", return_value=mocked_response):
            response = self.client.post(
                "/v1/assets/registration/plan",
                json={"symbols": ["NVDA"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rows"][0]["missing_symbols_to_register"], ["NVDA"])

    def test_register_ticker_route(self) -> None:
        mocked_response = AssetRegistrationByTickerResponse(
            requested_ticker="NVDA",
            alpaca_symbol="NVDA",
            alpaca_name="NVIDIA Corporation",
            figi="BBG000BBJQV0",
            classification_pass_name="common_stock",
            security_type="Common Stock",
            security_type_2="Common Stock",
            exchange_code="US",
            status="created",
            asset_id=101,
            created=True,
            already_registered=False,
            missing_from_alpaca=False,
            missing_figi=False,
            warnings=[],
        )
        with patch("api.app.main.execute_asset_registration_by_ticker", return_value=mocked_response):
            response = self.client.post(
                "/v1/app-components/assets/register-ticker",
                json={"ticker": "NVDA"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "created")
        self.assertEqual(response.json()["asset_id"], 101)

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
