"""Entry point of the retired `alpaca-etf-signal` Job, kept for one release (ADR 0027).

Launchers run by path, so this file stays while the platform may still list a Job bound to it;
revision 0028's release deletes it. ETF signals are gone, so a run does nothing: it writes one log
line and succeeds. It touches no table, so it needs no schema check (ADR 0012).
"""

from __future__ import annotations

import json
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    print(
        json.dumps(
            {
                "job": "alpaca-etf-signal",
                "retired": True,
                "detail": "ETF signals were removed (ADR 0027); this run does nothing.",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
