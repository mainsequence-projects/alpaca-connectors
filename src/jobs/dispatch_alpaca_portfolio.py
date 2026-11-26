"""Entry point of the workflow-declared `alpaca-portfolio-dispatcher` Job (ADR 0013).

The dispatcher holds the portfolio Job's daily schedule. Its scheduled run carries `--scheduled`
and starts one `alpaca-portfolio` run (`--configuration-uid <uid>`) per scheduled portfolio
configuration whose preflight passes, through the `runner` policy it declares on that Job: a Job
cannot declare access to itself, so the portfolio Job cannot start its own runs. The schema gate
(ADR 0012) comes first.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence

# Measured from here, before the first import of the package (docs/operations/jobs.md, "Job
# Resources"): the run's summary reports its imports, its schema gate and its work.
_STARTED = time.perf_counter()


def _seconds(start: float, end: float) -> float:
    return round(end - start, 2)


def main(argv: Sequence[str] | None = None) -> int:
    from alpaca_connectors.migrations.schema_gate import skip_if_schema_behind

    imported = time.perf_counter()
    if skip_if_schema_behind("Alpaca portfolio dispatcher Job"):
        return 0
    gated = time.perf_counter()

    from alpaca_connectors.operations.configuration_jobs import run_dispatcher_job

    summary = run_dispatcher_job("portfolio", argv)
    summary["start_timings"] = {
        "imports_seconds": _seconds(_STARTED, imported),
        "schema_gate_seconds": _seconds(imported, gated),
        "run_seconds": _seconds(gated, time.perf_counter()),
    }
    print(json.dumps(summary, sort_keys=True, default=str))
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
