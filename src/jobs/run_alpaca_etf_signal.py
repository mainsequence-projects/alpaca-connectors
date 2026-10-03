"""Execute the Alpaca ETF signal configuration linked to this Main Sequence Job."""

from __future__ import annotations

import json


def main() -> int:
    from alpaca_connectors.migrations.schema_gate import skip_if_schema_behind

    if skip_if_schema_behind("Alpaca ETF signal Job"):
        return 0

    from alpaca_connectors.operations import execute_current_signal_job

    result = execute_current_signal_job()
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
