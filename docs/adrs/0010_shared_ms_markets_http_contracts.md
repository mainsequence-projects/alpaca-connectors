# ADR: Shared ms-markets HTTP Contracts

## Status

Accepted on 2026-09-09.

## Context

The Alpaca and Binance connector APIs independently implemented the same provider-neutral HTTP
machinery: paginated collections, discovery descriptors, bulk-action request and preflight
contracts, sanitized errors, and observable-operation state. Keeping those copies synchronized made
contract upgrades depend on coordinated connector releases and allowed small wire-level differences
to accumulate.

`ms-markets` 1.0.14 exposes these contracts from the installable `msm.api.http` package. The
Command Center contract baseline used for this change is commit
`534db0e6b4d247b1f7693a530e19958e90343f52`, including
`command-center.resource_collection@v1`, `command-center.resource_discovery@v1`,
`command-center.bulk_action_execution@v1`, and `command-center.bulk_action_preflight@v1`.

## Decision

The connector requires `ms-markets>=1.0.14` and imports the provider-neutral HTTP types and
builders from `msm.api.http`.

The shared boundary owns:

- collection pagination and construction;
- resource discovery models;
- bulk-action execution requests and preflight responses;
- generic sanitized HTTP-error mapping; and
- observable-operation status, step, error, and response models.

The connector continues to own:

- Alpaca resource declarations and provider option parsing;
- Alpaca and Main Sequence Secret error classification;
- provider services and route orchestration; and
- the durable asset-registration operation MetaTable and transition logic.

The shared `InMemoryOperationStore` is not used for asset registration because replacing the
existing MetaTable would make accepted work process-local and would weaken the documented polling
contract.

## Consequences

- Alpaca and Binance expose the same provider-neutral wire contracts through one released package.
- Future contract fixes can be released in `ms-markets` and consumed by connectors without copying
  implementation code.
- Provider-specific behavior stays explicit and testable in this repository.
- Durable operation storage remains independently evolvable from the shared response contract.

## Verification

- collection, discovery, preflight, and operation response models are subclasses or direct uses of
  the public `msm.api.http` models;
- provider-specific error tests retain their public status codes and error codes;
- current Command Center valid fixtures pass their schemas and invalid fixtures are rejected; and
- the full test suite, strict MkDocs build, and distribution build pass with `ms-markets` 1.0.14.
