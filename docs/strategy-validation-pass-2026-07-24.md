# Strategy Validation Pass — 2026-07-24

Read-only analysis over the verified Corpus v1 replay evidence
(`main@a5434794e471137af83f6e5886b535fb9e3cfcd5`, 747 attempts, 0 unjoinable —
see [`corpus-v1-clean-baseline-report-2026-07-25.md`](corpus-v1-clean-baseline-report-2026-07-25.md),
which keeps its original filename date since it documents that prior pass,
not this one, and now carries its own 2026-07-25 correction addendum). No
strategy, gate, replay, or runtime code was changed to produce this. New
tooling: `scripts/strategy_validation_report.py` (reusable per-strategy
breakdown generator), `scripts/corpus_v1_orphan_resolution.py`
(carry-forward resolution of the orphans described below, generalized
2026-07-25 to any strategy rather than hardcoded to `orb_reclaim`, with a
`--verify` self-consistency check against `JournalReader`), and
`scripts/corpus_v1_apply_orphan_correction.py` (folds those resolutions back
into the Corpus v1 closure record — 747/747 resolved, corrected WR/PF/net/
expectancy). This document, the correction scripts, the corrected Corpus v1
artifacts, and the memory/handoff updates are bundled as one PR per the
operator's "narrow durability PR" instruction. No implementation changes are
proposed — evidence and classification only.

**Revision history**: v1 of this pass treated the 23 orphans below as safely
excludable from win-rate/PF/expectancy math (operator HOLD — non-random,
single-cause missing data is not neutral to exclude). v2 replaced that with
an orb_reclaim-only carry-forward resolution and before/after comparison
(operator APPROVE on the method, with two follow-up corrections: the
Corpus v1 closure record itself needed recomputing at 747/747, not just
`orb_reclaim`'s own numbers, and the "a real position would stay open"
claim overstated what carry-forward actually proves about live broker
behavior). This version applies both: the corrected Corpus v1 totals are in
`corpus-v1-clean-baseline-report-2026-07-25.md`, and the broker-fidelity
overclaim is corrected in Part 1 below. Every classification in this
document is unchanged through all three versions.

## Part 1 — The 23 non-WIN/LOSS attempts, classified exactly

All 23 are **orb_reclaim**, split 22 MES / 1 MNQ, `unjoinable_legacy=False`
(real `paper_order_id` on every one — the #327/#332 identity fix already
proved they aren't misjoined). Traced each of the 23 individually against its
raw journal file. Every single one fits one identical pattern, with no
exceptions:

- It is the **last approved TRADE decision** in that day's journal file.
- It is followed only by `WAIT` rows (position being monitored) for the rest
  of that day's candles.
- **No OUTCOME row for it appears anywhere in the file** — not a
  `NO_FILL`/`CANCELLED` outcome, not a delayed one. The file simply runs out
  of bars first.

That rules out "cancelled" and "no-fill": both of those produce an explicit
OUTCOME row in this journal format (confirmed by reading
`execution/paper_broker.py`'s `_entry_not_filled` path, which always emits
one), and none of the 23 has one. The correct, exact classification is:

**Genuinely open, orphaned at the replay day-boundary.** `replay_engine.py`
constructs a fresh `PaperBroker` for every single day file
(`memory/project_replay_identity_propagation_pr332.md` established this: "a
fresh `PaperBroker` is constructed on every single `ReplayEngine.run()` call,
and any position still open when a day's candles run out is simply
abandoned — no explicit close, no carry-forward"). These 23 are that
abandonment happening in practice. It is an artifact of the day-sliced
replay architecture, not real trading behavior, and not a defect in the
identity join — the join correctly reports them as open rather than
guessing.

It happens **only** to `orb_reclaim` (0/157 across the other five
strategies) and disproportionately to **MES** (22 of 23). `orb_reclaim`'s
target is 2.5R out from entry, the widest of the six strategies' targets —
plausibly it more often needs more bars-to-resolve than the other,
tighter-target strategies, making it the one most exposed to a day boundary
landing mid-trade. Not verified further; would need a bar-count histogram to
confirm, which is out of scope for this pass.

### Correction: this is non-random missing data, not safely excludable

The first version of this pass stopped at "already correctly excluded from
`resolved`/`win_rate`" and treated that as the end of the story. Operator's
HOLD verdict, correctly: exclusion is not neutral here. All 23 share one
strategy and one deterministic cause (a day-boundary artifact, not chance) —
that is exactly the shape of missing data that can bias win rate, PF, and
P&L if silently dropped, and the only way to know whether it does is to
actually resolve them, not assume the bias is small.

**End-of-day handling rule chosen: carry forward.** Three options were on
the table (carry forward, forced close, mark-to-market at the day boundary).
Carry-forward is the correct one for reproducing the *strategy/replay
rule*: `execution/day_only_exit.py`'s `DAY_ONLY_STRATEGIES` set contains
only `strat_4hr_retrigger` — nothing in `orb_reclaim`'s own design calls
for flattening it at day end, so replay/live would otherwise let it run
until stop/target. Forced-close or mark-to-market at the day boundary would
each impose an exit event the strategy's own logic never calls for — a
*new* bias, not a fix for the existing one.

**Correction (operator, 2026-07-25): this is not proof of live broker
behavior, and the first version of this section overstated it.** "Carry
forward reflects what the strategy/replay rule says should happen" is a
different claim from "a real position would stay open until stop/target
hit" — the latter also depends on the execution layer correctly keeping the
position protected, which is a separate question this analysis does not
touch. It has failed for real before: `execution/tradovate_broker.py`
(lines 802-826) documents that bracket child orders (stop/target) submitted
without an explicit `timeInForce=GTC` default to Day and can expire at
session close, leaving the position open but **unprotected** — this
happened live on MES 2026-07-21 (see
[[project_mes_orphan_incident]]/`memory/project_mes_orphan_incident.md`),
and was fixed by making `GTC` explicit on both bracket children. That fix
means today's live broker path shouldn't reproduce that exact failure, but
it also means "carry forward" here should be read narrowly: it resolves
these 23 the way the *strategy/replay design* says they should resolve, not
as a claim about broker order-management fidelity, which is unverified and
out of scope for this script.

**Resolution method** (`scripts/corpus_v1_orphan_resolution.py`): for each
of the 23, restore the exact position
(direction/entry/stop/target/contracts, already recorded in its TRADE
decision row) into a `PaperBroker` built from the identical production
config (`config.load_config()` — same slippage/runner/breakeven settings as
every other Corpus v1 trade, no new fill-model assumptions), then feed it
the real subsequent-day candle bars already on disk
(`data/replay_corpus_v1/`, no new Polygon pull) through the same
`broker.resolve_position()` call `replay_engine.py` itself uses, one bar at
a time, until a Fill resolves it. This is not a new implementation — it
drives the existing, already-audited PaperBroker fill logic across the day
boundary that `replay_engine.py`'s per-day loop currently stops at,
entirely from a read-only analysis script. One disclosed approximation: the
entry price used is the TRADE decision's *requested* `setup.entry`, not a
slippage-adjusted fill price (that price only exists post-fill, which never
happened for these 23 inside the original per-day run) — a small,
unavoidable gap on the order of the configured slippage.

**Result: all 23 resolved**, within 1-3 days each (max lookahead needed was
3 days; none required the full 30-day safety window). 11 WIN / 12 LOSS,
net **+$696.88**.

| | Resolved | Wins | Losses | WR | PF | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Before** (23 excluded) | 567 | 246 | 321 | 43.39% | 1.876 | $44,624.25 | $78.70 |
| **After** (carry-forward resolved) | 590 | 257 | 333 | 43.56% | 1.880 | $45,321.13 | $76.82 |
| Delta | +23 | +11 | +12 | +0.17pp | +0.004 | +$696.88 | -$1.88 |

Per instrument:

| | Resolved before → after | WR before → after | PF before → after | Net P&L before → after |
|---|---|---|---|---|
| MNQ (1 orphan) | 262 → 263 | 46.56% → 46.77% | 2.217 → 2.222 | $28,026.75 → $28,126.75 |
| MES (22 orphans) | 305 → 327 | 40.66% → 40.98% | 1.595 → 1.603 | $16,597.50 → $17,194.38 |

**This is `orb_reclaim`'s own numbers, not the Corpus v1 closure record.**
The 23 orphans are 79% of the corpus's attempts but not 79% of the
*resolved* population — the full six-strategy, both-instrument closure
record (`scripts/corpus_v1_apply_orphan_correction.py`, output in
`docs/corpus-v1-clean-baseline-report-2026-07-25.md`'s 2026-07-25 correction
addendum) moves **747/724 resolved → 747/747**, net P&L $53,428.05 →
$54,124.93 (+1.30%), WR 45.17% → 45.25%, PF (newly reported) 1.957 → 1.959,
expectancy $73.80 → $72.46 — the same immaterial-to-classification
direction as the `orb_reclaim`-only view above, just diluted across a
larger resolved base. See that document for the full corrected
per-instrument/H1-H2/quarterly/per-strategy tables — everything except the
`orb_reclaim` rows is byte-identical to the pre-correction figures, since no
other strategy had any orphans.

**The correction is real but small, and it does not change the
classification.** WR, PF, and net P&L all move in a favorable direction
(more wins than losses among the 23, net positive), but by fractions of a
point — nowhere near enough to move `orb_reclaim` out of PROMISING BUT
UNPROVEN, and nowhere near enough to touch the Part 2 finding that actually
drives that classification (the H1/H2/Q3 concentration, which these 23
trades are far too few and too evenly spread across quarters to affect).
Per the operator's instruction, `orb_reclaim` stays **PROMISING BUT
UNPROVEN** — this correction is confirmatory (the exclusion turned out not
to have been hiding a material bias), not exculpatory on its own; the
open Q3-durability question from Part 2 is what still gates VALIDATED.

## Part 2 — `orb_reclaim` deep validation (590 attempts, 79% of the corpus)

Full breakdown: `scripts/validation_orb_reclaim.json`. Headline numbers match
the operator's cited figures exactly (590/747 attempts, 567 resolved,
$44,624/$53,428 net P&L) — the table below is the pre-correction figure;
see Part 1 for the carry-forward-corrected $45,321.13 and confirmation the
correction is too small to change any conclusion drawn from this table.

| | Attempts | Resolved | Open | WR | PF | Net P&L | Expectancy | Avg Win | Avg Loss | Largest Loss | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MNQ | 263 | 262 | 1 | 46.6% | 2.217 | $28,027 | $107 | $418 | $-164 | $-246 | $3,093 |
| MES | 327 | 305 | 22 | 40.7% | 1.595 | $16,598 | $54 | $359 | $-154 | $-315 | $3,232 |
| Combined | 590 | 567 | 23 | 43.4% | 1.876 | $44,624 | $79 | $388 | $-159 | $-315 | $2,962 |

**Direction: LONG only, structurally — not a sample-size artifact.**
`strategy/signal_engine.py::_try_orb_reclaim` (lines 1963-1995) hard-codes
`direction="LONG"` and only fires on `state.orb.status == "reclaimed_high"` —
there is no reclaimed-low/SHORT branch in the code at all. "Does the edge
survive both directions" does not apply to this strategy by design; it only
ever trades one direction.

**H1 vs H2 — the one finding that matters most:**

| | Attempts | Resolved | WR | PF | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| H1 (25-07-24→26-01-23) | 285 | 272 | 40.8% | 1.63 | $9,102 | $33 |
| H2 (26-01-24→26-07-23) | 305 | 295 | 45.8% | 1.974 | $35,522 | $120 |

H2 carries **3.9x** the net P&L of H1 on almost the same attempt count. Both
instruments show the same direction (MNQ H1 exp $43 → H2 $162, 3.8x; MES H1
$25 → H2 $83, 3.3x) — this is not one instrument dragging the comparison.
Quarterly resolves *where* that lives:

| | Q1 | Q2 | Q3 | Q4 |
|---|---:|---:|---:|---:|
| Combined WR | 42.8% | 39.0% | **50.3%** | 41.3% |
| Combined PF | 1.786 | 1.546 | **2.556** | 1.643 |
| Combined expectancy | $30 | $36 | **$142** | $100 |
| MNQ expectancy | $58 | $30 | $148 | $176 |
| MES expectancy | $7 | $42 | $136 | $30 |

**Q3 is the standout quarter for both instruments** — and for **MES
specifically, Q3 is the entire H2 story**: MES Q4 expectancy ($30) reverts
almost exactly to Q1/Q2 baseline ($7/$42), meaning MES's H2 strength is
carried by one quarter, not a sustained level shift. MNQ's improvement is
more persistent (Q3 $148, Q4 $176 — higher still). This is the load-bearing
uncertainty for the whole corpus: the system-level "H2 >> H1" pattern the
operator already flagged in the original Corpus v1 review is, underneath,
mostly an `orb_reclaim`-on-MES-in-Q3 pattern, not a broad, even improvement.

**Session:** all three sessions (asian n=25, london n=321, new_york n=221
resolved) are individually profitable (PF 2.11 / 1.76 / 2.02) — the edge is
not concentrated in one session. Asian's sample is thin (28 attempts total)
so treat that cell as weak evidence, not as its own claim.

**Risk shape:** max drawdown $2,962 combined (2026-05-20→06-03, inside Q4) —
about 6.6% of total net P&L, not alarming on its own. Longest loss streak 10
trades (-$1,007) — worth a note for position sizing, not disqualifying.
No period (quarter or half, either instrument) was net-negative or
PF-under-1 anywhere — the worst cells (MES Q1 PF 1.207, MES Q4 PF 1.163) are
still profitable, just barely.

**P&L distribution** is the expected two-lobe shape for a fixed-R:R
strategy: a loss cluster in [-300,-100) (219 of 246 losses) from the
stop-first exit, and a spread of wins across [100,1000) from the 2.5R
target — consistent with the strategy's own bracket geometry, not a lumpy or
outlier-driven curve.

### Classification: orb_reclaim = **PROMISING BUT UNPROVEN**

Not BROKEN (net positive every quarter, both instruments, no negative-PF
period anywhere). Not WAIT (567 resolved trades across 208 distinct trading
days is a real sample, not a thin one). Not OVERFIT in the parameter-fitting
sense (this is out-of-sample walk-forward replay, not a fitted backtest).
Not VALIDATED, for one specific, named reason: the edge is not
demonstrated to be *time-stable* — H2's strength is disproportionately a
Q3 effect, and on MES it is *entirely* a Q3 effect that did not repeat in
Q4. The open question is whether Q3-level performance is a durable regime
this strategy captures repeatably, or a favorable trending quarter that
inflated the H2 comparison. That can only be resolved by watching whether a
*future* (not historically-resplit) period reproduces Q3-level numbers —
consistent with the standing evidence-phase directive's forward-measurement
gate, not by re-cutting this same historical window differently.

## Part 3 — The five smaller strategies

**Structural note before the numbers:** `orb_breakout`, `vwap_reclaim`,
`pdl_reclaim`, `vwap_rejection`, and `orb_rejection` all show **0 MES
attempts, 100% MNQ** in this corpus. This is *not* a data gap or a
replay-generation issue — `risk_rules.yaml`'s
`disabled_concepts_per_instrument.MES` block deliberately disables all five
on MES (2026-07-09 posture, citing the #236/#237/#238 evidence chain: "MES
orb_reclaim is the ONLY strategy with validated positive expectancy under
honest fills... everything else is disabled to make it the sole active MES
proof lane"). MNQ has no equivalent disabled-concepts entry. So "does the
edge survive both instruments" is not an applicable question for these
five — they are MNQ-only by an already-made, already-cited decision, not
untested-on-MES-by-oversight. The relevant question for each is instead:
does its edge hold across time on the one instrument it actually runs on.

### orb_breakout (MNQ only) — n=68 resolved, WR 54.4%, PF 2.245, net $3,656, expectancy $54
- H1 exp $50 vs H2 exp $60 — no dramatic imbalance, unlike orb_reclaim.
- Quarterly expectancy $43/$73/$63/$51 — all four quarters positive and in a
  narrow band; no single-quarter dependency.
- **Direction split is the real finding**: LONG (n=43) PF 3.144, expectancy
  $80 — SHORT (n=25) PF 1.151, expectancy **$8**, barely above breakeven.
- **Session split confirms it**: london (n=43) PF 3.227, expectancy $85 —
  new_york (n=22) PF 0.953, expectancy **-$2**, net negative.
- **Classification: PROMISING BUT UNPROVEN.** The whole-strategy aggregate
  looks solid, but it is carried almost entirely by LONG-in-london trades;
  SHORT trades and new_york-session trades are each independently
  indistinguishable from breakeven. Before trusting the aggregate number,
  the SHORT and new_york sub-populations need more evidence or an explicit
  decision to scope the strategy down to what's actually working.

### vwap_reclaim (MNQ only) — n=50 resolved, WR 56.0%, PF 4.309, net $3,759, expectancy $75
- H1 exp $19 vs H2 exp $160 (8.4x) — an even sharper imbalance than
  orb_reclaim's, on a fraction of the sample size.
- Quarterly: Q1 PF 1.572, Q2 PF 2.086, **Q3 PF 12.794** (n=11), Q4 PF 5.562.
  Q3 alone contributes $1,887 of the $3,759 full-period net P&L — **50% of
  the entire year's P&L from 11 trades in one quarter.**
- This matches the strategy's own prior finding already on record in
  `risk_rules.yaml`'s disabled-concepts comment ("40% WR on MES vs 100% MNQ
  — reclaim conditions fire too loosely") — a strategy already flagged as
  fragile enough to be MES-disabled.
- **Classification: WAIT.** n=50 with half the year's profit concentrated in
  11 trades from a single quarter is not distinguishable from a lucky
  quarter on this sample size, regardless of how good the headline PF looks.

### pdl_reclaim (MNQ only) — n=15 resolved, WR 53.3%, PF 2.856, net $807, expectancy $54
- H1 is 4 trades (net -$29); H2 is 11 trades (net $836). Individual quarters
  are as small as 1 trade (Q1).
- **Classification: WAIT.** Exactly the small-sample case the operator
  anticipated — no split here has enough trades to mean anything.

### vwap_rejection (MNQ only) — n=8 resolved, WR 62.5%, PF 4.432, net $453
- **Classification: WAIT.** 8 trades total; not evaluated further than that.

### orb_rejection (MNQ only) — n=16 resolved, WR 18.8%, PF 1.449, net $128, expectancy $8
- Low win rate offset by a favorable payoff ratio (avg win $138 vs avg loss
  -$22), net PF is still >1, but the absolute P&L over the full 12 months is
  **$128** — functionally noise-level on a dollar basis even though the
  ratio math is positive. Q2 (1 trade, -$20) and Q3 (2 trades, -$50) are
  individually negative, both far too small to read as signal.
- **Classification: WAIT.** n=16 and a full year's net P&L under $150 is not
  enough to call this either promising or broken.

## Summary table

| Strategy | Attempts | Resolved | Net P&L | Classification |
|---|---:|---:|---:|---|
| orb_reclaim | 590 | 590† | $45,321† | PROMISING BUT UNPROVEN — Q3 concentration, esp. on MES |
| orb_breakout | 68 | 68 | $3,656 | PROMISING BUT UNPROVEN — carried by LONG+london only |
| vwap_reclaim | 50 | 50 | $3,759 | WAIT — 50% of P&L from one quarter, n too small |
| pdl_reclaim | 15 | 15 | $807 | WAIT — sample too small for any split |
| vwap_rejection | 8 | 8 | $453 | WAIT — sample too small |
| orb_rejection | 16 | 16 | $128 | WAIT — sample too small, P&L noise-level |

† orb_reclaim's resolved/net-P&L figures reflect the Part 1 carry-forward
correction (all 23 day-boundary orphans resolved: 11 WIN/12 LOSS,
net +$696.88). The pre-correction figures were 567 resolved / $44,624 — the
correction changes neither the classification nor any other row.

None of the six strategies is classified BROKEN or OVERFIT. Every strategy
was net-positive over the full period; the open question in every
non-WAIT case is *time-stability and sub-population uniformity*, not sign.

## Evidence gaps (explicitly not fixed here)

- No period-end reproduction test exists yet for orb_reclaim's Q3-level
  performance — that requires a genuinely new forward period, not another
  re-cut of this same historical window.
- orb_breakout's SHORT and new_york-session sub-populations have never been
  isolated and separately evidenced before this pass.
- vwap_reclaim's Q3 concentration was visible in the combined corpus number
  but not previously broken out to this granularity.
- The day-boundary-orphan pattern (Part 1) is a real gap in the replay
  architecture's fidelity for wide-target strategies; whether it's worth
  fixing (e.g. carrying open positions across day files) is a separate,
  not-yet-scoped decision — no implementation change is proposed here.

## What this pass does not conclude

No go-live, promotion, or configuration decision follows from this pass.
Per the standing evidence-phase directive, this is descriptive evidence
only. The next possible steps (a forward-measurement check on orb_reclaim's
Q3 durability, a scoped-down orb_breakout backtest restricted to
LONG+london, or accumulating more vwap_reclaim/pdl_reclaim/vwap_rejection/
orb_rejection samples before revisiting WAIT) are each separate,
not-yet-started decisions for the operator to scope.
