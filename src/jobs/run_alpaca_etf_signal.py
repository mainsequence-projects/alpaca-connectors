"""Execute the Alpaca ETF signal configuration linked to this Main Sequence Job."""

from __future__ import annotations

import json


def main() -> int:
    from src.operations import execute_current_signal_job

    result = execute_current_signal_job()
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
