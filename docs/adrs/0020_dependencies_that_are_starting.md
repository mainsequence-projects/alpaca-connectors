# ADR 0020: Dependencies That Are Starting

## Status

Proposed on 2026-11-12 for
[issue #18](https://github.com/mainsequence-projects/alpaca-connectors/issues/18), and implemented
by the same change. Decision 2 bounds a wait by the caller's deadline only with mainsequence
9.0.39 or later, whose request identity reads that deadline (SDK ADR 0041); this repository locks
9.0.38, so until the SDK is raised a row call waits for ms-markets' own limit.

Amended on 2026-11-13 with [decision 5](#decision): a Job waits up to 300 s for a release that
is waking, after the scheduled dispatchers kept failing on a Markets API that was still asleep.

Updated on 2026-11-27: the repository is on mainsequence 9.0.43, so decision 2 bounds a row call
by the caller's deadline. The Alpaca Agent sends that deadline with every tool call from
ms-tau-sdk 2.0.33 (the lock has 3.0.5), so the Agent's calls are bounded by the time their tool
call has left ([Agent](../agent.md#application-tool-timeouts)).

Amended on 2026-11-28 for ms-markets 6.4.0, which sends each Markets API call through the SDK's
`ResourceRelease.call` (MainSequenceMarkets#160). Finding the Markets API now only names its
release: it no longer asks for the release's runtime access, takes no `timeout`, and never
raises `last state: waking`. The decisions stay; how they are carried out changes:

- **Decision 1:** the API's startup still finds the Markets API on its own thread, then asks
  once for its runtime access, which is what starts a release that is asleep.
- **Decision 2:** a row call still waits for the discovery only as long as its caller. The call
  itself then waits for a release that is starting inside the SDK, within the caller's
  deadline, else the SDK's own limit: twice the cold start the platform expects, at least 30 s,
  or 300 s while it states none.
- **Decision 3:** a Markets API start reaches the connector as the SDK's
  `ReleaseReadinessTimeoutError`, `ReleaseDeadlineTooShortError` or
  `ReleaseColdStartTimeoutError`, already starts. The `last state: waking` message now marks only
  a MetaTables discovery. `ReleaseUnavailableError` and its `ReleaseNotDeployedError` stay
  failures.
- **Decision 5:** the schema gate's `markets_client` builds ms-markets' own client, then waits for
  its release with the SDK's `wait_for_runtime_access(wait_timeout_seconds=300)`. That keeps the
  access the client's calls then reuse. A `MARKETS_API_URL` is called as it is.

## Context

The connector scales to zero, and so do the two Main Sequence releases it works through:

- the **Markets API**, which ms-markets finds in the caller's Environment on the first Markets row
  call ([ADR 0018](0018_markets_api_boundary.md); `msm.client.discovery`);
- the **MetaTables API**, which `metatables.endpoint` finds when the runtime attaches this
  project's tables at startup, and asks again when its release token is refreshed.

Each library asks the platform for the release's runtime access, which also starts a release that
is asleep, then asks again every few seconds until the release serves or 30 s pass. An application
that wakes the connector before its work still met an asleep Markets API: the
connector found it only on the first row call of `POST /v1/accounts`, waited 30 s for it, and
answered the generic `503 dependency_unavailable`, which a caller cannot tell from a broken
connector. The Markets API served 5 s later.

Separately, `api_http_error` answered every `ValueError` as `409 action_conflict` with the
exception's text. A pydantic `ValidationError` is a `ValueError`, so a provider payload that its
model refused (an Alpaca asset on an exchange the model does not list) reached the caller as a
conflict carrying the validation dump.

The Jobs have no caller, and the same 30 s failed them. Every scheduled dispatcher run since
2026-11-09 failed after about 50 s, so none started a signal, portfolio or bars run. The logs of
the 2026-11-12 and 2026-11-13 runs show where: the schema gate (ADR 0012), the launcher's first
call to the Markets API, raised `The Markets API did not become available within 30 s (last state:
waking)`. Examples are the first bars dispatcher run `71a2f694` (00:00 UTC), signal dispatcher run
`f3fb5d13` (00:30) and portfolio dispatcher run `5f69ecc8` (02:00), all on 2026-11-13. A release
asleep since the previous night needs longer than 30 s: on 2026-11-13 a read of the asleep Markets
API's `/health` took 74 s.

## Decision

1. **Start the Markets API with the connector.** The API's startup starts finding the Markets API
   on its own thread before it attaches the runtime (`alpaca_connectors.dependencies`,
   `prewarm_dependencies`). Asking the platform for the release's runtime access starts it, so the
   Markets API wakes while the connector does. Startup never waits for it and never fails because
   of it: a failure is logged, and the first row call asks again. Only a hosted release does this
   (the SDK's deployment markers); locally and in tests nothing is asked. The MetaTables API needs
   no separate start: the runtime attachment that follows starts it and waits for it, as before.
2. **Wait only as long as the caller.** Markets row calls go through `MarketsApiBackend`, the
   ms-markets lazy backend whose discovery runs on its own thread, one at a time, outside any
   request. A row call waits for it at most as long as the request's caller will still wait
   (`Mainsequence-Request-Timeout-Ms`, less 1 s for the answer), then gives up; the discovery goes
   on, so the caller's retry finds the Markets API. A call whose caller set no deadline (the CLI,
   a caller that sends none) waits for ms-markets' own limit, as before; a Job waits longer
   (decision 5).
3. **Answer a start as a start.** A request that meets a release still starting answers
   `503` with `{"code": "dependency_starting", "retryable": true}`, a fixed message that names no
   service, and `Retry-After: 5`. A start is: a row call that gave up at its caller's deadline; an
   ms-markets or MetaTables discovery that gave up while the platform still reported the release
   waking (also when it was raised from another error, as a MetaTables token refresh raises it); and
   the SDK's own `ReleaseReadinessTimeoutError`, `ReleaseDeadlineTooShortError` and
   `ReleaseColdStartTimeoutError`. A release the platform reports unavailable, or one that cannot
   be found, stays the generic `503 dependency_unavailable`. Errors no route maps get the same
   answer.
4. **A provider payload the connector cannot read is a provider failure.** A pydantic
   `ValidationError` answers `502 provider_failure` with a fixed message; the log records the model
   and where and why each field failed, never the values. `409 action_conflict` stays for the
   conflicts the connector raises itself, in its own words.
5. **A Job waits out a start.** The schema gate, which every launcher except the migration Job
   runs first, waits up to `JOB_WAKE_SECONDS` (300 s, `alpaca_connectors.dependencies`) for each
   release:
   - it finds the Markets API through `markets_client`, which resolves the endpoint with
     ms-markets' `resolve_markets_api_endpoint(timeout=...)`, because
     `MarketsClient.from_environment` takes no limit;
   - it reads its MetaTables revisions inside `metatables.endpoint.api_endpoint_scope` with the
     same `discovery_timeout`.

   The rest of the run finds both releases serving and keeps the libraries' own limits. A release
   still waking after 300 s fails the run, with the same `last state: waking` message.

```mermaid
sequenceDiagram
    participant Caller
    participant Connector
    participant Platform
    participant Markets as Markets API
    Caller->>Platform: wake the connector
    Platform->>Connector: start
    Connector-)Platform: runtime access of the Markets API (own thread)
    Platform-)Markets: start
    Connector->>Platform: runtime access of the MetaTables API, until it serves
    Caller->>Connector: POST /v1/accounts (deadline)
    alt the Markets API serves within the deadline
        Connector->>Markets: row calls
        Connector-->>Caller: 201
    else it is still starting at the deadline
        Connector-->>Caller: 503 dependency_starting, Retry-After: 5
        Caller->>Connector: POST /v1/accounts again
        Connector->>Markets: row calls
        Connector-->>Caller: 201
    end
```

## Consequences

- A caller can retry a `dependency_starting` answer by itself. An account registration is written
  by upsert on the Alpaca account's identity, so it can be sent again; a repeat that completes
  publishes one more holdings snapshot.
- An account sync that meets a start records its operation as failed with `dependency_starting`
  and `retryable: true`, and still answers `200`, as for every capture failure.
- Telling a waking discovery from a failed one reads the state that ms-markets and MetaTables put
  in their message (`last state: waking`); the tests drive both libraries' own discovery code with
  fake platform answers, so a change of either message fails them. If either library adopts the
  SDK's release calls, their errors are recognised already.
- A row call whose caller set a deadline may give up while the Markets API discovery is still
  asking the platform rather than waiting for a start; the answer is the same and so is the retry.
- A MetaTables token refresh and a Markets token refresh still wait for their library's own limit;
  only the first Markets discovery of a process is bounded by the caller's deadline.
- A scheduled run that wakes both releases can spend up to ten minutes in its gate before it fails,
  inside the Jobs' 3600 s limit. Its targets start minutes after the dispatcher and find the
  releases serving. `tests/test_dependency_starts.py` runs ms-markets' own discovery against a
  Markets API that wakes after 60 s: `MarketsClient.from_environment` gives up at 30 s, and a Job's
  client is found. The same tests check that the gate passes the budget to both releases and that
  nothing after the gate keeps it.
