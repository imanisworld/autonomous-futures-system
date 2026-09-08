# PaperBroker's `ioc_limit` path has no bracket-validity guard — which lanes inherit it

Status: **OPEN — a production defect is identified and quantified; no code change is made here.** Evidence only; no strategy, risk, replay, broker, config or deployment change. The fix is a runtime behaviour change under the 2026-09-30 evidence freeze and needs an operator ruling.

Script: `scripts/ioc_limit_bracket_guard_readacross.py` → `scripts/ioc_limit_bracket_guard_readacross_2026-09-08.json`. Both inputs are committed artifacts (`scripts/edge_decomposition_audit_results_candidates.jsonl.gz`, `scripts/inverse_orb_canonical_ioc_proof_2026-09-07.json`), so it runs from a fresh clone and does not read the gitignored `data/replay_polygon_5m/`.

Follow-on from `docs/inverse-orb-baseline-post-fill-decomposition-2026-09-08.md`, which established that the inverse ORB baseline's entire profit came from fills whose static stop sat on the wrong side of the fill. This asks the next question: **is that lane-specific, or does it belong to the fill model?**

It belongs to the fill model.

## The defect

`PaperBroker` already knows this rule. `execution/paper_broker.py:395-398` rejects an entry that fills beyond its own stop or target:

```python
if order.direction == "LONG" and not (order.stop < fill_entry < order.target):
    return self._cancel_pending_entry("ENTRY_BRACKET_INVALID_AT_FILL")
if order.direction == "SHORT" and not (order.target < fill_entry < order.stop):
    return self._cancel_pending_entry("ENTRY_BRACKET_INVALID_AT_FILL")
```

That check lives **only on the `stop_market` (resting-entry) path**. The `ioc_limit` path at `execution/paper_broker.py:221-238` computes

```python
fill_entry = max(limit_px, market - slip)   # SHORT
```

and returns a filled position with no validity check at all.

The marketable tolerance bounds only the **adverse** side. On the favourable side the fill is the market, however far the market has run from the plan level. When that distance exceeds the stop distance, the static stop — still anchored to the nominal entry — ends up between the fill and the target. The position is then structurally guaranteed to "stop out" **in profit**, and the P&L sign and the exit label disagree.

**The IOC tolerance is not the lever.** The edge-decomposition audit ran its IOC stage at 32 ticks and rejected the same five 4HR candidates that a 8-tick run rejects (`lanes[0].E_ioc_costs.{1,2,3}tick.no_fill_reasons.ENTRY_BRACKET_INVALID_AT_FILL = 5`). Tolerance changes which candidates fill on the adverse side; it cannot bound the favourable side.

## Read-across, at the 8-tick contract

IOC at the decision-bar close, 1 adverse tick, $1.48 round turn. "Phantom P&L" is what each invalid position books if it resolves on its own stop.

| Lane | Candidates | IOC fills | Invalid at fill | Median / max detachment (ticks) | Phantom P&L |
|---|---:|---:|---:|---|---:|
| 4HR Re-Trigger MNQ | 81 | 43 | **5 (11.6%)** | 65 / 358 | **+$175.60** |
| 60M 3-2-2 MNQ | 34 | 17 | 0 | 39 / 211 | $0 |
| 12HR Miyagi MNQ | 8 | 5 | 0 | 27.5 / 175 | $0 |
| ORB Breakout MNQ, inverted | 63 | 57 | **37 (64.9%)** | 75 / 293 | **+$1,106.24** |

The inverse lane's +$1,106.24 phantom estimate against its +$1,138.26 actually realised is a useful cross-check: essentially all of that lane's profit is this mechanism resolving as predicted. (The decomposition doc counts 38 rejections rather than 37 because it applies the full `validate_post_fill`; the extra arm fails only `actual_rr_minimum`, not bracket direction.)

## Finding — severity scales with detachment ÷ stop distance, not with the strategy

The wide-stop family is *structurally protected by the thing that makes it hard to size*. Its stops are 254 / 472 / 522 ticks at the median, so a 65-tick median detachment rarely crosses one. The inverted ORB mirrors a ~50-tick ORB stop, which a 75-tick median detachment crosses routinely.

That is the general rule: **a lane is exposed in proportion to how often its detachment exceeds its stop distance.** Any future lane on `ioc_limit` with tight stops relative to how stale its entry level can go will show the same artifact, whatever its signal is.

3-2-2 and Miyagi are clean at 8 ticks. At the audit's 32 ticks, 3-2-2 picks up one invalid fill — a candidate that does not fill at all at 8 — so its zero is a property of the tighter tolerance admitting fewer arms, not of immunity.

## Consequence for the hypothetical-ledger lane (spec D4)

Two different things, and only one of them is contaminated:

- **The spec's evidence is clean.** The wide-stop cells come from `scripts/edge_decomposition_audit.py`, which applies its own `_bracket_valid_at_fill` (`:580`, rejecting at `:615`) precisely because `PaperBroker` does not. The +$3,111 / +$3,077 / PF 3.13 cells already exclude these fills.
- **The spec's runtime is not.** §6 pre-registers the offline expectation by replaying "through production `ioc_limit`" — the unguarded path. As written, the lane would book at runtime the very fills its own written expectation excludes.

Scale of the gap in the D3-approved cell (4HR, R:R ≥ 1.0, family cap 400 ticks): two of the five invalid fills fall inside it — 2026-01-08 (R:R 1.42, stop 154 ticks, +$100.52) and 2026-04-06 (R:R 3.81, stop 297 ticks, +$29.02) — **+$129.54 against the cell's +$3,077, about 4%.** Material enough to make expectation and outcome disagree; nowhere near the inverse lane's 110%.

D4's choice of 8 ticks is unaffected and remains correct. The tolerance is simply not what governs this.

## Recommended fix, not applied here

Extend the existing guard from the `stop_market` branch to the `ioc_limit` branch of `PaperBroker.execute_bracket`, returning the same `ENTRY_BRACKET_INVALID_AT_FILL` cancellation. One place; closes it for the wide-stop lane, the inverse ORB lane, and anything else on `ioc_limit`; and removes the need for each research script to remember the rule independently.

Why it is not applied in this PR:

1. It is a **runtime behaviour change** under the standing directive freezing such changes to 2026-09-30.
2. It **changes historical paper results** anywhere `ioc_limit` was used — every affected cell would need re-scoring, not just re-running. That is an evidence-invalidation decision, not a bug fix to be slipped in.

Until it is ruled on, the mitigation in the spec is a build precondition (see §6), which costs nothing and blocks the mismatch at the point where it would matter.

**What would close this:** either the guard is extended and the affected cells re-scored, or an explicit operator decision that `ioc_limit` fills are allowed to open invalid brackets — in which case every lane using it needs the label/P&L contradiction documented in its evidence, because "STOP_HIT" will not mean a loss.
