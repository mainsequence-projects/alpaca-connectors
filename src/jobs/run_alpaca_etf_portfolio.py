"""Entry point of the workflow-declared `alpaca-etf-portfolio` Job (ADR 0013).

`--configuration-uid <uid>` executes one portfolio configuration; `--scheduled` (the Job's
scheduled run) starts one run per scheduled portfolio configuration whose preflight passes. The
schema gate (ADR 0012) comes first, before any runtime bootstrap. Executing a configuration
bootstraps the complete portfolio runtime before it reads any configuration.
"""

from __future__ import annotations

import json
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    from alpaca_connectors.migrations.schema_gate import skip_if_schema_behind

    if skip_if_schema_behind("Alpaca ETF portfolio Job"):
        return 0

    from alpaca_connectors.operations.configuration_jobs import run_configuration_job

    summary = run_configuration_job("portfolio", argv)
    print(json.dumps(summary, sort_keys=True, default=str))
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
