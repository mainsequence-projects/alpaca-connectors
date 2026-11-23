"""Entry point of the workflow-declared, unscheduled `alpaca-lifecycle-check` Job (ADR 0032).

A person runs it on demand. It runs the account and portfolio life cycle from inside the platform
against this branch's deployed API, as its workload, and prints the timed report. Its exit status
is 1 when any stage failed. The schema gate (ADR 0012) comes first.
"""

from __future__ import annotations


def main() -> int:
    from alpaca_connectors.migrations.schema_gate import skip_if_schema_behind

    if skip_if_schema_behind("Alpaca lifecycle check Job"):
        return 0

    from alpaca_connectors.operations.lifecycle_check import run_platform_job

    return run_platform_job()


if __name__ == "__main__":
    raise SystemExit(main())
