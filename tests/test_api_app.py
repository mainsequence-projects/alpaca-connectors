from __future__ import annotations

import re
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from api.app.main import app
from api.app.schemas import (
    AccountResponse,
    AssetRegistrationOperationResponse,
    AssetRegistrationRequest,
    AssetUniverseResponse,
    BarConfigurationUpdateAcceptedResponse,
    JobRunStatusResponse,
    ProjectConfigurationResponse,
)
from api.app.services.universes import preview_universe_run as preview_universe_service
from etfhextractor.exceptions import WorkbookParseError
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.holdings.services import AccountHoldingsRegistryError
from src.platform_secrets import PlatformSecretAccessError


def _asset_universe_response(
    *,
    uid: str = "universe-uid",
    source_uid: str = "source-uid",
    asset_category_uid: str = "category-uid",
    display_name: str = "S&P 500 holdings",
    symbol: str = "IVV",
    is_active: bool = True,
    asset_count: int = 0,
) -> AssetUniverseResponse:
    now = datetime(2026, 9, 3, tzinfo=UTC)
    return AssetUniverseResponse(
        uid=uid,
        source_uid=source_uid,
        asset_category_uid=asset_category_uid,
        display_name=display_name,
        symbol=symbol,
        source_url=f"https://example.com/{symbol.lower()}",
        description=f"Configured holdings universe for ETF {symbol}.",
        is_active=is_active,
        asset_count=asset_count,
        asset_category={
            "uid": asset_category_uid,
            "unique_identifier": f"HOLDINGS__{symbol}",
            "display_name": display_name,
            "description": f"Configured holdings universe for ETF {symbol}.",
        },
        created_at=now,
        updated_at=now,
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

    def test_api_is_organized_by_current_capabilities(self) -> None:
        visible_paths = {
            route.path
            for route in app.routes
            if isinstance(route, APIRoute) and route.include_in_schema
        }

        expected_capability_roots = {
            "/v1/accounts",
            "/v1/accounts/actions/capture-holdings",
            "/v1/accounts/{account_uid}/holdings",
            "/v1/accounts/{account_uid}/holdings/latest",
            "/v1/assets",
            "/v1/assets/registration/operations",
            "/v1/assets/registration/operations/{operation_uid}",
            "/v1/universe-sources",
            "/v1/universes",
            "/v1/universes/{universe_uid}/assets",
            "/v1/market-data/datasets",
            "/v1/market-data/bar-configurations",
            "/v1/operations/job-runs/{job_run_uid}",
        }
        self.assertTrue(expected_capability_roots.issubset(visible_paths))
        self.assertNotIn("/v1/universes/holdings/plan", visible_paths)
        self.assertNotIn("/v1/universes/holdings/execute", visible_paths)

    def test_capability_catalog_contains_domain_capabilities_without_connections(self) -> None:
        response = self.client.get("/v1/project-state/capabilities")

        self.assertEqual(response.status_code, 200)
        capability_keys = [item["key"] for item in response.json()["capabilities"]]
        self.assertEqual(
            capability_keys,
            [
                "project_state",
                "assets",
                "universes",
                "market_data",
                "accounts",
                "holdings",
                "portfolios",
                "operations",
            ],
        )
        self.assertNotIn("connections", capability_keys)

    def test_asset_registration_operation_start_returns_pollable_status(self) -> None:
        now = datetime.now(UTC)
        operation = AssetRegistrationOperationResponse(
            operation_uid="11111111-1111-4111-8111-111111111111",
            action="plan",
            status="queued",
            current_step=None,
            steps=[
                {
                    "key": "prepare_scope",
                    "label": "Prepare registration scope",
                    "status": "pending",
                }
            ],
            request=AssetRegistrationRequest(account_uid="account-uid", symbols=["AAPL"]),
            result=None,
            error=None,
            created_at=now,
            started_at=None,
            updated_at=now,
            completed_at=None,
        )
        with (
            patch(
                "api.app.routers.assets.start_asset_registration_operation",
                return_value=operation,
            ),
            patch("api.app.routers.assets.run_asset_registration_operation") as run_operation,
        ):
            response = self.client.post(
                "/v1/assets/registration/operations",
                json={
                    "action": "plan",
                    "request": {"account_uid": "account-uid", "symbols": ["AAPL"]},
                },
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["operation_uid"], operation.operation_uid)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(response.json()["poll_after_ms"], 500)
        run_operation.assert_called_once()

    def test_asset_registration_operation_poll_is_never_cached(self) -> None:
        now = datetime.now(UTC)
        operation = AssetRegistrationOperationResponse(
            operation_uid="11111111-1111-4111-8111-111111111111",
            action="plan",
            status="running",
            current_step="prepare_scope",
            steps=[
                {
                    "key": "prepare_scope",
                    "label": "Prepare registration scope",
                    "status": "running",
                    "started_at": now,
                }
            ],
            request=AssetRegistrationRequest(account_uid="account-uid", symbols=["AAPL"]),
            result=None,
            error=None,
            created_at=now,
            started_at=now,
            updated_at=now,
            completed_at=None,
        )
        with patch(
            "api.app.routers.assets.get_asset_registration_operation_status",
            return_value=operation,
        ) as get_status:
            response = self.client.get(
                f"/v1/assets/registration/operations/{operation.operation_uid}"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["current_step"], "prepare_scope")
        get_status.assert_called_once_with(operation.operation_uid, owner_uid=None)

    def test_asset_registration_requires_a_registered_account_uid(self) -> None:
        response = self.client.post(
            "/v1/assets/registration/operations",
            json={"action": "plan", "request": {"symbols": ["AAPL"]}},
        )

        self.assertEqual(response.status_code, 422)

    def test_discovery_config_returns_application_contract(self) -> None:
        mocked_response = ProjectConfigurationResponse(
            supported_component_providers=["ishares"],
            migrated_market_data_profiles=["1d/sip/all"],
        )
        with patch(
            "api.app.routers.project_state.get_project_configuration",
            return_value=mocked_response,
        ):
            response = self.client.get("/v1/project-state/configuration")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mocked_response.model_dump(mode="json"))

    def test_bar_configuration_update_submits_job_and_returns_poll_url(self) -> None:
        configuration_uid = "11111111-1111-4111-8111-111111111111"
        job_run_uid = "33333333-3333-4333-8333-333333333333"
        accepted = BarConfigurationUpdateAcceptedResponse(
            configuration_uid=configuration_uid,
            job_uid="22222222-2222-4222-8222-222222222222",
            job_run_uid=job_run_uid,
            status="PENDING",
            status_url=f"/v1/operations/job-runs/{job_run_uid}",
        )
        with (
            patch(
                "api.app.routers.bar_configurations.submit_configuration_update",
                return_value=accepted,
            ) as submit,
            patch("src.market_data.execute_market_data_update") as execute,
        ):
            response = self.client.post(
                f"/v1/market-data/bar-configurations/{configuration_uid}/actions/update",
                json={},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), accepted.model_dump(mode="json"))
        submit.assert_called_once_with(configuration_uid)
        execute.assert_not_called()

    def test_bar_configuration_update_rejects_runtime_overrides(self) -> None:
        response = self.client.post(
            "/v1/market-data/bar-configurations/"
            "11111111-1111-4111-8111-111111111111/actions/update",
            json={"force_update": False, "dataset_uid": "not-allowed"},
        )

        self.assertEqual(response.status_code, 422)

    def test_job_run_status_is_pollable_and_never_cached(self) -> None:
        job_run_uid = "33333333-3333-4333-8333-333333333333"
        status = JobRunStatusResponse(
            uid=job_run_uid,
            job_uid="22222222-2222-4222-8222-222222222222",
            job_name="Alpaca Bars Update",
            configuration_uid="11111111-1111-4111-8111-111111111111",
            status="RUNNING",
            execution_start=datetime.now(UTC),
            execution_end=None,
            commit_hash="abc123",
            runtime_image_uid="44444444-4444-4444-8444-444444444444",
            runtime_image_digest="sha256:123",
            command_args=[
                "--configuration-uid",
                "11111111-1111-4111-8111-111111111111",
            ],
            logs_url="https://logs.example/run",
            error=None,
        )
        with patch(
            "api.app.routers.operations.get_job_run",
            return_value=status,
        ) as get_status:
            response = self.client.get(f"/v1/operations/job-runs/{job_run_uid}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json(), status.model_dump(mode="json"))
        get_status.assert_called_once_with(job_run_uid)

    def test_account_registration_rejects_raw_credentials(self) -> None:
        response = self.client.post(
            "/v1/accounts",
            json={
                "environment": "paper",
                "api_key_secret_name": "ALPACA_API_KEY",
                "secret_key_secret_name": "ALPACA_SECRET_KEY",
                "api_key": "raw-value-must-not-be-accepted",
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_account_secret_references_expose_names_only(self) -> None:
        visible_secrets = [
            SimpleNamespace(name="ALPACA_PAPER_SECRET_KEY", value="private-value"),
            SimpleNamespace(name="ALPACA_PAPER_API_KEY", value="another-private-value"),
        ]
        with patch(
            "api.app.services.accounts.msc.Secret.filter",
            return_value=visible_secrets,
        ):
            response = self.client.get("/v1/accounts/secret-references")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["items"],
            [
                {"name": "ALPACA_PAPER_API_KEY"},
                {"name": "ALPACA_PAPER_SECRET_KEY"},
            ],
        )
        self.assertNotIn("private-value", response.text)
        self.assertNotIn("another-private-value", response.text)

    def test_account_response_drops_internal_payload_and_fingerprint(self) -> None:
        response = AccountResponse.model_validate(
            {
                "uid": "account-uid",
                "account_uid": "account-uid",
                "unique_identifier": "123__ALPACA_PAPER",
                "account_name": "Paper",
                "is_paper": True,
                "account_is_active": True,
                "api_key_secret_name": "ALPACA_PAPER_API_KEY",
                "secret_key_secret_name": "ALPACA_PAPER_SECRET_KEY",
                "api_key_fingerprint": "fingerprint",
                "raw_account_payload": {"account_number": "123"},
            }
        ).model_dump(mode="json")

        self.assertNotIn("raw_account_payload", response)
        self.assertNotIn("api_key_fingerprint", response)

    def test_collection_pagination_requires_aligned_offset(self) -> None:
        response = self.client.get("/v1/assets?limit=25&offset=1")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "invalid_request")

    def test_collection_ordering_rejects_unknown_field(self) -> None:
        response = self.client.get("/v1/assets?ordering=not_a_field")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "invalid_request")

    def test_openapi_never_declares_raw_alpaca_credential_fields(self) -> None:
        schemas = self.client.get("/openapi.json").json()["components"]["schemas"]
        property_names = {
            property_name
            for schema in schemas.values()
            for property_name in schema.get("properties", {})
        }

        self.assertNotIn("api_key", property_names)
        self.assertNotIn("secret_key", property_names)
        self.assertNotIn("secretValues", property_names)
        self.assertIn("api_key_secret_name", property_names)
        self.assertIn("secret_key_secret_name", property_names)

    def test_universe_source_preview_route_returns_conflict_on_blocker(self) -> None:
        with patch(
            "api.app.routers.universe_sources.preview_source",
            side_effect=ValueError("blocked"),
        ):
            response = self.client.post(
                "/v1/universe-sources/source-uid/actions/preview",
                json={"timeout": 30},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "action_conflict",
                "message": "blocked",
                "retryable": False,
            },
        )

    def test_universe_source_preview_hides_provider_exception_details(self) -> None:
        provider_message = "provider workbook contains private diagnostic context"
        with patch(
            "api.app.routers.universe_sources.preview_source",
            side_effect=WorkbookParseError(provider_message),
        ):
            response = self.client.post(
                "/v1/universe-sources/source-uid/actions/preview",
                json={"timeout": 30},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["code"], "provider_failure")
        self.assertNotIn(provider_message, response.text)

    def test_holdings_bulk_action_rejects_registry_relaxation_option(self) -> None:
        response = self.client.post(
            "/v1/accounts/actions/capture-holdings",
            json={
                "selection": {"mode": "explicit", "uids": ["account-uid"]},
                "options": {"register_missing_assets": "false"},
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "invalid_request")

    def test_account_registration_rejects_legacy_registry_relaxation(self) -> None:
        response = self.client.post(
            "/v1/accounts",
            json={
                "environment": "paper",
                "api_key_secret_name": "ALPACA_API_KEY",
                "secret_key_secret_name": "ALPACA_SECRET_KEY",
                "register_missing_assets": False,
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_account_registration_rejects_optional_initial_holdings_flag(self) -> None:
        response = self.client.post(
            "/v1/accounts",
            json={
                "environment": "paper",
                "api_key_secret_name": "ALPACA_API_KEY",
                "secret_key_secret_name": "ALPACA_SECRET_KEY",
                "capture_initial_holdings": False,
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_account_registration_identity_conflict_exposes_affected_asset(self) -> None:
        message = (
            "Alpaca asset identity mismatch for held position 'AAPL': the position references "
            "asset UUID 11111111-1111-4111-8111-111111111111, but the asset catalog returned "
            "'WRONG' with UUID 33333333-3333-4333-8333-333333333333."
        )
        with patch(
            "api.app.routers.accounts.create_account_registration",
            side_effect=ValueError(message),
        ):
            response = self.client.post(
                "/v1/accounts",
                json={
                    "environment": "paper",
                    "api_key_secret_name": "ALPACA_API_KEY",
                    "secret_key_secret_name": "ALPACA_SECRET_KEY",
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["message"], message)

    def test_direct_holdings_capture_rejects_legacy_registry_relaxation(self) -> None:
        response = self.client.post(
            "/v1/accounts/account-uid/holdings/actions/capture",
            json={"register_missing_assets": False},
        )

        self.assertEqual(response.status_code, 422)

    def test_holdings_list_can_scope_to_the_latest_account_snapshot(self) -> None:
        collection = {
            "items": [],
            "pageInfo": {
                "pageIndex": 0,
                "pageSize": 25,
                "totalItems": 0,
                "hasNextPage": False,
                "hasPreviousPage": False,
            },
        }
        with patch(
            "api.app.routers.holdings.list_holdings",
            return_value=collection,
        ) as list_holdings:
            response = self.client.get("/v1/accounts/account-uid/holdings/latest?limit=25&offset=0")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(list_holdings.call_args.kwargs["latest_only"])
        self.assertEqual(list_holdings.call_args.args[0], "account-uid")

    def test_holdings_registry_failure_returns_exact_assets(self) -> None:
        error = AccountHoldingsRegistryError(
            unresolved_symbols=["AAPL", "USD"],
        )
        with patch("api.app.routers.holdings.capture_holdings", side_effect=error):
            response = self.client.post(
                "/v1/accounts/account-uid/holdings/actions/capture",
                json={},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "action_conflict")
        message = response.json()["detail"]["message"]
        self.assertIn("AAPL, USD", message)
        self.assertIn("No holdings snapshot was written", message)

    def test_unhandled_api_errors_use_sanitized_fallback(self) -> None:
        private_message = "provider exception containing private context"
        safe_client = TestClient(app, raise_server_exceptions=False)
        with patch(
            "api.app.routers.accounts.get_account",
            side_effect=RuntimeError(private_message),
        ):
            response = safe_client.get("/v1/accounts/account-uid")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "dependency_unavailable")
        self.assertNotIn(private_message, response.text)

    def test_secret_access_error_explains_that_visible_secret_value_could_not_be_read(self) -> None:
        message = (
            "Main Sequence Secret 'ALPACA_API_KEY__JOSE_DEV' exists, but this API runtime "
            "could not read its value."
        )
        with patch(
            "api.app.routers.accounts.preflight_account_registration",
            side_effect=PlatformSecretAccessError(message),
        ):
            response = self.client.post(
                "/v1/accounts/registration/preflight",
                json={
                    "environment": "paper",
                    "api_key_secret_name": "ALPACA_API_KEY__JOSE_DEV",
                    "secret_key_secret_name": "ALPACA_SECRET_KEY__JOSE_DEV",
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "secret_resolution_failed",
                "message": message,
                "retryable": True,
            },
        )

    def test_discovery_uses_installed_command_center_contract_shape(self) -> None:
        response = self.client.get("/v1/universe-sources/discovery")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["contract"], "command-center.resource_discovery@v1")
        self.assertEqual(payload["resource"]["identity"], {"fields": ["uid"]})
        self.assertIn("controls", payload["list"])
        self.assertIn("columns", payload["list"])
        self.assertIsInstance(payload["bulk_actions"], list)
        self.assertEqual(
            response.headers["cache-control"],
            "private, max-age=0, must-revalidate",
        )
        self.assertIn("authorization", response.headers["vary"].lower())
        self.assertTrue(response.headers["etag"].startswith('"'))

    def test_universe_discovery_advertises_lifecycle_and_delete_actions(self) -> None:
        response = self.client.get("/v1/universes/discovery")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        columns = {column["id"]: column for column in payload["list"]["columns"]}
        self.assertIn("uid", columns)
        self.assertEqual(columns["uid"]["header"], "UID")
        self.assertNotIn("account-name", columns)
        self.assertNotIn("unique-identifier", columns)
        self.assertIn("is-active", [column["id"] for column in payload["list"]["columns"]])
        self.assertEqual(
            [action["id"] for action in payload["bulk_actions"]],
            ["activate", "deactivate", "remove"],
        )
        delete_action = payload["bulk_actions"][-1]
        self.assertEqual(delete_action["confirmation"]["word"], "DELETE")

    def test_universe_create_stores_an_empty_configured_universe(self) -> None:
        universe = _asset_universe_response(
            uid="universe-dia",
            source_uid="source-dia",
            asset_category_uid="category-dia",
            display_name="Dow Jones ETF holdings",
            symbol="DIA",
        )
        with patch(
            "api.app.routers.universes.create_universe",
            return_value=universe,
        ) as create:
            response = self.client.post(
                "/v1/universes",
                json={
                    "name": "Dow Jones ETF holdings",
                    "symbol": "DIA",
                    "source_url": "https://example.com/dia",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["uid"], "universe-dia")
        self.assertEqual(response.json()["source_uid"], "source-dia")
        self.assertEqual(response.json()["asset_category_uid"], "category-dia")
        self.assertEqual(response.json()["asset_count"], 0)
        create.assert_called_once()

    def test_universe_run_executes_selected_universe(self) -> None:
        universe = _asset_universe_response()
        result = {
            "universe_uid": "universe-uid",
            "source_uid": "source-uid",
            "asset_category_uid": "category-uid",
            "account_uid": "account-uid",
            "asset_uids": ["asset-aapl", "asset-msft", "asset-nvda"],
            "asset_count": 3,
            "existing_asset_uids_by_symbol": {},
            "created_asset_uids_by_symbol": {
                "AAPL": "asset-aapl",
                "MSFT": "asset-msft",
                "NVDA": "asset-nvda",
            },
            "openfigi_unmatched_symbols": [],
        }
        with (
            patch("api.app.routers.universes.get_universe", return_value=universe),
            patch(
                "api.app.routers.universes.run_universe",
                return_value=result,
            ) as run,
        ):
            response = self.client.post(
                "/v1/universes/actions/run",
                json={
                    "selection": {"mode": "explicit", "uids": ["universe-uid"]},
                    "options": {"account_uid": "account-uid"},
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["asset_count"], 3)
        run.assert_called_once_with(
            "universe-uid",
            account_uid="account-uid",
            timeout=30.0,
        )

    def test_universe_assets_returns_standard_paginated_asset_resources(self) -> None:
        asset = {
            "uid": "asset-aapl",
            "unique_identifier": "ALPACA::aapl-uuid",
            "asset_type": "equity",
            "alpaca_asset_id": "aapl-uuid",
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "exchange": "NASDAQ",
            "status": "active",
            "tradable": True,
            "figi": None,
            "composite_figi": None,
        }
        collection = {
            "items": [asset],
            "pageInfo": {
                "pageIndex": 0,
                "pageSize": 10,
                "totalItems": 1,
                "hasNextPage": False,
                "hasPreviousPage": False,
            },
        }
        with patch(
            "api.app.routers.universes.list_universe_assets",
            return_value=collection,
        ) as list_assets:
            response = self.client.get(
                "/v1/universes/universe-uid/assets",
                params={"limit": 10, "offset": 0, "search": "AAPL", "ordering": "-ticker"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), collection)
        list_assets.assert_called_once_with(
            "universe-uid",
            limit=10,
            offset=0,
            search="AAPL",
            ordering="-ticker",
        )

    def test_universe_run_preflight_allows_automatic_constituent_registration(self) -> None:
        universe = _asset_universe_response()
        preview = {
            "universe": universe.model_dump(mode="json"),
            "plan_summary": {
                "symbols_to_register": ["AAPL", "MSFT"],
                "missing_symbols_from_alpaca": [],
            },
            "has_blockers": False,
            "blockers": [],
            "warnings": [
                "Run will register 2 missing Alpaca-backed constituent asset(s) through "
                "selected account account-uid before refreshing the universe membership."
            ],
        }
        with (
            patch("api.app.routers.universes.get_universe", return_value=universe),
            patch(
                "api.app.routers.universes.preview_universe_run",
                return_value=preview,
            ) as preview_run,
        ):
            response = self.client.post(
                "/v1/universes/actions/run/preflight",
                json={
                    "selection": {"mode": "explicit", "uids": ["universe-uid"]},
                    "options": {"account_uid": "account-uid"},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["allowed"])
        self.assertEqual(payload["matched_count"], 1)
        self.assertEqual(payload["blockers"], [])
        self.assertEqual(payload["warnings"], preview["warnings"])
        self.assertIn("register 2 missing", response.text)
        preview_run.assert_called_once_with(
            "universe-uid",
            account_uid="account-uid",
            timeout=30.0,
        )

    def test_universe_run_requires_an_execution_account(self) -> None:
        response = self.client.post(
            "/v1/universes/actions/run/preflight",
            json={
                "selection": {"mode": "explicit", "uids": ["universe-uid"]},
                "options": {},
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("options.account_uid", response.text)

    def test_universe_preview_blocks_only_symbols_missing_from_alpaca(self) -> None:
        universe = _asset_universe_response()
        registration_plan = SimpleNamespace(
            missing_symbols_from_alpaca=["UNKNOWN"],
        )
        registration_resolution = SimpleNamespace(
            missing_assets=[SimpleNamespace(symbol="AAPL")],
            unresolved_symbols_from_alpaca=["UNKNOWN"],
            existing_off_catalog_assets_by_symbol={},
        )
        plan = SimpleNamespace(
            registration_plan=registration_plan,
            registration_resolution=registration_resolution,
            account_uid="account-uid",
            has_blockers=lambda: True,
            summary=lambda: {
                "symbols_to_register": ["AAPL"],
                "missing_symbols_from_alpaca": ["UNKNOWN"],
            },
        )
        with (
            patch(
                "api.app.services.universes.get_universe",
                return_value=universe,
            ),
            patch(
                "api.app.services.universes.preview_asset_universe",
                return_value=plan,
            ),
        ):
            preview = preview_universe_service(
                "universe-uid",
                account_uid="account-uid",
                timeout=30.0,
            )

        self.assertTrue(preview["has_blockers"])
        self.assertEqual(len(preview["blockers"]), 1)
        self.assertIn("UNKNOWN", preview["blockers"][0])
        self.assertEqual(len(preview["warnings"]), 1)
        self.assertIn("register 1 missing", preview["warnings"][0])

    def test_universe_deactivate_action_updates_selected_universe(self) -> None:
        universe = _asset_universe_response(
            asset_count=1,
        )
        inactive = universe.model_copy(update={"is_active": False})
        with (
            patch("api.app.routers.universes.get_universe", return_value=universe),
            patch("api.app.routers.universes.update_universe", return_value=inactive) as update,
        ):
            response = self.client.post(
                "/v1/universes/actions/deactivate",
                json={
                    "selection": {"mode": "explicit", "uids": ["universe-uid"]},
                    "options": {},
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["results"][0]["is_active"])
        self.assertFalse(update.call_args.args[1].is_active)

    def test_universe_delete_preflight_names_referencing_bar_configuration(self) -> None:
        universe = _asset_universe_response()
        blocker = (
            "Universe universe-uid is referenced by bar configuration "
            "Daily IVV bars (bars-configuration-uid)."
        )
        with (
            patch("api.app.routers.universes.get_universe", return_value=universe),
            patch(
                "api.app.routers.universes.universe_delete_blockers",
                return_value=[blocker],
            ),
        ):
            response = self.client.post(
                "/v1/universes/actions/remove/preflight",
                json={
                    "selection": {"mode": "explicit", "uids": ["universe-uid"]},
                    "options": {},
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["allowed"])
        self.assertEqual(response.json()["blockers"], [blocker])

    def test_every_discovery_payload_obeys_manifest_column_and_action_constraints(self) -> None:
        paths = [
            "/v1/assets/discovery",
            "/v1/accounts/discovery",
            "/v1/accounts/account-uid/holdings/discovery",
            "/v1/universe-sources/discovery",
            "/v1/universes/discovery",
            "/v1/market-data/bar-configurations/discovery",
            "/v1/market-data/datasets/discovery",
            "/v1/market-data/datasets/dataset-uid/observations/discovery",
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                controls = payload["list"]["controls"]
                filter_keys = {item["key"] for item in controls["filters"]}
                for item in controls["filters"]:
                    self.assertIn(item["type"], {"text", "boolean", "select"})
                    if item["type"] == "select":
                        self.assertTrue(item.get("options"))
                ordering_keys = set(controls["ordering"])
                for column in payload["list"]["columns"]:
                    self.assertRegex(column["id"], re.compile(r"^[a-z][a-z0-9-]*$"))
                    self.assertIn("value_path", column)
                    self.assertIn("data_type", column)
                    if "sortable_key" in column:
                        self.assertIn(column["sortable_key"], ordering_keys)
                    if "filter_key" in column:
                        self.assertIn(column["filter_key"], filter_keys)
                for action in payload["bulk_actions"]:
                    self.assertTrue(action["endpoint"].startswith("/"))
                    if "preflight_endpoint" in action:
                        self.assertTrue(action["preflight_endpoint"].startswith("/"))

    def test_asset_name_discovery_column_has_generic_renderer_metadata(self) -> None:
        response = self.client.get("/v1/assets/discovery")

        self.assertEqual(response.status_code, 200)
        name_column = next(
            column for column in response.json()["list"]["columns"] if column["id"] == "name"
        )
        self.assertEqual(name_column["value_path"], "name")
        self.assertEqual(name_column["data_type"], "text")
        asset_type_column = next(
            column for column in response.json()["list"]["columns"] if column["id"] == "asset-type"
        )
        self.assertEqual(asset_type_column["value_path"], "asset_type")
        self.assertEqual(asset_type_column["data_type"], "text")


if __name__ == "__main__":
    unittest.main()
