# ADR: Deployment-Workflow Migrations And Schema-Gated Jobs

## Status

Proposed on 2026-10-03; partially implemented the same day (see
[Implementation Status](#implementation-status)). Acceptance requires the open checks under
[Verification Before Acceptance](#verification-before-acceptance). Amends the rollout section of
[ADR 0011](0011_api_managed_alpaca_credential_secrets.md).

## Context

Until now, project MetaTable revisions were applied by hand. AGENTS.md told operators to run
`metatables migrations upgrade --provider ... head` against the hosted environment before pushing.
MetaTables 0.1.9 forbids this. A hosted runtime is migrated only by a Job in the deployment
workflow, which applies each provider from the candidate image before anything that uses the
tables rolls out. Developer and agent sessions must not run `upgrade` or `downgrade` against a
hosted API, and applications must not migrate at startup.

Code and schema never change at exactly the same moment:

| Code | Schema | When | Result |
|---|---|---|---|
| old | new | the previous release keeps serving while the migration runs | safe when the revision only adds nullable or defaulted columns and tables |
| new | old | something starts on the new image before the migration has run | fails |

The second row is the dangerous one. Models map every column, so any read of a changed table
selects the new columns. For example, after `0015` every account read (`list_account_registrations`,
`get_account_registration`) fails with "column does not exist" on a database still at `0014`.
That applies whether or not the caller needs credentials.

These resources run this repository's code, each on its own image:

- the FastAPI release and the coding agent, declared in `.mainsequence/workflows/`;
- the declared Jobs `alpaca-bars-update` and `daily-stock-bars-holdings-ivv`;
- one Job per signal configuration and one per portfolio configuration. The API creates these at
  runtime with `PlatformJob.create(...)`, and they appear in no workflow file.

A workflow can only make the resources it declares wait for the migration (`needs: [migrate]`).
If the platform moves the API-created Jobs to a new image directly from the push, one of them can
run new code against the old schema during the short gap before the migration finishes.

The Main Sequence SDK 9.0.5 skills also state that `automatic_deployment` is set only in workflow
files and that Job updates no longer accept it. `signal_jobs.py` and `portfolio_jobs.py` still send
it on create, and `signal_jobs.py` also sends it on update.

## Decision

New code never runs against an old schema:

```text
git push origin main            any commit that carries revision N+1
        |
        v
.mainsequence/workflows/   execution graph, runs on every deployment
+-------------------------------------------------------------------------------+
| image    build image I_new from the pushed commit                             |
|   |                                                                           |
|   v                                                                           |
| migrate  Job on I_new: src/jobs/migrate_alpaca_connectors.py                  |
|            upgrade_application("alpaca_connectors.migrations:migration")      |
|            DB N -> N+1           (already at N+1: "already current", no-op)   |
|            schema only: no data repairs, no backfills                         |
|   |                                                                           |
|   +-- exit != 0 --> STOP. Nothing below deploys; everything stays on I_old.   |
|   |                 I_old works on DB N and on N+1 (revisions only add).      |
|   v exit 0                                                                    |
| deploy   needs: [migrate]                                                     |
|            FastAPI release                          I_old -> I_new            |
|            coding agent                             I_old -> I_new            |
|            alpaca-bars-update, daily IVV Jobs       I_old -> I_new            |
+-------------------------------------------------------------------------------+

Outside the graph: one Job per signal or portfolio configuration, created by the API.
They can switch I_old -> I_new at any moment, even before "migrate" finishes, so
every launcher in src/jobs/ except the migration Job starts with a schema gate:

  src/jobs/run_alpaca_etf_signal.py        (same gate in every other launcher)
  +--------------------------------------------------------------------+
  | db   = revision applied on the database          e.g. N            |
  | code = head revision shipped in this image       e.g. N+1          |
  | if db is behind code:                                              |
  |     print("schema N behind code N+1; skipped")                     |
  |     exit 0        -> no extraction, no Alpaca call, no writes;     |
  |                      the next scheduled run does the work          |
  | else:                                                              |
  |     run the Job                                                    |
  +--------------------------------------------------------------------+

The only risky window, and what each resource does in it:

time   ------------------------------------------------------------------------->
DB              N . . . . . . . . . . . . | N+1 . . . . . . . . . . . . .
                                          ^ migrate finishes
API, agent,     I_old . . . . . . . . . . | I_old -> I_new  never I_new on N
declared Jobs
signal Job      I_old . . I_new  skipped  | I_new  run      I_new on N is skipped,
                                 by gate  |                 never failed
```

### 1. One migration Job per deployment

`src/jobs/migrate_alpaca_connectors.py` runs on every deployment from the candidate image and
calls `metatables.upgrade_application(...)` for each provider in
`alpaca_connectors.migrations.schema_gate.MIGRATION_PROVIDERS`: only
`alpaca_connectors.migrations:migration`. A database already at head reports `already current`
and is left unchanged. Any exception fails the Job.

The Job applies schema revisions only. A one-off data repair belongs to the release that needs it,
not to every deployment: it runs once as a reviewed operation after that release rolls out. For
example, ADR 0011's `alpaca-connectors account backfill-secret-uids --execute` runs once after the
release that ships `0015`.

The Job never applies `msm_migrations:migration`. As ms-markets 2.1.1 documents, only the
ms-markets deployment's own `migrate-markets` Job migrates the ms-markets schema; applications that
install ms-markets apply only their own providers. (2.1.0 briefly said the opposite and 2.1.1
withdrew it.) Our provider's tables reference ms-markets tables, so our migration Job fails until
the ms-markets deployment has created them.

### 2. The workflow waits for the migration

The workflow declares the migration Job and an `execution` graph: prepare image, then migrate, then
deploy the FastAPI release, the coding agent and the declared Jobs, each with `needs: [migrate]`.
The exact file layout, `api_version` and resource kinds come from the branch's
`workflow-template` and must pass `validate-workflow` before the file is committed. The existing
FastAPI release and coding agent must keep their current resource identities.

The platform constrains this. An `execution` step may reference only resources declared in the same
workflow file, and `api_version` 2.3.0, which the graph needs, accepts only the kinds `fastapi`,
`harness_agent`, `job` and `static_site`. A resource therefore joins the graph only once its
declaration lives in the migration workflow file with a 2.3.0 kind; see
[Implementation Status](#implementation-status).

### 3. Launchers that the graph cannot gate check the schema first

Every launcher under `src/jobs/` except the migration Job starts with a schema gate. It reads the
provider's revision applied on the database and compares it with the head revision the image ships:

- **database behind the code:** log it and exit successfully without extracting, calling Alpaca,
  or writing anything;
- **database at or ahead of the code:** run normally. Ahead means an older image is running after
  a newer migration, which rule 4 keeps safe.

The gate turns a failed run into a clean skip. It does not recover the skipped work: a skipped signal
run is still an ETF-weights observation that is never recorded, so the platform answer below still
matters. Portfolio runs recover fully on their next run.

### 4. Every revision stays usable by the previous release

Revisions add first, with new tables and nullable or defaulted columns, and drop or rename only in a
later release once no deployed code uses the old shape. `0015` follows this rule; its permanent
`credential_source` default exists for exactly that reason.

### 5. Where migrations run

- **Local development:**
  `metatables --local migrations upgrade --provider alpaca_connectors.migrations:migration head`
  against the verified local SQLite runtime.
- **Hosted environments:** only the migration Job. Sessions may run the read-only
  `metatables migrations current` to confirm the deployed revision.
- Redeploying an older image does not roll back the schema; a hosted downgrade is a separate,
  explicitly requested operation.

### 6. Code no longer sets automatic deployment on Jobs

Job updates stop sending `automatic_deployment`. Job creation still sends it: both existing
API-created Jobs report `automatic_deployment: true`.

## Implementation Status

Implemented on 2026-10-03:

- `src/jobs/migrate_alpaca_connectors.py` applies `alpaca_connectors.migrations:migration` with
  `metatables.upgrade_application` and prints one JSON line per provider. The schema gate checks
  `msm_migrations:migration` and our provider (`GATED_PROVIDERS`). If the ms-markets deployment has
  not yet migrated to the version this image pins, Jobs skip instead of failing.
- `.mainsequence/workflows/alpaca-connectors-api.yaml` declares the API and that Job in one file
  at `api_version` 2.3.0, with the graph: prepare the image from the API, run the migration Job, then
  deploy the API with `needs: [migrate]`. It keeps the workflow `name` and the API `key`; the API
  moves from `kind: resource_release` (`resource_uid`) to `kind: fastapi` with
  `source_path: api/app/main.py`, the pattern ms-markets uses for its own API (its commit `29dc96b`
  made the same conversion). An earlier standalone migrations workflow, which ran the Job but gated
  nothing, is removed.
- `alpaca_connectors.migrations.schema_gate` and the gate in all four Job launchers. The applied
  revision comes from the provider's version MetaTable through a governed SELECT. A version table
  whose catalog row is missing or only `reserved` counts as never migrated, because the catalog
  reserves it before the first migration and activates it afterwards.
- Signal and portfolio Job updates no longer send `automatic_deployment`.

Not yet gated by the graph:

- **Coding agent:** `alpaca-connectors-agent.yaml` uses `kind: code_repository_coding_agent`, which
  the current platform rejects (`unsupported_kind`), so that declaration is not applied today.
- **Declared Jobs:** `alpaca-bars-update` and `daily-stock-bars-holdings-ivv` have automatic
  redeployment disabled, so pushes never move them to a new image. The gate protects them when they
  are redeployed.

Until those resources join the graph, the agent can briefly serve new code against the old schema
during a rollout; this is accepted during development. **Unverified:** whether converting the API
declaration keeps release `6318ffdd-…` or creates a new release. Check the release UID after the
first deployment of this file.

## Consequences

- A schema change lands with a single push; there are no manual schema steps, and a failed
  migration blocks the rollout without disturbing the running release. A release that also needs a
  one-off data repair names that step in its own ADR or release notes.
- The workflow becomes the single place that orders schema and code. Adding a new deployed resource
  that reads project tables requires a `needs: [migrate]` edge.
- API-created Jobs remain outside that ordering. The gate keeps them from failing in the gap, at the
  cost of possibly skipping one scheduled run.
- Each schema change carries a compatibility obligation toward the previous release, which
  reviewers must check.

## Verification Before Acceptance

Results recorded on 2026-10-03:

1. **Workflow template:** done. The graph exists at `api_version` 2.3.0; steps must reference
   same-file resources; `resource_release` is not a 2.3.0 kind. The standalone migration workflow
   validates. **Open:** whether converting the API to `kind: fastapi` keeps its release.
2. **API-created Jobs:** partly done. One signal Job and one portfolio Job exist on `main`, both
   with `automatic_deployment: true`. **Open:** whether they move to a new image on the push or
   after the graph. The gate covers either answer.
3. **Job identity:** **open.** The Environment's hosted MetaTables API answers `503 Initialize the
   runtime DataSource in Settings first`, so no hosted MetaTables call can succeed yet.
4. **Applied revision:** done. The gate's governed read works against the local runtime: with no
   completed migration it reports `behind` and the signal launcher exits 0 without side effects.
   ms-markets' `JSONB` columns used to block every provider that depends on it on local SQLite
   ([MainSequenceMarkets#15](https://github.com/mainsequence-projects/MainSequenceMarkets/issues/15));
   MetaTables 0.1.12 and ms-markets 2.1.0 fix that. **Open:** a full local run of the migration Job
   and the gate's `active` path, which is covered by unit tests only so far.
5. **Job creation:** done. Creation accepts `automatic_deployment`; updates no longer send it.

Running `run_migration(..., operation="current")` reserves the version table's catalog row even
though it is read-only for the database; the gate reads the catalog without that call.
