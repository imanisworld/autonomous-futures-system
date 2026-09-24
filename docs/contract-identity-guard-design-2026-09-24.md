# Contract-identity guard implementation design (2026-09-24)

**Type:** implementation design only. No code, tests, Pine, config, runtime or deployment change.
**Status:** design lane only; broker execution remains blocked until a later implementation is reviewed and proven.
**Builds on:** `docs/contract-identity-audit-2026-09-24.md` (#965), which found that neither the current TradingView alert payload nor the Tradovate order path carries a trustworthy dated-contract identity today.

## 1. Safety objective

Before any broker order is allowed, the system must prove that the dated contract routed to Tradovate is the same dated contract that produced the alert's entry, stop and target prices.

The guard must fail closed when identity is missing, malformed or mismatched. It must not replace `_ROLL_DAYS = 8` with a new inferred roll-date constant. The guard compares names; it does not predict roll dates.

## 2. Scope

### In scope for the later implementation

- MNQ and MES only.
- TradingView Pine emits an optional `contract_hint` field in the alert JSON.
- Webhook payload and `BracketOrder` preserve the hint without changing strategy decisions.
- Tradovate broker compares the normalized hint against the resolved routed contract immediately after `_find_contract_id` and before any order body is built.
- Rollout starts observe/log-only and requires an explicit pin-style flag before blocking.

### Out of scope

- No M2K, MGC, MCL or options expansion.
- No live execution enablement.
- No replacement roll calendar.
- No Tradovate market-data websocket dependency in the first implementation.
- No changes to strategy entries, exits, stops, sizing, risk rules, scanner behavior or replay scoring.
- No deployment in the design PR.

## 3. Alert-side `contract_hint`

### 3.1 Field name

The alert JSON adds one new nullable field:

```json
"contract_hint": "CME_MINI:MNQZ2026"
```

Allowed values:

- full TradingView dated symbol, preferred: `CME_MINI:MNQZ2026`, `CME_MINI:MESH2027`;
- root/month/year without exchange, tolerated for tests and internal fixtures: `MNQZ2026`, `MESH2027`;
- normalized short form, accepted only after normalizer support is explicit: `MNQZ6`, `MESH7`;
- `null` when Pine cannot prove the identity.

The preferred emitted format should be the full TradingView symbol with four-digit year. That keeps the alert self-describing and avoids decade ambiguity.

### 3.2 Candidate contract generation

For each supported root, Pine generates candidate dated symbols from the quarterly equity-index futures cycle:

| Root | Exchange prefix | Quarterly month codes |
|---|---|---|
| `MNQ` | `CME_MINI:` | `H`, `M`, `U`, `Z` |
| `MES` | `CME_MINI:` | `H`, `M`, `U`, `Z` |

Candidate generation is not a roll rule. It should produce adjacent plausible dated contracts around the bar date, not decide which one is active.

Minimum candidate set per bar:

1. the nearest quarterly contract whose delivery month is not clearly behind the bar's calendar quarter;
2. the next quarterly contract after it.

A safer implementation may include the previous quarterly contract as a third candidate during the first days after a delivery month, but the first implementation should keep the comparison simple and testable unless the two-candidate approach fails in proof.

If candidate symbol construction produces an unavailable symbol or `request.security()` returns `na`, that candidate is ignored. If no candidate can be evaluated, emit `null`.

### 3.3 Comparison method

For the current chart bar on `MNQ1!` or `MES1!`, Pine compares the continuous chart's bar behavior against each candidate dated contract requested at the same timeframe.

Recommended primary comparison:

- chart delta: `close - close[1]` from the continuous chart;
- candidate delta: `request.security(candidate, timeframe.period, close - close[1])`;
- match when absolute delta difference is within a small tick-based tolerance for that root.

Recommended secondary check:

- candidate close is not `na`;
- chart and candidate both have valid prior closes;
- candidate is not stale across the current bar.

Do not rely only on absolute close equality because continuous futures may be back-adjusted historically. The alert's current realtime prices still need later proof, but the identity hint should be derived from the same per-bar behavior method that found the TradingView switch dates in #960.

### 3.4 Match states

| State | Pine behavior | Alert value |
|---|---|---|
| exactly one candidate matches | emit that dated symbol | `"CME_MINI:MNQZ2026"` |
| zero candidates match | identity unknown | `null` |
| multiple candidates match | ambiguous | `null` |
| candidate data unavailable | identity unknown | `null` |
| unsupported root | identity unknown | `null` |

Ambiguity must never pick a winner. `null` is acceptable because the broker guard will either log it during observe mode or block it under enforcement.

### 3.5 Pine proof required before implementation approval

Before the Pine change can be used in the broker path, collect manual or fixture proof for both MNQ and MES:

- normal non-roll day: exactly one matching candidate;
- near roll window: old candidate before switch and new candidate after switch;
- unsupported or unavailable candidate: `null`;
- no production alert is modified until the payload change is reviewed.

## 4. Alert schema and compatibility

### 4.1 Payload shape

Current alerts remain valid while enforcement is disabled. New alerts include `contract_hint`:

```json
{
  "ticker": "MNQ1!",
  "strategy": "orb_breakout",
  "direction": "LONG",
  "entry": 25000.25,
  "stop": 24980.25,
  "target": 25040.25,
  "contract_hint": "CME_MINI:MNQZ2026"
}
```

### 4.2 Backward compatibility

- Payload parser accepts missing `contract_hint` as `None`.
- Runner passes `None` through to `BracketOrder` when absent.
- Observe mode logs missing hints but does not block.
- Enforcement mode blocks missing hints with `CONTRACT_IDENTITY_UNKNOWN` before contacting Tradovate.

### 4.3 Schema discipline

The field should be optional at the payload layer but treated as required by policy once enforcement is pinned on the box. Do not silently synthesize a hint from root, ticker or `_ROLL_DAYS` when the alert omits it.

## 5. Broker-side guard

### 5.1 Compare location

The compare belongs in `execute_bracket`:

1. receive `BracketOrder` with `instrument` root and optional `contract_hint`;
2. call `_find_contract_id(root)`;
3. receive the routed Tradovate contract id and resolved symbol/name;
4. normalize `contract_hint` and the resolved routed symbol;
5. if enforcement flag is set, block on unknown, unnormalizable or mismatch;
6. only then build the Tradovate order body.

This location is intentionally after contract resolution and before order body construction. A blocked guard must result in no order request being sent to Tradovate.

### 5.2 Normalization rules

Canonical comparison form:

```text
<ROOT><MONTH_CODE><FOUR_DIGIT_YEAR>
```

Examples:

| Input | Canonical |
|---|---|
| `CME_MINI:MNQZ2026` | `MNQZ2026` |
| `MNQZ2026` | `MNQZ2026` |
| `MNQZ6` | `MNQZ2026`, only when the expected decade can be derived unambiguously from the routed contract context |
| `MESH2027` | `MESH2027` |

Normalizer requirements:

- strip exchange prefix before parsing;
- uppercase root and month code;
- accept only supported roots for this guard: MNQ, MES;
- accept only quarterly month codes: H, M, U, Z;
- preserve four-digit years when present;
- expand one-digit years only when unambiguous against the routed symbol/date context;
- return unnormalizable rather than guessing.

### 5.3 Block reasons

The implementation should use explicit no-fill/cancel reasons:

| Reason | Trigger | Tradovate request? |
|---|---|---|
| `CONTRACT_IDENTITY_UNKNOWN` | enforcement on and hint is absent/null/empty | no |
| `CONTRACT_IDENTITY_UNNORMALIZABLE` | enforcement on and either hint or routed symbol cannot be normalized | no |
| `CONTRACT_IDENTITY_MISMATCH` | enforcement on and normalized hint differs from normalized routed symbol | no |

These reasons belong in the same journal/no-fill taxonomy used for existing broker-side cancelled fills. They should be visible in monitoring and Discord/status output as "why no trade" evidence.

## 6. Rollout flag

Proposed policy flag name:

```text
CONTRACT_IDENTITY_GUARD_ENFORCED=true
```

Behavior:

| Flag state | Behavior |
|---|---|
| unset / false | observe only: log normalized hint, routed symbol and verdict; never block solely for identity |
| true | fail closed on unknown, unnormalizable or mismatch |

The flag is a safety pin, not permission for live trading. Even with the flag enabled, broker execution remains subject to all existing paper/live, account-pin, risk, session and deployment gates.

The first implementation should default to observe mode. Enforcement should wait for alert payload proof and roll-seam evidence.

## 7. Validation plan

### 7.1 Unit tests

Add tests for the normalizer:

- `CME_MINI:MNQZ2026` -> `MNQZ2026`;
- `MNQZ2026` -> `MNQZ2026`;
- `MNQZ6` expands only when unambiguous;
- lowercase inputs normalize;
- unsupported root fails;
- unsupported month code fails;
- malformed strings fail;
- `None`/empty fails as unknown.

### 7.2 Broker guard tests

Mock `_find_contract_id` and assert:

- observe mode never blocks but logs verdict;
- enforcement + missing hint -> `CONTRACT_IDENTITY_UNKNOWN`;
- enforcement + malformed hint -> `CONTRACT_IDENTITY_UNNORMALIZABLE`;
- enforcement + routed symbol malformed -> `CONTRACT_IDENTITY_UNNORMALIZABLE`;
- enforcement + mismatch -> `CONTRACT_IDENTITY_MISMATCH`;
- enforcement + match proceeds to order-body construction;
- all blocked cases send no Tradovate order request.

### 7.3 Payload and runner tests

- existing fixtures without `contract_hint` still parse;
- new fixtures with `contract_hint` parse and reach `BracketOrder` unchanged;
- old alerts in observe mode retain current behavior;
- old alerts in enforcement mode block before order submission.

### 7.4 Pine / TradingView proof

Before production alerts are changed:

- run the Pine probe on MNQ and MES continuous charts;
- capture emitted `contract_hint` during ordinary sessions;
- test candidate unavailable behavior;
- verify no alert is attached to scratch probes;
- review production Pine diff separately.

### 7.5 Roll-seam proof

Before enforcement can be considered:

- replay or collect evidence across at least one quarterly roll seam for MNQ and MES;
- verify hint flips when TradingView continuous actually flips;
- verify the broker resolved contract matches the hint on ordinary bars;
- verify any disagreement blocks under enforcement in paper mode;
- verify no order is submitted on unknown/mismatch cases;
- record examples in journal/evidence docs.

### 7.6 Paper proof before broker-order path approval

Required before any broker-order path can be considered unblocked:

- enforcement enabled in paper/shadow only;
- observed matching hints on real alerts;
- observed clear block for at least one synthetic or replay mismatch;
- no live trading enabled;
- account pin, risk gates, session filters and journal output verified.

## 8. Implementation order for a later code lane

1. Add normalizer and tests.
2. Add optional `contract_hint` to payload model and alert fixtures.
3. Add optional `contract_hint` to `BracketOrder` and runner plumbing.
4. Add broker guard in observe mode only.
5. Add Pine `contract_hint` emission and manual proof.
6. Collect paper evidence, including roll-seam validation.
7. Only after proof, consider enabling `CONTRACT_IDENTITY_GUARD_ENFORCED=true` in paper.

Do not combine this with token rotation, Discord pacing, market-closed alerts, contract-economics hardening, requirements lock regeneration, or strategy work.

## 9. Final gate

This design does not approve implementation, deployment or broker execution. It only defines the minimum safety control needed before broker orders can move past the #960 blocker.
