# ADR 0030: Workloads Get Their Roles Through Their Branch

## Status

Accepted on 2026-11-19. Requires MetaTables 0.1.70: its ADR 0029 adds branch access, and
[MetaTables#119](https://github.com/mainsequence-projects/MetaTables/issues/119) lets the provider
builder pass it on.

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
   | `alpaca-connectors-markets` | `editor` |

   The migration Job declares it on every push, including when the schema is already current.
   Every workload this repository's workflow files deploy has the role on its first request: the
   API, the Agent, the migration Job, the bars and portfolio Jobs and both dispatchers. A renamed
   or recreated workload is on the same branch, so it keeps the role.
2. **One role per namespace, for every workload.** `editor` is the role the API and the portfolio
   Job need, so the dispatchers and the Agent hold it too. This is not a security boundary,
   because whoever can push to the branch already controls every one of these workloads.
   `editor` on `alpaca-connectors-markets` adds nothing the workloads lack, since every instance
   is also in `alpaca-connectors`. It keeps a recreated migration Job able to add the instances
   there.
3. **Each provider declares the same set.** A declaration replaces the branch's previous one, so
   a provider that declared less would remove the other's bindings. A test checks that both
   providers carry `BRANCH_ACCESS`.
4. **The migration Job creates both namespaces.** In a new Environment it creates
   `alpaca-connectors` by registering the first table there. It creates
   `alpaca-connectors-markets` when finalization adds the instances' declared membership. It
   holds `editor` on each one it creates. No person creates either namespace, and
   `grant-markets-access` no longer creates one.
5. **A refused declaration fails the deploy.** MetaTables raises `BranchAccessRefused` after
   finalization, the migration Job fails, and the rollout stops before any workload runs without
   its role.
6. **Markets' access is a person's grant to the Markets branch.** The Markets workloads belong to
   another repository. MetaTables ADR 0029 keeps sharing with people: code declares roles only
   for its own branch. Once per Environment, after the first deployment, the owner of
   `alpaca-connectors-markets` or an Organization admin runs
   `alpaca-connectors instances grant-markets-access --markets-branch-uid <uid> --execute`. That
   grants the `MainSequenceMarkets` branch `data_viewer`, which then covers every Markets
   workload through its renames and redeployments.

## Rollout

In Production:

1. The migration Job holds `editor` on both namespaces through Team `b545003f`, so its first
   declaration succeeds. The push that ships this ADR writes the two `declared` branch bindings.
2. After that push, the namespace owner may remove the per-workload bindings on
   `alpaca-connectors`, including the two #44 grants. Each one is now covered by the branch
   binding. Keep the Team's `editor` until the branch binding shows in `get_access`.
3. The owner of `alpaca-connectors-markets` grants the Markets branch `data_viewer`
   (decision 6). Then they may remove the Markets workload User's direct binding.

## Consequences

- A new Environment needs these people-run steps:
  - creating the `ALPACA_CONNECTORS_DATA_KEY` Secret;
  - granting the namespaces an owner;
  - granting the Markets branch `data_viewer` on `alpaca-connectors-markets`.

  No workload needs a hand grant.
- Renaming a Job key or recreating a release needs nobody. The #44 failure can no longer come
  from a rename.
- Every workload of the branch holds `editor`, and least privilege inside the branch is gone. A
  workload that must hold less belongs on another branch.
- A namespace owner can still remove a `declared` binding, but the next push declares it again
  while the code declares it.
