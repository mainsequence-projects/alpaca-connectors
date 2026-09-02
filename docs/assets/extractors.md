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

- `src/etf_holdings.py`

## Supported Providers

- `ishares`
- `invesco`
- `vanguard`
- `state_street`

## URL Strategy

Provider URLs are derived by `etfhextractor` by ticker and provider pattern. This project does not
carry provider parser code.

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
alpaca-connectors asset register --seed-tickers IVV --component-provider ishares
alpaca-connectors asset register --seed-tickers QQQ --component-provider invesco
alpaca-connectors asset register --seed-tickers VNQ --component-provider vanguard
alpaca-connectors asset register --seed-tickers SPY --component-provider state_street
```

## Important Decision

`SPY` is not treated as an iShares product. If `SPY` components are needed, they come from the State Street extractor.
