# Cross-Market Paper Admission (M2K, MGC, MCL, MBT) — Preregistration (2026-09-23)

**RESEARCH / AUDIT ONLY. NO EXECUTION AUTHORITY.** Nothing in this document
enables, disables, promotes or re-parameterizes any strategy, lane, risk rule,
broker path or deployment. The live runtime stays PAPER / OBSERVE only and
`LIVE_TRADING_ENABLED=false` is not in scope. Passing this plan makes a
market/setup pair *eligible for a proposal*; it never switches anything on by
itself.

## 1. Why this exists

On 2026-09-23 the operator asked to add the four collection-only markets to
the paper bot. The standing change rule (handoff #125) needs four things for
any change to what the bot trades:

1. a pre-registered test with fixed thresholds;
2. evidence that clears it;
3. a staged rollout that can be undone;
4. explicit operator GO for that change.

This document is item 1.

**What was seen before registration.** One week of the
`cross_instrument_observation_v1` lane (epoch `62546883+2026-09-16T12:17:19Z`,
data through 2026-09-22) was seen. Results per market (1 contract, gross,
before fees):

| Market | Resolved trades | Net | PF |
|---|---|---|---|
| MBT | 364 | +$96 | 1.02 |
| M2K | 278 | −$756 | 0.76 |
| MCL | 248 | −$2,743 | 0.65 |
| MGC | 276 | −$3,653 | 0.80 |

That week motivates the plan. It is not evidence for any pair. Because it
was seen, no pair can be admitted on Stage A alone: every admission needs
the fresh-data confirmation in §5.

## 2. Population (fixed)

**Markets:** `M2K`, `MGC`, `MCL`, `MBT`. MNQ and MES are out of scope; their
paper lanes keep their own rules.

**Setups:** only observation families whose outcomes are resolved into
simulated trades (`collection_mode: structural_outcome` in
`config/cross_instrument_observation.json`):

`strat_212`, `strat_122`, `strat_22_continuation_observed`,
`strat_22_reversal_observed`, `strat_312_observed`,
`strat_322_reversal_observed`, `impulse_first_pullback_observed`,
`trend_consolidation_break_observed`, `transition_failed_breakdown_reclaim`.

That makes 9 setups × 4 markets = **36 pairs**. Each pair is judged alone.
Pairs are never pooled across markets or setups.

`signal_metrics` families (`ema_pullback_trend`, `orb_false_break_fade`,
`gap_fill`, overnight sweeps) are excluded. Their brackets are not
authoritative and are never resolved into trades.

**Source:** `logs/cross_instrument_observation_v1.jsonl` on the box, epoch
`62546883+2026-09-16T12:17:19Z`, rows with `record_type == "OUTCOME"`. If the
campaign epoch changes, a new epoch starts a new count. Rows from different
epochs are never mixed.

**A terminal trade** is an OUTCOME row with `entry_filled == true` and
`result` in `WIN`, `LOSS` or `EXPIRED` (an expired trade exits at the last
close). `NO_FILL` rows are not trades. A trade is ordered by `exit_timestamp`,
or by `resolved_at_bar_ts` when the exit timestamp is empty. It is dated by
the trade date of its `signal_timestamp`.

The observation detectors, brackets, fill model (touch fill, stop first when
both are hit in one bar) and expiry rules are used **as recorded**. They are
not re-run, retuned or corrected for this plan.

## 3. Costs (fixed; may only be raised)

Net P&L per trade = `gross_pnl_dollars_1_contract` − commission − 2 ticks of
slippage (1 tick adverse per side):

| Market | Commission + fees, round trip | Slippage (2 ticks) | Total per trade |
|---|---|---|---|
| M2K | $1.48 | 2 × $0.50 | **$2.48** |
| MGC | $2.00 | 2 × $1.00 | **$4.00** |
| MCL | $2.00 | 2 × $1.00 | **$4.00** |
| MBT | $6.00 | 2 × $0.50 | **$7.00** |

- M2K uses the repo's micro equity-index figure ($1.48, as for MNQ/MES).
- MGC, MCL and MBT are conservative assumptions, not broker statements.
- Before any activation (§7) they must be checked against a Tradovate demo
  fill or statement. A cost may be **raised** at any time; lowering one needs
  a new preregistration.

## 4. Stage A: screen (weekly)

**Checkpoints:** every Friday at 17:00 ET, starting Friday 2026-09-25. At each
checkpoint every pair is scored on all of its terminal trades so far (§2)
whose exit is at or before the checkpoint. A pair **passes Stage A** at the
first checkpoint where all of these hold:

| # | Criterion |
|---|---|
| A1 | ≥ **40** terminal trades |
| A2 | ≥ **15** distinct trade dates |
| A3 | Net after costs > $0 |
| A4 | Profit factor after costs ≥ **1.94** (frozen null p95) |
| A5 | Both chronological halves (split by trade count) net > $0 |
| A6 | The three best trade dates contribute ≤ **60%** of net |
| A7 | Max drawdown after costs ≤ **$1,750** (1 contract) |

The pass checkpoint is called **D**. Stage A is deterministic: rerunning the
evaluator on the same data gives the same D, so D never has to be stored.

## 5. Stage B: confirmation on fresh trades (once per pair)

Stage A looks weekly at 36 pairs, and the first week was seen before
registration, so a pass is expected by chance alone. Stage B controls for
that:

- The confirmation sample is the **next 40 terminal trades whose
  `signal_timestamp` is after D**. No trade from the Stage A sample is reused.
- **CONFIRMED** if, on those 40 trades after costs:
  - net > $0;
  - PF ≥ **1.94**;
  - both halves (20 / 20) net > $0.
- **REJECTED** otherwise. A rejected pair is closed for this plan. It cannot
  re-enter Stage A and cannot be re-screened with changed costs, windows or
  filters. Trying it again needs a new preregistration.
- There is one confirmation attempt per pair.

## 6. Deadline and outcomes

- **Deadline 2027-03-31.**
- A pair that has not passed Stage A by then is `NOT_ADMITTED`.
- A pair that passed Stage A but has fewer than 40 fresh trades by then is
  `INSUFFICIENT_CONFIRMATION`.
- The evaluator (`scripts/cross_market_paper_admission.py`) reports every
  pair's status. The status is one of `SCREENING`, `CONFIRMING`, `CONFIRMED`,
  `REJECTED`, `NOT_ADMITTED` or `INSUFFICIENT_CONFIRMATION`.
- The evaluator also reports the Stage A numbers, D, and the confirmation
  numbers.

## 7. Activation (only after CONFIRMED; each pair separately)

A CONFIRMED pair may be proposed for the paper bot. Each proposal is its own
change and needs **all** of the following.

**1. Wiring PR.** The setup becomes an executable paper strategy for that
market. A replay parity test must show that the executable version produces
the same signal times, directions, entry, stop and target as the observation
detector on the recorded bars.

**2. Broker readiness.**
- The market's contract-roll rule is coded and checked against Tradovate's
  contract list.
- The tick size and value come from `config/futures_contracts.py`.
- The market is added to `config/paper_market_admission.json` (empty
  today). The broker refuses orders for any market not listed there,
  besides MNQ and MES.

**3. Costs.** Verified as §3 requires.

**4. Staged rollout.**
- Size: 1 contract.
- At most one open position in that market.
- Existing account-level limits unchanged.
- The first 20 paper trades are reviewed before anything else is added.
- **Rollback:** remove the pair from `config/paper_market_admission.json` and
  restart.

**5. Explicit operator GO** for that pair.

A CONFIRMED status is not a GO. Activating several pairs at once, or pooling
pairs to reach 40 trades, is not allowed.

## 8. What is NOT allowed

- Changing a threshold, cost (except raising it), checkpoint, sample size or
  the deadline.
- Re-screening a REJECTED pair.
- Pooling trades across pairs or epochs.
- Admitting a pair on Stage A alone.
- Filling gaps in the observation data.
- Treating any status as runtime authority.

## 9. Ownership

Registered by Claude (auditor lane) on operator instruction, 2026-09-23. The
evaluator and the switched-off broker support land in the same PR as this
document. No merge, deploy, restart, paper activation or broker action is
authorized by this document.

**No proof, no run.**
