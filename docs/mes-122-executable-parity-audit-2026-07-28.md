# MES 1-2-2 (strat_122) executable-parity audit — 2026-07-28

**Branch**: `claude/mes-122-executable-parity-audit` (isolated worktree, separate from `claude/system-status-snapshot`).
**Script**: `scripts/strat_122_executable_parity_audit.py`. **Raw results**: `scripts/mes_122_executable_parity_audit_results.json`.
**No runtime, strategy-rule, risk-limit, deployment, or broker-routing changes were made.** This is a pure evidence run.

## Scope and method

Unlike the 4HR/3-2-2/Miyagi executable-parity audits, MES `strat_122`'s canonical
evidence (#337, `scripts/strat_212_122_canonical_evidence_run.py`) already ran
through the real `ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker`
path, calling `strategy.strat_212_122.advance_strat_212_122` — the exact
function live/replay both share (PR #319). Verified directly by reading that
script (not assumed): real engine, no bespoke research-only fill/detection
logic, no `_GATE_EXEMPT` bypass (checked `strategy/signal_engine.py` and
`risk/risk_engine.py` — `strat_122`/`strat_212` are on neither set). So there
is no "research code vs runtime code" split to audit here, unlike 4HR's
standalone-research-function situation.

The real open parity question, independently confirmed by reading
`risk_rules.yaml` directly: #337's canonical run used
`enabled_concepts=["strat_212","strat_122"]` ONLY and cleared
`disabled_concepts_per_instrument` entirely (isolated, 2-strategy config).
Current production `risk_rules.yaml` has `strat_122` genuinely enabled for MES
(not in `disabled_concepts_per_instrument.MES`) alongside `orb_reclaim` and
shadow-only `vwap_hold` — a materially smaller set than the full 11-strategy
list, but still a DIFFERENT config than #337's isolation. Two passes:

1. **Reproduction** — rerun #337's exact isolated config against the same
   corpus (`data/replay_corpus_v1_market_condition_fixed`, 313 MES days) on
   today's code. Confirms no drift from the committed 33-candidate MES
   `strat_122` population before trusting it as ground truth.
2. **Production-config** — rerun the identical corpus under the ACTUAL current
   production config (`load_config()` unmodified), then look up what happened
   at each of pass 1's 33 known trigger bars.

## Results

**Reproduction: clean.** `reproduction_mismatches=0`, `extra_candidates_in_isolated_rerun_not_in_337=0`
— #337's 33-candidate MES `strat_122` population reproduces exactly on today's code.

**Production-config: only 16 of 33 (48.5%) survive as executable.**

| Bucket | n | W | L | WR | Net P&L | Meaning |
|---|---|---|---|---|---|---|
| #337 isolated evidence (as reported) | 33 | 11 | 22 | 33.3% | +$428.75 | The original "PROMISING BUT UNPROVEN" evidence base |
| **Executable in production** (verified identical direction/entry/stop/target/rr_ratio) | **16** | **5** | **11** | **31.2%** | **+$120.00** | What would actually fire live |
| Preempted (a different strategy's setup won that bar's selection) | 7 | 5 | 2 | 71.4% | +$432.50 | strat_122 never gets a chance — a different, already-selected candidate wins the bar |
| Blocked (no engine decision at that bar at all) | 10 | 1 | 9 | 10.0% | -$123.75 | An open position (from `orb_reclaim` or cross-day carry-forward) blocks ALL decision evaluation through the bar |

Every one of the 16 executable survivors was verified to match the isolated
run's direction/entry/stop/target/rr_ratio exactly (0 field mismatches) —
this is genuine formula/timing parity for the surviving population, not
merely "some trade happened."

### Root causes of the two non-executable buckets

**Preemption (7/33, 21%)**: `vwap_hold` (6 of 7) and `orb_reclaim` (1 of 7)
win the same-bar candidate-selection ranking under the real production
concept list. `vwap_hold` is globally SHADOW_ONLY (`strategy_permission_gate`)
— it wins the setup slot and then itself resolves to `NO_TRADE`, so the bar
produces **no trade for anyone**: a pure opportunity loss, not a substitution.
These 7 preempted candidates were the STRONGEST performers in #337's isolated
population (71.4% WR, +$432.50) — production silently loses the best trades.

**Blocked (10/33, 30%)**: reconstructed by direct replay of the actual
sequential 313-day production run (not inferred) — `orb_reclaim` opened a LONG
position on 2025-07-24 that was still open at day-close and carried forward
(PR #339 cross-day carry-forward) into 2025-07-25, where it kept resolving
against the new day's bars until ~19:00 UTC. `replay/replay_engine.py`'s
carry-forward pre-scan (`run()`, lines ~201-324) walks every bar of the new
day against the inherited position before the main per-bar decision loop
starts, and bars it scans through (`idx < skip_to`) never reach
`DecisionEngine` at all — no journal entry of any kind is written for them.
strat_122's 03:00 candidate on 2025-07-25 fell inside this window and was
never evaluated. This is legitimate, correct, and intentional engine
behavior (mirrors live's `BLOCKED_OPEN_POSITION` gate) — not a bug — but
#337's isolated run, where `strat_212`/`strat_122` were the ONLY active
concepts, essentially never had a competing open position to collide with,
so it never surfaced this loss channel. All 10 blocked candidates were
`LONG` — consistent with `orb_reclaim` and `strat_122` both tending to fire
in the same trending-up regime.

## Walk-forward (executable-only population, n=16)

| Half | n | W | Net P&L |
|---|---|---|---|
| H1 (first 8 by date) | 8 | 2 | +$11.25 |
| H2 (last 8 by date) | 8 | 3 | +$108.75 |

Both halves net positive, but n=8 per half is too thin for this to mean
anything (a single flipped trade changes the sign of H1). Avg win $81.00 /
avg loss -$25.91 (≈3.1:1) is a coherent low-win-rate/high-R:R profile
consistent with the strategy's design (2-6R reversal targets) — 31.2% WR is
comfortably above the ~24% breakeven WR that R:R implies, so this is not an
internally incoherent result. The problem is scale, not shape: 16 trades
across 313 trading days (~14 months) is roughly one trade every 3-4 weeks,
and +$120 total net P&L over that period is economically indistinguishable
from noise.

## Verdict: WAIT (downgrade from PROMISING BUT UNPROVEN)

Not VALIDATED — nowhere near sufficient n or margin. Not BROKEN — the
executable population isn't net-negative and its risk/reward shape is
internally coherent. Not classic OVERFIT — this isn't an in-sample/
out-of-sample walk-forward failure. Not UNSAFE — no execution-safety defect
was found. The specific failure mode here is **evidence-population
mismatch**: #337's isolated 2-strategy replay validated a candidate
population that the real, currently-deployed production concept list cannot
actually produce — over half (51.5%) of the "evidence" trades either get
preempted by a strategy that then itself produces no trade, or never reach
evaluation at all because another strategy's position is still open. The
genuinely executable subset (n=16, +$120.00 net) is too thin and too
economically marginal to sustain a "promising" label on its own.

**Recommendation for the operator**: reclassify MES `strat_122` from
PROMISING BUT UNPROVEN to WAIT pending a larger executable-only sample (the
current 16-trade population needs meaningfully more data collected under the
REAL production concept list, not the isolated 2-strategy config, before any
further promotion decision). No runtime/config change is needed to produce
this — `strat_122` is already correctly wired and already running under the
real production config in current forward-demo observation; this audit only
corrects how its PAST evidence should be read.

## What this audit did NOT change

- No strategy rules, risk limits, deployment, or broker routing were
  modified.
- No regression tests were added — this is a pure research/evidence finding
  in `replay/replay_engine.py`'s carry-forward/preemption behavior, not a
  code defect. The carry-forward and single-setup-per-bar selection logic
  are both working exactly as designed; the finding is about which
  historical trades that design would and wouldn't have allowed to execute.
- `risk_rules.yaml` was read but never written (script verifies this is
  never touched, matching #337's own convention).
