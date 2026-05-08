# ETF Holdings Extractors

See also:

- `docs/etf_extraction.md` for the standalone ETF extraction architecture and package boundary

## Scope Boundary

The extractor layer is independent of MainSequence.

Provider extractors do only one job:

- read published provider holdings sources
- parse component tickers
- return expanded symbol universes

They do not register assets, create categories, or publish DataNodes.

Modules:

- `etf_extraction/extractors/ishares.py`
- `etf_extraction/extractors/invesco.py`
- `etf_extraction/extractors/vanguard.py`
- `etf_extraction/extractors/state_street.py`
- `etf_extraction/extractors/registry.py`

## Supported Providers

- `ishares`
- `invesco`
- `vanguard`
- `state_street`

## URL Strategy

Provider URLs are derived dynamically by ticker and provider pattern. The project does not hardcode a separate static holdings URL for every ETF.

## Published-Source Rule

The extractors parse what the providers actually publish.

- prefer official download links or official holdings payloads
- do not invent hidden URLs when the provider does not publish them
- browser automation is allowed when the published page requires client-side interaction

## Browser Support

The repo includes `playwright` as a dependency because some provider flows need browser-backed fetching.

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
