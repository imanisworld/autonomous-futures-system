# Inverse ORB — would recomputing the bracket from the actual fill rescue the lane?

Status: **CLOSED — no. Recomputing is worse than refusing.** Evidence only; no strategy, risk, replay, broker, config, runtime or deployment change.

Script: `scripts/inverse_orb_recomputed_bracket_study.py` → `scripts/inverse_orb_recomputed_bracket_study_2026-09-08.json`.

## The question

The post-fill decomposition (`docs/inverse-orb-baseline-post-fill-decomposition-2026-09-08.md`) showed the canonical baseline's entire profit sits in fills whose entry landed beyond its own static bracket. Two responses are possible, and they are different systems:

- **REFUSE** — do not open a position whose fill is already past its own bracket. This is what the live Tradovate leg already does via post-fill validation, and it is now what `PaperBroker` does on every entry path.
- **RECOMPUTE** — keep the trade, but translate the bracket onto the actual fill, preserving the stop and target *distances* (and therefore R:R). No venue does this today.

The decomposition explicitly left this open: *"It does not settle whether a non-detached inverse ORB has an edge."* This study settles the recompute half.

## Method

Same population (the frozen 63-arm canonical proof, 57 filled), same entry the proof used (fill + one adverse tick on the market leg), same 5-minute bars, same pessimistic same-bar rule (stop wins ties), same five-session forward window, same $1.48 round-trip commission as `scripts/inverse_orb_ioc_tolerance_sweep.py`. Only the bracket anchor moves.

**Control first.** Every filled arm was re-resolved against its **original** bracket and checked against the frozen proof's own recorded exit reason and net. **57 of 57 reproduce, 0 disagreements** — so the replay machinery is not what changes between the arms below. The script refuses to print treatment numbers if that control fails.

## Result

| Arm | n | Net | W/L | PF | H1 | H2 |
|---|---:|---:|---|---:|---:|---:|
| Baseline as recorded | 57 | **+$1,026.64** | 40 / 17 | 5.28 | +571.56 | +455.08 |
| **REFUSE** — admissible fills only | 20 | **−$61.10** | 3 / 17 | 0.75 | −132.30 | +71.20 |
| **RECOMPUTE** — bracket moved to the fill | 57 | **−$249.86** | 16 / 41 | 0.77 | −191.94 | −57.92 |

Recomputing does not rescue the lane. It is **negative in both halves**, PF 0.77, and it is *worse in absolute terms than refusing* — because it puts 57 trades of exposure on the book to lose $250, where refusing puts 20 on to lose $61.

Sessions under recompute: Asian −$161.88, London −$217.60, New York +$129.62. Exits: 41 `STOP_HIT`, 16 `TARGET_HIT`, 0 unresolved.

## The decisive split

Splitting the recomputed result by whether each arm was admissible under the original bracket:

| Original status | n | Net under recompute | W/L | PF |
|---|---:|---:|---|---:|
| Originally **rejected** (the artifact set) | 37 | **−$193.26** | 10 / 27 | 0.73 |
| Originally admitted | 20 | −$56.60 | 6 / 14 | 0.85 |

**The 37 arms that carried +$1,138.26 as "wins" produce −$193.26 once the bracket is honest.** That is a direct measurement, not an inference: their profit was the geometry, not the direction. Give those same trades a bracket that actually surrounds their entry and they lose money like everything else.

## What this means for the two options

- **Refusing is correct and sufficient.** The shipped `PaperBroker` guard is not throwing away recoverable edge — the discarded trades are *negative* when measured properly.
- **Recomputing is not worth building.** It is a genuine execution change (no venue does it), it would need its own parity work on the live leg, and it buys a worse number.
- Neither option produces a lane worth promoting. The inverse ORB verdict of **BROKEN — negative evidence** stands under both.

## Reconciliation with the decomposition (20 vs 19)

The decomposition reports 19 admissible arms at −$111.62; this study reports 20 at −$61.10. Both are correct and they measure slightly different things:

- This study's admissibility test is **bracket geometry only** — the exact rule the shipped `PaperBroker` guard applies (`stop < fill < target`, mirrored for shorts).
- The decomposition used the **full `validate_post_fill`**, which additionally enforces `actual_rr_minimum`.

Exactly one arm — **2025-07-24** — passes the geometry check and fails the R:R minimum. That single arm is the whole difference. The larger population (57 filled) and the aggregate baseline (+$1,026.64) agree exactly between the two.

The halves also differ marginally from the inventory's published +$546.08 / +$480.56 because this script computes its own median-date split; the total net matches the canonical figure to the cent, which is the control that matters.

## Limits

- **Reproducibility.** Regenerating the JSON needs the local 5-minute bar corpus at `data/replay_polygon_5m/MNQ`, which is gitignored and absent from a fresh clone. The committed JSON is the reproducible record; the script fails loudly with a clear message rather than silently producing a partial result.
- **Hypothetical.** No deployed path recomputes a bracket from the fill. This measures what would have happened, not what any venue does.
- It does not settle whether an inverse ORB with a **genuinely non-detached entry** has an edge. Detachment here is a median 75 ticks and a max of 293; a rule whose fill is bounded on *both* sides would be a different population, and producing it is a strategy change, not a re-run.

## Verdict

**BROKEN — negative evidence** stands. Both available responses to the detached-fill defect leave the lane negative, so there is no version of the current rule worth promoting. Recompute is closed as an option; do not re-open it without a bounded-entry population.
