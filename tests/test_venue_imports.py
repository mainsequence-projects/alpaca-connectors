"""The venue packages never load the ETF extractor (ADR 0027).

Asset registration, accounts, holdings, market data, execution, portfolios (those built from
provided weights included) and the account sync run without `etfhextractor` and without the ETF
modules, so the ETF objects can be drained and the library removed without touching them. Each
check imports in a fresh interpreter: this test process has already loaded everything.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

VENUE_MODULES = (
    "alpaca_connectors.assets",
    "alpaca_connectors.assets.calendars",
    "alpaca_connectors.account",
    "alpaca_connectors.account.services",
    "alpaca_connectors.holdings",
    "alpaca_connectors.holdings.services",
    "alpaca_connectors.market_data",
    "alpaca_connectors.market_data.configurations",
    "alpaca_connectors.market_data.services",
    "alpaca_connectors.execution",
    "alpaca_connectors.execution.services",
    "alpaca_connectors.portfolios",
    "alpaca_connectors.portfolios.bars_scope",
    "alpaca_connectors.portfolios.execution",
    "alpaca_connectors.portfolios.provided_weights",
    "alpaca_connectors.operations.account_sync",
    "alpaca_connectors.runtime",
)

ETF_MODULES = frozenset(
    {
        "alpaca_connectors.operations.signal_job_configurations",
        "alpaca_connectors.operations.signal_jobs",
        "alpaca_connectors.portfolios.alpaca_etf_signal",
        "alpaca_connectors.portfolios.etf_tracking",
        "alpaca_connectors.universes.etf_holdings",
        "alpaca_connectors.universes.materialized",
        "alpaca_connectors.universes.registry",
        "alpaca_connectors.universes.services",
        "alpaca_connectors.universes.sources",
    }
)


def _loaded_after_importing(*modules: str) -> set[str]:
    code = (
        "import importlib, json, sys\n"
        "for name in sys.argv[1:]:\n"
        "    importlib.import_module(name)\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, *modules],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
        timeout=180,
    )
    return set(json.loads(result.stdout.splitlines()[-1]))


def test_the_venue_packages_load_neither_the_etf_extractor_nor_an_etf_module() -> None:
    loaded = _loaded_after_importing(*VENUE_MODULES)

    assert set(VENUE_MODULES) <= loaded
    assert "etfhextractor" not in loaded
    assert loaded.isdisjoint(ETF_MODULES), sorted(loaded & ETF_MODULES)


def test_the_schema_gate_every_job_launcher_imports_never_loads_the_etf_extractor() -> None:
    # Every launcher imports the gate, and with it the migration provider, before any work.
    loaded = _loaded_after_importing("alpaca_connectors.migrations.schema_gate")

    assert "etfhextractor" not in loaded
