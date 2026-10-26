# Markets API Boundary Runbook

ms-markets ADR 0047 makes the Markets API the boundary between this application and Markets:
Markets registries (assets, accounts, categories, portfolios, signals, order managers) are read
and written through the Markets API, the time series this application produces live in its own
`alpaca-connectors` namespace, and no workload of this application holds a role on
`mainsequence.markets`. This page lists, in order, what a person does in each Environment to move
an existing deployment there. Nothing here runs from code or from an agent session.

## 1. Move The Project Tables To `alpaca-connectors`

Until this release the project mixins inherited the `mainsequence.markets` namespace from
ms-markets, so all thirteen project tables were registered there. The mixins in
`alpaca_connectors.metatables` now declare `alpaca-connectors`; tables that a fresh Environment
creates are registered there directly. Existing tables are moved once with the MetaTables table
patch (MetaTables 0.1.56 or later), which reassigns the namespace and identifier of a registered
table:

- the table's `uid`, its physical name, its rows, its foreign keys and its update records do not
  change, and the account keys need no re-encryption (the keyring seals values, not catalog
  names);
- updater identities do not change: the bars updaters bind their output table by physical name,
  and `update_hash` does not include the table's namespace or identifier;
- the catalog identifier gains the namespace prefix ms-markets gives every table outside the
  Markets namespace (`markets_identifier`):

| Physical table | Identifier before | Identifier after |
|---|---|---|
| `alpaca_connectors__asset_alpaca` | `AlpacaAssetDetails` | `alpaca-connectors.AlpacaAssetDetails` |
| `alpaca_connectors__bars_1d_sip_all` | `alpaca_stock_bars_1d_sip_all` | `alpaca-connectors.alpaca_stock_bars_1d_sip_all` |
| `alpaca_connectors__bars_1d_iex_raw` | `alpaca_stock_bars_1d_iex_raw` | `alpaca-connectors.alpaca_stock_bars_1d_iex_raw` |
| `alpaca_connectors__acct_alpaca` | `AlpacaAccountDetails` | `alpaca-connectors.AlpacaAccountDetails` |
| `alpaca_connectors__account_key_request` | `AccountKeyRequest` | `alpaca-connectors.AccountKeyRequest` |
| `alpaca_connectors__universe_source` | `UniverseSource` | `alpaca-connectors.UniverseSource` |
| `alpaca_connectors__asset_universe` | `AssetUniverse` | `alpaca-connectors.AssetUniverse` |
| `alpaca_connectors__bars_configuration` | `AlpacaBarsConfiguration` | `alpaca-connectors.AlpacaBarsConfiguration` |
| `alpaca_connectors__bars_configuration_asset` | `AlpacaBarsConfigurationAsset` | `alpaca-connectors.AlpacaBarsConfigurationAsset` |
| `alpaca_connectors__asset_registration_operation` | `AssetRegistrationOperation` | `alpaca-connectors.AssetRegistrationOperation` |
| `alpaca_connectors__etf_signal_job_configuration` | `AlpacaETFSignalJobConfiguration` | `alpaca-connectors.AlpacaETFSignalJobConfiguration` |
| `alpaca_connectors__portfolio_rebalance_configuration` | `PortfolioRebalanceConfiguration` | `alpaca-connectors.PortfolioRebalanceConfiguration` |
| `alpaca_connectors__etf_portfolio_configuration` | `AlpacaETFPortfolioConfiguration` | `alpaca-connectors.AlpacaETFPortfolioConfiguration` |

The "before" column is what the code registered; check it against the catalog before the move.
The only places the identifier is shown are the market-data dataset reads (`identifier`), which
read it from the catalog. The dataset `key` (`1d/sip/all`) and every uid are unchanged.

`MSM_AUTO_REGISTER_NAMESPACE`, when it is set, silently replaces the declared namespace (and
suffixes physical names). No hosted resource sets it; never set it outside tests and local
examples.

Who runs it: a person who holds `editor` or `owner` on `mainsequence.markets` (or on each of the
thirteen tables) and `editor` or `owner` on `alpaca-connectors`, or an Organization admin. The
table patch refuses a caller without `tables.update` on the table or `tables.create` on the
destination namespace.

When: after step 2 has given the workloads their roles on `alpaca-connectors` (a moved table is
covered by the roles of its new namespace only), and before the roles on `mainsequence.markets`
are revoked (step 6). Runtime attachment and updater binding resolve these tables by physical
name, so both the previous and this release keep working on either side of the move.

```python
from metatables import MetaTable

from alpaca_connectors.migrations import all_project_metatable_models

models = {model.__table__.name: model for model in all_project_metatable_models()}
tables = MetaTable.filter_by_body(physical_table_name__in=sorted(models), limit=100)
assert len(tables) == len(models), "every project table must be registered exactly once"
before = {table.physical_table_name: (table.uid, table.namespace, table.identifier) for table in tables}
print(before)
for table in tables:
    model = models[table.physical_table_name]
    table.patch(namespace=model.__table__.info["namespace"], identifier=model.__metatable_identifier__)
after = MetaTable.filter_by_body(physical_table_name__in=sorted(models), limit=100)
assert {table.physical_table_name: table.uid for table in after} == {
    name: uid for name, (uid, _, _) in before.items()
}
```

Run it with `MSM_AUTO_REGISTER_NAMESPACE` unset, from a checkout of this release, against the
Environment's MetaTables (`mainsequence` session of the person above). Keep the printed `before`
mapping: patching each table back with its old namespace and identifier undoes the move.
