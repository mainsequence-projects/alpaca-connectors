"""Execute one reviewed, stored Alpaca bar configuration."""

from __future__ import annotations

import os


def main() -> int:
    from src.market_data import execute_market_data_update

    configuration_uid = os.environ.get("ALPACA_BARS_CONFIGURATION_UID", "").strip()
    if not configuration_uid:
        raise RuntimeError(
            "ALPACA_BARS_CONFIGURATION_UID must identify an enabled stored bar configuration."
        )
    execute_market_data_update(configuration_uid=configuration_uid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
