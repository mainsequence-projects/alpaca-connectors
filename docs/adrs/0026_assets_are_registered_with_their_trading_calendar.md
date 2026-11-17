# ADR 0026: Assets Are Registered With Their Trading Calendar

## Status

Implemented and released on 2026-11-17 on ms-markets 4.0.1 and etfhextractor 0.6.0. In
Production, `alpaca-connectors asset link-calendars --execute` linked all 513 Alpaca assets the
same day, before the release: 512 to `NYSE` and 1 to `CRYPTO_24_7`; a dry run afterwards found
none without a calendar (see [Rollout](#rollout)).

## Context

ms-markets 4.0.0 stores a calendar as session rules and exceptions (its ADR 0055) and links
every asset to one stored calendar, `AssetCalendar` (its ADR 0056). `InterpolatedPrices` computes
an asset's bars from that calendar. It no longer reads a calendar name from the asset scope, and
refuses a scope entry that carries one with a `TypeError`. A run whose scope has an asset with no
link fails as a whole, and the error names each such asset. Nothing chooses a default calendar.
ms-markets' revision 0028 creates the link table with no rows, so after the Markets upgrade every
asset is unlinked, and every portfolio valuation of this application fails until its assets are
linked.

ms-markets 4.0.1 (its ADR 0057) lets each payload that writes an asset name the calendar:
`Asset.upsert`, `Asset.upsert_many` and the assets of an account registration. An upsert links
or moves the asset. An account registration links only an asset that has no calendar yet. It
adds `AssetCalendar.link_many` for linking many assets at once.

This application writes assets in three places: the single-asset upsert, the batch registration
behind Universe sync, the asset routes and the CLI, and the account registration behind account
registration and every holdings sync. None of them named a calendar.

## Decision

1. **The Alpaca class decides the calendar.**
   `alpaca_connectors.assets.alpaca_asset_details.TRADING_CALENDAR_BY_ALPACA_CLASS` maps
   `us_equity` and `us_option` to `NYSE`, and `crypto` and `crypto_perp` to `CRYPTO_24_7`, the
   stored 24/7 calendar ms-markets documents. It covers every class `ASSET_TYPE_BY_ALPACA_CLASS`
   registers, and a test keeps the two maps equal.
2. **Every asset write names it.** Each payload of the batch registration (`Asset.upsert_many`),
   the single upsert (`Asset.upsert`) and each held asset of the account registration carries
   `calendar_identifier`. The batch writes every asset of its plan, so registering a Universe
   again also links its assets that were registered before 4.0. An account registration keeps a
   calendar someone moved (ms-markets ADR 0057).
3. **The calendar is stored before it is named.** A registration names a calendar by
   `unique_identifier`, and ms-markets refuses one it does not store. `ensure_trading_calendars`
   reads each distinct calendar once and writes a missing one once from pandas-market-calendars,
   through etfhextractor's `ensure_trading_calendar`. A holdings sync therefore makes one
   `Calendar.filter` read before its one registration call.
4. **The interpolation scope carries identifiers only.** `build_alpaca_interpolated_prices` no
   longer takes `calendar_identifier`. The rebalance calendar still drives the calendar events
   and the Portfolio row. The interpolated-price tables are named from the bars source, the
   cadence and the interpolation settings, and not from the scope, so no table changes.
5. **One admin command links what was registered before.**
   `alpaca-connectors asset link-calendars` reads this project's asset details table and the
   assets Markets lists without a trading calendar (`AssetCalendar.unlinked_assets`, every page
   and type). It links only the Alpaca assets in both, one `AssetCalendar.link_many` per
   calendar of their class; an asset that has a calendar is not written, and `relink=False`
   keeps one linked between the read and the write. Without `--execute` it reports, for each
   calendar, how many assets it has and how many are unlinked, and writes nothing. It also covers
   assets Alpaca no longer lists, which no registration writes again.

`ensure_trading_calendar` stores a missing calendar to the library's last date and never extends
an existing one. Moving a stored calendar to a newer library release is the Markets deployment's
`resync-calendars` Job.

## Consequences

- A new asset is never left without a calendar, whichever path registers it.
- A price update fails only for an asset this application did not register. Assets of a Universe
  are registered by this application, so a Universe-backed portfolio is covered.
- A holdings sync makes one more Markets read.
- An asset that someone moved to another calendar is moved back the next time a Universe or the
  asset routes register it, because an upsert says what the asset is. A sync and
  `link-calendars` never move it.

## Rollout

1. Release etfhextractor 0.6.0 (tag `v0.6.0`), then move this application's lock to it and to
   ms-markets 4.0.1, and regenerate `requirements.txt`.
2. Push. The deployed Jobs and API then run ms-markets 4.0.1.
3. In each Environment, run `alpaca-connectors asset link-calendars`, read the report, then run it
   with `--execute`. Until then, every interpolation over an unlinked asset fails and names it.
