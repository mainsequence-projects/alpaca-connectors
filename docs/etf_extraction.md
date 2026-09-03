# ETF Extraction Boundary

ETF constituent extraction is implemented by the external `etfhextractor` dependency. This
repository supplies application orchestration around it.

## Universe Workflow

The user maintains extraction targets as `UniverseSource` rows. Each row supplies a symbol and
absolute source URL. Preview passes that URL to `ETFHoldingsReader.read(...)`; sync validates
registration coverage before materializing an ms-markets AssetCategory.

There is no source-code list of managed ETFs and no ticker-to-provider inference map.

```bash
alpaca-connectors universe-source create \
  --name "S&P 500 source" \
  --symbol IVV \
  --url "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf"
alpaca-connectors universe-source preview <SOURCE_UID>
alpaca-connectors universe sync --source-uid <SOURCE_UID> --execute
```

The packaged default uses the official US iShares IVV product page. Running `universe-source
seed-defaults` reconciles that source under its stable UUID, so an older seeded URL is updated in
place rather than duplicated. Provider parsing remains owned by `etfhextractor`, not by a
connector-local compatibility layer.

## Asset Registration Workflow

Asset registration may still perform an explicit one-off seed expansion when the caller provides
both the ticker and provider. That is discovery input for the strict Alpaca/OpenFIGI registration
operation; it is not the durable universe registry.

```bash
alpaca-connectors asset register --seed-tickers IVV --component-provider ishares
```

## Portfolio Workflow

Analytical ETF portfolio construction consumes `etfhextractor` signals and explicit provider or
source URL input. It does not restore a hidden provider map.
