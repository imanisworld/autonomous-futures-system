# DRAFT — Prospective 1-2-2 option-exit evidence contract (ox-exit-122-v1)

**State:** SPECIFICATION / INDEPENDENT REVIEW REQUIRED / NOT IMPLEMENTED / NOT AUTHORIZED TO COLLECT OR SCORE / NO TRADE AUTHORITY  
**Prepared:** 2026-11-11 ET  
**Source baseline:** GitHub main `5381f038ec529a5cf1f26b76b7201925bc40f1e4`. This is **not** a verified VPS release pin.  
**Scope:** A prospective, observation-only *fixed-horizon option return* for the existing `OPTIONS_122_IEX_PROSPECTIVE_COLLECTOR` and `122-IEX-E1` evidence lane. No new setup, strategy tuning, broker/portfolio state, order, alert, or futures path.

## Why this is the next question

Read-only VPS evidence inspected 2026-11-11: from 2026-11-02 through 2026-11-11, the existing 1-2-2 collector recorded 83 `TRIGGERED` resolutions, 70 with `capture_gate_eligible=true`, and 37 with `option_evidence_usable=true`/`CAPTURED`. The remaining 46 were blocked (32 no liquid contract, 13 no proven pre-trigger arm, 1 stale quote timestamp). None of the 37 usable entry captures contained an exit bid or option P&L. The existing observer also recorded provider timeout errors. Counts are a diagnostic snapshot, **not** an approved trading sample or proof of profitability.

The October 11 handoff specifies `ox-exit-122-v1-2026-10-11` but expressly says it is design-only. This document refines the measurement contract for **independent review**. It does not register a new trial, approve runtime collection, or unlock the research HOLD.

## Frozen candidate identity and admission

1. Admit only **new**, post-activation entry captures from the already approved 20-symbol, 30m `122-IEX-E1` lane. Never backfill or rescore the prior 101/37/other entry captures. Separate any later evidence epoch from earlier data using an immutable explicit `measurement_epoch_id`, the approved code SHA, the epoch activation timestamp, and the first eligible `setup_id`.
2. Retain the canonical `ARMED → RESOLUTION → RECONCILIATION` chain and validated canonical identity. `RESOLUTION.observation.status=TRIGGERED`, `capture_gate_eligible=true`, `option_evidence_usable=true`, `option_evidence.status=CAPTURED`, selector replay parity, exact source/raw hash and a real `prearmed_at < trigger_crossed_at` are mandatory. A contemporaneously captured exit may be retained pending delayed SIP, but cannot count as an eligible observation until a separately verified, matching `CONFIRMED_SAME_REVERSAL` reconciliation passes. IEX-only signals, negative-latency/source conflicts, missing canonical bindings, or unmatched identity remain `DATA_BLOCKED`; do not repair by inference.
3. The entry contract is the **production-selected contract** from the immutable selector evidence, not a newly selected contract at exit. Match full option symbol, underlying ticker, expiration, strike, right (CALL/PUT), multiplier, and source-provenance fields. Check that `selected_contract` equals `production_selection.contract.symbol` and that entry `ask`, `bid`, quote timestamp and provider/source are present and internally consistent. Never substitute a different, more liquid, or cheaper contract.

## Measurement clock and quote semantics

- The exit boundary is **exactly the setup's already frozen `observation.watch_until`**. Do not derive it from an observed high/low, favorable P&L, target touch, or later reconciliation. `watch_until` is fixed before the exit is known.
- A **separate isolated, read-only quote sampler** may, if separately approved, request the selected contract's existing expiration's option-chain market-data quote near `watch_until` and match the *same exact option symbol* within the chain. Do not call the account, broker, trade, order, or execution APIs. No change to the running `options-122-prospective` collector without a separate deployment approval.
- **Proposed maximum capture window (requires independent market-data semantics review):** first valid observation at/after `watch_until`, with observed quote's **bid timestamp** and observation timestamp no later than `watch_until + 120s`; never use a quote timestamp before `watch_until`. Require an explicit timezone-aware quote/decision clock and verify both bid/ask timestamps or conservatively reject. An after-boundary bid describes an **observed exit opportunity within the specified window**, not an exact instantaneous fill at the boundary. If provider semantics do not support this window without look-ahead, do not implement.
- Store the full proof: `setup_id`, `measurement_epoch_id`, original entry-evidence hash, `watch_until`, collector version/source, option symbol/strike/right/expiry, entry ask and quote clock, exit bid **and ask**, bid/ask timestamps, observer timestamp, provider, underlying price/source/timestamp/age, delay from `watch_until`, and raw quote/market-data evidence digest. Retain `WATCH_END` or a specific `DATA_BLOCKED:<reason>` outcome; never invent quote values or replace missing values with zero.
- Reject missing, nonpositive, crossed, reversed-clock, stale, unsupported, unmatched-contract, duplicate/contradictory, unproven-epoch, invalid session, late-only, and provider-error cases **before any financial output**. Replay must be deterministic and check quote/provenance digests. Missing quote data is not a losing or zero-return trade.

## Financial interpretation

- Only for fully matched, timely, reconciled, valid observations: **one-contract hypothetical gross fixed-horizon change = `(exit_bid - entry_ask) × 100`**. Separately report `entry_spread`, spread percentage, mark quality, and fee/slippage *assumptions* (with no claim about actual brokerage costs). The result must be labeled `HYPOTHETICAL_FIXED_HORIZON_MARK`, **not realized trade P&L, a stop-respecting backtest, or validated expectancy**.
- A 25% premium stop is part of the frozen Options Paper V1 policy, but entry-and-watch-end quotes alone **cannot prove whether that stop was hit beforehand or at what fill**. No 25%-stop strategy P&L may be published without separate prospective, time-ordered intrawindow executable bid evidence and an approved stop/fill/ambiguity contract. Likewise, no target-based exit may be inferred from a structural target event.
- Entry-policy quality gates remain: DTE ≥14 (45+ preferred), spread ≤10% of mid, option volume ≥100, OI ≥500, absolute delta 0.30–0.70, pre-existing invalidation and target, planned premium risk ≤$300 per trade, aggregate cap $1,000, 25% premium stop. Recording an observation is **not** opening or reserving a position. Reject or flag missing required data; do not lower limits to increase the count.
- Report `CAPTURED`, `PENDING_SIP`, `RECONCILED_ELIGIBLE`, `DATA_BLOCKED`, `UNPRICED`, and `HYPOTHETICAL_FIXED_HORIZON_MARK` distinctly. The reconciled denominator must not silently omit invalid or unresolved records. Never combine ACTIVE scanner P&L with 1-2-2 observer hypothetical marks.

## Isolated implementation boundary, in order

**Gate A — independent review before code:** reviewer checks source-to-field mapping, provider-specific bid/ask clock semantics, immutable epoch and first-eligible boundary, watch-end sampling feasibility, lag/censor rules, reconciliation ordering, no-survivorship bias, and the explicit fixed-horizon-vs-stop distinction. Record an independent signed review on the exact spec SHA. Revise spec if necessary before any implementation.

**Gate B — code-only / synthetic fixtures:** pure validator and reducer; adversarial tests for mismatched option symbol/expiry/right/strike, reversed timestamps, quote before watch end, quote too late, stale/crossed/zero prices, missing entry ask or exit bid, invalid source/SIP/hash, entry after trigger-lag cap, duplicate outcomes, wrong epoch, gap/early close, no-stop-path proof, and profit/fee arithmetic. Require explicit no-broker/no-order/no-live dependencies. No production DB/journal modification.

**Gate C — independent code review + exact-head CI:** acceptance must prove read-only source path allowlist and exact selected-contract parity; no network call or scheduler in a pure evaluator; no silent quote fallback; immutable separate append-only sidecar and provenance; fail-closed behavior. No merge absent independent approval.

**Gate D — later, separate operator rollout GO only:** verify deployed release integrity, pinned service/config, correct market-data entitlements, read-only/account-path restrictions, separate fresh prospective epoch and quote cadence, rate limits/429 backoff, quote storage continuity and rollback. No restart/deploy/enable of a timer, new evidence Day 1, scoring, or change to live trading permissions under this PR. Existing `options-scanner`, `options-122-prospective`, futures, and risk rules remain unchanged.

**Stop condition:** Until Gates A–D pass separately, this is an **unimplemented research specification**, and any profitability or trading promotion verdict remains **WAIT**.
