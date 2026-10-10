# 4HR 5-minute setup identity — additive v2 evidence proposal

**2026-10-10 | Draft, future-only, OFF by default. Not a change to any frozen MGC/MNQ trial or executable strategy.**

## Purpose

The canonical 4HR 5m detector contains the 4AM and retrace-setup bar stamps, but existing `candidate_key` does not persist those stamps. The natural 1m observer uses a different key: `date|direction|trigger|setup_bar_ts|four_am_bar_ts`. Comparing the two previous evidence streams by date, ticker, approximate price, or outcome would produce fabricated matches. This proposal writes *new* source identity separately so future observations can be compared without rewriting old journals.

## Proposed new opt-in

- `WIDE_STOP_4HR_JOIN_PROVENANCE_V2_ENABLED` defaults **OFF**; only `true`, `1` or `yes` enables recording.
- On the **existing canonical 5m 4HR** candidate path only, write one immutable sidecar JSON for the first candidate key to `<existing_log_dir>/wide_stop_4hr_join_v2/YYYY-MM-DD/<sha256(date|candidate_key)>.json`. This is *not* the existing `wide_stop_4k` hypothetical ledger, observation epoch, trial ledger, strategy inventory, or `tradovate_demo_evidence`.
- `schema=wide_stop_4hr_5m_join_provenance_v2`, `kind=5M_CANONICAL_CANDIDATE_IDENTITY`, `source_timeframe=5m`, `observation_only=true`, `broker_authorized=false`, `execution_reachable=false`.
- Capture original detector `state.direction`, `trigger`, `trading_date`, `setup_bar_ts`, `four_am_bar_ts`, recorded 5m open timestamp and *actual 5m close/decision time*, candidate key, submitted entry/stop/target, and normalized dated source contract (only if the alert actually asserted a parseable quarterly contract). Do not invent a contract from front-month guess or symbol root.
- Preserve the original machine timestamp strings when constructing the exact natural-1m `arm_key`. Reject/unmatch partial, non-timezone-aware, wrong-date, mistimed, wrong-side-bracket or non-triggered identities; keep UNKNOWN/UNNORMALIZABLE contract states explicit. A source with no proven dated contract has `joinability=UNMATCHABLE`, not a false `MATCH`.
- `compare_with_1m_touch` is a *pure classifier*. A positive result means `MATCHED_IDENTITY_ONLY`, **never** execution, stop, fill, reward/risk, temporal ordering, performance or broker parity.

## Noninterference and exclusions

No feature flag or config is changed on the box. Default OFF means no sidecar files. When ON later under independent approval, the implementation is wrapped in fail-soft handling; inability to write sidecar cannot affect a trade, ledger or incoming bar. Existing 5m and 1m trigger producers, paper/demo accounting, arm claims, duplicate gates, R:R limits and contract guard remain authoritative and unchanged. The sidecar has its own path, and exclusive file creation prevents duplicate records for the same full candidate key. **Do not backfill archived candidates, merge sidecar rows into historical returns, or reset the Oct 4 observer evidence epoch.**

## Checks / next gates

1. Focused `pytest -q tests/test_wide_stop_4hr_join_provenance.py`; full exact-head CI; inspect current main vs branch, ensure no orders or related runtime promotion.
2. Independent breaker: reject forged same-day/ticker-only matches, wrong setup/four-AM stamp, 1m unknown or mismatched month, a 5m input not yet completed, gap-through, duplicate candidate key and corrupt sidecar. Confirm semantics of 5m alert timestamp against actual live payload and the 5m detector; the code currently requires the candidate decision time to equal source bar **open plus five minutes**.
3. Separately authorize whether to **enable new evidence collection**. It must start prospectively with its own explicit evidence identity. No permission to modify the current frozen 1m observer, its epoch or the approved trial.
4. Once eligible natural arms and future 5m candidates can be joined, run an independently preregistered **mechanism-only** 1m-vs-5m parity comparison; keep no-fills and unmatched rows. Do not claim a strategy positive expectancy from identity matches. The existing 10 natural arms/20 trading days/2 calendar months observer minimum remains a distinct gate.

## Status

**Implementation staged in a draft PR only.** It is not an active collector, nor proof that two entry strategies are economically identical. Independent review and exact-head CI must pass before operator considers enabling the feature. No merge/deploy/trades authorized.
