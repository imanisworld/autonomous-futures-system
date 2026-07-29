# MES `strat_212` loss anatomy — PRECONDITION NOT MET (population does not reconcile)

**Status: BLOCKED before analysis. No loss anatomy is published in this PR.**

The task set an explicit gate: *"Preserve the exact existing candidate
population and prove 225/225 reconciliation before using the analysis as
evidence."* That gate **fails**, so no anatomy was built on top of it.

## Finding

| Source | MES `strat_212` resolved trades |
|---|---|
| Committed artifact `scripts/strat_212_122_canonical_evidence_raw_trades.jsonl` | **225** (167 L / 58 W, zero `unjoinable_legacy`) |
| On-disk canonical journals `logs/replay_strat212_122_canonical/MES/` | **222** |
| Reconciled | **222 / 225** |

Three trades exist in the artifact with no corresponding `TRADE` decision in
the journals: **2026-07-03, 2026-07-05, 2026-07-07** (one each).

## Root cause — not a pairing artifact, not missing data

Ruled out first:

- **Not a join defect.** The authoritative join is by `paper_order_id`
  (per `scripts/strat_212_122_canonical_evidence_report.py`, "the same
  exact-paper_order_id identity join (#327/#332), no FIFO fallback"). All 222
  journal `TRADE` rows carry a `paper_order_id`. A naive "next OUTCOME in the
  same file" pairing undercounts differently (217) because PR #339 cross-day
  carry-forward puts some outcomes in a later day's file — that is a separate,
  known effect and is **not** the cause of the 3-trade gap.
- **Not missing/truncated journals.** All three days are present and complete:
  journal line counts equal corpus bar counts exactly (68/68, 8/8, 92/92), and
  313/313 journal days exist against 313 corpus days.

Actual cause: on all three dates the journals record **`RISK_REJECTED`**, not
`TRADE`, with an identical reason:

```
Account drawdown 21.2% exceeds max 20.0% from peak $1,500.00.
```

The 20% drawdown breaker had already tripped in the journaled run by early
July 2026, so those three setups never became trades.

## Why this matters (and is not cosmetic)

`RISK_REJECTED` here is **path-dependent**: it depends on cumulative realized
P&L and peak account balance at that point in the run. The committed 225-trade
artifact was therefore produced under an account/daily-state path in which the
breaker had **not** tripped by those dates; the on-disk journals were produced
under one in which it had.

Consequence: the "frozen 225-trade canonical population" is **slightly more
permissive than a faithful causal run** — it contains 3 trades that a run
honoring the drawdown breaker blocks. Any expectancy, PF, win-rate, or
loss-anatomy figure computed over all 225 inherits that.

This is the same class of defect this project has repeatedly found and fixed
(a journaled label not corresponding to the runtime event it is assumed to
represent), so it is reported rather than smoothed over.

## What was NOT done, deliberately

- No loss anatomy over 225 trades (the gate failed).
- No anatomy over the reconciled 222 either — silently substituting a
  different population for the one specified would defeat the purpose of the
  gate.
- No variants, no filters, no parameter selection (out of scope this pass).
- No runtime, `risk_rules.yaml`, broker, config, deployment, or PR #377 changes.

## Operator decision required (one of)

1. **Re-run the canonical evidence generator fresh** so journals and artifact
   come from one identical, breaker-consistent path, then run the anatomy over
   whatever population that produces (may be 222, 225, or another number).
2. **Declare the 222 reconciled trades the analysis population**, explicitly
   accepting that 3 artifact trades are excluded because the drawdown breaker
   blocked them.
3. **Disable the drawdown breaker for the evidence run only** (research-only,
   clearly labeled) to reproduce the 225 population, accepting that the result
   describes a system without that survival floor.

Option 1 is the most faithful; option 2 is the cheapest and is defensible if
the 3 blocked trades are documented as excluded-by-breaker. Option 3 changes
what is being measured and should not be chosen silently.

## Reproduce

```bash
python3 research/strat_212_population_reconciliation.py \
  --artifact scripts/strat_212_122_canonical_evidence_raw_trades.jsonl \
  --logs-root logs/replay_strat212_122_canonical
```

Output committed as `research/strat_212_population_reconciliation.json`.
