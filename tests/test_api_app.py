from __future__ import annotations
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app.main import app
from api.app.schemas import (
    AssetRegistrationByTickerResponse,
    AssetSearchSelectOption,
    AssetSearchSelectPagination,
    AssetSearchSelectResponse,
    LightweightOhlcChartResponse,
)


class ApiAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

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

    def test_lightweight_ohlc_chart_route(self) -> None:
        mocked_response = LightweightOhlcChartResponse(
            unique_identifier="BBG000BBJQV0",
            node_identifier="alpaca_stock_bars_1d_sip_all",
            start_date="2026-04-01",
            end_date="2026-04-08",
            point_count=2,
            spec={
                "fitContent": True,
                "series": [
                    {
                        "id": "ohlc",
                        "type": "candlestick",
                        "data": [
                            {
                                "time": "2026-04-01",
                                "open": 100.0,
                                "high": 101.0,
                                "low": 99.0,
                                "close": 100.5,
                            }
                        ],
                    }
                ],
            },
            spec_json="{\"fitContent\":true,\"series\":[]}",
        )
        with (
            patch(
                "api.app.main.resolve_lightweight_ohlc_asset_unique_identifier",
                return_value="BBG000BBJQV0",
            ),
            patch("api.app.main.execute_lightweight_ohlc_chart", return_value=mocked_response),
        ):
            response = self.client.post(
                "/v1/charts/lightweight/ohlc",
                params={
                    "ticker": "NVDA",
                    "start_date": "2026-04-01",
                    "end_date": "2026-04-08",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unique_identifier"], "BBG000BBJQV0")
        self.assertEqual(response.json()["point_count"], 2)

    def test_lightweight_ohlc_chart_route_accepts_query_fields(self) -> None:
        mocked_response = LightweightOhlcChartResponse(
            unique_identifier="BBG000BBJQV0",
            node_identifier="alpaca_stock_bars_1d_sip_all",
            start_date="2026-04-01",
            end_date="2026-04-08",
            point_count=1,
            spec={"series": []},
            spec_json="{\"series\":[]}",
        )
        with (
            patch(
                "api.app.main.resolve_lightweight_ohlc_asset_unique_identifier",
                return_value="BBG000BBJQV0",
            ),
            patch("api.app.main.execute_lightweight_ohlc_chart", return_value=mocked_response),
        ):
            response = self.client.post(
                "/v1/charts/lightweight/ohlc",
                params={
                    "ticker": "NVDA",
                    "start_date": "2026-04-01",
                    "end_date": "2026-04-08",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unique_identifier"], "BBG000BBJQV0")

    def test_lightweight_ohlc_chart_route_returns_search_results(self) -> None:
        mocked_response = AssetSearchSelectResponse(
            query="NVDA",
            asset_category_unique_identifier="HOLDINGS__IVV",
            items=[
                AssetSearchSelectOption(
                    unique_identifier="BBG000BBJQV0",
                    label="NVDA",
                    ticker="NVDA",
                    name="NVIDIA Corporation",
                    figi="BBG000BBJQV0",
                    display="NVDA - NVIDIA Corporation",
                )
            ],
            pagination=AssetSearchSelectPagination(page=1, limit=20, hasMore=False),
        )
        with patch(
            "api.app.main.search_assets_for_lightweight_ohlc_select",
            return_value=mocked_response,
        ):
            response = self.client.post(
                "/v1/charts/lightweight/ohlc",
                params={"ticker": "NVDA"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["unique_identifier"], "BBG000BBJQV0")

    def test_lightweight_ohlc_chart_route_ignores_empty_json_body_for_selector_bootstrap(self) -> None:
        response = self.client.post(
            "/v1/charts/lightweight/ohlc",
            params={
                "node_identifier": "alpaca_stock_bars_1d_sip_all",
                "asset_category_unique_identifier": "HOLDINGS__IVV",
                "page": 1,
                "limit": 20,
            },
            json={},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])

    def test_lightweight_ohlc_chart_openapi_exposes_only_ticker_and_dates(self) -> None:
        operation = self.client.get("/openapi.json").json()["paths"][
            "/v1/charts/lightweight/ohlc"
        ]["post"]
        parameter_names = {parameter["name"] for parameter in operation["parameters"]}

        self.assertEqual(parameter_names, {"ticker", "start_date", "end_date"})
        self.assertNotIn("requestBody", operation)

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
