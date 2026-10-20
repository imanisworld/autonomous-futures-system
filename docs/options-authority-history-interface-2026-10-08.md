# #1184 — Options authority-history validation (source only)

**State:** Offline defensive test contract; NOT DEPLOYED, NOT AUTHORITATIVE.

## What exists

- options_evidence/authority_history.py: read-only canonical event replay using sequence, event digest, fixed service principal, exact ASCII human IDs, strict UTC ordering, authenticated event/approval callbacks, and independently checked latest-head metadata.
- tests/test_options_authority_history.py: forged grant, removed revocation, stale snapshot/head, bad signer, missing approval, invalid chronology, Unicode/confusable identity, duplicate-key/torn-tail serialization, RETIRED and evaluator grant prohibition.
- Any replay returns ReplayDiagnostic. The `grant_observed` field is a NON-AUTHORITATIVE diagnostic; `execution_authority` remains exactly False, including on validated fixture grants. No trading control consumes it.

## Hard security boundary

The ReplayTrust callbacks are an abstract offline interface. They are not, and cannot replace, the required independently permissioned durable event store, protected latest-head anchor or proof of authenticated operator approval. A malicious Python caller could supply their own in-process callbacks; this is why the output is deliberately not executable authority. Hash chains without independently protected latest-head identity are forgeable or replayable.

The system has ZERO preapproved human principal IDs. The approved_humans allowlist defaults to no configuration, and the validator refuses absent trust. Sample HMAC keys in tests are fixed public fixture bytes, not credentials. No real approver, key, store, permission, or machine authorization is supplied or implied.

## Before any real authority persistence or execution wiring

1. The operator must explicitly select independently controlled write paths for event records and latest-head attestation, verified on the actual server with read-only permission evidence. Reader/evaluator cannot edit either, and a stale signed head cannot be replayed as latest.
2. A separately authenticated signing/verifier mechanism and verified approval-reference registry must be provisioned with key ownership, rotation/revocation and failure behavior. A callback supplied by trading code is not proof.
3. The operator must supply exact allowlisted human principal IDs; no identity is inferred from input strings, env values, historical fitness state or a test fixture.
4. A separate source PR and independent security review must test the actual implementation and anti-rollback behavior before any storage, runtime fitness, ticket, risk or broker integration.
5. Any approval activation, paper trading, or live trading remains a distinct operator decision after deployment gates.

## Non-goals and restrictions

- Do not connect this module to fitness, scanner, options manager, alert, broker, risk or execution paths.
- Do not persist or rewrite any existing authority history, journal, epochs or collector records.
- Do not use returned diagnostic grants to start forward evidence or authorize trading.
- No repo source merge by itself is a runtime or trade authorization.

**OPTIONS HOLD · FUTURES HOLD.**
