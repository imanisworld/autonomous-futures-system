# ORB Reclaim V4-R — study results

Preregistration: [`ORB_RECLAIM_V4R_PREREGISTRATION_2026-07-27.md`](ORB_RECLAIM_V4R_PREREGISTRATION_2026-07-27.md)
(committed before any code ran; nothing in that document changed after
seeing results below).

## Verdict: V4-R DOES NOT PASS the preregistered primary criteria

V4-R is the best-performing of the three populations tested — profitable,
PF > 1.2, both isolated and less concentrated than V4-original — but it
fails two of the preregistration's own explicit, frozen pass conditions:
**H2 is negative**, and **a single month (2025-12) carries 70.6% of total
net P&L.** Both were pass/fail criteria set before the study ran, not
judgment calls made after seeing the numbers.

| Criterion (frozen in preregistration §5) | Result | Pass? |
|---|---|:---:|
| Positive net P&L after costs | +$449.37 | ✅ |
| PF > 1.2 | 1.338 | ✅ |
| Positive expectancy per fill | +$14.50 | ✅ |
| Both H1 and H2 positive | H1 +$900.57 / **H2 −$451.20** | ❌ |
| No single month dominating | 2025-12 = **70.6%** of net P&L | ❌ |
| Realistic fill model | ioc_limit, by construction | ✅ |
| No lookahead | causal transitions only, by construction | ✅ |
| Sample size tier | n=31 resolved fills | 30–49 tier: weak evidence even if everything else passed |

Two hard failures on criteria that were fixed in advance specifically to
prevent a strong-looking net number from masking a fragile or lucky result
— exactly what they caught here. **Classification: does not clear the bar
for PROMISING BUT UNPROVEN.** Reported as WAIT — the underlying rule-anatomy
finding (documented pattern outperforms the implemented one) still stands
and is worth preserving as a candidate, but this specific preregistered
population is not evidence of a viable strategy on its own.

## An important terminology finding, not a methodology error

V4-R (`prior_rejected_high`) and V4-original (`true_reclaim`) turned out to
be **98% overlapping populations** (867/885 raw candidates agree; only 18
disagree) — not the materially different populations the corrected
terminology implied they might be. This is a real mathematical property of
the ORB-status transition state machine, not a detection bug: any bar
sequence that closes above the ORB high, later drops back to at-or-below it
(which the state machine always tags `rejected_high` when it happens as a
discrete transition), and then closes above again (`reclaimed_high`) will
almost always satisfy both flags simultaneously — the "closed above earlier"
and "was explicitly rejected earlier" conditions are nearly logically
equivalent for this pattern, not independent conditions. Confirmed directly:
both isolated runs produced nearly identical figures (V4-original net
+$399.60 PF 1.331 vs. V4-R net +$449.37 PF 1.338, n=30 vs. n=31). The
terminology correction requested before this study (§0 of the
preregistration) was still the right call — Pass 1's own labels were
genuinely misleading — but readers should not expect V4-R to diverge
sharply from V4-original in practice; it doesn't.

## Method — three populations, as preregistered

### Population 1 — Raw detector (no runtime gate applied at all)

Own independent fill/exit simulation (`scripts/orb_reclaim_v4r_detector.py`
— ioc_limit single-bar-close + pessimistic stop/target walk-forward,
matching `execution/paper_broker.py`'s exact mechanics, but with **zero**
risk/quality gate applied — only the strategy's own `reclaimed_high` +
VWAP-above requirement and its bracket):

| Population | Candidates | Filled | Resolved | WR | Net P&L | PF | Expectancy |
|---|---:|---:|---:|---:|---:|---:|---:|
| first_cross (today's production rule) | 885 | 440 | 440 | 32.3% | −$1,431.34 | 0.913 | −$3.25 |
| v4_original (NY + true_reclaim) | 243 | 116 | 116 | 34.5% | −$13.31 | 0.997 | −$0.11 |
| v4_r (NY + prior_rejected_high) | 239 | 114 | 114 | 35.1% | +$67.78 | 1.016 | +$0.59 |

At the raw-rule level, none of the three populations show a real edge —
all are within noise of breakeven. The strategy's edge, such as it is, only
appears after the account's real quality/risk gates filter the population
(populations 2–3 below) — the gates are doing real, non-trivial work here,
not just adding friction.

### Population 2 — Runtime-filtered (reaches RiskEngine)

Full `ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker`, isolated
per variant (own dedicated account each — see "methodology correction"
below), no hypothetical exemptions, every gate exercised exactly as
deployed on `main` today.

| | first_cross (shared account) | v4_original (isolated) | v4_r (isolated) |
|---|---:|---:|---:|
| Raw candidates | 885 | 243 | 239 |
| `MARKET_CONDITION_NOT_TRENDING` | 370 | 107 | 103 |
| `MARKET_CONDITION_NOT_TRADABLE` (CHOPPY/DEAD) | 115 | 38 | 38 |
| `ENTRY_DETACHED_FROM_PRICE` | 39 | 9 | 9 |
| `EMA_STACK_NOT_ALIGNED` | 20 | 14 | 14 |
| `SIGNAL_BAR_VOLUME_TOO_LOW` | 2 | 0 | 0 |
| Skipped (position already open) | 26 | 11 | 11 |
| Other (WAIT / weak bar close) | 45 | 3 | 3 |
| `max_drawdown` (account halt) | **193** | 0 | 0 |
| **Reached RiskEngine** | **75** | **61** | **61** |
| — approved | 75 | 61 | 61 |
| — rejected at RiskEngine (stop-cap/confluence/target-distance) | 0 | 0 | 0 |

Zero RiskEngine-layer rejections for any population in this sample —
`max_stop_ticks`, `min_confluence_grade`, and `target_too_close` never
fired once for `orb_reclaim` across the full 313-day corpus. This is
notable and different from every prior strategy audited this session
(Miyagi, 3-2-2): `orb_reclaim`'s own bracket construction (stop capped
internally at 80/40 ticks, well inside RiskEngine's 120/60-tick backstop;
target's 2.5R multiple structurally clears the 15pt floor) never comes
close to any of those limits. `MARKET_CONDITION_NOT_TRENDING` is the
dominant blocker by a wide margin — `orb_reclaim` is not in
`_TRENDING_GATE_EXEMPT` and this session found no basis to add it (no
audit was requested or performed for that gate this pass — noted, not
investigated, since population 2/3 already clear a real edge without it).

### Population 3 — Filled (real fills, real P&L)

| | first_cross | v4_original | v4_r |
|---|---:|---:|---:|
| Resolved fills | 38 | 30 | 31 |
| W / L | 12 / 26 | 14 / 16 | 15 / 16 |
| Win rate | 31.6% | 46.7% | 48.4% |
| Net P&L | −$213.74 | +$399.60 | **+$449.37** |
| PF | 0.858 | 1.331 | 1.338 |
| Expectancy/fill | −$5.62 | +$13.32 | +$14.50 |
| Max drawdown | $414.70 | $602.11 | $723.36 |
| H1 | −$213.74 (n=38, all of H1 — see below) | +$729.55 (n=15) | +$900.57 (n=16) |
| H2 | $0 (n=0 — see below) | −$329.95 (n=15) | −$451.20 (n=15) |
| MNQ | −$164.44 (n=3) | +$78.64 (n=7) | +$78.64 (n=7) |
| MES | −$49.30 (n=35) | +$320.96 (n=23) | +$370.73 (n=24) |
| Top month | 2025-11 (+$155.10, but overall net is negative) | 2025-12 (+$317.04, 79.3% of net) | 2025-12 (+$317.04, 70.6% of net) |

**first_cross's H2 = 0 fills, not a data gap**: the shared-account run's
`max_drawdown` breaker trips partway through H1 (193 subsequent rejections,
confirmed a permanent halt for the rest of the account's life, not a
cooldown) and every candidate for the remainder of the study — all of H2 —
is rejected on that basis. This matches Pass 1's own framing exactly
("breaker-off runs as the uncensored diagnostic substrate, breaker-on as
the canonical one") and independently confirms today's deployed
`orb_reclaim` definition is a real, structural liability to the shared
paper account, not just individually unprofitable trades in isolation.

**v4_r's H2 weakness is genuine trade-level underperformance, not an
account-mechanics artifact**: zero `max_drawdown` rejections appear in
v4_r's isolated run (confirmed in the population-2 table above) — the
isolated account never halts. H2's −$451.20 comes entirely from real
trades losing money (10 of 15 H2 fills were losses), not from the account
being blocked from trading.

## Methodology correction made mid-study (documented, not silently fixed)

The first isolated-audit attempt ran the **unrestricted** population through
one continuous shared account and then labeled which bars were also
V4-R/V4-original-eligible after the fact. That correctly measures "today's
deployed behavior" (population reported above as `first_cross`) but does
NOT correctly isolate V4-R's own standalone risk profile: `first_cross`'s
real, confirmed losing trades share the same account and drawdown-breaker
state as the V4-R subset, so a halt caused by unrelated `first_cross`
losses could block a V4-R candidate that would otherwise have traded —
exactly the "own isolated filtered replay" requirement Pass 1's own
caveats section already flagged, and the same session-isolated-account
precedent PR #352 used. Corrected by re-running V4-original and V4-R each
in their own fresh, dedicated, continuous account
(`scripts/orb_reclaim_v4r_isolated_variant_audit.py`, in-process monkeypatch
of `DecisionEngine._try_orb_reclaim` gated on a precomputed eligibility set
from the raw detector's already-causal transition history — zero committed
runtime file changed). The `first_cross` numbers in this report are from
the original (correct-for-its-own-purpose) unrestricted run; V4-original
and V4-R are from the corrected isolated runs.

A second data question resolved during the audit: `NO_ENGINE_DECISION_AT_BAR`
(11 for each isolated variant, 26 for `first_cross`) initially looked like
a data-integrity gap. Directly verified against the journal: these are bars
correctly SKIPPED by `replay/replay_engine.py`'s own `skip_to` mechanism
because an earlier same-day (or carried-over) position was still open
(`position_rules.max_open_positions: 1`) — confirmed by finding the exact
approved+filled trade immediately preceding each gap (e.g. MES 2025-08-26:
an approved WIN opens at 16:00 UTC, and the following 15 bars — 16:15
through 19:45 — are all silently skipped by the position-open fast-forward,
exactly matching the gap). Not a defect; reported as `SKIPPED_POSITION_
ALREADY_OPEN` in the final tables above.

## Rule-anatomy question — answered

**Yes, the underlying finding is confirmed**: the documented pattern
(rejection-then-reclaim) meaningfully outperforms today's implemented
pattern (any close-cross, no rejection required) — first_cross nets
−$213.74/PF 0.858 vs. v4_r's +$449.37/PF 1.338 over the identical corpus and
identical bracket. **No, that finding does not translate into a population
that clears this session's evidence bar** — the H2 and month-concentration
failures mean the improvement is real but not yet trustworthy at this
sample size and this time distribution.

## Disposition

- **No engine, config, signal, or risk file was touched.** Everything in
  this report is evidence/research only, matching every other strategy
  audited this session.
- **V4-R and V4-original: WAIT.** Not promoted, not rejected outright — the
  rule-anatomy improvement is real and worth preserving as a candidate, but
  this preregistered population fails two explicit, pre-committed pass
  criteria. Re-testing requires either a longer corpus (more H2-equivalent
  periods to check whether 2026-02→05's drawdown was a regime event or
  structural) or accepting the strategy needs further rule refinement
  before its next evidence pass — not proposed or designed here, per the
  preregistration's own scope boundary.
- **Today's deployed `orb_reclaim` (first_cross)**: confirmed net-negative
  over the full available corpus and structurally capable of tripping the
  account's own drawdown breaker on its own trading (not rescued by any
  gate this session found reason to touch). Not altered by this research;
  already governed by existing risk controls.
- **`max_stop_ticks` / `min_confluence_grade` / `target_too_close`**: never
  fired for `orb_reclaim` in this entire study — no audit finding either
  way, simply not material to this strategy's bracket geometry.
- **`MARKET_CONDITION_NOT_TRENDING`**: the dominant blocker by volume (370
  of 885 raw candidates). Not audited for parity this pass — `orb_reclaim`
  predates and is unrelated to the 5-minute-native TRENDING-exemption work
  done for Miyagi/3-2-2 this session, and V4-R already clears a real
  (if criteria-failing) edge without touching it. Flagged here as a
  candidate question for a future pass, not investigated.

## Reproduction

```bash
# Population 1 — raw detector + own fill simulation
python3 scripts/orb_reclaim_v4r_detector.py --out <path>

# Population 2/3 — first_cross, shared account (today's deployed behavior)
python3 scripts/orb_reclaim_v4r_runtime_audit.py --raw <detector-out> --out <path>

# Population 2/3 — V4-original and V4-R, each isolated (dedicated account)
python3 scripts/orb_reclaim_v4r_isolated_variant_audit.py \
    --raw <detector-out> --variant v4_original --out <path>
python3 scripts/orb_reclaim_v4r_isolated_variant_audit.py \
    --raw <detector-out> --variant v4_r --out <path>

# Final aggregation
python3 scripts/orb_reclaim_v4r_aggregate_report.py \
    --raw <detector-out> --first-cross-audit <path> \
    --v4-original-audit <path> --v4-r-audit <path> --out <path>
```

Corpus: `data/replay_corpus_v1_market_condition_fixed` (MNQ+MES, 313 daily
files each, 2025-07-24..2026-07-23, #338-corrected, gitignored).
