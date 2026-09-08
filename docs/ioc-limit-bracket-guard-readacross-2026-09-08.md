# PaperBroker's `ioc_limit` path has no bracket-validity guard — which lanes inherit it

Status: **CLOSED — the production path is already guarded upstream; the exposure is research-harness-side only.** Evidence only; no strategy, risk, replay, broker, config or deployment change, and none is recommended.

> **Correction, 2026-09-08 (same day).** This note first read as "a production defect is identified" and recommended carrying the guard into `PaperBroker.ioc_limit` as a runtime fix pending an operator ruling on the freeze. **That recommendation was wrong and is withdrawn.** Measuring the blast radius showed the live path is already protected by `ENTRY_DETACHED_FROM_PRICE` one layer up, and that no committed replay evidence is affected. The defect is real, but it lives in research harnesses that bypass the signal engine, not in production. §"Measured blast radius" and §"Recommendation" below carry the corrected position; the mechanism sections above them were and remain accurate.

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

## Measured blast radius — no committed replay evidence is affected

Script: `scripts/ioc_limit_guard_replay_log_survey.py` → `scripts/ioc_limit_guard_replay_log_survey_2026-09-08.json`. It pairs every `OUTCOME` row with the `TRADE` decision preceding it in the same journal and asks whether the recorded fill sits inside its own bracket. (It reads `logs/`, which is gitignored, so it does not run from a fresh clone — its output is committed so the result stays auditable.)

**18,723 paired trades across 9 replay roots. 0 invalid at fill. 0 unattributable outcomes.** Of those, **6,845 are `ioc_limit`** runs — 1,183 + 252 + 2,982 + 2,428 across the four 622-day `ioc_limit` roots.

A zero is only worth anything if the check could have fired, so:

| Roots | Trades | Invalid | Fills deviating from plan | Max deviation |
|---|---:|---:|---:|---:|
| 4 × `ioc_limit` | 6,845 | 0 | 2,408 | **32 ticks** |
| 5 × `market` | 11,878 | 0 | 11,878 | 1 tick |

Fills do deviate — about a third of `ioc_limit` fills do — and the deviation caps at **exactly the configured 32-tick entry tolerance**, never beyond. The `market` roots cap at 1 tick, their slippage. So the check was live and simply never fired.

## Why zero — the guard already exists, one layer up

`strategy/signal_engine.py:1517` rejects a candidate as `ENTRY_DETACHED_FROM_PRICE` when its stop and target no longer straddle the live price. Its own comment states the purpose: *"this guard exists to stop a MARKET fill landing on the wrong side of an anchored bracket."* That is this defect, guarded at the signal layer rather than the broker layer. Far-detached candidates never reach `PaperBroker`, which is why the deviation ceiling is the tolerance.

Two narrow carve-outs disable it — `proof_market_entry_active` (the MNQ orb_breakout **proof** lane) and `permission_gate_exception` (MNQ vwap_hold, new_york). **The inverse ORB lane receives neither:** `proof_market_entry_active` requires the proof mode to be active, and `config/settings.py:1123` forbids the proof and inverse modes being active simultaneously. So whenever the inverse lane runs, the straddle guard applies to it.

**Consequence for the inverse ORB baseline.** Its +$1,026.64 is not merely an artifact of an unguarded fill model — it describes trades **production was structurally incapable of taking**, because the straddle gate would have rejected those candidates at decision time. The canonical proof reaches them only by taking already-approved source arms and filling the mirrored order against a later market price, which is a path the signal engine never executes. This strengthens the BROKEN verdict in `docs/inverse-orb-baseline-post-fill-decomposition-2026-09-08.md` rather than qualifying it.

## Recommendation — do not change `PaperBroker`

The earlier recommendation to carry the guard into the `ioc_limit` branch is **withdrawn**. There is no production exposure to close: the live path is guarded upstream, and no recorded replay result would move. Applying it would re-score the inverse ORB baseline for no safety gain, and would spend a freeze exception on defence-in-depth.

The real gap is narrower and belongs to tooling: **a research harness that reproduces the fill model without also reproducing the gate that protects it will manufacture this artifact.** Two harnesses do reproduce the gate's effect correctly — `scripts/edge_decomposition_audit.py` via its own `_bracket_valid_at_fill`, and the DEMO wiring via post-fill validation. `inverse_orb_canonical_ioc_proof` did not, and produced a headline number that stood for a day.

So the durable fix is a convention, not a code change: **any harness filling stored candidates outside the signal engine must assert bracket validity at fill, and report what it rejected.** The wide-stop spec precondition (§6) is exactly this applied to that lane's build-step-1 replay, and stands unchanged — a build-step replay is such a harness.

**What would reopen the production question:** a new carve-out from `ENTRY_DETACHED_FROM_PRICE`, or a lane that submits an `ioc_limit` order without passing the signal engine's straddle check. Either would put the broker layer back on the hook, and the guard should then be added.
