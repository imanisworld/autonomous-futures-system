# Contract-identity guard design amendment — `syminfo.current_contract` proof (2026-09-24)

**Type:** docs-only design amendment. No code, tests, Pine, config, runtime, alert, broker, VPS or deployment change.
**Builds on:** `docs/contract-identity-audit-2026-09-24.md` and `docs/contract-identity-guard-design-2026-09-24.md`.
**Status:** design correction only. Broker execution remains blocked until a later implementation is reviewed, tested and proven.

## 1. Reason for this amendment

After the design PR was merged, a TradingView Desktop probe found a better alert-side identity source than the earlier candidate-contract search design.

The previous design assumed Pine needed to generate candidate dated contracts and infer the active contract by comparing the continuous chart against those candidates. The probe showed Pine can read `syminfo.current_contract` on a continuous futures chart.

That finding changes the preferred Pine design, but it does **not** implement the guard and does **not** unblock broker orders.

## 2. TradingView probe result

Observed on live continuous futures charts:

| Continuous chart | `syminfo.current_contract` | Chart close | Named contract close | Result |
|---|---:|---:|---:|---|
| `CME_MINI:MNQ1!` | `MNQZ2026` | `30758` | `30758` | match |
| `CME_MINI:MES1!` | `MESZ2026` | `7767.75` | `7767.75` | match |
| `CME_MINI:M2K1!` | `M2KZ2026` | `2856.6` | `2856.6` | match |
| control: `M2K1!` vs `M2KH2027` | — | `2856.6` | `2882.4` | no match |

The observed `syminfo.current_contract` value is a plain dated ticker with four-digit year and no exchange prefix, for example `MNQZ2026`.

Tradovate commonly represents the same contract as a short form such as `MNQZ6`. The broker normalizer must reduce both sides to a common root + month code + year identity before comparing.

## 3. Important limitation

`syminfo.current_contract` is **not historical per bar**. On a daily chart, it reported the currently active contract on old bars as well.

Therefore a name-only alert field is insufficient. If TradingView's current-contract name ever lags or leads the continuous chart's actual price basis, a name-only check could falsely pass.

The alert must verify that the named contract's live-bar price matches the continuous chart's live-bar price before emitting the hint.

## 4. Amended alert-side design

The preferred first implementation should use `syminfo.current_contract` plus a live-bar price equality proof.

### 4.1 Alert-side algorithm

For supported continuous futures charts, MNQ and MES first:

1. Read `syminfo.current_contract`.
2. Reject empty, `na`, unsupported or malformed values.
3. Build/request the dated contract's own series from that value.
4. On the live alert bar, compare:
   - continuous chart close; and
   - `request.security()` close for the dated contract named by `syminfo.current_contract`.
5. Emit `contract_hint` only when the two closes match exactly or within an explicitly documented tick-size tolerance.
6. Emit `null` when the name is missing, malformed, unsupported, unavailable, stale, lagged or price-mismatched.

The initial implementation should prefer exact equality if TradingView returns raw current-contract prices for live continuous futures bars. If a tolerance is added, it must be no wider than necessary for tick-size precision and must be tested per instrument.

### 4.2 Alert value

When proven, emit the plain dated symbol from TradingView:

```json
"contract_hint": "MNQZ2026"
```

The broker normalizer may also accept full TradingView-style symbols, such as `CME_MINI:MNQZ2026`, but the observed source value is the plain dated ticker.

When not proven, emit:

```json
"contract_hint": null
```

Do not synthesize a contract hint from `_ROLL_DAYS`, root, calendar date or broker routing rules.

## 5. What this supersedes from the prior design

This amendment supersedes the candidate-contract search as the **preferred first alert-side implementation**.

The candidate-contract search remains a fallback or diagnostic method, but it should not be the first implementation while `syminfo.current_contract` plus price equality is available.

The following parts of the existing design remain unchanged:

- `contract_hint` is optional at the payload layer;
- missing/`null` hint is allowed in observe mode;
- enforcement mode blocks missing or mismatched identity;
- comparison belongs in `execute_bracket` after `_find_contract_id` and before order-body construction;
- no order request is sent to Tradovate when enforcement blocks;
- rollout starts log-only;
- broker execution remains blocked until implementation and proof are complete.

## 6. Broker-side normalization update

The normalizer must handle the newly observed source format:

| Input | Canonical comparison form |
|---|---|
| `MNQZ2026` | `MNQZ6` or equivalent structured identity |
| `CME_MINI:MNQZ2026` | `MNQZ6` or equivalent structured identity |
| `MNQZ6` | `MNQZ6` |
| `MESZ2026` | `MESZ6` |

The exact internal canonical representation can be either short string form (`MNQZ6`) or a structured tuple (`root=MNQ`, `month=Z`, `year=2026`). The representation must preserve enough information to compare TradingView and Tradovate symbols without guessing.

Unsupported roots, unsupported month codes, missing year, malformed strings and ambiguous short years must fail as unnormalizable.

## 7. Block reasons remain unchanged

The future implementation should still use the same fail-closed reasons:

| Reason | Trigger |
|---|---|
| `CONTRACT_IDENTITY_UNKNOWN` | enforcement on and `contract_hint` is absent, null, empty, unavailable or price proof failed |
| `CONTRACT_IDENTITY_UNNORMALIZABLE` | enforcement on and either hint or routed symbol cannot be normalized |
| `CONTRACT_IDENTITY_MISMATCH` | enforcement on and normalized hint differs from normalized routed symbol |

In observe mode, log the hint, routed symbol, normalized forms, price-proof verdict and final guard verdict without blocking.

## 8. Validation changes

Before enforcement can be considered, the implementation must prove:

1. `syminfo.current_contract` returns the expected live current contract on MNQ and MES continuous charts.
2. The named contract's live-bar close matches the continuous chart's live-bar close on ordinary bars.
3. A wrong candidate, such as a next contract during the current contract's active period, does not match.
4. Missing, malformed, unsupported or price-mismatched hints become `null` and then log/block according to rollout mode.
5. The guard logs enough information to detect lag or false-null rates during observe mode.
6. Enforcement is not enabled until a real roll window has been observed in paper/observe mode or equivalent roll-seam evidence is accepted.

M2K was useful as a probe/control, but this implementation remains MNQ/MES first. Do not expand the trading scope to M2K from this finding.

## 9. Remaining risks

- The extra `request.security()` call may briefly lag the continuous chart at bar close. That should produce `null` and a safe block under enforcement, not a wrong order.
- A live-bar equality check proves the current alert bar's price basis, not historical bar provenance.
- Switch-day behavior still needs observation because `syminfo.current_contract` is current-state, not historical per bar.
- Trailing-stop or later exit-order paths need the same identity guard before they are enabled for broker routing.

## 10. Implementation order after this amendment

A later code PR should be narrow and log-only first:

1. Add payload and `BracketOrder.contract_hint` plumbing.
2. Add normalizer and unit tests for TradingView and Tradovate symbol forms.
3. Add broker compare in observe mode only, after `_find_contract_id` and before order body construction.
4. Add alert-side Pine change using `syminfo.current_contract` plus live-bar price equality proof.
5. Re-create TradingView alerts only after webhook-secret handling is reviewed.
6. Collect observe-mode evidence, including a roll-window proof before enforcement.
7. Consider enforcement only in paper/shadow after proof.

Do not combine this with token rotation, Discord delivery, market-closed alert suppression, contract-economics hardening, requirements lock regeneration or strategy work.

## 11. Final gate

This amendment updates the design only. It does not approve implementation, deployment, broker execution or live trading.
