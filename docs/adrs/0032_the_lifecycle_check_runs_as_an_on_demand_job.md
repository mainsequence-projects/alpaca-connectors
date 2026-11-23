# ADR 0032: The Lifecycle Check Runs As An On-Demand Job

## Status

Accepted on 2026-11-23. Decisions 1 and 2 are implemented. The Job of decisions 3 to 6 was
declared on 2026-11-23 and its declaration removed the same day: for now the check runs only from
a person's machine, because a run in the platform is as slow as one from there (the platform's own
services reach their database and backend through tunnels). Its launcher,
`src/jobs/run_lifecycle_check.py`, and `run_platform_job` stay, and the
`ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_SECRET_KEY` Secrets stay in Production, for when the Job is
declared again in `alpaca-connectors-api.yaml`.

## Context

The account and portfolio life cycle check drives a real Alpaca paper account through this API,
in 13 steps ([Development Workflow](../operations/development.md)). Until now it ran only from a
person's machine, against the local stack or, with `--release-uid`, against a deployed release.
A deployed run on 2026-11-23 took about 27 minutes.

Two things made it slow or unreliable:

- **Stage 10 was sequential.** Its three portfolios were created one after another, and each
  portfolio Job run was polled to its end before the next started. In production, the dispatcher
  starts every scheduled portfolio's run back to back without waiting
  (`dispatch_scheduled_configurations`). So the check tested a path production does not take,
  and it took about 15 minutes for it.
- **The run depended on the machine it started from.** Its calls reached the platform through
  that machine's connection, and the machine's sign-in could expire mid-run. The run's times
  mixed the platform's own times with that connection's.

The timings showed where the rest of the time goes. For each of 18 calls, the time measured on
the person's machine, at the platform's gateway and in this API's own log agree within about
one second. The rest is inside the platform: a MetaTables row operation took a median of 1.5 s,
and this API and Markets make their operations one after another.

## Decision

1. **Independent calls run together.**
   - Stage 10 creates its three portfolios at once: each one's signal definition, weights and
     configuration form one chain. It then starts their three portfolio Job runs at once, as the
     dispatcher does, and waits for all three. The stage fails naming every run that did not end
     `SUCCEEDED`.
   - The weights publishes, and the Markets portfolios' upserts and deletes, run together too.
2. **The check is package code.** `alpaca_connectors.operations.lifecycle_check` holds the
   stages, the timings and the report. `scripts/lifecycle_check.py` runs it from a person's
   machine, and the Job's launcher runs it in the platform.
3. **The `alpaca-lifecycle-check` Job runs it in the platform, on demand.**
   - It is declared in `alpaca-connectors-api.yaml`, the only file that can name the `alpaca`
     release.
   - It has no schedule and is no step of the execution graph, so a push never runs it. A person
     runs it with `mainsequence code-repository jobs run <uid>`.
   - Its launcher, `src/jobs/run_lifecycle_check.py`, starts with the schema gate
     ([ADR 0012](0012_deployment_workflow_migrations_and_schema_gated_jobs.md)).
4. **It acts as its own workload.**
   - It holds `user` on `alpaca` and calls the API through the platform's release access, like
     any caller. The account it registers belongs to that workload
     ([ADR 0019](0019_account_owners_vouched_for_by_trusted_applications.md): the acting user).
   - It finds this branch's one FastAPI release in its own Environment, so no UID is configured.
   - It holds `reader` on the MetaTables and MainSequenceMarkets branches, as every Job does, to
     reach Markets through the Markets API.
   - It holds no `runner`: the API starts the portfolio Job runs, as it does for every caller.
5. **The paper keys are Secrets.**
   - The Job reads `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_SECRET_KEY` with `value_reader`. A
     person sets their values in the Environment before the push that declares them.
   - The keys go only to the account create route and to Alpaca's own client, as they do from a
     person's machine. No report or log carries them.
6. **The report goes to the Job's logs.** It is the same report a person's run prints, followed
   by its JSON on one line that starts with `lifecycle report`. The run exits 1, so the JobRun
   ends `FAILED`, when any stage failed.

## Consequences

- Stage 10 takes about as long as one portfolio chain and one portfolio Job run, instead of three
  of each, and it now tests concurrent runs on the shared tables, as production makes them.
- A run in the platform does not depend on a person's machine or sign-in, and its times are the
  platform's own.
- A Job run and a run from a person's machine use the same paper account, so they must not run
  at the same time.
- Concurrent portfolio runs on the shared tables are now tested. A conflict between them is a
  production defect, reported upstream, not worked around in the check.
- With the three runs started together, the ms-markets #118 case (a new portfolio taking another
  portfolio's progress as its own) is caught when the shared rebalance-state table already holds
  another portfolio's progress, as every Environment's does after its first portfolio run.
