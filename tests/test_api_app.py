from __future__ import annotations
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.app.main import app
from api.app.services import (
    resolve_lightweight_ohlc_asset_unique_identifier,
    search_assets_for_lightweight_ohlc_select,
)
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

    def test_cors_preflight_allows_local_vite_frontend(self) -> None:
        response = self.client.options(
            "/v1/charts/lightweight/ohlc",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, Authorization",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://localhost:5173",
        )
        self.assertIn("POST", response.headers["access-control-allow-methods"])
        self.assertIn("OPTIONS", response.headers["access-control-allow-methods"])
        self.assertIn("Authorization", response.headers["access-control-allow-headers"])
        self.assertIn("Content-Type", response.headers["access-control-allow-headers"])

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
        self.assertEqual(response.json()["mode"], "chart")
        self.assertEqual(response.json()["unique_identifier"], "BBG000BBJQV0")
        self.assertEqual(response.json()["point_count"], 2)
        self.assertNotIn("spec_json", response.json())

    def test_lightweight_ohlc_chart_route_accepts_query_fields(self) -> None:
        mocked_response = LightweightOhlcChartResponse(
            unique_identifier="BBG000BBJQV0",
            node_identifier="alpaca_stock_bars_1d_sip_all",
            start_date="2026-04-01",
            end_date="2026-04-08",
            point_count=1,
            spec={"series": []},
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
        self.assertEqual(response.json()["mode"], "chart")
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
        self.assertEqual(response.json()["mode"], "selector")
        self.assertEqual(response.json()["items"][0]["unique_identifier"], "BBG000BBJQV0")

    def test_lightweight_ohlc_resolver_uses_backend_asset_unique_identifier(self) -> None:
        # Provider facts (ticker/figi/name) now come from OpenFigiDetails, flattened by
        # _find_backend_assets_by_identifier into one search-friendly view (no current_snapshot).
        asset = SimpleNamespace(
            unique_identifier="BBG000BJKPG0",
            uid="11111111-1111-1111-1111-111111111111",
            figi="BBG000BJKPG0",
            ticker="IVV",
            name="iShares Core S&P 500 ETF",
            exchange_code="US",
        )

        with patch(
            "api.app.services._find_backend_assets_by_identifier",
            return_value=[asset],
        ):
            unique_identifier = resolve_lightweight_ohlc_asset_unique_identifier(
                identifier="IVV",
            )

        self.assertEqual(unique_identifier, "BBG000BJKPG0")

    def test_lightweight_ohlc_select_searches_backend_assets(self) -> None:
        asset = SimpleNamespace(
            unique_identifier="BBG000BBJQV0",
            uid="22222222-2222-2222-2222-222222222222",
            figi="BBG000BBJQV0",
            ticker="NVDA",
            name="NVIDIA Corporation",
            exchange_code="US",
        )

        with patch(
            "api.app.services._find_backend_assets_by_identifier",
            return_value=[asset],
        ):
            response = search_assets_for_lightweight_ohlc_select(query="NVDA")

        self.assertEqual(response.items[0].ticker, "NVDA")
        self.assertEqual(response.items[0].unique_identifier, "BBG000BBJQV0")

    def test_lightweight_ohlc_resolver_suggests_registering_missing_ticker(self) -> None:
        with patch(
            "api.app.services._find_backend_assets_by_identifier",
            return_value=[],
        ):
            with self.assertRaisesRegex(ValueError, "Register the ticker first"):
                resolve_lightweight_ohlc_asset_unique_identifier(identifier="sadfasdf")

    def test_lightweight_ohlc_chart_route_suggests_registering_missing_ticker(self) -> None:
        with patch(
            "api.app.main.resolve_lightweight_ohlc_asset_unique_identifier",
            side_effect=ValueError(
                "Identifier 'SADFASDF' was not found in backend assets by "
                "unique_identifier, ticker, or FIGI. Register the ticker first, "
                "then retry loading the chart."
            ),
        ):
            response = self.client.post(
                "/v1/charts/lightweight/ohlc",
                params={
                    "ticker": "sadfasdf",
                    "start_date": "2026-04-01",
                    "end_date": "2026-04-08",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Register the ticker first", response.json()["detail"])

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
        openapi_schema = self.client.get("/openapi.json").json()
        operation = openapi_schema["paths"]["/v1/charts/lightweight/ohlc"]["post"]
        parameter_names = {parameter["name"] for parameter in operation["parameters"]}

        self.assertEqual(parameter_names, {"ticker", "start_date", "end_date"})
        self.assertNotIn("requestBody", operation)
        response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]

        self.assertEqual(response_schema["$ref"].rsplit("/", 1)[-1], "LightweightOhlcResponse")
        self.assertNotIn("anyOf", response_schema)
        response_properties = openapi_schema["components"]["schemas"]["LightweightOhlcResponse"][
            "properties"
        ]
        self.assertIn("spec", response_properties)
        self.assertNotIn("spec_json", response_properties)

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
