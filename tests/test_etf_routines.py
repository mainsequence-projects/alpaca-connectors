from __future__ import annotations

import io
import contextlib
import textwrap
import unittest
from pathlib import Path
import tempfile

from src.jobs.run_etf_maintenance_routines import (
    RoutineSpec,
    _build_routine_commands,
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

    def test_build_routine_commands_dry_run_does_not_execute(self) -> None:
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
        commands = _build_routine_commands(routine, dry_run=True)
        self.assertEqual(
            commands[0][1],
            ["asset", "register", "--seed-tickers", "ivv", "--component-provider", "ishares"],
        )
        self.assertEqual(
            commands[1][1],
            ["holdings-category", "create", "--etf-ticker", "ivv"],
        )
        self.assertIn("--plan-only", commands[2][1])
        self.assertIn("--plan-only", commands[3][1])

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


if __name__ == "__main__":
    unittest.main()
