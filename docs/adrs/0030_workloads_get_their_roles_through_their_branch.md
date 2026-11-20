# ADR 0030: Workloads Get Their Roles Through Their Branch

## Status

Accepted on 2026-11-19. Requires MetaTables 0.1.70: its ADR 0029 adds branch access, and
[MetaTables#119](https://github.com/mainsequence-projects/MetaTables/issues/119) lets the provider
builder pass it on.

Amended by [ADR 0031](0031_markets_series_are_published_not_shared.md) in the same release:
Markets reads no table of this application, so `alpaca-connectors-markets` is gone from
decisions 1, 2, 4 and 6, and the branch holds its role on `alpaca-connectors` alone.

It replaces the per-workload role tables in [Jobs And Scheduling](../operations/jobs.md) and the
[Markets API Boundary Runbook](../operations/markets-api-boundary.md). It also amends the
namespace setup in [ADR 0025](0025_markets_reads_the_instances_through_a_second_namespace.md)
(rollout step 2).

## Context

Every Job, the FastAPI release and the Agent call MetaTables as their own workload User. Until
now, a person granted each workload User its role on `alpaca-connectors`, in every Environment.
A recreated workload gets a new User, and nothing carried the role over.

Issue #44 is that failure. The rename of [ADR 0027](0027_one_portfolio_path.md) created the
portfolio Job and its dispatcher anew, with new workload Users. Neither held a role, so they could
not see the version table. The schema gate read the applied revision as "none", and every
scheduled run skipped while reporting success. A person granted the roles by hand on 2026-11-19.

The Markets namespace had a manual step of its own. A person ran `grant-markets-access`, which:

- created `alpaca-connectors-markets`;
- granted the migration Job's Team `editor` on it;
- granted the Markets workload User `data_viewer` on it, again per workload User.

MetaTables ADR 0029 makes a code repository branch a principal. A binding to a branch gives its
role to every active workload of that branch, worked out on each request. A migration provider
declares its own branch's roles with `branch_access`, after each finalization.

## Decision

1. **The branch holds this application's roles.** Both migration providers declare
   `settings.BRANCH_ACCESS` through `SHARED_PROVIDER_KWARGS`:

   | Namespace | Role |
   |---|---|
   | `alpaca-connectors` | `editor` |

   The migration Job declares it on every push, including when the schema is already current.
   Every workload this repository's workflow files deploy has the role on its first request: the
   API, the Agent, the migration Job, the bars and portfolio Jobs and both dispatchers. A renamed
   or recreated workload is on the same branch, so it keeps the role.
2. **One role, for every workload.** `editor` is the role the API and the portfolio Job need, so
   the dispatchers and the Agent hold it too. This is not a security boundary, because whoever
   can push to the branch already controls every one of these workloads.
3. **Each provider declares the same set.** A declaration replaces the branch's previous one, so
   a provider that declared less would remove the other's bindings. A test checks that both
   providers carry `BRANCH_ACCESS`.
4. **The migration Job creates the namespace.** In a new Environment it creates
   `alpaca-connectors` by registering the first table there, and holds `editor` on it. No person
   creates it.
5. **A refused declaration fails the deploy.** MetaTables raises `BranchAccessRefused` after
   finalization, the migration Job fails, and the rollout stops before any workload runs without
   its role.
6. **Markets holds no role on this application.** The Markets workloads belong to another
   repository, and MetaTables ADR 0029 keeps sharing with people: code declares roles only for its
   own branch. Markets needs none: this application publishes to Markets and reads it back
   through the Markets API (ADR 0031), so no person grants the Markets branch anything.

## Rollout

In Production:

1. The migration Job holds `editor` on `alpaca-connectors` through Team `b545003f`, so its first
   declaration succeeds. The push that ships this ADR writes the `declared` branch binding.
2. After that push, the namespace owner may remove the per-workload bindings on
   `alpaca-connectors`, including the two #44 grants. Each one is now covered by the branch
   binding. Keep the Team's `editor` until the branch binding shows in `get_access`.
3. ADR 0031's rollout removes `alpaca-connectors-markets` with every role on it.

## Consequences

- A new Environment needs these people-run steps:
  - creating the `ALPACA_CONNECTORS_DATA_KEY` Secret;
  - granting the namespace an owner.

  No workload needs a hand grant.
- Renaming a Job key or recreating a release needs nobody. The #44 failure can no longer come
  from a rename.
- Every workload of the branch holds `editor`, and least privilege inside the branch is gone. A
  workload that must hold less belongs on another branch.
- A namespace owner can still remove a `declared` binding, but the next push declares it again
  while the code declares it.
