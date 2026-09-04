# Asset Universes And Holdings Categories

A registered universe is a connector-owned `AssetUniverse`, not an ms-markets `AssetCategory`.
Every row has two explicit relationships:

```text
UniverseSource.uid <--- AssetUniverse.source_uid
AssetCategory.uid <--- AssetUniverse.asset_category_uid
```

The Universe UID identifies the operator-managed lifecycle resource. The Source UID identifies the
explicit extraction configuration. The Asset Category UID identifies the container whose
memberships are replaced by a successful Run. `AssetCategory.metadata_json` is not configuration
and stores neither Source UID nor active state.

## Flow

1. Create a universe with a name, ETF ticker, and explicit source URL.
2. Persist or resolve that exact source, create an empty category, and store the relationships on a
   new `AssetUniverse`. Creation performs no extraction.
3. Run the registered Universe UID separately.
4. Run receives a registered Alpaca Account UID as execution input, extracts current holdings
   through `etfhextractor`, and creates a provider-native registration plan.
5. Missing Main Sequence assets are registered idempotently as part of execution. Symbols Alpaca
   cannot resolve block the Run and are named explicitly. A symbol absent from the current Alpaca
   catalog can still run when one exact canonical Alpaca Asset/detail for that symbol was previously
   registered; the resolver finds those identities with one bulk query.
6. Replace memberships only in the linked category and only after the full constituent set exists.
   Materialization delegates to the `ms-markets>=1.0.3`
   `AssetCategory.replace_memberships` API, which bulk-upserts the complete desired set in one
   governed operation and removes stale memberships in one delete operation. It never executes one
   membership insert per constituent.
7. Return the same extraction as one complete `AlpacaETFHoldingsSignal` DataFrame batch and publish
   it to the canonical ms-markets `SignalWeightsStorage` table.

The browser/API creation route is `POST /v1/universes`. Its response distinguishes `uid`,
`source_uid` and `asset_category_uid`. Run is a separate API action with its own preflight and
requires `options.account_uid`; the account is not persisted on the Universe.

Selecting a Universe in the browser loads `GET /v1/universes/{universe_uid}` for its explicit
source/category links, then loads the linked membership through
`GET /v1/universes/{universe_uid}/assets`. The latter returns the same paginated Asset resource
contract as `GET /v1/assets`; it is category-scoped with a set-based membership join and is not
requested while rendering the Universe collection.

Universe execution persists two distinct outputs from the same extraction:

- `AssetCategoryMembership` stores the current category/Asset relationship without weights.
- `SignalWeightsStorage` stores the observed normalized weights under the stable
  Universe-derived `signal_uid`.

The selected Account UID is serialized in the signal updater configuration so automatic execution
can resolve credentials, but it does not change the signal UID and is not stored as a signal-table
dimension. Each execution timestamp means “observed at”; it does not guarantee the provider weights
were economically effective at that exact time.

```bash
alpaca-connectors universe-source preview <SOURCE_UID>
alpaca-connectors universe list
alpaca-connectors universe get <UNIVERSE_UID>
alpaca-connectors universe run <UNIVERSE_UID>
alpaca-connectors universe run <UNIVERSE_UID> --execute
alpaca-connectors universe delete <UNIVERSE_UID>
alpaca-connectors universe delete <UNIVERSE_UID> --execute
```

## Lifecycle And Deletion

`AssetUniverse.is_active` controls Run and downstream use. Deactivation preserves the Universe,
source, category, and memberships for inspection but prevents Run and new bar-configuration use.

Deletion is blocked while a bar configuration references the Universe. Otherwise it deletes
memberships, the `AssetUniverse`, and the linked category in that order. It never deletes the
separately managed `UniverseSource` or any Asset.
