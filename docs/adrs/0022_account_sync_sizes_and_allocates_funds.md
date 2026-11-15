# ADR 0022: The Account Sync Sizes And Allocates The Account's Funds

## Status

Proposed on 2026-11-15, for the owner's review. Nothing is implemented.

Rewritten the same day on ms-markets ADR 0054, which withdrew the unit account targets this
ADR was first drafted on. The first draft had the connector write the account target in
units and resize it when a portfolio rebalanced. That is gone: the account target states
exposure, and the connector sizes it at every sync.

The decision marked (owner) was made by the owner on 2026-11-15.

## Context

ms-markets 3.7 fixes who owns what (ms-markets ADR 0054):

- **The account target states exposure.** A row of `TargetPositionsStorage` with
  `target_type="portfolio"` holds `weight_notional_exposure` (a share of the account's NAV) or
  `constant_notional_exposure` (an amount) in one `Portfolio`. It names no asset and no units.
  Writing it creates the `VirtualFund` of that account and portfolio.
- **ms-markets prices nothing.** Turning an exposure into units needs prices, so it is the
  provider's work, done at a sync.
- **A sync is one call.** The provider publishes the account's holdings at time `T`, sizes
  every exposure of the target into units, and calls `allocateAccountVirtualFunds` with those
  units as `sized_targets`. ms-markets assigns the holdings to the funds without any price.
- **ms-markets records the sync.** `AccountAllocationLinesStorage` gets one row per sync,
  owner and asset, with the sized target and the assigned units. The owner is a virtual fund,
  or the account itself for a direct asset target and for what no fund takes. The delta is
  target minus assigned. `getAccountVirtualFundAllocation` reads the lines of a sync back.

This connector is the provider for Alpaca accounts, so the sizing is its responsibility. Today
it does none of it:

- **The sync stops at holdings.** `POST /v1/accounts/{account_uid}/actions/sync` reads Alpaca
  and publishes one `AccountHoldingsSet` (`capture_alpaca_account_holdings`). Its operation
  record has one step, `capture_holdings`. The order path publishes holdings after fills through
  the same capture (`execution/services.py`, `_persist`).
- **No code reads a target or allocates.** Nothing in `src/` touches target positions,
  position sets, virtual funds or allocation.
- **The lock is at ms-markets 3.4.0.** `pyproject.toml` allows `>=3.4.0,<4`.

What the connector already has for sizing:

- **Prices:** `alpaca_latest_trade_prices` reads latest trades from Alpaca's data API.
- **Rounding rule:** each asset's `fractionable` flag (`AlpacaAssetDetails`).
- **Portfolio weights:** `AlpacaPortfolioWeightsStorage`, indexed by `time_index`.
- **Holdings in units:** the snapshot it publishes is already a positive `quantity` and a
  `direction` per asset, with cash as a `USD` holding.

Two things the connector does not own:

- **The account target.** The exposure is the user's request. An application writes it
  through the Markets API (`add_account_target_positions`). The connector reads it.
- **The portfolio weights.** The portfolio run is their only writer. The sync reads them as
  published and never writes, adjusts or re-times one.

## Decision

### 1. Depend on ms-markets 3.7

Raise the floor to `ms-markets>=3.7.0,<4`, relock and re-export `requirements.txt`. The Markets
API of the Environment must serve 3.7 before this ships.

### 2. The sync gains two steps after the capture

`sync_account` runs, in order:

| Step key | What it does |
| --- | --- |
| `capture_holdings` | As today: read Alpaca, publish the holdings snapshot at time `T` |
| `size_targets` | Read the account's `PositionSet` in force at `T` and size each of its exposures into units |
| `allocate_funds` | Plan with those units, then allocate with the plan's digest |

An account with no `PositionSet` at or before `T` ends after `capture_holdings`, with the two
new steps marked skipped. That is today's behavior.

### 3. What the sync reads at time `T`

Everything is selected by `time_index <= T`, where `T` is the holdings snapshot's time:

- **Target:** the account's `PositionSet` with the latest `position_set_time <= T`.
- **Weights:** for each `portfolio_uid` in that `PositionSet`, the latest
  `AlpacaPortfolioWeightsStorage` rows with `time_index <= T`, as the portfolio published them.
- **Prices:** Alpaca's latest trade for every asset the sizing needs.
- **Account value:** the `equity` Alpaca reports for the account in the same read as the
  holdings ([decision 4](#4-the-sizing-rule)).

The connector never writes a `PositionSet`. It sizes at every sync, with that sync's prices,
because ms-markets stores no units for a target: the sized units of a sync are valid for that
sync only.

### 4. The sizing rule

For each exposure row of the `PositionSet`:

| Row | Size `S` in USD |
| --- | --- |
| `weight_notional_exposure = x` | `x × the account's Alpaca equity` |
| `constant_notional_exposure = a` | `a` |
| An asset row with `single_asset_quantity` | Not sized: it is already in units |

**The account value is Alpaca's reported equity (owner).** Which value a share of NAV is
taken from is the provider's decision, and for Alpaca it is the `equity` of the Alpaca
account, read with the holdings at the sync. The connector does not compute a NAV of its own
from positions and prices. If Alpaca reports no equity, the step fails.

For a portfolio row, with the portfolio's weights `w_i`:

```text
raw_units_i = S * w_i / price_i
units_i     = raw_units_i                 if the asset is fractionable
              round half up to a whole    otherwise
```

For an asset row stated as a share or an amount, the same rule with one asset and a weight
of 1.

- A negative weight gives a short: a positive `quantity` with `direction: -1`.
- An asset that rounds to zero has no position.
- A portfolio with no weights at or before `T` is sized to nothing: an empty `positions`
  list. Its fund still gets its holdings set.
- An asset with no price fails the step. Nothing is allocated with part of a target.

Alpaca offers stocks and ETFs here, so there is no contract multiplier. The rule lives in
`alpaca_connectors.account` beside the holdings capture and is the only place that prices a
target.

The result is one `sized_targets` entry per exposure, in the shape ms-markets takes:

```json
{
  "target_type": "portfolio",
  "target_uid": "<portfolio uid>",
  "positions": [{"asset_uid": "<asset uid>", "quantity": 1200, "direction": 1}]
}
```

### 5. Plan, then allocate

The connector calls `plan_account_virtual_funds` with the `PositionSet`, `valuation_time=T` and
the `sized_targets`, then `allocate_account_virtual_funds` with the same inputs and the plan's
`plan_digest`. It uses the default policy with the version it read from the policy catalog.

- ms-markets writes each fund's holdings and the sync's lines. The connector stores neither.
- A shortfall is not refused. The allocation is written and the deltas are recorded.
- `already_applied` is a success: the same sync repeated writes nothing.
- A retired portfolio in the target fails the step with the blocker's message.

### 6. What the sync answers

The operation result gains, beside today's `holdings_set_uid`, `time_index` and `holdings_rows`:

| Field | Meaning |
| --- | --- |
| `position_set_uid` | The `PositionSet` in force for this snapshot, or null |
| `allocation_applied` | True when the allocation was written, false when already applied or skipped |
| `lines` | Per owner and asset: `target`, `assigned` and `delta`, as ms-markets recorded them |

The lines are also read from the Markets API at any time with
`GET /api/v1/account/{account_uid}/virtual-fund-allocations/`.

The connector does not trade the deltas. Placing orders stays with the application, through the
execution routes of [ADR 0016](0016_order_execution_for_authorised_applications.md).

### 7. A failure after the capture keeps the holdings

The holdings snapshot is published before sizing starts. If `size_targets` or `allocate_funds`
fails, the operation ends `failed` at that step with a public error, and the snapshot stays. The
next sync captures a new snapshot and tries again.

The Markets refusals map to public errors: `not_found`, `target_after_snapshot`,
`invalid_sized_targets`, `plan_changed`, `allocation_exists`, a policy version mismatch and a
retired portfolio.

### 8. The order path does the same

`_persist` publishes holdings after fills. It then runs the same `size_targets` and
`allocate_funds` steps, so every snapshot the connector publishes has its lines. As with the
holdings capture today, a failure there is a warning on the batch answer, not an error.

`OrderBatchResult` also gains the snapshot's time beside `holdings_set_uid`, because an
allocation is addressed by that time.

## Worked Example

Portfolio A is 60% AAPL and 40% MSFT. Portfolio B is 50% MSFT and 50% JNJ. The account's
`PositionSet` asks for 40% of its NAV in A and 300,000 in B.

At the sync, Alpaca reports AAPL 1,000, MSFT 600, JNJ 1,000 and TSLA 1,640, and an equity of
1,000,000. The latest trades are AAPL 200, MSFT 400, JNJ 150 and TSLA 250.

| Fund | Size | Asset | Calculation | Sized units |
| --- | --- | --- | --- | --- |
| A | 400,000 | AAPL | 400,000 × 60% ÷ 200 | 1,200 |
| A | | MSFT | 400,000 × 40% ÷ 400 | 400 |
| B | 300,000 | MSFT | 300,000 × 50% ÷ 400 | 375 |
| B | | JNJ | 300,000 × 50% ÷ 150 | 1,000 |

The connector sends those four positions. ms-markets records:

| Owner | Asset | Target | Assigned | Delta |
| --- | --- | --- | --- | --- |
| Fund A | AAPL | 1,200 | 1,000 | 200 |
| Fund A | MSFT | 400 | 309.68 | 90.32 |
| Fund B | MSFT | 375 | 290.32 | 84.68 |
| Fund B | JNJ | 1,000 | 1,000 | 0 |
| Direct | TSLA | 0 | 1,640 | −1,640 |

## Consequences

- Every holdings snapshot the connector publishes has its lines in ms-markets: each fund's
  target, what it was assigned and the delta, and the same for direct targets.
- A new exposure, or new portfolio weights, reach the funds at the next sync.
- The sync makes more calls: one target read, one weights read per portfolio, one price read,
  and two Markets calls. It still runs inside the request.
- A share of NAV follows Alpaca's equity, which Alpaca computes with its own prices. The
  units are sized with the latest trades, so a fund's sized value can differ slightly from
  its share of the equity.
- Cash is a holding. An account that states no direct target for `USD` records its cash as a
  direct line with a target of zero.
- A change made outside the connector (a dividend, a corporate action, a trade placed in Alpaca
  directly) is seen only at the next sync. There is no scheduled account sync, and this ADR adds
  none.
- The connector adds no table: the target is the application's, the lines are ms-markets'.

## Implementation Plan

1. Raise the ms-markets floor, relock, re-export.
2. Add the sizing rule and its tests: a share of Alpaca's equity and an amount, fractionable and
   whole-share rounding, shorts, a missing price, an asset that rounds to zero, a portfolio
   with no weights.
3. Add the `PositionSet` read at `T` and the weights read at `T`.
4. Add the plan and allocate calls and the refusal mapping.
5. Extend the sync operation's steps and result, and `OrderBatchResult`.
6. Call the same steps from `_persist`.
7. Update `docs/account/registration.md`, `docs/api.md`, the account workflow skill and the
   capability catalog.

No revision is needed: the operation's `steps` and `result` are JSON.

## Not Verified

- How the connector reads the weights of a portfolio it does not compute itself.
- That the API workload's identity may read the target and call the plan and allocate routes
  on the Markets API. The owner states it may; it was not exercised.
- ms-markets 3.7 is not released. Nothing here was run against a Markets API on 3.7.
