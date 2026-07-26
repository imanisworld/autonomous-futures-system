# 4HR Re-Trigger — Batch 1 Evidence Report (2026-07-26)

**Authorized scope:** `docs/strategy-rules/4HR_AUDIT_HANDOFF.md` Section 5 ("Batch 1"),
narrowed by operator authorization to the fixed-at-entry completed-1H-candle stop plus
walk-forward and slippage-sensitivity evidence only. Options/QQQ P&L stayed out of scope
(separately blocked on missing historical options-chain data). No strategy, detector,
stop, replay, runtime, risk, broker, or deployment code was changed. Isolated branch
`claude/4hr-batch1-evidence`, off `origin/main`. Not merged, not deployed.

## What was reproduced and why

`strategy/four_hr_retrigger.py::advance_4hr_retrigger` (PR #317, `main@945fbdf`) computes
a stop **once, at entry** — the most recently completed 1H candle's low (LONG) / high
(SHORT). Once a trade is `TRIGGERED` the state machine returns its own persisted state
unchanged for the rest of the day (`strategy/four_hr_retrigger.py:169-170`) — it never
advances or ratchets that stop again. There is no ratchet implementation anywhere in this
codebase (`grep -rn "ratchet" --include="*.py" .` matches only unrelated
`stocks_advisory/` files).

`4HR_ReTrigger_Rules.md`'s documented stop **is** the ratcheting 1H flip
(`4HR_AUDIT_HANDOFF.md` Section 3), and that document names the ratcheting variant as the
strategy's own PASS/FAIL gate. That variant does not exist in code to test, and building
it would itself be a stop-logic change — out of this study's authorized scope. This study
therefore evidences what the canonical strategy **actually executes today**: the
fixed-at-entry stop. A ratcheting stop can only ever be as-or-more favorable than a fixed
one (it can only move in the trade's favor), so this result is not evidence *against* the
documented rule — it is silent on it.

## Dataset

- Source: `data/replay_polygon_5m/{MNQ,MES}/` (real Polygon 5-minute bars, already on
  disk — no new data pull)
- MNQ: 621 daily files, 140,115 bars, 2024-07-02 → 2026-06-26
- MES: 621 daily files, 140,111 bars, 2024-07-02 → 2026-06-26

## Exact command

```
python3 scripts/four_hr_retrigger_stop_study.py \
  --candles data/replay_polygon_5m --instrument MNQ --instrument MES \
  --out scripts/four_hr_retrigger_stop_study_results.json
```

Full machine-readable output: `scripts/four_hr_retrigger_stop_study_results.json`.

## Assumptions (explicit, matching established system conventions — not new one-offs)

| Assumption | Value | Source |
|---|---|---|
| Commission (round-trip) | $1.48 | matches `execution/mnq_strat_evidence.py::MNQ_COMMISSION_ROUND_TRIP` and `execution/mes_trend_consolidation_break_evidence.py::MES_COMMISSION_ROUND_TRIP` |
| Baseline slippage | 1 tick, adverse, market-order legs only | `config/settings.py::fill_slippage_ticks` production default |
| Sensitivity slippage | 1 / 2 / 3 ticks | per Batch-1 spec |
| Same-bar stop+target | stop-first (`pessimistic_both_hit=True`) | `config/settings.py` production default |
| Entry fill model | `market` (fills at the trigger level, adverse-slipped) | `config/settings.py` production default |
| Resolution | 5m bars, strictly-prior closed bars only, no lookahead | matches `execution/paper_broker.py` |
| Day-only exit | `execution.day_only_exit` (PR #318), exact 15:55-16:00 ET bar, stop/target take precedence on the same bar, missing EOD bar fails closed (excluded, never substituted) | matches `replay/replay_engine.py:536-571` exactly |

## Setups and fills

| | Candidates detected | Resolved | Excluded (fail-closed) |
|---|---|---|---|
| MNQ | 81 | 80 | 1 (`2026-06-19`, `EOD_BAR_MISSING_FAIL_CLOSED`) |
| MES | 76 | 75 | 1 (`2025-05-26`, `EOD_BAR_MISSING_FAIL_CLOSED`) |

Both exclusions are the same honest fail-closed behavior already established across this
codebase's other evidence scripts (corpus_v1, mnq_strat_evidence): a signal fired but the
data needed to resolve it (the exact 15:55 ET bar) wasn't present in that day's file, so
the trade is excluded, never guessed at or substituted with a later bar.

## Results — fixed-at-entry completed-1H-candle stop (baseline 1-tick slippage)

| | Resolved | Wins | Losses | Win rate | Net P&L | Expectancy/trade | Profit factor | Max DD |
|---|---|---|---|---|---|---|---|---|
| **MNQ** | 80 | 49 | 31 | 61.2% | **$3,069.60** | $38.37 | 1.774 | $908.30 |
| **MES** | 75 | 39 | 36 | 52.0% | **$166.50** | $2.22 | 1.072 | $663.05 |

## Walk-forward (chronological halves, baseline slippage)

| | H1 net P&L | H1 PF | H2 net P&L | H2 PF |
|---|---|---|---|---|
| MNQ | +$1,794.80 | 2.21 | +$1,274.80 | 1.513 |
| MES | **+$801.49** | 2.14 | **-$634.99** | 0.607 |

MNQ holds up in both halves. MES does not — it earns essentially its entire edge in H1
and gives more than all of it back in H2.

## Slippage sensitivity (net P&L)

| | 1 tick | 2 tick | 3 tick |
|---|---|---|---|
| MNQ | $3,069.60 | $3,014.10 | $2,958.60 |
| MES | $166.50 | $31.50 | **-$103.50** |

MNQ is stable — a $110 total swing across the full 1-3 tick range on a >$3,000 result.
MES flips sign by 3 ticks of slippage — its entire baseline edge is thinner than a single
extra tick of realistic cost.

## Long vs short (baseline slippage)

| | Long net P&L | Long PF | Short net P&L | Short PF |
|---|---|---|---|---|
| MNQ | $575.16 | 1.321 | $2,494.44 | 2.148 |
| MES | **-$433.05** | 0.684 | $599.55 | 1.632 |

For both instruments the edge is concentrated on the short side. MES's long side is
already net-negative on its own at baseline slippage, before any sensitivity stress.

## Classification

**MNQ: PROMISING BUT UNPROVEN.** Positive net P&L in both chronological halves and at
every slippage sensitivity point (1/2/3 ticks). This is a single in-sample offline study,
not forward/live evidence — it cannot be called VALIDATED from this alone, but it clears
every bar this study set out to test.

**MES: BROKEN** (under the fixed-at-entry stop, as currently executable). Fails
walk-forward (H2 net P&L is negative, erasing H1's edge) and fails slippage sensitivity
(flips to a losing profit factor by 3 ticks). Its long side alone is already net-negative
at baseline. This does not mean the *documented* (ratcheting) 4HR strategy is broken for
MES — that variant was never built or tested here — but the specific stop the canonical
code executes today does not hold up for MES under conditions it must survive.

**Do not report a single blended number for both instruments.** Combined net P&L
($3,236.10 combined) is driven almost entirely by MNQ and would misrepresent MES's result
if quoted alone — the same "combined aggregate can hide a half or instrument that doesn't
hold up" failure mode already flagged for Corpus v1.

**Options/QQQ P&L: out of scope, unchanged.** Still blocked on missing historical
options-chain data; not attempted here.

## What this does and does not settle

- Settles: the *executable* 4HR entry logic (PR #317) plus its fixed-at-entry stop
  produces a real, positive, walk-forward-stable, slippage-stable edge for **MNQ** in this
  2-year offline sample, and does **not** for **MES** under the same conditions.
- Does not settle: whether the *documented* ratcheting stop would change either result
  (untested, unbuilt, out of scope here); whether this holds forward/live (in-sample
  only); options/QQQ P&L (separately blocked).

## Artifacts

- `scripts/four_hr_retrigger_stop_study.py` — the study driver (imports the canonical
  `advance_4hr_retrigger` state machine and the canonical `execution.day_only_exit` /
  `execution.paper_broker` modules; no reimplementation of detector or stop logic)
- `scripts/four_hr_retrigger_stop_study_results.json` — full machine-readable results
  (per-instrument, per-slippage-variant, per-half, per-direction, plus every individual
  resolved/excluded trade)
- `tests/test_four_hr_retrigger_stop_study.py` — 15 regression tests covering this
  script's own resolution loop (day-only-exit precedence, fail-closed exclusion,
  same-bar pessimistic stop-first), summary math, chronological split, and the
  per-instrument (never-blended) classification logic
