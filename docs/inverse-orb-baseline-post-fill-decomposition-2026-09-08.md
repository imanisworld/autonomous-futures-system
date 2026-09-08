# Inverse ORB — the canonical baseline decomposed by post-fill admissibility

Status: **CLOSED — the 63-arm canonical baseline's entire profit is carried by fills the production post-fill validator rejects.** Evidence only; no strategy, risk, replay, broker, config or deployment change.

Script: `scripts/inverse_orb_baseline_post_fill_decomposition.py` → `scripts/inverse_orb_baseline_post_fill_decomposition_2026-09-08.json`. It reads the frozen proof (`inverse_orb_canonical_ioc_proof_2026-09-07.json`, SHA-256 `033e6a4d…f149ba`, population fingerprint `f32b1b1d…9e2da2`), re-derives each arm's inverse order with the production `mirror_order`, re-fills it with the real `PaperBroker` under the frozen 8-tick marketable IOC contract, and asks the production `validate_post_fill` whether that fill is admissible. **The re-derived fills reproduce the baseline's own fill/no-fill status on all 63 arms (0 disagreements), and every dollar below is the canonical JSON's own `net` field** — nothing is re-priced, re-timed or re-labelled. The only thing computed is which side of the post-fill gate each arm falls on.

This came out of the PR #500 DEMO-wiring parity proof, which found that 38 of 57 modelled fills fail the post-fill checks the DEMO route retains. That was read as "entry parity does not imply outcome parity". It is worse than that.

## The split

| Cell | n | Net | W/L by P&L | PF | Exits | Detachment at fill (median / max ticks) |
|---|---:|---:|---|---:|---|---|
| **Rejected by post-fill validation** | 38 | **+$1,138.26** | 38 / 0 | — (no losers) | STOP_HIT 37, TARGET_HIT 1 | 91.5 / 294 |
| Admitted by post-fill validation | 19 | **−$111.62** | 2 / 17 | 0.53 | STOP_HIT 17, TARGET_HIT 2 | 30 / 49 |
| All filled (canonical baseline) | 57 | +$1,026.64 | 40 / 17 | 5.28 | STOP_HIT 54, TARGET_HIT 3 | 76 / 294 |

Failure counts across the rejected set: `actual_rr_minimum` 38, `stop_direction` 37.

**The rejected set is not merely where the profit is concentrated — it is more than all of it.** Remove it and the lane is negative.

## Finding 1 — a 38-0 record whose wins are stop-outs

The rejected set books 38 positive trades and zero negative ones, and 37 of the 38 exit on `STOP_HIT`. A strategy does not win by being stopped out. The canonical rows say so themselves: **37 of the 38 are recorded as `"result": "LOSS"` while carrying a positive `net`.** Arm 1, verbatim from the frozen JSON:

```
source LONG  E=18891.75  S=18879.25  T=18919.25
inverse SHORT E=18891.75  S=18904.25  T=18864.25
fill_market  = 18946.50           (219 ticks above the nominal entry)
"result": "LOSS", "exit_reason": "STOP_HIT", "gross": 83.5, "net": 82.02
```

A short filled 54.75 points **above** its own entry level. Both bracket children now sit below the fill, with the stop nearer than the target, so the stop is reached first and books **+$82.02**. The `result` label and the P&L sign disagree because the bracket is inverted relative to the actual fill — the label follows which child was hit, the money follows where the fill was.

`validate_post_fill` catches exactly this as `stop_direction`: for a short, the stop must be above the fill.

## Finding 2 — the mechanism is unbounded favourable-side detachment

The frozen contract's "8-tick marketable IOC" tolerance bounds only the **adverse** side. A limit order fills at the best available price, so when the market has run away from a stale ORB level in the *favourable* direction, the fill is arbitrarily far from the nominal entry — while the static stop and target are still computed from that nominal entry.

Detachment at fill, in ticks from the nominal entry:

| Set | median | min | max | share beyond the 8-tick tolerance |
|---|---:|---:|---:|---:|
| Rejected | 91.5 | 5 | 294 | 97.4% |
| Admitted | 30 | 0 | 49 | 73.7% |

Detachment is pervasive in both sets — even the admitted arms sit a median 30 ticks off their entry. What separates the two sets is only whether the fill crossed its own static stop. This is the same `ENTRY_DETACHED_FROM_PRICE` pathology the 2026-09-05 stale-ORB-bracket audit root-caused (44/44 `orb_breakout` setups detached since 08-25), showing up here as profit rather than as a rejected setup.

## Finding 3 — the admitted set fails the pipeline gates on its own

Restricting to the 19 arms whose fills are admissible:

| Metric | Value |
|---|---|
| Net / PF | −$111.62 / 0.53 |
| Walk-forward halves (date median) | H1 −$119.82 / H2 **+$8.20** |
| Asian / London / New York | −$3.98 / −$105.36 / −$2.28 |
| Sample | 19 fills |

Negative overall, negative in the first half, negative in every session, and 19 fills is far below the 30-per-cell pipeline minimum. There is no cell here to promote.

For contrast, the Strategy Inventory currently records the canonical population as "+$1,026.64, PF 5.28, H1 +$546.08 / H2 +$480.56, Asian +$63.62 / London +$179.90 / New York +$783.12". **Every one of those positive cells is carried by the rejected set.**

## What this does not say

- It does not say the DEMO wiring is wrong. The wiring is what surfaced this: it retains post-fill validation, and that is correct. Relaxing those checks to reproduce the paper number would be building the artifact into the venue path.
- It does not re-price any trade or claim the replay was mis-executed. The fills are reproduced exactly; the defect is in the bracket geometry that follows a detached fill, not in the fill model.
- It does not settle whether a *non-detached* inverse ORB has an edge. It settles that the current evidence cannot answer that, because 89.5% of the baseline's filled arms are detached beyond tolerance and the admissible remainder is 19 negative trades.

## Verdict

Per the inventory taxonomy this is **BROKEN — negative evidence**, superseding PROMISING BUT UNPROVEN. The lane's headline result is an artifact of static brackets applied to detached fills, and the only admissible subset is negative on every axis the pipeline gates test.

Proposed inventory changes:
1. ORB Breakout — inverted (MNQ evidence lane): verdict → **BROKEN — negative evidence**; record the decomposition and retire the +$1,026.64 / PF 5.28 cells as artifact, keeping the JSON as provenance.
2. Pending Research: close "Inverse ORB population reproduction" — sample is no longer the binding constraint; admissibility is.
3. The forward paper lane should not accumulate against this baseline. Whatever it collects must be scored on its own, under post-fill validation.

**What would reopen it:** a population in which the entry is not detached at fill — i.e. an entry rule whose fill price is bounded on *both* sides, or a bracket recomputed from the actual fill rather than the nominal entry — showing both halves positive on ≥ 60 admissible fills. Nothing in the current corpus supplies that, and producing it is a strategy change, not a re-run.
