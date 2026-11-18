"""Apply the MetaTable schema revisions this application reads before it rolls out.

The deployment workflow runs this Job from the candidate image on every push (ADR 0012). It applies
schema revisions only; one-off data repairs belong to the release that needs them. It applies only
this application's providers: the ms-markets schema is migrated by the ms-markets deployment's own
`migrate-markets` Job (ms-markets 2.1.1). An exception fails the Job, which blocks the rollout while
the previous release keeps serving. Last, it reports the contract instances Markets cannot read yet
(issue #37), so a missing grant shows at deploy, not at the first call that needs it; the report
never fails the Job.
"""

from __future__ import annotations

import json


def main() -> int:
    from alpaca_connectors.migrations.schema_gate import MIGRATION_PROVIDERS
    from metatables import upgrade_application

    for provider in MIGRATION_PROVIDERS:
        result = upgrade_application(provider)
        print(
            json.dumps(
                {
                    "provider": provider,
                    "revision": result.get("revision"),
                    "state": "migrated" if result.get("migrated") else "already current",
                    "data_source_uid": result.get("data_source_uid"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    from alpaca_connectors.operations.markets_access import markets_instances_report

    print(json.dumps({"markets_access": markets_instances_report()}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
