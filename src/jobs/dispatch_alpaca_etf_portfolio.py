"""Entry point of the retired `alpaca-etf-portfolio-dispatcher` Job, kept one release (ADR 0027).

Launchers run by path, so this file stays while the platform may still list a Job bound to it;
revision 0028's release deletes it. A run is the `alpaca-portfolio-dispatcher` Job's: it
delegates to `dispatch_alpaca_portfolio.py`, which checks the schema first (ADR 0012).
"""

from __future__ import annotations

import runpy
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    launcher = runpy.run_path(str(Path(__file__).with_name("dispatch_alpaca_portfolio.py")))
    return launcher["main"](argv)


if __name__ == "__main__":
    raise SystemExit(main())
