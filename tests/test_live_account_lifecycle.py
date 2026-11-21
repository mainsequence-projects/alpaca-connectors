"""The account life cycle end to end, on a real Alpaca paper account and a local stack (ADR 0023).

The run itself is ``scripts/lifecycle_check.py``: its stages, checks, timings and cleanup. This
test runs it once and fails with its report. Nothing here runs unless ``ALPACA_E2E=1``; the stack
it needs is in the script's docstring.

    ALPACA_E2E=1 MARKETS_API_URL=http://127.0.0.1:8322 .venv/bin/python -m pytest \
        tests/test_live_account_lifecycle.py -s
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ALPACA_E2E") != "1",
    reason="The live end-to-end test runs only with ALPACA_E2E=1 (see the module docstring).",
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "lifecycle_check.py"


def _lifecycle_check():
    spec = importlib.util.spec_from_file_location("lifecycle_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses look their module up by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_account_and_portfolio_life_cycle() -> None:
    lifecycle_check = _lifecycle_check()
    api_url = os.environ.get("ALPACA_LIFECYCLE_API_URL", "http://127.0.0.1:8321")
    clock, warnings = lifecycle_check.run(api_url=api_url.rstrip("/"))
    report = lifecycle_check.render(clock)
    print(report)
    for warning in warnings:
        print(f"warning: {warning}")
    assert clock.ok, report
