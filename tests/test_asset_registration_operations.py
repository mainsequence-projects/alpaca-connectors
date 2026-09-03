from __future__ import annotations

import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from api.app.schemas import AssetRegistrationDiscoveryResponse, AssetRegistrationRequest
from api.app.services.assets import (
    build_asset_registration_discovery,
    run_asset_registration_operation,
)
from requests import ConnectionError as RequestsConnectionError
from requests import Timeout as RequestsTimeout

from src.operations.asset_registration import (
    fail_asset_registration_operation,
    registration_steps,
)


class AssetRegistrationOperationTests(unittest.TestCase):
    def test_plan_and_execute_steps_match_real_orchestration_boundaries(self) -> None:
        self.assertEqual(
            [step["key"] for step in registration_steps("plan")],
            [
                "prepare_scope",
                "load_alpaca_assets",
                "resolve_openfigi_identities",
                "check_existing_assets",
                "finalize_plan",
            ],
        )
        self.assertEqual(
            [step["key"] for step in registration_steps("execute")],
            [
                "prepare_scope",
                "load_alpaca_assets",
                "resolve_openfigi_identities",
                "check_existing_assets",
                "register_assets",
                "finalize_result",
            ],
        )

    def test_registration_plan_emits_ordered_step_transitions(self) -> None:
        request = AssetRegistrationRequest(symbols=["AAPL"])
        plan = SimpleNamespace(
            matches_by_symbol={"AAPL": SimpleNamespace(symbol="AAPL")},
            unresolved_symbols=[],
            missing_symbols_from_alpaca=[],
            warnings_by_symbol={},
            summary=lambda: {"requested": 1},
        )
        resolution = SimpleNamespace(
            existing_assets_by_symbol={},
            missing_matches=[SimpleNamespace(symbol="AAPL")],
            summary=lambda: {"missing": 1},
        )
        events: list[tuple[str, str, str | None]] = []

        def build_plan(**kwargs):
            progress = kwargs["progress"]
            progress("load_alpaca_assets", "running", "Loading Alpaca catalog.")
            progress("load_alpaca_assets", "succeeded", "Loaded Alpaca catalog.")
            progress("resolve_openfigi_identities", "running", "Resolving OpenFIGI.")
            progress("resolve_openfigi_identities", "succeeded", "Resolved OpenFIGI.")
            return plan

        with (
            patch(
                "api.app.services.assets.build_alpaca_us_equity_registration_plan",
                side_effect=build_plan,
            ),
            patch(
                "api.app.services.assets.resolve_alpaca_us_equity_registration_plan",
                return_value=resolution,
            ),
        ):
            result = build_asset_registration_discovery(
                request,
                progress=lambda key, status, message: events.append((key, status, message)),
            )

        self.assertTrue(result.can_register)
        self.assertEqual(
            [(key, status) for key, status, _ in events],
            [
                ("prepare_scope", "running"),
                ("prepare_scope", "succeeded"),
                ("load_alpaca_assets", "running"),
                ("load_alpaca_assets", "succeeded"),
                ("resolve_openfigi_identities", "running"),
                ("resolve_openfigi_identities", "succeeded"),
                ("check_existing_assets", "running"),
                ("check_existing_assets", "succeeded"),
                ("finalize_plan", "running"),
                ("finalize_plan", "succeeded"),
            ],
        )

    def test_failed_worker_reports_credential_dns_failure_before_openfigi(self) -> None:
        request = AssetRegistrationRequest(symbols=["AAPL"])

        def fail_loading_catalog(_request, **kwargs):
            kwargs["progress"]("load_alpaca_assets", "running", "Loading Alpaca catalog.")
            dns_error = socket.gaierror("name resolution failed")
            connection_error = RequestsConnectionError("backend unavailable")
            connection_error.__cause__ = dns_error
            credential_error = RuntimeError(
                "Failed to retrieve MainSequence secret 'ALPACA_API_KEY'."
            )
            credential_error.__cause__ = connection_error
            raise credential_error

        with (
            patch(
                "api.app.services.assets.build_asset_registration_discovery",
                side_effect=fail_loading_catalog,
            ),
            patch("api.app.services.assets.set_asset_registration_step"),
            patch("api.app.services.assets.fail_asset_registration_operation") as fail_operation,
        ):
            run_asset_registration_operation(
                "11111111-1111-4111-8111-111111111111",
                action="plan",
                request=request,
            )

        fail_operation.assert_called_once_with(
            "11111111-1111-4111-8111-111111111111",
            step_key="load_alpaca_assets",
            error={
                "code": "credential_service_dns_failure",
                "message": (
                    "DNS could not resolve the configured Main Sequence backend while "
                    "retrieving the Alpaca credential secret. OpenFIGI was not called."
                ),
                "retryable": True,
            },
        )

    def test_failed_worker_reports_openfigi_timeout(self) -> None:
        request = AssetRegistrationRequest(symbols=["AAPL"], timeout=17)

        def fail_openfigi(_request, **kwargs):
            progress = kwargs["progress"]
            progress("load_alpaca_assets", "running", "Loading Alpaca catalog.")
            progress("load_alpaca_assets", "succeeded", "Loaded Alpaca catalog.")
            progress("resolve_openfigi_identities", "running", "Resolving OpenFIGI.")
            raise RequestsTimeout("private request detail")

        with (
            patch(
                "api.app.services.assets.build_asset_registration_discovery",
                side_effect=fail_openfigi,
            ),
            patch("api.app.services.assets.set_asset_registration_step"),
            patch("api.app.services.assets.fail_asset_registration_operation") as fail_operation,
        ):
            run_asset_registration_operation(
                "11111111-1111-4111-8111-111111111111",
                action="plan",
                request=request,
            )

        fail_operation.assert_called_once_with(
            "11111111-1111-4111-8111-111111111111",
            step_key="resolve_openfigi_identities",
            error={
                "code": "openfigi_timeout",
                "message": "OpenFIGI did not respond within the configured 17-second timeout.",
                "retryable": True,
            },
        )

    def test_failed_step_marks_future_steps_skipped(self) -> None:
        operation = SimpleNamespace(steps=registration_steps("execute"))
        with (
            patch(
                "src.operations.asset_registration.get_asset_registration_operation",
                return_value=operation,
            ),
            patch(
                "src.operations.asset_registration.AssetRegistrationOperation.update",
                return_value=operation,
            ) as update_operation,
        ):
            fail_asset_registration_operation(
                "11111111-1111-4111-8111-111111111111",
                step_key="resolve_openfigi_identities",
                error={
                    "code": "provider_failure",
                    "message": "Provider failed.",
                    "retryable": True,
                },
            )

        steps = update_operation.call_args.args[1]["steps"]

        self.assertEqual(steps[2]["status"], "failed")
        self.assertEqual(steps[2]["message"], "Provider failed.")
        self.assertEqual(steps[3]["status"], "skipped")
        self.assertEqual(steps[4]["status"], "skipped")
        self.assertEqual(steps[5]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
