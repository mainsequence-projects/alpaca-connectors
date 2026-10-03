"""Execute the ETF portfolio configuration linked to this Main Sequence Job."""

from __future__ import annotations

import json


def main() -> int:
    from alpaca_connectors.migrations.schema_gate import skip_if_schema_behind

    # Before the runtime bootstrap, which itself resolves project tables.
    if skip_if_schema_behind("Alpaca ETF portfolio Job"):
        return 0

    from alpaca_connectors.runtime import start_portfolio_job_engine

    start_portfolio_job_engine()

    from alpaca_connectors.operations import execute_current_portfolio_job

    result = execute_current_portfolio_job()
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
