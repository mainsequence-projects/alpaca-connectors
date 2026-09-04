# ETF Holdings Extractors

See also:

- `docs/etf_extraction.md` for the external `etfhextractor` dependency boundary

## Scope Boundary

The extractor layer is not implemented in this repository anymore. Provider extraction is owned by
the external [`mainsequence-projects/etfholdingextractor`](https://github.com/mainsequence-projects/etfholdingextractor)
package and imported as `etfhextractor`.

Provider extractors do only one job:

- read published provider holdings sources
- parse component tickers
- return normalized ETF holdings models

They do not register assets, create categories, or publish DataNodes.

Local integration point:

- `src/universes/etf_holdings.py`

## Supported Providers

- `ishares`
- `invesco`
- `vanguard`
- `state_street`

## Source strategy

For durable universe management, the exact provider URL is stored in a user-maintained
`UniverseSource` MetaTable row and passed directly to `ETFHoldingsReader.read(url)`. The repository
does not keep a ticker list, ticker-to-provider map, or inferred universe target.

The asset-registration command accepts exact symbols only. Provider-derived expansion exists only
inside a configured Asset Universe Run. The source is durable; the Alpaca account is explicit
execution input and is not part of the Universe.

## Published-Source Rule

The extractors parse what the providers actually publish.

- prefer official download links or official holdings payloads
- do not invent hidden URLs when the provider does not publish them
- browser automation is allowed when the published page requires client-side interaction

## Browser Support

The external extractor may require Playwright for browser-backed provider flows.

Install the browser runtime with:

```bash
bash scripts/install_browser_runtime.sh
```

## Examples

```bash
alpaca-connectors universe-source create \
  --name "iShares Core S&P 500 ETF" \
  --symbol IVV \
  --url "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf"
alpaca-connectors universe-source preview <source-uid>
alpaca-connectors universe run <universe-uid> --execute
```

## Important Decision

Provider identity is determined from the stored source URL by `etfhextractor`; this repository does
not infer one from the symbol.
