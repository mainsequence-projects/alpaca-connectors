"""Entry point of the workflow-declared `alpaca-etf-signal` Job (ADR 0013).

`--configuration-uid <uid>` executes one signal configuration. The API, the CLI and the
`alpaca-etf-signal-dispatcher` Job's scheduled run start these runs; the Job has no schedule of
its own. The schema gate (ADR 0012) comes first.
"""

from __future__ import annotations

import json
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    from alpaca_connectors.migrations.schema_gate import skip_if_schema_behind

    if skip_if_schema_behind("Alpaca ETF signal Job"):
        return 0

    from alpaca_connectors.operations.configuration_jobs import run_configuration_job

    summary = run_configuration_job("signal", argv)
    print(json.dumps(summary, sort_keys=True, default=str))
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
