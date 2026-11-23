"""Run the account and portfolio life cycle from this machine.

    .venv/bin/python scripts/lifecycle_check.py [--release-uid UID] [--keep] [--json PATH]

The check is ``alpaca_connectors.operations.lifecycle_check``, which the unscheduled
``alpaca-lifecycle-check`` Job also runs inside the platform (ADR 0032); its docstring says what
each stage does and what a run needs.
"""

from __future__ import annotations

import sys

from alpaca_connectors.operations.lifecycle_check import main

if __name__ == "__main__":
    sys.exit(main())
