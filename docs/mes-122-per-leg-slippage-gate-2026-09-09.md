# MES 1-2-2: true per-leg adverse-slippage execution-realism gate (2026-09-09)

**Verdict: EVIDENCE ONLY. Zero runtime files changed** — the only additions are a new
evidence script and its output JSON. No strategy, entry, stop, target, filter, risk,
config, or deployment change. Nothing tuned.

This is the final execution-realism gate requested in review of PR #547, before any MES
1-2-2 paper-collection decision.

## The defect being measured

The prior "1/2/3 adverse tick" runs were not per-leg slippage tests. For `strat_122`:

- **Entry leg (all 40 trades) was never slipped.** The causal resolver only returns a
  candidate once the watched bar has already shown the entry triggering, so
  `replay/replay_engine.py:711` calls `broker.restore_position(..., entry=decision.setup.entry)`
  directly instead of routing through the broker's entry-fill machinery.
  `PaperBroker.restore_position()` (`execution/paper_broker.py:643`) records that price
  verbatim and never consults `self._slippage_ticks`. The broker's real stop-entry
  primitive `_activate_pending_stop_entry` (`:401`) *does* slip the entry (`:417`, `:421-428`)
  and then re-validate the bracket against the slipped fill (`:434-437`), cancelling with
  `ENTRY_BRACKET_INVALID_AT_FILL` — but strat_122 never reaches it.
- **Same-bar exits (7 of 40 trades) were never slipped.** The `pre_resolved` branch
  (`replay_engine.py:661`) resolves via `broker.force_resolve()` at the exact structural
  price; `force_resolve` (`paper_broker.py:730`) applies no slippage either.

Ordinary stop/gap exits *were* already slipped inside `resolve_position()`. Resting-LIMIT
target exits fill clean at their price, which is correct — a limit order fills at its limit
or not at all.

## Method

`scripts/mes_122_per_leg_slippage_gate.py` monkeypatches the two slippage-free primitives —
the same technique the existing harnesses already use for `DecisionEngine` /
`advance_strat_212_122` — and **re-runs the full 313-day replay engine** at each level. It
deliberately does not post-process a fixed trade list, because a slipped entry can
invalidate a bracket, change an outcome, change exit timing, change the account balance and
therefore change which later signals the risk gates admit. Population change is measured,
not assumed.

Per level N: entry slipped N ticks adverse (once per trade, after the causal entry is
established) → bracket re-validated with the primitive's own rule → same-bar `force_resolve`
exits slipped N ticks → the broker's own `fill_slippage_ticks` also moved to N so ordinary
stop/gap exits are stressed at N rather than staying at 1. Gap-aware fills, pessimistic
same-bar handling, breakeven/runner off, fixed 1 MES, $1.48 RT commission all unchanged.

Two guardrails, both of which fired during development and caught real harness bugs:

1. **The control must reproduce PR #547 exactly** (40 trades / 11W-29L / +$90.80 / closed DD
   $219.11 / MTM DD $219.11) or the script aborts. This caught a first version whose
   baseline came out $8.75 light — it was slipping the 7 same-bar exits even at the control,
   because the added slippage and the broker's own market-exit slippage were sharing one knob.
2. **Every trade's bar-by-bar walk is cross-checked against the engine's own outcome.** This
   caught a double-slip: `replay_engine.py`'s cross-day carry-forward calls
   `restore_position()` a *second* time with the already-slipped entry, so the two
   overnight/weekend trades were being charged an extra tick each. Entries are now slipped
   exactly once per `paper_order_id`. Final run: **0 mismatches at every level.**

## Results

| level | candidates | fills | bracket-invalid | W/L | net after commission | PF | H1 | H2 | closed DD | max MTM DD |
|---|---|---|---|---|---|---|---|---|---|---|
| **merged baseline (PR #547)** | 40 | 40 | 0 | 11W/29L | **+$90.80** | 1.104 | −$80.85 | +$171.65 | $219.11 | $219.11 |
| **1 tick per leg** | 40 | 40 | **0** | 11W/29L | **+$32.05** | **1.035** | −$110.85 | +$142.90 | $229.11 | $229.11 |
| 2 ticks per leg | 40 | 40 | 0 | 11W/29L | −$57.95 | 0.941 | −$158.35 | +$100.40 | $246.61 | $246.61 |
| 3 ticks per leg | 40 | 40 | 0 | 11W/29L | −$147.95 | 0.860 | −$205.85 | +$57.90 | $267.56 | $270.06 |

Sensitivity — additionally slipping resting-LIMIT target exits (not realistic for a limit
order, but the review asked for "1 adverse tick to every exit"):

| level | net after commission | PF | max MTM DD |
|---|---|---|---|
| 1 tick per leg, strict target exits | +$22.05 | 1.024 | $229.11 |
| 2 ticks per leg, strict target exits | −$77.95 | 0.921 | $246.61 |
| 3 ticks per leg, strict target exits | −$177.95 | 0.832 | $277.56 |

## Trades changed or disappeared vs the 40-trade run

**Disappeared: 0. Appeared: 0. Bracket-invalid at fill: 0 — at every level, including 3
ticks.** All 40 trades changed, by slippage only: no result flip (11W/29L at every level),
no `exit_reason` flip, no change in exit timing. The population is completely stable, because
the 1-2-2 structural stop always sits far enough from entry that 1–3 ticks never pushes the
fill outside the bracket.

The P&L delta reconciles exactly, with no residual:

- **1 tick:** −47 ticks = −$58.75. 33 trades × 1 tick (entry only: 24 `STOP_HIT`, 8
  `TARGET_HIT`, 1 `STOP_GAP`) + 7 same-bar trades × 2 ticks (entry + exit: 4
  `OUTSIDE_AFTER_TRIGGER`, 3 `TARGET_HIT_SAME_BAR`) = 47.
- **2 ticks:** −119 ticks (80 entry + 14 same-bar exit + 25 extra market-exit ticks).
- **3 ticks:** −191 ticks (120 + 21 + 50).

This is exactly the 47-tick / $58.75 figure the review predicted by hand, and the predicted
+$32.05 — now confirmed by a full engine re-run rather than assumed, with the population
proven stable rather than hoped stable.

At 3 ticks the max MTM drawdown ($270.06) exceeds the closed-trade drawdown ($267.56) for the
first time: an open position's unrealized dip, not a trade close, sets the record.

## Replay vs forward-paper slippage parity

**Identical — both bypass entry slippage in the same way.** Verified by direct trace:

- Forward-paper opens strat_212/122 with `broker.restore_position(...)` at
  `webhook/runner.py:2455` and returns before ever reaching `execute_bracket` (`:2725`),
  mirroring `replay_engine.py:711`.
- Forward-paper's same-bar branch is `restore_position` (`:2417`) → `force_resolve` (`:2425`),
  mirroring `replay_engine.py:652`/`:661`. Neither slips.
- Carried-position exits use the same `resolve_position(NextBarOHLC(open, high, low))` call
  in both paths (`runner.py:1268`, `replay_engine.py:806`).
- Both read the identical config fields into `PaperBroker` (`runner.py:312-326`,
  `replay_engine.py:226-240`); deployed `fill_slippage_ticks` is 1.0 and
  `entry_fill_model` is `market`.

So the optimism is symmetric: forward-paper collection would carry exactly the same
unslipped-entry assumption as the replay evidence. It is not a replay-only artifact.

## What this does and does not settle

Settled: the corrected population is **stable** under realistic per-leg execution cost — no
trade disappears, no bracket goes invalid, no outcome flips — and at a genuine 1 adverse
tick per leg the edge is **+$32.05 / PF 1.035** over 40 trades, not +$90.80 / PF 1.104.

Not settled, and not addressed here: whether a PF of 1.035 over n=40 is an edge at all. It
is far inside the null band (`project_null_baseline_2026_07_29`: p95 PF 1.94). Two ticks per
leg is already negative. This gate was about fill realism, not about whether the edge exists.

## Files

- `scripts/mes_122_per_leg_slippage_gate.py` — new evidence harness
- `scripts/mes_122_per_leg_slippage_gate_2026-09-09.json` — full per-level output, per-trade
  rows, and per-level diffs vs the merged baseline
- this doc

No runtime, config, risk, strategy, deployment, or paper-enablement change.
