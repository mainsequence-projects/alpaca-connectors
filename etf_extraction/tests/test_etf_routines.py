from __future__ import annotations

import io
import contextlib
import textwrap
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from src.jobs.run_etf_maintenance_routines import (
    RoutineSpec,
    _build_routine_steps,
    _load_routines,
    run_routines,
)


class EtfRoutinesTests(unittest.TestCase):
    def test_load_routines_applies_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "etf_routines_test.yaml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    defaults:
                      frequency_id: 1h
                      etf_update_period: hourly
                      include_seed_registration: false
                    routines:
                      - etf_ticker: ivv
                        component_provider: ishares
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            routines = _load_routines(config_path)
            self.assertEqual(len(routines), 1)
            self.assertEqual(routines[0].etf_ticker, "IVV")
            self.assertEqual(routines[0].frequency_id, "1h")
            self.assertEqual(routines[0].feed, "sip")
            self.assertEqual(routines[0].etf_update_period, "hourly")
            self.assertFalse(routines[0].include_seed_registration)

    def test_build_routine_steps_dry_run_exposes_service_previews(self) -> None:
        routine = RoutineSpec(
            etf_ticker="ivv",
            component_provider="ishares",
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            etf_update_period="daily",
            include_seed_registration=True,
            include_category_sync=True,
            include_etf_price_update=True,
            include_category_price_update=True,
        )
        steps = _build_routine_steps(routine, dry_run=True)
        self.assertEqual(steps[0].label, "register_holdings_assets")
        self.assertIn("expand_etf_seed_symbols", steps[0].preview_text)
        self.assertEqual(steps[1].label, "sync_holdings_category")
        self.assertIn("build_holdings_asset_category_plan", steps[1].preview_text)
        self.assertEqual(steps[2].label, "update_etf_prices")
        self.assertIn("build_stock_bars_node", steps[2].preview_text)
        self.assertEqual(steps[3].label, "update_category_prices")
        self.assertIn("build_stock_bars_node", steps[3].preview_text)

    def test_run_routines_dry_run_prints_expected_summary(self) -> None:
        routine = RoutineSpec(
            etf_ticker="ivv",
            component_provider="ishares",
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            etf_update_period="daily",
            include_seed_registration=False,
            include_category_sync=False,
            include_etf_price_update=True,
            include_category_price_update=False,
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = run_routines([routine], dry_run=True, continue_on_error=True)
        self.assertEqual(exit_code, 0)
        self.assertIn("update_etf_prices", stdout.getvalue())

    def test_run_routines_executes_service_steps(self) -> None:
        routine = RoutineSpec(
            etf_ticker="IVV",
            component_provider="ishares",
            frequency_id="1d",
            feed="sip",
            adjustment="all",
            etf_update_period="daily",
            include_seed_registration=True,
            include_category_sync=True,
            include_etf_price_update=False,
            include_category_price_update=False,
        )
        with (
            patch(
                "src.jobs.run_etf_maintenance_routines._execute_seed_registration"
            ) as execute_seed_registration,
            patch(
                "src.jobs.run_etf_maintenance_routines._execute_holdings_category_sync"
            ) as execute_holdings_category_sync,
        ):
            exit_code = run_routines([routine], dry_run=False, continue_on_error=False)

        self.assertEqual(exit_code, 0)
        execute_seed_registration.assert_called_once_with(routine)
        execute_holdings_category_sync.assert_called_once_with(routine)


if __name__ == "__main__":
    unittest.main()
