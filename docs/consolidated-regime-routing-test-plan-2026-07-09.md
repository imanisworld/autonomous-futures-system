# Consolidated Regime-Routing / ORB / Exit-Timing Test Plan — 2026-07-09

## Scope discipline

No `demo_proof` started. No production behavior changed. No demo/live order routing. No
diff to `execution/`/`risk/`/`config/`/`webhook/`/`broker*`/`runner`/GEX/fill
resolver/trade caps. No gates loosened or stops widened in production. External research
(the Mesfin preprints and related synthesis from the prior message) is treated as
hypothesis input for what to test, not as production proof — every number in this report
comes from this repo's own replay/shadow/real-journal evidence.

## Before building — what's already answered vs genuinely new

| Area | Already answered by | Genuinely new work needed |
|---|---|---|
| A. Regime routing (RANGE_BOUND) | Nothing — PR #237/#238's population (`STRUCTURE_PRESENT_BUT_NOT_QUALIFIED`) structurally excludes RANGE_BOUND rows, since they terminate at `MARKET_CONDITION_NOT_TRENDING` before ever reaching the `STRUCTURE_GATES` those PRs selected on (confirmed by reading `collect_shadow_rows()`) | Yes — built this round, see Section A |
| B. ORB decomposition (width/ATR/confirmation type/hold time) | PR #240's `orb_role` gives per-instrument/session classification only (no width/ATR/confirmation-type axes) | Yes — **not built this round**, see Section B for why |
| C. Target variants (current/0.5R/0.75R/1.0R/next-level) | **Fully answered**, PR #240 `analyze_targets`/`_target_for` | none |
| C. Wider-stop sweep + MAE/MFE + bars-to-target-after-stop | **Fully answered**, PR #240 `analyze_stops`/`_simulate_trade_stop` (stop multiples 1.0/1.25/1.5/2.0×, real losses that later reached target, MAE/MFE in R) | none |
| C. Time-based exits (60/65/75-min) | Nothing | Yes — **not built this round**, see Section C |
| C. ATR-calibrated stop | Nothing | Yes — **not built this round**, see Section C |
| D. VWAP role classification | **Fully answered**, PR #240 `analyze_family_roles`/`vwap_role` | none |
| E. Session breakdown (MES/MNQ × London/NY/Asian) | **Fully answered**, PR #240 `summarize_honest_baselines`, PR #237 `by_session` | none — re-cited below, plus fresh real-data session split for the new finding in Section A |

Per the operator's own instruction ("do not duplicate prior reports unless extending them
directly") — sections C, D, E below are almost entirely **citations** of already-computed
numbers, not new computation. Section A is genuinely new. Section B is scoped but
deliberately not built this round (reasoning below).

## Data sources

- **622-day Polygon replay** (`data/replay_polygon/`, `logs/replay_622d_*`): deterministic,
  reproducible, used by PR #236/#240/#241 and cited again here (Sections C/D/E, and the
  `orb_role`/`vwap_role` MES/MNQ classifications in Section A's context). Its own
  `market_condition` is derived internally, not from live Pine labels — per PR #59's commit
  message, "0/1274 non-TRENDING" across the 555-day precursor set. **This dataset cannot
  test RANGE_BOUND behavior at all** — it essentially never produces that label.
- **Real box journal** (`/root/afs-shared/logs/journal_2026-*.jsonl`, synced to local
  scratchpad for this session, 06-15 through 07-09): the only source with genuine Pine-fed
  `RANGE_BOUND` labels and the only source with the `range_signal`/`shadow_setups`
  `SHADOW_OUTCOME` resolution rows used in Section A. Not reproducible/committable (real
  demo trading data, public repo) — same reasoning as PRs #242/#243, so this section is a
  forensic report, not a script.

## Section A — Regime routing test: does RANGE_BOUND need its own playbook?

**Question**: are `RANGE_BOUND` bars currently killed by `MARKET_CONDITION_NOT_TRENDING`
before any range-specific logic gets a chance to evaluate, and if it had, would it have
been any good?

**Method**: for every real-box `NO_TRADE` row where `market_condition == "RANGE_BOUND"` and
`failed_gates == ["MARKET_CONDITION_NOT_TRENDING"]` (i.e. the pure, single-gate block
confirmed in PR #243), joined against the box's own already-resolved `SHADOW_OUTCOME`
journal rows (via `candidate_key`, matching the causal live resolver's own pessimistic
same-bar, trade-through-entry fill logic — no new simulation, same method as PR #237).
Two candidate populations exist on these bars:

**A1. Repurposed trend-strategies, evaluated unconditionally as shadow (`shadow_setups`
lane)**: 327 resolved (filled, WIN/LOSS) candidates. Combined: n=327, WR=35.5%, net
**-$472.63**, expectancy **-$1.45/trade**. Largest cells: MES `strat_22_continuation_observed`
(n=77, WR 37.7%, exp +$0.10 — noise), MNQ `strat_22_continuation_observed` (n=87, WR 39.1%,
exp -$8.32), MNQ `orb_false_break_fade` (n=20, WR 5.0%, exp -$61.20 — bad), MNQ
`strat_22_reversal_observed` (n=32, WR 43.8%, exp +$46.50 — the one large positive cell,
right at `MIN_CELL_N`, thin). **Verdict: the existing trend-oriented strategy set does not
work when admitted on RANGE_BOUND bars** — consistent with the intuition that a
range-specific playbook is a different thing from "just turn trend strategies loose in
chop," not evidence that no range playbook could work.

**A2. A dedicated range-specific signal already exists and already resolves**
(`range_signal` lane, `signal_type: RANGE_BREAK_CLOSE` — "15m close above/below the range
edge, break confirmed"; per project memory this lane was known to be deployed observe-only,
but its outcome quality had never been assessed). 234 resolved candidates, originating from
RANGE_BOUND bars specifically:

| Instrument | n | WR | Net | Expectancy | H1 exp | H2 exp | Top-3 outlier share |
|---|---|---|---|---|---|---|---|
| MES | 90 | 87.8% | +$2,241.45 | **+$24.90** | +$11.40 | +$38.41 | 25.4% |
| MNQ | 144 | 54.2% | -$12,524.98 | **-$86.98** | -$100.65 | -$73.31 | 14.6% |

MES by session: asian n=46 WR 84.8% exp +$13.13; london n=14 WR 85.7% exp +$83.37;
new_york n=30 WR 93.3% exp +$15.68 — **positive in every session**, walk-forward consistent
(both halves positive, second half stronger), not outlier-dependent (well under the 40%
threshold used throughout this session), clears `MIN_CELL_N` 3x over.

MNQ by session: asian n=70 exp -$95.79; london n=45 exp -$104.07; new_york n=29 exp -$39.19
— **negative in every session**, walk-forward consistent negative, not a fluke, not
outlier-dependent either (14.6%) — this is a real, consistent loser, not noise.

**This is the single most significant finding across this and all four prior audit PRs this
session.** MES has a real, evidence-clean candidate for exactly the question being asked —
whether RANGE_BOUND deserves its own playbook instead of a blanket kill — and the candidate
(`range_signal`/`RANGE_BREAK_CLOSE`) already exists in the codebase as an observe-only
shadow lane; it has simply never been assessed for quality before. MNQ's version of the
same signal is a clear, consistent loser and should not be routed the same way.

**Caveats, stated plainly**: this is 3 weeks of real data (06-29 earliest `range_signal`
row seen through 07-09), not the 622-day depth used elsewhere this session — the walk-forward
split here is a 3-week H1/H2, not a multi-year one. It also has not been tested against a
realistic fill/friction model beyond what the causal live resolver already applies (pessimistic
same-bar, does not model slippage/commissions, consistent with every other shadow-lane
caveat this session). Classification below reflects this — a real, high-quality lead, not a
production-ready result.

## Section B — ORB decomposition: scoped, not built this round

The consolidated ask wants ORB decomposed by instrument/session/ORB-width/ATR-regime/5m-vs-15m
confirmation/close-vs-wick/reclaim-vs-breakout/hold-time/target-type/stop-type — a
5-8-dimensional breakdown. Not built this round because:

- **No ATR computation exists anywhere in `scripts/`** — this is new infrastructure, not a
  re-slice of existing output, and is a meaningfully sized build on its own.
- **5-minute confirmation cannot be tested against real data right now**: PR #243 confirmed
  `FIVE_MIN_FEED_ENABLED=false` on the box (drift-gate-pinned) — there is no live 5m-confirmed
  MES/MNQ fill history to compare against 15m-only behavior. It could only be tested inside
  the 622-day replay (simulated 5m confirmation against Polygon 5m bars), which is a
  materially different, larger build than re-slicing existing output.
- Given Section A just surfaced a much higher-confidence, already-nearly-complete finding
  (a real, working range-specific signal sitting unused), building an 8-dimensional ORB
  decomposition on speculation is not "the smallest plan that tests what matters now" — it's
  the largest remaining item and the one with the least existing scaffolding.

**If pursued next**: smallest first cut would be ORB width alone (already-recorded
`orb.high`/`orb.low` in `context`) crossed with instrument/session, reusing PR #240's
`orb_role` population — no ATR needed for that first slice. ATR-regime and 5m-confirmation
are the two genuinely large sub-builds and should be scoped separately if this is prioritized.

## Section C — Stop/target/time-exit: mostly already answered, two new gaps named

Already fully computed by PR #240 (cited, not rebuilt):
- **Target variants** (current/0.5R/0.75R/1.0R/next-level): `analyze_targets`, `_target_for`.
  `next_level` already implements "nearest system level" using real structural fields
  (`orb_high`/`vwap`/`previous_day_high`/`hod`/`lod`/zone edges).
- **Wider-stop sweep** (1.0×/1.25×/1.5×/2.0× current stop) with per-instrument and
  per-instrument-strategy breakdowns: `analyze_stops`.
- **MAE/MFE in R-multiples** for every simulated trade: `_simulate`/`_simulate_trade_stop`.
- **"Whether wider stop helped or worsened expectancy"**: directly answered by
  `wider_stop_overall`/`wider_stop_by_instrument` in `analyze_stops`' output.
- **"Time from stop-out to target later reached"**: directly answered —
  `losses_that_later_reached_original_target` with `bars_until_target_after_stop` per trade.
- **Target-too-ambitious vs bad-setup distinction**: `analyze_target_ambition`/
  `classify_gates_10way` already produce this split (`TARGET_TOO_AMBITIOUS` is a distinct
  label from `BAD_STRATEGY` in the existing 10-way taxonomy).

Genuinely new, **not built this round**:
- **Bar-count time-based exits** (60/65/75-minute holds, independent of price target/stop) —
  no existing function resolves a trade by elapsed bars rather than price levels. Smallest
  build: a new resolution function parallel to `_simulate` that exits at a fixed bar offset
  from entry using `_bar_index`/`_candles` (both already exist) — this is a real but modest
  addition, scoped for a follow-up rather than bundled into this already-large report.
- **ATR-calibrated stop** (vs the existing fixed-multiple sweep) — blocked on the same
  missing ATR infrastructure named in Section B.

## Section D — VWAP role: fully answered, cited

PR #240's `vwap_role` output (622-day replay, `ioc_limit_static` fill model):

| Cell | Classification | Detail |
|---|---|---|
| MES `vwap_hold` | `VWAP_CONTEXT_ONLY` | asian exp -$10.10, london exp -$14.49, new_york exp **+$8.68** — session-dependent sign flip, the exact pattern that defines context-only rather than standalone |
| MNQ `vwap_hold` | `PROMISING_BUT_UNPROVEN` | asian +$4.81, london +$2.23, new_york -$10.69 — thin, inconsistent |
| MNQ `vwap_reclaim` | `BAD_STRATEGY` | negative-leaning across sessions |

No new work needed — this directly answers the consolidated ask's Section D. Matches PR
#239's production decision (MES `vwap_hold` already demoted to shadow-only via the separate
`strategy_permission_gate`, independent of this classification).

## Section E — Session behavior: fully answered, cited + fresh evidence

Already answered by PR #240 (`summarize_honest_baselines`, full session/strategy/instrument
matrix) and PR #237 (`by_session` shadow-candidate breakdown). MES vs MNQ already have
different active rule sets in production (PR #239's MES narrowing to `orb_reclaim`
only — MNQ untouched). MNQ London vs NY/RTH: PR #240's `orb_role` shows MNQ `orb_reclaim`
itself is `INSUFFICIENT_DATA` from replay (too few resolved cases per session to classify).

**Fresh evidence from Section A directly bears on this question too**: the new
`range_signal` finding is itself a session-behavior result — MES range-break-close works in
every session tested (asian/london/new_york all positive), while MNQ's version fails in
every session (all three negative). This is a clean instrument-level split, not a
session-level one — sessions do not rescue MNQ's version or hurt MES's.

## Final classifications

| Candidate | Classification |
|---|---|
| MES `range_signal` (RANGE_BREAK_CLOSE), RANGE_BOUND-originating | **`REGIME_ROUTING_CANDIDATE`** — real, walk-forward-consistent (3wk), non-outlier-dependent, session-robust; short real-data history, not yet production-grade evidence |
| MNQ `range_signal` (RANGE_BREAK_CLOSE), RANGE_BOUND-originating | **`BAD_STRATEGY`** — consistent loser, all sessions, both halves |
| Repurposed trend-strategies admitted on RANGE_BOUND (shadow_setups lane) | **`BAD_STRATEGY`** (combined) — do not use as the RANGE_BOUND playbook |
| MES `orb_reclaim` | **`VALIDATED_INTERNAL`** — already PR #240's `VALIDATED` label (622-day replay, London/NY positive, only Asian weak); this audit adds nothing new here, just confirms it stands |
| MNQ `orb_reclaim` | **`INSUFFICIENT_DATA`** — unchanged from PR #240 |
| MES `vwap_hold` | **`VWAP_CONTEXT_ONLY`** (PR #240, cited) |
| MNQ `vwap_hold` / `vwap_reclaim` | **`PROMISING_BUT_UNPROVEN`** / **`BAD_STRATEGY`** (PR #240, cited) |
| ORB width/ATR/confirmation-type decomposition | **`WAIT`** — scoped, not built, named as the next real gap |
| Bar-count time-based exits, ATR-calibrated stops | **`WAIT`** — scoped, not built, named as the next real gap |
| External Mesfin-style regime/Markov-classifier research | **`PROMISING_EXTERNAL_BASE_RATE`** — directionally consistent with this audit's own finding (a probabilistic/structural regime signal beating a binary trend filter), but not implemented or tested against this system's own data; the internal `range_signal` finding is a different, already-built, already-evidenced mechanism, not a GMM/Markov model — do not conflate the two when deciding what to build next |

## Final answer

1. **What should VP test next?** Extend the `range_signal` observe-only lane's real evidence
   window on MES specifically — let it keep accumulating (it's already running, unconditional,
   no code change needed) rather than building anything new yet. In parallel, the smallest
   real gap worth building is bar-count time-based exits (Section C) — it's a small, well-scoped
   addition reusing existing simulation infrastructure.
2. **What should VP stop testing?** The repurposed-trend-strategies-as-a-RANGE_BOUND-playbook
   idea (Section A1) — clearly negative, walk-forward consistent, not worth further scrutiny.
   Also: stop treating "trend as hard blocker" as a single monolithic question — Section A shows
   the real answer is instrument-specific (MES has a real range-context candidate; MNQ does not),
   not a single yes/no.
3. **Is MES `orb_reclaim` still active-worthy?** Yes — `VALIDATED_INTERNAL`, unchanged from PR
   #240, and this audit found nothing to contradict it.
4. **Does MNQ have any coherent candidate?** No — `orb_reclaim` is `INSUFFICIENT_DATA`
   (PR #240), the trend-modifier search found nothing (PR #241), and MNQ's version of the new
   `range_signal` finding is a confirmed `BAD_STRATEGY`. MNQ has no coherent candidate as of
   this audit across five separate investigations this session.
5. **Does RANGE_BOUND need a separate playbook?** For MES: the evidence says yes, one that
   already exists and already works in observe-only form (`range_signal`) — this is the
   headline finding of this report. For MNQ: no — its version of the same mechanism loses
   consistently.
6. **Should trend remain a hard blocker?** As a single global rule, no longer clearly correct
   for MES specifically, given a real non-trending candidate now has evidence behind it. It
   should remain a hard blocker for MNQ, where nothing has been found to justify relaxing it.
   This is an instrument-specific answer, not a system-wide one.
7. **Are stops/targets the actual failure?** Not shown as the primary failure mode anywhere in
   this session's evidence (PR #240's target-variant and wider-stop sweeps don't show a
   dominant tight-stop or over-ambitious-target problem across the board) — the more
   consistent finding across five audits is a routing/eligibility question (what gets a chance
   to trade at all), not an exit-mechanics question.
8. **Smallest safe behavior change worth demo-testing later, if any?** Not something to build
   now — this audit's own constraints (no `demo_proof`, no production change) hold. If the
   operator later wants to pursue it: the smallest conceivable next step would be a config-only
   change narrowly admitting MES `range_signal`/RANGE_BREAK_CLOSE setups on RANGE_BOUND bars
   into the SAME observe-only shadow evaluation that already runs today (zero routing change,
   just keep collecting real evidence with awareness of this finding) — not a live-order change
   of any kind, and not something this report is recommending be built yet, only naming as the
   smallest possible next increment if the operator chooses to pursue it.

## Scope

Docs-only. Zero `execution/`/`risk/`/`config/`/`webhook/`/`broker*`/`strategy/`/`main.py`/
`risk_rules.yaml` diff. No `proof_builder`/`demo_proof` code. No script added — Section A is
real box journal analysis (not reproducible/committable, same precedent as PRs #242/#243);
Sections C/D/E cite already-existing, already-tested code from PR #240. No gates loosened, no
stops widened, no strategy posture changed, no demo/live order routing.
