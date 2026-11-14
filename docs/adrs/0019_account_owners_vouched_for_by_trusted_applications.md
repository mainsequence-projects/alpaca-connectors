# ADR 0019: Account Owners Vouched For By Trusted Applications

## Status

Accepted on 2026-11-07 at the owner's request to implement it, for
[issue #9](https://github.com/mainsequence-projects/alpaca-connectors/issues/9); that request
approved the names under [Names](#names). Implemented in the connector the same day; the hosted
review of section 8 runs once revision `0022` has been applied in each Environment. The owner
decided three points on 2026-11-07:

- a vouched call reaches only its person's accounts (section 6);
- a malformed allow-list stops the API from starting (section 3);
- existing accounts take their owner from whoever last entered the keys, services included, and
  the hosted accounts are reviewed once (section 8).

It amends:

- [ADR 0014](0014_sdk_request_identity.md) §4: the identity module also reads the request's caller
  context, and only there;
- the `AGENTS.md` rule "No UID comes from a body, header or tool argument": a trusted caller may
  name an owner through caller context (section 3);
- [ADR 0016](0016_order_execution_for_authorised_applications.md) decision 7, for vouched calls
  only (section 6);
- the ownership bullet of [ADR 0017](0017_encrypted_account_credentials.md) ("Per-person ownership
  is recorded, not enforced"), for vouched calls only.

No Environment lists a vouching caller until that caller meets section 9.

Amended on 2026-11-14 by [ADR 0022](0022_configurations_never_block_an_owners_removal.md) for
issue #25:

- configurations never block an owner's removal, which disables them instead;
- only an account's owner configures it;
- an external person's claim of an account without an owner is refused while enabled
  configurations run with its keys (section 5).

## Terms

| Term | Meaning |
| --- | --- |
| Gateway application | An application that serves its own people, who are not Main Sequence users, and signs in to Main Sequence as an external workload (tdag-django ADR-0056). |
| External person | One of a gateway application's people. Main Sequence knows only the gateway's workload, never the person. |
| Vouching caller | The Main Sequence workload that calls this connector and states which external person a call is for. It is the gateway itself, or an application the gateway calls. |
| Caller context | The W3C Baggage keys a call carries under the prefix `mainsequence.` (tdag-django ADR-0057). |
| Vouched call | A call whose caller is on the allow-list (section 3) and whose caller context names an external person. |

## Context

### Accounts for people who are not Main Sequence users

A gateway application's people reach Main Sequence only through the gateway's workload. When a
vouching caller calls this connector for one of them, the verified caller is the vouching caller,
never the person. So an external person cannot own an Alpaca account registered here, and any
application that serves such people cannot link a person's own account.

The first such case is issue #9: Hunch's `hunch-backend` is the gateway, and HunchAPI is the
vouching caller. Nothing in this decision is specific to it.

### What the connector records today

- **No owner exists.** `credentials_updated_by_user_uid` is the Main Sequence user who last wrote
  the keys. Every rotation overwrites it (`stored_credential_values`). An application that reads it
  as the owner sees the owner change whenever someone else rotates the keys. The credential
  migration Job keeps it unchanged.
- **Registration is an upsert.** `register_alpaca_account` keys the row on the Alpaca account
  number and the paper flag. A second registration of the same Alpaca account replaces the keys
  and the author, whoever sends it, as long as Alpaca accepts the keys.
- **Reads are not scoped.** Any caller the release admits lists and reads every account. Whose
  account an application may use is that application's decision (ADR 0016 decision 7).

### Caller context (tdag-django ADR-0057)

ADR-0057 lets an application carry its own context in W3C Baggage, with keys of the form
`mainsequence.<application>.<name>`. A gateway application names its person with two keys under
its own segment:

- `mainsequence.<segment>.external_user_gateway`: the workload User UID of the gateway;
- `mainsequence.<segment>.external_user_uid`: the gateway's own opaque id for the person.

Main Sequence delivers the context as received and never checks it. ADR-0057 §3 makes trust the
application's job, at each hop: accept context only from a caller trusted to vouch for it, checked
against the authenticated caller at the hop that reads it; from any other caller, ignore it; treat
missing context as "no external person" and never fall back to broader access.

### What stands in the way

- `AGENTS.md` says no UID comes from a body, header or tool argument, and ADR 0014 §4 says no route
  reads an identity header. Baggage is a header.
- This repository locks `mainsequence` 9.0.33, which does not read baggage. `mainsequence` 9.0.38
  does (its ADR 0040): `install_request_identity` attaches the request's `baggage` to the
  OpenTelemetry context for that request, and only once it has verified the caller. ADR-0057's
  rollout note of 2026-11-07 still lists this SDK step as open.
- An application between the gateway and this connector may forward incoming baggage unchanged.
  If it also forwards a person's keys on a call it did not verify,
  anyone who may call that application can name any external person here (section 9).

## Decision

### 1. An owner is a person pair

An account's owner is `(owner_gateway_uid, owner_user_uid)`:

- a Main Sequence user is `(uid, uid)`;
- an external person is `(gateway workload User UID, the gateway's opaque person id)`. A vouched
  pair whose two values are equal is refused, so an external person can never be reported as a
  Main Sequence user.

The owner is set when the account is first registered. Rotations, renames and active-state changes
never change it. There is no transfer route: to move an account, its registration is removed and
the new owner registers it.

`credentials_updated_by_user_uid` keeps its meaning: the Main Sequence user who last wrote the
keys. On a vouched call that is the vouching caller, because the person has no Main Sequence user.
The owner names the person.

### 2. The acting owner

`alpaca_connectors.api.app.identity.acting_owner()` decides it once per request:

| Call | Acting owner | Caller context |
| --- | --- | --- |
| Requester-bound (the Alpaca Agent working for a person) | `(requester, requester)` | ignored |
| Caller on the allow-list, both keys present and valid, gateway allowed for that caller | `(gateway, user)` | used |
| Caller on the allow-list, both keys present, gateway not allowed for that caller | refused, `403 caller_context_untrusted_gateway` | — |
| Caller on the allow-list, one key only, an empty or oversized value, or equal values | refused, `400 caller_context_invalid` | — |
| Caller on the allow-list, neither key | `(caller, caller)`, as today | — |
| Any other caller | `(caller, caller)`, as today | logged as an unverified claim, never used |

A refusal never falls back to the caller's own identity. Values are limited to 64 characters for
the gateway and 255 for the person id. They are stored as given and only ever bound as statement
parameters, never used to build a query, path or log format. Results and errors never quote them.

### 3. The vouching allow-list

`ALPACA_CONNECTORS_VOUCHING_CALLERS` is set in `.mainsequence/workflows/alpaca-connectors-api.yaml`.
It is a JSON list; each entry names one vouching caller, the segment of its keys, and the gateways
it may vouch for:

```json
[
  {
    "caller_uid": "<vouching caller workload User UID>",
    "segment": "<application segment>",
    "gateway_uids": ["<gateway workload User UID>"]
  }
]
```

- The connector reads `mainsequence.<segment>.external_user_gateway` and
  `mainsequence.<segment>.external_user_uid`. It names no application in code.
- A gateway that calls this connector directly lists its own UID as both `caller_uid` and a
  gateway. An application between them lists its own UID as `caller_uid` and the gateways it
  serves.
- One value serves every Environment: UIDs are unique, and the caller assertion is already bound
  to its Environment (ADR 0014 §2). A gateway with one workload per Environment lists each.
- Each workload that calls with a person's context is its own entry. A Job is listed only if it
  forwards the context (section 9).
- Unset or `[]` means no caller vouches, and behaviour is exactly as today.
- A malformed value fails application creation (decided by the owner on 2026-11-07). The
  deployment shows the failure and the previous revision keeps serving. Starting with an empty list
  instead would silently turn every vouched call into a call by the vouching caller itself, and
  accounts would be registered with that caller as owner.

### 4. Reading caller context

Routes and services never read baggage. Only `identity.py` does, through OpenTelemetry's standard
API (`opentelemetry.baggage`), as ADR-0057 §10 item 5 says applications should.

The SDK fills that context: from `mainsequence` 9.0.38, `install_request_identity` reads the
request's `baggage` header after it has verified the caller and attaches it for the request. The
connector moves to `mainsequence` 9.0.38 with this change and adds no middleware or header parsing
of its own. Malformed or oversized baggage is no context, and never fails a request (ADR-0057
§12.6).

### 5. Registration

`POST /v1/accounts` is unchanged in shape. The keys still pass once in the body, are stored
encrypted (ADR 0017), and are never returned or logged.

1. Alpaca authenticates the keys and returns the account number, as today.
2. Before any write, the asset registration of held positions included, the connector compares
   the existing row for that Alpaca account with the acting owner:
   - no row: the acting owner becomes the owner;
   - a row with the same owner: registration proceeds as today, and the owner is unchanged;
   - a row without an owner (legacy, section 8): the acting owner becomes the owner;
   - a row with another owner: refused, `409 account_owned_by_another`. The message does not
     name the owner.
3. A registration or rotation that carries `key_request_uid` on a vouched call is refused
   (`400 caller_context_invalid`). Key requests belong to Main Sequence people signed in to the
   site (ADR 0015).

The check and the upsert are separate statements. Two owners registering the same Alpaca account
at the same moment resolve to the last writer; both need valid keys for that account.

### 6. A vouched call reaches only the person's accounts

Decided by the owner on 2026-11-07. A bug in a vouching caller can then never reach another
person's account: a call for person A that names person B's account places no order and reads
nothing.

On a vouched call, every route that names an account resolves it through one check,
`require_account_access(account_uid)`. An account the person does not own answers
`404 not_found`, the same answer as a missing account, and nothing about the account is read
from Alpaca.

This covers the account path, query, body or options of:

- `/v1/accounts/{account_uid}` and its actions (`refresh`, `remove`, `capture-holdings`);
- `/v1/accounts/{account_uid}/holdings/*` and `/v1/accounts/{account_uid}/orders/*`;
- universe previews and runs, bar configurations, and signal and portfolio configurations.

`GET /v1/accounts` returns only the person's accounts. Routes that name no account (assets, market
data, project state) are unchanged. Calls that are not vouched are unchanged: for them, ADR 0016
decision 7 still holds.

### 7. Listing by owner

`GET /v1/accounts` accepts `owner_gateway_uid` and `owner_user_uid`, both or neither (otherwise
`400`), matched exactly. They filter; they are never an author or owner. On a vouched call they
can only narrow section 6's scope. Account discovery lists both as filterable fields.

### 8. Storage and records

- Revision `0022_add_account_owner` adds two nullable columns to `AlpacaAccountDetails`:
  `owner_gateway_uid` (`String(64)`) and `owner_user_uid` (`String(255)`). It also adds one index
  on the pair and copies `credentials_updated_by_user_uid` into both where that is set. It adds
  only; nothing is dropped.
- A row whose author is unknown keeps a null owner until it is registered again. An author that is
  a workload User (an application that registered with its own identity) becomes that workload's
  `(uid, uid)` owner, rather than leaving the row for the next registration to claim (decided by
  the owner on 2026-11-07). Operators review the hosted accounts' owners once in each Environment
  after the revision runs.
- `AccountResponse` gains `owner: {gateway_uid, user_uid} | null`, and keeps
  `credentials_updated_by_user_uid`.
- The API logs the acting owner pair on registration, rotation, removal, holdings capture and
  order submission. The pair is public by contract (ADR-0057 §2); keys never appear.
- The execution ledger (ADR 0016 decision 5) is unchanged.

### 9. What a vouching caller must do

The connector trusts an entry's caller to have checked the person on that request. Before an entry
is added, the vouching caller:

1. sends the two keys to the connector only on a call made for a person it verified on the
   inbound request, with its own proof from the gateway (ADR-0057 §3);
2. removes its segment's `external_user_*` keys from the baggage it forwards on every other call to
   the connector, including calls made for a Main Sequence user. Forwarding baggage unchanged would
   turn this connector into a confused deputy;
3. runs Jobs that call the connector with a person's context only under run permission that only
   the vouching caller holds (ADR-0057 §3, Q16), and lists them here only if they forward the
   context;
4. checks `owner` on the registration response before it links the account to the person.

### 10. Unchanged

- Main Sequence users, the site and the Alpaca Agent: their acting owner is
  `(acting user, acting user)`. The Agent's 17 tools and the key-request flow are unchanged, and
  no tool takes an owner.
- The CLI calls the account services directly and records no author today, so a CLI registration
  has a null owner. Its first registration through the API records one (section 5).
- Jobs in this repository read no caller context. The bars, signal and portfolio Jobs, the frozen
  identities, and the signal UIDs are untouched.
- Main Sequence decides nothing from caller context (ADR-0057 §12); this connector decides only
  what sections 2, 5 and 6 say.

## Names

| Name | Kind |
| --- | --- |
| `owner_gateway_uid` (`String(64)`), `owner_user_uid` (`String(255)`), and their index | `AlpacaAccountDetails` columns |
| `0022_add_account_owner` | migration revision |
| `ALPACA_CONNECTORS_VOUCHING_CALLERS` and its JSON shape (`caller_uid`, `segment`, `gateway_uids`) | API environment variable |
| `external_user_gateway`, `external_user_uid` under `mainsequence.<segment>.` | caller context keys read |
| `owner` (`gateway_uid`, `user_uid`) on `AccountResponse` | response field |
| `owner_gateway_uid`, `owner_user_uid` on `GET /v1/accounts` | query parameters |
| `caller_context_invalid` (400), `caller_context_untrusted_gateway` (403), `account_owned_by_another` (409) | error codes |
| `acting_owner()`, `require_account_access()` | functions |

## Alternatives considered

- **An owner field in the registration body.** Rejected: it breaks the rule that no UID comes from
  a body, it covers only registration, and caller context already travels on every call.
- **The connector verifies the gateway's own proof of the person.** Rejected: that proof is
  addressed to the application the gateway calls, which does not forward it.
- **Treat the person as a platform requester.** Rejected: external people are not Main Sequence
  users, and baggage is never a requester (ADR-0057 §12.4).
- **Trust any caller's context.** Rejected: anyone who may call the release could then name any
  owner (ADR-0057 §14).

## Consequences

- An application that serves external people can register and link a person's own Alpaca account
  once it meets section 9 and the allow-list names it. Issue #9 is the first.
- Applications that read `credentials_updated_by_user_uid` as the owner move to `owner`.
- Existing accounts gain an owner from the backfill, and a rotation no longer changes who owns an
  account. After revision `0022` runs, an operator reviews the owners of the hosted accounts once
  in each Environment.
- Registering an Alpaca account that another owner holds is refused instead of taking it over.
- `AGENTS.md`, the `identity.py` docstring, the [API](../api.md) and the account documentation
  describe the acting owner, the allow-list and the scope.
- The connector moves to `mainsequence` 9.0.38, which reads the caller context for it.

## Verification

`tests/test_account_ownership.py` calls the API through the SDK's real local request identity,
which attaches the `baggage` header once it has verified the caller:

- every row of section 2's table: a listed caller naming a person, without context, through an
  untrusted gateway, with one key, equal values or an oversized value; context from a caller
  outside the allow-list; and a requester-bound call that carries context. No refusal quotes the
  gateway or the person id;
- the allow-list: unset, empty, valid, each malformed shape (never quoted), application creation
  failing on a malformed value, and the workflow's empty list;
- registration: another owner's account is refused before any Account, asset, detail or holdings
  write; a new, unowned or same-owner registration records the owner, and the vouching caller
  stays the key author; a registration without an owner (the CLI) leaves it alone; a rotation's
  values carry no owner;
- the API: a vouched registration records the person and reports `owner`; a Main Sequence
  caller's never does; `409 account_owned_by_another`; key requests refused for an external
  person; the scoped and filtered listing; every account route of section 6 answering `404` for
  another person's account without calling its service; a person's own account; calls for Main
  Sequence users reading nothing extra; the Agent registering for its requester;
- revision `0022`: its parent, columns, index name (equal to the model's), backfill and downgrade.

`tests/test_schema_gate.py` expects head `0022`.

## Code

- `src/alpaca_connectors/account/ownership.py`: the owner pair, the registered owner and the
  refusal of another owner's account.
- `src/alpaca_connectors/account/alpaca_account_details.py` and
  `migrations/versions/alpaca_connectors/0022_add_account_owner.py`: the columns and the backfill.
- `src/alpaca_connectors/account/services.py`: the check before any write, the owner on the
  registration row, and the owner filter on the listing.
- `src/alpaca_connectors/api/app/caller_context.py`: the allow-list, validated by `create_app()`.
- `src/alpaca_connectors/api/app/identity.py`: `acting_owner()`.
- `src/alpaca_connectors/api/app/services/accounts.py`: `require_account_access()`, its route
  dependency for the holdings and order routes, and the owner action log; the account, asset,
  universe, bar and signal configuration routers call it for every account they name.
- `src/alpaca_connectors/api/app/schemas.py` and `errors.py`: `owner` on `AccountResponse` and
  `409 account_owned_by_another`.
- `src/alpaca_connectors/agent_tools.py`: the Agent registers for its requester.
- `pyproject.toml` and `uv.lock`: `mainsequence` 9.0.38.
- `.mainsequence/workflows/alpaca-connectors-api.yaml`: `ALPACA_CONNECTORS_VOUCHING_CALLERS` as
  `[]` until a vouching caller meets section 9.
