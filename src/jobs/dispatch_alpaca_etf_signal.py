"""Entry point of the workflow-declared `alpaca-etf-signal-dispatcher` Job (ADR 0013).

The dispatcher holds the signal Job's daily schedule. Its scheduled run carries `--scheduled` and
starts one `alpaca-etf-signal` run (`--configuration-uid <uid>`) per scheduled signal
configuration, through the `runner` policy it declares on that Job: a Job cannot declare access to
itself, so the signal Job cannot start its own runs. The schema gate (ADR 0012) comes first.
"""

from __future__ import annotations

import json
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    from alpaca_connectors.migrations.schema_gate import skip_if_schema_behind

    if skip_if_schema_behind("Alpaca ETF signal dispatcher Job"):
        return 0

    from alpaca_connectors.operations.configuration_jobs import run_dispatcher_job

    summary = run_dispatcher_job("signal", argv)
    print(json.dumps(summary, sort_keys=True, default=str))
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
