# ADR 0025: Markets Reads The Instances Through A Second Namespace

## Status

Withdrawn by [ADR 0031](0031_markets_series_are_published_not_shared.md) with the release that
implements it (2026-11-20, on ms-markets 4.2.0). From that release Markets reads no table of this
application: nothing is declared in `alpaca-connectors-markets`, `share_with_markets`,
`grant-markets-access` and the migration Job's `markets_access` report are removed, and the
namespace is removed in each Environment (ADR 0031, Rollout step 6). What follows records the
decision as it was.

Decided by the owner and implemented on 2026-11-17, on MetaTables 0.1.67 and ms-markets 3.8.2.
Rolled out in Production the same day: the migration Job added the seven instances to
`alpaca-connectors-markets` and moved the thirteen project tables out of `mainsequence.markets`,
and the owner ran `alpaca-connectors instances grant-markets-access` (see [Rollout](#rollout)).

Amends [ADR 0024](0024_portfolios_from_provided_weights.md), whose registration failed in
Production for the reason below (alpaca-connectors#37).

Amended on 2026-11-19 by [ADR 0030](0030_workloads_get_their_roles_through_their_branch.md):
the migration Job creates `alpaca-connectors-markets` and its branch holds `editor` there, and the
Markets access of decision 2 is granted to the `MainSequenceMarkets` branch, not to a Markets
workload User. `grant-markets-access` now takes `--markets-branch-uid` and creates nothing.

## Context

This application owns instances of the Markets time-series contracts (ms-markets ADR 0047):
signal weights, portfolios, portfolio weights, rebalance state, calendar events, orders, trades,
and one interpolated-price table for each bars source. They are Markets primitives, kept in
`alpaca-connectors` beside the application's own tables: the account keys, configurations,
Universes and operations.

Markets reads an instance by table uid as its own workload, and refuses one it cannot see. A
role on `alpaca-connectors` would also cover the application's own tables, so the runbook had a
person grant the Markets workload `data_viewer` on three tables, one by one, in each
Environment. Production never got those grants, and the first provided-weights registration
answered `404 ... is not a visible TimeIndexMetaTable`. Orders, trades and the other instances
had no grant in any Environment.

Since MetaTables 0.1.66 (its ADR 0028) a table can be in several namespaces, and a role on any
of them applies to it.

## Decision

1. **Every Markets contract instance is also in `alpaca-connectors-markets`.**
   `alpaca_connectors.instances.share_with_markets` declares it with
   `__metatable_namespaces__` on the seven instances and on every interpolated-price table.
   Nothing else is declared there.
2. **The Markets workload holds `data_viewer` on `alpaca-connectors-markets`** and no role on
   `alpaca-connectors` or on any table of it. One grant per Environment covers every instance,
   including those added later. Since ADR 0030 the grant names the `MainSequenceMarkets` branch,
   so it also covers every Markets workload.
3. **The application's own tables stay only in `alpaca-connectors`.** The account keys table is
   never declared in the second namespace.
4. **No revision.** A namespace is not a schema change: the next `upgrade head` of each provider
   adds the memberships when it finalizes its tables.
5. **The project requires `mainsequence-metatable>=0.1.66`.** That client and the hosted API
   change together: an older client is refused by an API on 0.1.66 or later, and this client is
   refused by an older API.
6. **The project requires `ms-markets>=3.8.2`,** the first release on that client. Earlier
   releases read the one `namespace` of a table in the account delete preview, which the API no
   longer returns (MainSequenceMarkets#79).

## Rollout

In each Environment, in this order:

1. The hosted MetaTables API serves 0.1.66 or later, and the Markets API runs ms-markets
   3.8.2 or later.
2. The owner runs `alpaca-connectors instances grant-markets-access`, which creates
   `alpaca-connectors-markets`, grants the Markets workload `data_viewer` on it and grants the
   migration Job's Team `editor` on it
   ([runbook, steps 1 and 5](../operations/markets-api-boundary.md)).
3. The project tables still registered in `mainsequence.markets` leave it. The migration removes
   a declared namespace the code no longer lists, which needs `tables.create` on
   `mainsequence.markets`. Where the migration Job holds it, as in Production through its Team,
   the Job moves them itself; elsewhere a person moves them first
   ([runbook, step 7](../operations/markets-api-boundary.md)), or the finalization is refused.
4. This release is pushed. The migration Job adds the seven memberships; a person runs
   `alpaca-connectors portfolio prepare-interpolated-prices` for the interpolated-price tables.

## Consequences

- A new contract instance is readable by Markets once it is built through
  `alpaca_connectors.instances`; nobody grants per table.
- The second namespace only adds access. A principal with a role on `alpaca-connectors` reads
  the instances as before.
- The three per-table grants of the earlier runbook can be revoked once the namespace grant is
  in place.

## Open

- The registration of ADR 0024 is still not atomic: the failed call in Production left stored
  weights and a `SignalMetadata` row without a portfolio configuration. **Resolved on 2026-11-18**
  ([issue #37](https://github.com/mainsequence-projects/alpaca-connectors/issues/37)): a publication binds its signal to the instance before it writes a row, so a refused
  binding answers `503 signal_not_registered`, names no table and stores nothing. A create that
  fails deletes everything it stored, its signal's rows included. The migration Job reports, on
  every deploy, the instances Markets cannot read yet.
