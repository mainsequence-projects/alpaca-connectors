# ADR 0022: Configurations Never Block An Owner's Removal

## Status

Accepted on 2026-11-14 for
[issue #25](https://github.com/mainsequence-projects/alpaca-connectors/issues/25), and implemented
by the same change. The owner chose this fix on 2026-11-14 over the issue's proposal of one
registration per account and owner (see [Alternatives](#alternatives-considered)).

It amends [ADR 0019](0019_account_owners_vouched_for_by_trusted_applications.md):

- section 5, Registration: an external person's claim of an account without an owner can now be
  refused (decision 3);
- section 6: a configuration update that names no account is now checked on the configuration's
  own account (decision 2).

It needs no schema change and no revision.

## Context

The connector keeps one registration per Alpaca account. `AlpacaAccountDetails` is keyed on
`account_uid` alone, so the keys and the owner are stored once per ms-markets `Account`. Signal and
bar configurations name the `Account` (`account_uid`, `ondelete="RESTRICT"`) and run with whatever
keys its registration holds. Neither records who made it.

Issue #25, observed on 2026-11-14 on a hosted release with a paper account, found three ways one
principal blocks another:

- **Removal was refused by configurations.** `remove_account_registration` answered
  `409 action_conflict` while any signal configuration named the account, including one another
  principal made. A Main Sequence user can configure any account, because
  `require_account_access` reads nothing for a caller who is not an external person. So an external
  person's application could not finish removing that person, the step it needs to erase the
  person, and only the configuration's author could unblock it. Bar configurations also run with
  the account's keys, but removal did not check them; after a removal they failed for lack of keys.
- **A vouched registration claimed an account without an owner** (ADR 0019 section 5) and replaced
  its keys, even when signal configurations already ran on it. From then on those configurations
  ran with the person's keys and blocked the person's removal. This is the observed case: a legacy
  account without an owner (ADR 0019 section 8), configured by a Main Sequence user.
- **A second principal cannot register the account at all**: `409 account_owned_by_another`.

## Decision

### 1. Removal disables the configurations that run with the account

`remove_account_registration` never refuses because of configurations. Before it deletes the
registration and its keys, it disables every enabled configuration that names the account:

| Kind | Before | After | Effect |
| --- | --- | --- | --- |
| Signal configuration | `enabled` (reported as `scheduled`) | `false` | Out of the signal Job's scheduled run, as a pause does |
| Bar configuration | `enabled` | `false` | No run at all (ADR 0021 decision 2); a portfolio linked to it is blocked |

- Only the flag is written (`updated_at` too), and nothing the configuration references is
  validated, so a configuration whose universe is gone is disabled all the same. A configuration
  already disabled is left alone.
- The configurations stay and keep their account. The `Account` is only deactivated, so the
  `RESTRICT` key is never tested. They are enabled again only after the account is registered again,
  and then only by its owner (decision 2).
- The result adds `disabled_signal_configuration_uids` and `disabled_bar_configuration_uids`. It
  names configurations by UID only, because another principal named them.
- `POST /v1/accounts/actions/remove/preflight` no longer lists configurations as blockers. It
  counts the configurations it will disable in a warning. The CLI's `account remove` without
  `--execute` lists their UIDs, and the Agent's delete no longer stops on them.

### 2. Only the owner configures an owned account

Creating, updating or resuming a configuration makes it run with an account's keys. Once the
account has an owner, only that owner may do it:

- **Covered:** signal configuration create, update and resume (API routes and the Agent's
  `alpaca_manage_etf_signal`); bar configuration create and update (routes and
  `alpaca_manage_bar_configuration`).
- **The account judged** is the one the configuration will run with: the account the request names,
  else the configuration's current one. Moving a configuration off an owned account onto the
  caller's own is allowed.
- **Answers:** a Main Sequence caller gets `409 account_owned_by_another`, which does not name the
  owner. An external person keeps ADR 0019 section 6's `404` for any account that is not theirs. An
  update that names no account used to skip that check; it now applies to the configuration's own
  account.
- **Unchanged:** an account without an owner stays open to every caller. The signal pause
  action, deleting and running a configuration are unchanged; an update that only unschedules is
  still an update. The CLI calls the services directly and carries no owner
  (ADR 0019 section 10).

The check is `require_owner_may_configure_account` in the API's account services, called by the
signal and bar configuration services, so the routes and the Agent's tools share it. The Agent acts
for its requester, `(requester, requester)`.

### 3. An external person never claims an account that configurations run on

An external person's registration that would make them the owner of an account (no registration,
or one without an owner) is refused with `409 account_in_use` while enabled signal or bar
configurations name the account:

- It is checked after Alpaca has named the account and before any write, like ADR 0019's
  `account_owned_by_another`. The message names no configuration.
- Without a registration row the `Account` can still exist, from a removal, and configurations can
  still name it, so Markets is asked for the `Account` by its unique identifier. This is the only
  Markets read before the registration's writes, and only for this claim.
- Disabled configurations do not count. After decision 1, an owner who removed an account can
  register it again, and the configurations it disabled stay disabled.
- Only external people are checked. A Main Sequence user who registers a legacy account without an
  owner usually registers their own account with their own configurations, and ADR 0019 section 8
  has those rows take an owner at their next registration. The same owner registering again is
  never checked.

### What does not change

- One registration per account, so a second principal still gets `409 account_owned_by_another`
  (the issue's third case).
- The ms-markets `Account` identity, holdings, the frozen identities, signal UIDs and every schema.

## Alternatives considered

- **One registration per account and owner** (the issue's proposal 1 to 3). Each owner would hold
  their own keys, configurations would bind to a registration, and a removal would remove only the
  acting owner's registration. It answers all three cases. It was set aside on 2026-11-14 because
  it needs:
  - a new registration table;
  - a Job that copies the encrypted keys under the keyring (ADR 0017);
  - new bindings for both configuration kinds;
  - a choice of registration for every key consumer (holdings, refresh, orders, universes, bars,
    signals, portfolios);
  - a new account response shape;
  - a staged hosted rollout.

  This ADR does not rule it out.
- **The guards alone** (the issue's fallback): refuse cross-principal configurations and vouched
  claims, but keep refusing removal. The account already blocked on hosted would stay blocked until
  its configuration's author acted.
- **Deleting the dependent configurations on removal.** This would destroy another principal's
  work and their portfolios' links. Disabling keeps both and stops every run.

## Consequences

- An owner can always remove their own registration and its keys. The hosted account of issue #25
  is removed by its application's next `DELETE`, which pauses the Main Sequence user's signal
  configuration; its author points it at another account or deletes it.
- A configuration's author is not told when a removal disables it. They see it on the configuration
  (`scheduled` or `enabled` false) and in the runs it no longer gets, and the removal's result and
  log name it.
- A portfolio linked to a disabled bar configuration is blocked (ADR 0021). One that uses a paused
  signal configuration has no new weights until the signal is resumed on an account with keys.
- A Main Sequence caller can no longer create, update or resume configurations on an account
  another owner holds, which any caller could do before. Accounts without an owner are unaffected.
- A configuration created on the account between a removal's read and its delete stays enabled
  and fails for lack of keys, as bar configurations always did after a removal.

## Verification

`pytest` passes 711 tests, and `ruff check`, `ruff format --check` and `mkdocs build --strict`
pass. `tests/test_account_configuration_owners.py`:

- **The rules:** which registrations count as an external person's claim, another owner's account
  refused without naming the owner, and only enabled configurations counted.
- **Claims:** an external person's claim of a new or unowned account in use is refused before any
  Markets or detail write. The owner again, a Main Sequence user and the CLI are never checked. The
  claim reads the row's account, or asks Markets by identity when there is no row. The API answers
  `409 account_in_use`.
- **Configurations through the API:** for signal create, update and resume and bar create and
  update, another owner's account answers `409` and reaches no service. The owner, an unowned
  account and the external person's own account pass. Another person's configuration answers `404`,
  including on an update that names no account. Removing a mutated check fails these tests.
- **Removal:** the preflight warns instead of blocking, the owner's `DELETE` succeeds, the CLI's dry
  run lists the configurations, and the Agent passes its requester and no longer stops on them.

`tests/test_account_services.py` covers the removal service: only enabled configurations are
disabled, signal before bar, both before the keys are deleted, and the result names them by UID.
The bar configuration service tests check that create and update pass the configured account to the
owner check.

## Code

- `src/alpaca_connectors/account/ownership.py`: `AccountInUseError`, `require_owner_may_configure`
  and `is_claim_by_external_person`.
- `src/alpaca_connectors/account/services.py`: `enabled_account_configurations`,
  `disable_account_configurations`, the claim check in `register_alpaca_account` and the removal.
- `src/alpaca_connectors/market_data/configurations.py`: `bar_configurations_for_account` and
  `disable_bar_configuration`.
- `src/alpaca_connectors/api/app/services/accounts.py`: `require_owner_may_configure_account` and
  `configurations_disabled_by_removal`. The signal and bar configuration services call the first.
- `src/alpaca_connectors/api/app/errors.py`: `409 account_in_use`.
- `src/alpaca_connectors/api/app/routers/`: the account remove preflight, and the signal and bar
  configuration routes passing the acting owner.
- `src/alpaca_connectors/agent_tools.py` and `.tau/skills/alpaca-account-workflow/SKILL.md`: the
  Agent configures for its requester and reports what a deletion disabled.
- `src/alpaca_connectors/cli/account.py`: the dry run lists the configurations to disable.
