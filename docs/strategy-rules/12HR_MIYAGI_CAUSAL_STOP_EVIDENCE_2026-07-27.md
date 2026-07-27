# 12HR Miyagi — causal-stop evidence closure (PR #362 gate)

## Verdict: BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS

**MNQ: 0/8 (0%)** real historical causal-stop candidates fit inside the account's existing `max_stop_ticks` cap.
**MES: 2/10 (20%)** fit.

This is not a WAIT (insufficient sample) finding — it is a structural incompatibility between this strategy's causal stop-reference formula and the account's own, independently-validated risk limit. **PR #343's old evidence (MNQ PF 2.81 n=8 / MES PF 1.98 n=10) is not valid evidence for the deployable strategy** — see "Why the old numbers don't count" below. `max_stop_ticks` itself is confirmed a legitimate account-protection control (not a parity defect) and is **preserved unchanged**; this report does not propose widening it.

This closes the evidence effort tracked under PR #362 (`claude/miyagi-12hr-demo-readiness`). PR #362 is held / not merged; see "Disposition" below.

## Why this run exists

PR #343's canonical evidence used a stop-reference formula (`_completed_one_hour_stop`, reused verbatim from `strategy/four_hr_retrigger.py`) with a confirmed lookahead defect: 4/8 of the MNQ triggered signals and 7/10 of the MES triggered signals referenced a stop-reference bar that had not yet closed at the strategy's own decision point. PR #362 fixes the stop causally (uses only bars closed strictly before the decision timestamp). This effort reruns the evidence using the causally-corrected stop, through the strategy's real runtime code, to determine whether the corrected numbers still support deployment.

## Method — two complementary tools, one decisive result

Two separate scripts were built for this effort; they answer different questions and are both included for reproducibility.

### 1. `scripts/miyagi_12hr_causal_stop_evidence.py` — full-engine parity harness

Drives `strategy/strat_12hr_miyagi.py` through the actual `replay/replay_engine.py -> strategy/signal_engine.py::DecisionEngine` path — the same runtime path #362 wires into the live/paper webhook, not a bespoke research harness. This is the strongest possible parity proof *when it can see candidates at all*.

**On `main` as of this branch, this script currently reports n=0 for both instruments.** That is expected and separately explained, not a defect in this tool: `main` (pre-[PR #365](https://github.com/imanisworld/autonomous-futures-system/pull/365)) still has the four global signal/risk-layer gates (`MARKET_CONDITION_NOT_TRENDING`, `TREND_STRENGTH_BELOW_REQUIRED`, `EMA_STACK_NOT_ALIGNED`, `RR_BELOW_MINIMUM`) blocking every 5-minute-native candidate with zero basis in Miyagi's own rules/detector/runtime — the same four parity defects proven and fixed in #365 for `strat_322_first_live` (and, pre-scoped/inert since Miyagi has no runtime wiring on `main`, for `strat_12hr_miyagi`). Results/tooling are included here as provenance and as a ready-made full-engine regression check for if/when Miyagi is ever wired to `main`; they are **not** the basis for this report's verdict.

### 2. `scripts/miyagi_causal_stop_distribution.py` — decisive, fix-independent study

Drives `advance_strat_12hr_miyagi` (the pure state machine) directly, bar-by-bar, across all 34 known historical candidate dates from the 5-minute-native corpus (`data/replay_corpus_v1_5m/{MNQ,MES}`, built this session via `scripts/polygon_to_replay.py --timeframe 5` — confirmed to natively write #338-canonical `market_condition`/`reconstructed_market_condition` fields, no new corpus tooling needed). This bypasses the four unrelated signal-layer gates entirely — it answers one question only: **for every real historical trigger, what does the causally-corrected stop distance look like against the account's existing `max_stop_ticks` cap?** This is independent of #365 and is the basis for this report's verdict.

Both scripts pin canonical entry-tolerance ticks explicitly (MNQ=32, MES=16) rather than trusting the ambient `.env`, which has drifted to MNQ=16/MES=8 — see the parked, separately-tracked `.env` drift investigation. This drift is orthogonal to the stop-cap finding below (the stop distance is computed from the 8AM-derived bracket, not from entry tolerance).

## Causal-stop vs. `max_stop_ticks` — full distribution

Cap: MNQ 120 ticks, MES 60 ticks (`risk_rules.yaml`, unchanged, preserved).

| | Triggered | Pass cap | Fail cap | % rejected | Median ticks | p75 | p90 | Max observed | Stop changed vs. old (lookahead) formula |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **MNQ** | 8 | **0** | 8 | 100.0% | 564.0 | 643.5 | 754.5 | 805.0 | 4/8 |
| **MES** | 10 | **2** | 8 | 80.0% | 122.0 | 210.5 | 230.0 | 258.0 | 7/10 |

### MNQ — per trade

| Date | Dir | Entry | Causal stop | Ticks (cap=120) | Cap | Old result | Stop changed |
|---|---|---:|---:|---:|:---:|---|:---:|
| 2024-08-22 | SHORT | 19905.875 | 20025.750 | 479.5 | FAIL | WIN | No |
| 2024-08-23 | SHORT | 19637.500 | 19778.500 | 564.0 | FAIL | LOSS | No |
| 2024-09-18 | SHORT | 19698.125 | 19733.000 | 139.5 | FAIL | WIN | **Yes** |
| 2024-10-11 | LONG | 20420.500 | 20313.250 | 429.0 | FAIL | WIN | No |
| 2024-10-23 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2024-12-11 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2025-02-27 | SHORT | 21212.125 | 21373.000 | 643.5 | FAIL | WIN | **Yes** |
| 2025-03-06 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2025-03-21 | LONG | 19876.250 | 19675.000 | 805.0 | FAIL | WIN | **Yes** |
| 2025-05-16 | SHORT | 21402.875 | 21501.250 | 393.5 | FAIL | WIN | **Yes** |
| 2025-05-23 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2025-09-25 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2025-12-18 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2026-01-14 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2026-02-11 | SHORT | 25276.875 | 25465.500 | 754.5 | FAIL | WIN | No |

### MES — per trade

| Date | Dir | Entry | Causal stop | Ticks (cap=60) | Cap | Old result | Stop changed |
|---|---|---:|---:|---:|:---:|---|:---:|
| 2024-07-12 | — | — | — | — | CORPUS_DATE_MISSING | — | — |
| 2024-07-17 | — | — | — | — | CORPUS_DATE_MISSING | — | — |
| 2024-08-22 | SHORT | 5639.375 | 5669.000 | 118.5 | FAIL | WIN | No |
| 2024-08-23 | SHORT | 5603.375 | 5656.000 | 210.5 | FAIL | LOSS | **Yes** |
| 2024-09-19 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2024-10-23 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2024-10-25 | SHORT | 5852.500 | 5883.000 | 122.0 | FAIL | WIN | **Yes** |
| 2025-03-06 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2025-03-21 | LONG | 5710.625 | 5658.750 | 207.5 | FAIL | WIN | **Yes** |
| 2025-04-02 | LONG | 5675.250 | 5610.750 | 258.0 | FAIL | WIN | No |
| 2025-04-30 | LONG | 5569.500 | 5512.000 | 230.0 | FAIL | WIN | **Yes** |
| 2025-05-23 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2025-09-25 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2025-10-08 | SHORT | 6765.375 | 6774.000 | 34.5 | **PASS** | LOSS | **Yes** |
| 2025-12-04 | SHORT | 6861.375 | 6870.500 | 36.5 | **PASS** | WIN | **Yes** |
| 2026-01-14 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2026-02-05 | — | — | — | — | EXPIRED | NO_FILL | — |
| 2026-02-11 | SHORT | 6972.625 | 7011.500 | 155.5 | FAIL | WIN | No |
| 2026-04-10 | SHORT | 6860.000 | 6882.000 | 88.0 | FAIL | WIN | **Yes** |

**Checkpoint reference trade**: MNQ 2026-02-11 — the causally-corrected state machine reproduces PR #343's original trigger/entry/target exactly (25276.875 SHORT), confirming this study's causal-stop mechanics match #362's intent; only the stop distance (754.5 ticks, correctly wider than the old lookahead-affected value) differs, and that wider distance is precisely why it fails the cap.

## Why the old numbers don't count

PR #343's reported MNQ PF 2.81 (n=8) and MES PF 1.98 (n=10) were computed using the lookahead-affected stop formula on unfiltered trigger counts — they do not represent trades that could ever actually be taken under the account's existing stop-size limit. Under the causally-corrected stop (the only stop formula #362 could legally ship), 8/8 MNQ and 8/10 MES of those same trigger events would never reach a fill in the first place — `RiskEngine` would reject the bracket for exceeding `max_stop_ticks` before entry. The old P&L, win rate, and profit factor describe a strategy variant that cannot exist under current risk architecture. There is no valid resize/rescale of the old numbers that produces a usable estimate for the executable strategy — the surviving sample (MNQ n=0, MES n=2) is too small to compute a profit factor at all.

## `max_stop_ticks` — audited, not a parity defect, unchanged

Unlike the four gates fixed in #365, `max_stop_ticks` was directly investigated against Miyagi's own rules doc, detector, and runtime and found to be a deliberate, pre-existing, account-wide risk control — not an accidental parity gap. It is preserved exactly as-is. This report does not request, and this branch does not contain, any change to `risk_rules.yaml`'s stop-size limits.

## Disposition

- **PR #362** (`claude/miyagi-12hr-demo-readiness`): HOLD — closed without merging, per operator direction. Branch preserved (not deleted).
- **This evidence** is the closure record for that effort.
- **PR #365** (`claude/paper-execution-parity-fixes`): a separate, independently-scoped correction for the four *unrelated* global gates that were also found blocking both Miyagi and the already-deployed `strat_322_first_live`. #365 does **not** rescue Miyagi — the stop-cap incompatibility documented here is structural and orthogonal to those four gates (the decisive study above never touches them).
- A future Miyagi variant with a bounded-stop hypothesis (e.g., a tighter/structural stop that fits inside the existing cap) would be a **new strategy variant** requiring its own research/evidence pass from scratch — out of scope here, not proposed by this report.

## Reproduction

```bash
# Decisive study (fix-independent, drives the pure state machine directly)
python3 scripts/miyagi_causal_stop_distribution.py

# Full-engine parity harness (requires #365's exemption fixes to see
# candidates at all on main; included for provenance/future regression use)
python3 scripts/miyagi_12hr_causal_stop_evidence.py \
  --logs logs/replay_miyagi_12hr_causal_stop \
  --out scripts/miyagi_12hr_causal_stop_evidence_results.json \
  --report /dev/null
```

Corpus: `data/replay_corpus_v1_5m/{MNQ,MES}` (5-minute-native, #338-corrected fields, 2024-07-02 → 2026-07-23, gitignored — regenerate via `scripts/polygon_to_replay.py --timeframe 5`, requires `POLYGON_API_KEY`).
