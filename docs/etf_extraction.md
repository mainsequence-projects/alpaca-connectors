# ETF Extraction Boundary

ETF constituent extraction is implemented by the external `etfhextractor` dependency. This
repository supplies application orchestration around it.

## Universe Workflow

The user maintains extraction targets as `UniverseSource` rows. Each row supplies a symbol and
absolute source URL. Preview passes that URL to `ETFHoldingsReader.read(...)`. A separately
registered `AssetUniverse` links the source to its materialization category. It represents the ETF
holdings source used to resolve current constituents and weights. Run receives an Alpaca account as
execution input, registers missing Alpaca-backed assets through that account, and replaces
membership in exactly that category only after the complete set is available.

There is no source-code list of managed ETFs and no ticker-to-provider inference map.

```bash
alpaca-connectors universe-source create \
  --name "S&P 500 source" \
  --symbol IVV \
  --url "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf"
alpaca-connectors universe-source preview <SOURCE_UID>
alpaca-connectors universe run <UNIVERSE_UID> --execute
```

The packaged default uses the official US iShares IVV product page. Running `universe-source
seed-defaults` reconciles that source under its stable UUID, so an older seeded URL is updated in
place rather than duplicated. Provider parsing remains owned by `etfhextractor`, not by a
connector-local compatibility layer.

## Asset Registration Boundary

The standalone Assets workflow accepts exact symbols only. Provider-derived constituent
registration is internal to Universe Run and uses the account selected for that execution.

## Portfolio Workflow

Analytical ETF portfolio construction consumes the connector-owned `AlpacaETFHoldingsSignal`.
Its only durable inputs are the registered `universe_uid` and runtime `account_uid`; ticker,
provider, and URL come from the Universe and its linked Source. `etfhextractor` remains the
provider extraction dependency and no hidden provider map is restored.
