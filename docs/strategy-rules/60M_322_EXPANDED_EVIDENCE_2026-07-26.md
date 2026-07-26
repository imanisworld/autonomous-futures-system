# MNQ 60M 3-2-2 First Live — Expanded Evidence Study (2026-07-26)

**Status: PROMISING BUT UNPROVEN.** No configuration, execution, risk, or
deployment behavior changed. This is a research-only evidence report.

## 0. Why this study looks different from what was originally asked for

The task that produced this document started from a false premise that had to
be corrected before any evidence work could begin, and the correction
happened twice more mid-study. All three corrections are documented here so
the trail is auditable.

### 0.1 The premise contradiction

The operator's original brief described the MNQ 60M 3-2-2 detector, its
reconciliation, and its honest-fill baseline as **already settled on `main`**,
citing exact figures (32 setups / 20 fills / 17W-3L / net **+$1,537.70** / PF
**8.00** / H1 $1,086.88 / H2 $450.82 / LONG $1,108.36 / SHORT $429.34 /
1-4 tick slippage $1,557.70/$1,537.70/$1,517.70/$1,498.20).

This is false on current `main`. As of this study, `docs/strategy-rules/
Strategy_Inventory.md` and `docs/strategy-rules/README.md` both still classify
60M 3-2-2 as **"WAIT — build detector,"** citing only the old *manual,
external* study (n=31, +$66.50 expectancy) with an explicit gap note: *"no
coded detector, walk-forward halves not confirmed, slippage sensitivity
unknown."* No PR for a 3-2-2 detector has ever existed in this repository's
GitHub history (`git log --all --grep` across all branches finds none; the
only committed 3-2-2 artifact on `main` is the rules document itself).

The real source of the cited numbers was traced to `docs/strategy-rules/
HONEST_FILL_REPLAY_RESULTS.md` and three research modules
(`research/detector_322_first_live.py`, `research/reconcile_322_first_live.py`,
`research/replay_322_honest_fill.py`) that exist **only** on a dangling,
never-PR'd branch `codex/4hr-reconciliation` at commit
`fa7babd05a0dc41e580792665dc0c503839e0184` (merge-base with `main` is
`b7d8e8603e31fb1d5e4b5cd503f86a9fc0924a73`, 2026-07-23; the branch is 11
commits ahead of that base and stops there — abandoned in-flight work, never
merged).

**Operator-approved resolution:** read the codex branch's research files as
reference only (`git show`, never checked out or built on), port the same
logic into this new `claude/*` branch as an independently-verified rewrite,
verify it reproduces the documented baseline, then proceed to the expanded
evidence study. That is what this document reports.

### 0.2 Evidence-integrity correction (mid-study)

The original instruction also said to treat exact reproduction of the old
$1,537.70/PF-8.00 numbers as a pass/fail gate. The operator corrected this
mid-study: **the abandoned branch's numbers were produced under its own
defective EOD-handling** (see §2 below — it substituted `eligible[-1].close`
for any unresolved trade instead of ever recording `EOD_BAR_MISSING`). Once
that defect is fixed, the old numbers are not a valid target to reproduce —
doing so would mean either under-fixing the defect to hit the old number, or
falsely claiming the corrected code reproduces a result it was never trying
to match. The corrected instruction: run a **legacy-semantics reproduction**
purely as a porting-fidelity check (labeled provenance-only, never treated as
valid evidence), then run the **corrected canonical baseline** as the real
Group 1 result for the rest of the study, reporting whatever numbers that
produces — even if they differ from the old figures.

### 0.3 Shared market-condition parity blocker (mid-study)

A separate, concurrent audit on `main` confirmed that runtime/Pine and the
legacy replay `market_condition` field disagree on 33,635/47,066 bars
(27,967 replay-`TRENDING` bars would not be `TRENDING` under the Pine
formula), and that the global signal-engine gate requires `TRENDING` to admit
a trade. **This does not affect this study's candidate generation, fills, or
core metrics**, verified directly:

```
$ grep -n "market_condition\|TRENDING\|trend_direction\|trend_strength" \
    research/detector_322_first_live.py \
    research/replay_322_honest_fill.py \
    research/reconcile_322_first_live.py
# (no output — zero matches, confirmed by exit code 1)
```

`detect_322_first_live()` is a pure 7AM/8AM/9AM/10AM OHLC pattern detector
with no market-condition precondition (matches `docs/strategy-rules/
Detector_Specifications.md`, "Detector 3 — 60M 3-2-2 First Live"), and
`replay_322_honest_fill.py`'s fill/exit mechanics run purely on raw OHLC bars
with no `ReplayEngine` or TRENDING-gate dependency. The blocker only touches
Robustness Question #10 below (regime-dependency), where it is called out
explicitly per field.

**Update (same day):** the shared fix landed as PR #338, merged to `main` at
`0057bc23ca02719f89558cbc4100947fab59720b` ("Fix replay market-condition
parity"). This branch was rebased onto that commit before finalizing (`git
rebase origin/main`, fast-forward, no conflicts — this branch had no prior
commits of its own). PR #338 rematerialized `data/replay_corpus_v1` (both
MNQ and MES, 626 files, 47,066 bars, 0 mismatches after) to a canonical-value
copy, confirming the `reconstructed_market_condition` field this study had
already been reading (§5, Question #10) is bit-identical to the corrected
engine-facing `market_condition` value — i.e. the 12 candidate dates that
fall inside `replay_corpus_v1`'s coverage were already tagged with
parity-valid values in this study's first pass, independently reconfirmed
against the rematerialized corpus post-fix (spot-checked all 12 dates
directly; identical results, see Question #10 for the updated wording). PR
#338 did **not** rematerialize `data/replay_polygon`/`data/replay_polygon_5m`
(a different, older cache with no `reconstructed_*` fields at all) — those
remain on the legacy heuristic for the 22 candidate dates before
`replay_corpus_v1`'s 2025-07-24 coverage starts, which stay labeled
diagnostic-only in Question #10, for a different, still-unresolved reason
(no canonical field exists for that cache, not that the known fix is
unapplied to it).

This lane's `research/replay_322_honest_fill.py` also does not use
`ReplayEngine` at all (it is a standalone, day-independent function — no
shared broker/engine instance, no cross-day state), so the separately-reported
cross-day position carry-forward defect in `replay/replay_engine.py` (see
`AGENT_HANDOFF.md`, 2026-07-26 entries) does not apply to this lane either;
this strategy is day-only and flattens by 4PM ET as designed.

---

## 1. Port verification

`research/detector_322_first_live.py` and `research/reconcile_322_first_live.py`
were ported **verbatim** — diffed line-by-line against `git show
fa7babd:research/<file>.py`; the only difference in either file is an added
provenance docstring, confirmed via `diff`.

`research/replay_322_honest_fill.py` was ported with one intentional fix (§2).

Ported test suites: `tests/test_detector_322_first_live.py`,
`tests/test_reconcile_322_first_live.py` (both verbatim), and
`tests/test_replay_322_honest_fill.py` (ported, with the EOD-related test
renamed to match the fixed behavior, plus 4 new tests added for
`EOD_BAR_MISSING`). All 35 tests pass on Python 3.13 / pytest 9.1.1 with no
compatibility fixes required. Full repo suite: **3768 passed, 4 skipped, 0
failed** (`pytest -q`), confirming no regressions versus `origin/main`.

The original external human-study manual-entries CSV that
`reconcile_322_first_live.py` was designed to reconcile against **was never
committed anywhere in this repository's history** (searched all commits on
all branches; none found). Formal TP/FP reconciliation against that external
study could not be repeated. Detector correctness is instead established via:

1. The full ported unit-test suite (35/35 passing), covering every branch of
   the pattern logic (outside-bar checks, direction resolution, gap-open vs.
   live-break entry pricing, no-cap stop, non-MNQ rejection, missing-bar
   fail-closed, invalidation).
2. **Hand-verification against raw source bars** for the specifically-named
   spot-check date, **2024-08-30**: resampling the raw 15-minute rows by hand
   produces 7AM (H 19546.25/L 19513.0), 8AM outside bar (H 19574.75/L
   19508.25), 9AM breaking only the high (19603.0 > 19574.75, 19513.0 is NOT
   < 19508.25) → **SHORT**, trigger 19513.0, and a 10AM low of 19466.5 that
   breaks the trigger. The ported detector reproduces this exactly, and this
   date is confirmed present with `direction=SHORT` in both the legacy and
   corrected candidate runs.
3. Two DST-transition dates (2024-11-04, fall-back; 2025-03-10, spring-forward)
   were hand-checked to confirm the 15m→60m resampler anchors correctly on ET
   wall-clock hours across the transition (each produced exactly 4 sub-bars
   per hour with the expected UTC offset).

### 1.1 Legacy-semantics reproduction (provenance-only — NOT valid evidence)

Run using the **unmodified** codex-branch source (`git show
fa7babd:research/{detector,replay}_322_first_live.py`, copied to a throwaway
location, never committed to this repo), against the identical
2024-07-02..2026-06-26 MNQ data used for the corrected canonical run:

| | Documented (abandoned branch) | This reproduction (legacy semantics) | Delta |
|---|---:|---:|---:|
| Candidates | 32 | 34 | +2 |
| Fills | 20 | 21 | +1 |
| W/L | 17/3 | 18/3 | +1W |
| Net P&L | $1,537.70 | $1,546.46 | +$8.76 |
| PF | 8.00 | 8.04 | +0.04 |
| H1 net | $1,086.88 | $1,086.88 | **exact match** |
| H2 net | $450.82 | $459.58 | +$8.76 |
| LONG net | $1,108.36 | $1,108.36 | **exact match** |
| SHORT net | $429.34 | $438.10 | +$8.76 |
| Slippage 1/2/3/4 ticks | $1,557.70/$1,537.70/$1,517.70/$1,498.20 | $1,567.46/$1,546.46/$1,525.46/$1,504.96 | +$9.76 each |

The divergence is exactly one extra trade: **2026-06-11 SHORT, net +$8.76**,
plus one extra non-filled candidate contributing $0. H1 and LONG match to the
penny; every diverging figure differs by exactly $8.76 (one trade's net P&L).
Both extra candidates fall at the very end of the study window
(2026-06-26 cutoff), consistent with the original ephemeral run having used a
data snapshot captured a few days earlier than this environment's current
`data/replay_polygon` cache (the original run's exact input snapshot was
never committed, so this cannot be fully confirmed — but the unit-test suite,
the named spot-check, and the exact-match H1/LONG splits together are strong
evidence the port itself is faithful, and the divergence is a data-snapshot
artifact rather than a logic difference). This legacy-semantics result is
**not used anywhere in the rest of this study** — it exists solely to confirm
the port was done correctly, per the operator's provenance-only instruction.

---

## 2. The EOD-handling defect, and its fix

**Defect (abandoned branch):** `_resolve_exit()` treated "no stop/target hit
by 16:00 ET" as an `"EOD"` exit priced at `eligible[-1]["close"]` —
i.e. whatever bar happened to be last in the eligible window — even when that
bar was not actually the canonical 15:55-16:00 ET day-only-exit bar (e.g. a
day whose 5-minute feed ends early or has a gap at that exact bar). This
silently manufactured a price for a bar that was never confirmed to be the
one the strategy's own rules require.

**Current main's settled contract** (`docs/strategy-rules/
60M_322_FirstLive_Rules.md`, "Common Day-Only Exit — 4:00 PM ET", established
by the shared day-only-exit foundation, PR #318 / `main@14e2af2`):

> On the 15:55–16:00 ET 5-minute bar, resolve the canonical stop or target
> first if either is reached. If the position remains unresolved, close it
> with exit reason `DAY_ONLY_FLATTEN` at the close of that exact bar. If that
> exact bar is missing, record `EOD_BAR_MISSING` as unresolved evidence — do
> not estimate or substitute a price, and do not count a `WIN`, `LOSS`, or
> `BREAKEVEN`.

**Fix scope decision:** `execution/day_only_exit.py` already implements this
contract, but its top-level imports pull in `execution.broker_interface` and
`execution.paper_broker` — runtime broker code that `replay_322_honest_fill.py`'s
own docstring says this research module must never depend on. Per the
operator's explicit guardrail (do not touch shared production/replay/
execution code even to reuse it conveniently), the contract was
**reimplemented locally** inside `replay_322_honest_fill.py`, using the same
exit-reason string constants (`DAY_ONLY_FLATTEN`, `EOD_BAR_MISSING`) so the
vocabulary matches across replay and runtime without an import.

**Effect on the canonical baseline:** in the corrected 2024-07-02..2026-06-26
run, one trade (**2025-01-20, SHORT**) changed classification. Under the old
defective logic it was priced as an `"EOD"` **loss of -$49.24**. Under the
corrected logic, its exact 15:55 ET bar is genuinely absent from the data —
confirmed by direct inspection of `data/replay_polygon_5m/MNQ/MNQ_2025-01-20.jsonl`,
which has 5-minute bars for the normal morning session through ~12:55 PM ET
and then jumps straight to an 18:00 PM ET evening/overnight session, with
**no bars at all in the 1:00 PM–6:00 PM ET window**. 2025-01-20 is Martin
Luther King Jr. Day, a CME-observed holiday with a modified/early-closed day
session — this is a genuine holiday data gap, not a bug. The trade is now
correctly recorded as `EOD_BAR_MISSING` / `UNRESOLVED` (net_pnl = `None`,
excluded from win/loss/PF/expectancy-per-fill, contributing $0 — not silently
dropped — to expectancy-per-signal), which **removes a fantasy loss** and
increases net P&L to $1,595.70 (see §3). This is the correction, worked end
to end on real data, functioning exactly as designed.

---

## 3. Data-coverage decision — OOS EXPANSION BLOCKED BY DATA COVERAGE

Verified directly in this environment (not assumed from any prior report):

| Cache | Instrument | Granularity | Confirmed range | File count |
|---|---|---|---|---|
| `data/replay_polygon/MNQ` | MNQ | 15m, full day | 2024-07-01 .. 2026-06-26 | 622 |
| `data/replay_polygon_5m/MNQ` | MNQ | 5m, RTH-only | 2024-07-02 .. 2026-06-26 | 621 |
| `data/replay_corpus_v1/MNQ` | MNQ | 15m, full day (+reconstructed columns) | 2025-07-24 .. 2026-07-23 | 313 |

`data/replay_corpus_v1` was not pre-symlinked into this worktree (unlike the
other two caches) — it was located at `/Users/djb.a.e/MAINVSCODE/
autonomous-futures-system/data/replay_corpus_v1` on this machine and
symlinked in read-only for this study (`data/replay_corpus_v1 ->
.../autonomous-futures-system/data/replay_corpus_v1`), touching nothing in
the main checkout.

`replay_corpus_v1` extends ~4 calendar weeks past the other two caches'
2026-06-26 cutoff (through 2026-07-23), but **only at 15-minute granularity**.
An exhaustive search of this environment (`find` across the whole data tree
and adjacent worktrees) found **no 5-minute-granularity MNQ cache anywhere**
covering 2026-06-27 through 2026-07-23, and no MNQ 5-minute data before
2024-07-02 either.

The 60M 3-2-2 honest-fill replay requires 5-minute bars for two causal
reasons that cannot be satisfied by coarser data: (1) recovering the first
strict trigger crossing in the 10:00–11:00 ET entry window, and (2) resolving
the exact 15:55–16:00 ET day-only-flatten bar. Substituting 15-minute bars
would require guessing the intra-15-minute price path — exactly the kind of
lookahead/optimistic-fill invention this study is required to avoid.

**Conclusion: OOS EXPANSION BLOCKED BY DATA COVERAGE.** The original
32(→34)-candidate cohort already exhausts the locally available MNQ history
at both required granularities (15m detector input + 5m replay input).
Genuine historical expansion is blocked on acquiring new 5-minute MNQ bar
data beyond 2026-06-26 and/or before 2024-07-01 — both outside this task's
scope. Per the study's own instructions, this is reported as a complete,
legitimate outcome, not a failure to finish: **Group 2 = 0 candidates**,
**Group 3 = identical to the corrected Group 1**, and every robustness
question below is answered using only the corrected 34-candidate cohort,
explicitly labeled as constrained by data availability.

MES was not used to increase sample size (out of scope; MES stays explicitly
excluded for 3-2-2 per `Strategy_Inventory.md`, unchanged on current `main`).

---

## 4. Required metrics — three result groups

### Group 1: Corrected canonical baseline (2024-07-02 .. 2026-06-26)

Base case, 2-tick slippage. Full JSON:
`docs/strategy-rules/evidence_322/group1_corrected_baseline.json`.

| Metric | Overall | LONG | SHORT | H1 (2024-07-02..2025-06-29) | H2 (after 2025-06-29..2026-06-26) |
|---|---:|---:|---:|---:|---:|
| Candidates | 34 | 17 | 17 | 22 | 12 |
| Fills | 21 | 11 | 10 | 13 | 8 |
| NO_FILL (`ENTRY_NOT_FILLED`) | 13 | 6 | 7 | 9 | 4 |
| `POST_FILL_INVALID_STOP` | 0 | 0 | 0 | 0 | 0 |
| `EOD_BAR_MISSING` (unresolved) | 1 | 0 | 1 | 1 | 0 |
| Resolved fills | 20 | 11 | 9 | 12 | 8 |
| Wins | 18 | 11 | 7 | 12 | 6 |
| Losses | 2 | 0 | 2 | 0 | 2 |
| Win rate (of resolved) | 90.0% | 100.0% | 77.8% | 100.0% | 75.0% |
| Profit factor | 10.36 | undefined (0 losses) | 3.86 | undefined (0 losses) | 3.70 |
| Expectancy / filled trade | $79.79 | $100.76 | $54.15 | $94.68 | $57.45 |
| Gross P&L | $1,660.50 | $1,144.00 | $516.50 | $1,175.00 | $485.50 |
| Total costs | $64.80 | $35.64 | $29.16 | $38.88 | $25.92 |
| **Net P&L** | **$1,595.70** | **$1,108.36** | **$487.34** | **$1,136.12** | **$459.58** |
| Max drawdown | $167.24 | $0.00 | $170.48 | $0.00 | $167.24 |
| Avg win | $98.12 | $100.76 | $93.97 | $94.68 | $105.01 |
| Avg loss | -$85.24 | n/a | -$85.24 | n/a | -$85.24 |
| Largest win | $221.26 | $180.76 | $221.26 | $169.76 | $221.26 |
| Largest loss | -$167.24 | n/a | -$167.24 | n/a | -$167.24 |
| Max consecutive wins | 7 | 5 | 3 | 7 | 2 |
| Max consecutive losses | 1 | 0 | 2 | 0 | 1 |

**Slippage sensitivity (net P&L / PF, overall):**

| Ticks | Net P&L | PF |
|---|---:|---:|
| 1 | $1,615.70 | 10.59 |
| 2 (base) | $1,595.70 | 10.36 |
| 3 | $1,575.70 | 10.14 |
| 4 | $1,556.20 | 9.92 |

**Additional chronological partitions (quarters):**

| Quarter | Candidates | Fills | Net P&L |
|---|---:|---:|---:|
| 2024-Q3 | 7 | 2 | $194.02 |
| 2024-Q4 | 4 | 3 | $281.78 |
| 2025-Q1 | 6 | 4 | $345.78 |
| 2025-Q2 | 5 | 4 | $314.54 |
| 2025-Q3 | 5 | 3 | $307.28 |
| 2025-Q4 | 2 | 1 | -$167.24 |
| 2026-Q1 | 1 | 0 | $0.00 |
| 2026-Q2 | 4 | 4 | $319.54 |

**Yearly distribution:**

| Year | Candidates | Fills | Net P&L |
|---|---:|---:|---:|
| 2024 (partial, from Jul) | 11 | 5 | $475.80 |
| 2025 | 18 | 12 | $800.36 |
| 2026 (partial, through Jun 26) | 5 | 4 | $319.54 |

**Monthly distribution:** 17 months have at least one candidate (of the ~24
months in range); full table in
`docs/strategy-rules/evidence_322/group1_extended_metrics.json` ->
`monthly_distribution`. **Best month: 2025-03, +$290.02. Worst month:
2025-10, -$167.24** (the single STOP loss).

**Top-N winner concentration:**

| | Trades | Contribution | Share of net P&L |
|---|---|---:|---:|
| Top 1 | 2025-09-05 SHORT | $221.26 | 13.9% |
| Top 3 | + 2026-05-13 LONG, 2024-10-10 LONG | $571.78 | 35.8% |
| Top 5 | + 2025-03-17 SHORT, 2025-03-21 LONG | $861.80 | 54.0% |

**Gap-open representation:** **0 of 34 candidates were gap-open.** Every
candidate resolved via a live intrabar break of the 9AM boundary during the
10:00–11:00 ET window. Gap-open pricing logic (`entry_price = ten["open"]`
when the market opens beyond the trigger) is fully covered by unit tests but
has **zero real historical occurrences** in this entire ~2-year sample —
directly relevant to Robustness Question #9 below.

### Group 2: Newly added out-of-sample historical evidence

**0 candidates — OOS EXPANSION BLOCKED BY DATA COVERAGE** (§3). No 5-minute
MNQ data exists outside 2024-07-02..2026-06-26 anywhere in this environment.

### Group 3: Combined evidence

**Identical to Group 1** (Group 2 is empty). See
`docs/strategy-rules/evidence_322/group3_combined.json`.

---

## 5. Robustness questions (all 11, answered against the corrected Group 1 cohort)

1. **Does the strategy remain profitable outside the original 32-setup
   cohort?** Not testable — no genuine out-of-sample MNQ data exists at the
   required granularity (§3). The corrected cohort itself (34 candidates, 2
   more than originally documented) is not a new sample; it is the same
   population re-detected against the same cache, with 2 additional
   near-boundary dates that a slightly different data snapshot happened to
   include.
2. **Do both major chronological halves remain positive?** Yes. H1 net
   $1,136.12 (12 resolved fills, 12W/0L), H2 net $459.58 (8 resolved fills,
   6W/2L). Both positive, though H1's win rate (100%) is unusually clean for
   only 12 observations.
3. **Do LONG and SHORT remain independently viable?** Both are net positive
   (LONG $1,108.36, SHORT $487.34), but **LONG is 11-for-11 with zero
   losses** across the entire sample — a profit factor of "undefined" (no
   losing trades to divide by) is not evidence of a stronger edge, it is
   evidence of a small, lucky-so-far sample. SHORT carries both losses in the
   dataset (including the single STOP and the single EOD_BAR_MISSING). This
   asymmetry is a genuine concentration/robustness concern, not a strength.
4. **Is the edge stable across multiple months/years?** Directionally yes —
   positive in all three partial/full years (2024/2025/2026) and 6 of 8
   quarters, with one clearly negative quarter (2025-Q4, -$167.24, driven
   entirely by the single STOP loss) and one flat quarter (2026-Q1, zero
   fills). With only 20 resolved fills spread across 2 years, "stability"
   here means "no catastrophic quarter," not statistical confidence.
5. **Is profitability concentrated in a small number of trades?** Yes,
   materially. The top 5 of 18 winning trades (28% of winners, 25% of
   resolved fills) account for **54.0%** of total net P&L. This matches the
   same concentration pattern the abandoned branch's own diagnostic flagged
   (its top-3 figure was 37.2% of a slightly different, uncorrected cohort).
   Concentration is real and persists after the EOD correction.
6. **Is profitability concentrated in one short market period?** No single
   quarter or year dominates to the point of carrying the whole result — the
   largest single quarter (2025-Q1, $345.78) is 21.7% of net P&L, not a
   majority — but see #5: concentration is at the *trade* level (5 trades,
   54%), not the *period* level.
7. **Does the edge survive adverse-slippage sensitivity?** Yes, comfortably.
   Net P&L only declines $39.50 (2.5%) from 1 tick ($1,615.70) to 4 ticks
   ($1,556.20), and PF stays above 9.9 throughout. This is the most
   unambiguously positive robustness result in the study.
8. **Is max drawdown controlled relative to expectancy?** Yes on its face —
   max drawdown $167.24 versus expectancy/fill $79.79 (about 2.1x), and that
   drawdown is a single trade (the one STOP loss), not a losing streak (max
   consecutive losses = 1 overall). But with only 20 resolved fills, one more
   adverse trade of similar size would materially change this ratio.
9. **Are gap-open cases represented in real historical evidence?** No — zero
   of 34 candidates were gap-open. The gap-open entry-pricing branch of both
   the detector and the replay engine is unit-tested but has never actually
   fired once in ~2 years of MNQ history in this dataset. This is a real,
   previously-undocumented gap in the evidence: a rule that exists and is
   coded correctly, but has no live confirmation.
10. **Are there meaningful market-condition or regime dependencies?**
    **Updated post-PR #338** (shared market-condition parity fix, merged to
    `main@0057bc2`, this branch rebased onto it before finalizing — see
    §0.3). For the 12 candidates that fall within `data/replay_corpus_v1`'s
    coverage (2025-07-24 onward), the canonical, parity-valid tag is now
    **confirmed, not merely diagnostic**: 8 `TRENDING`, 4 `RANGE_BOUND`,
    directly re-verified against the PR's own rematerialized corpus
    (`/private/tmp/replay_corpus_v1_market_condition_parity/MNQ/`, produced
    by `scripts/rematerialize_market_condition_corpus.py` as part of #338)
    — identical to what this study's first pass already reported from
    `reconstructed_market_condition`, because PR #338's own regression proof
    established that field was already bit-identical to the corrected
    canonical value (0 mismatches across 47,066 bars); it simply wasn't
    wired into `ReplayEngine` before. **The "diagnostic only" caveat is
    dropped for this portion of the sample.** Two of those 12 dates
    (2025-10-16 and 2026-05-13) are legacy-tagged `TRENDING` but
    canonical-tagged `RANGE_BOUND` — a direct, small-scale confirmation of
    the same parity gap the shared audit found at system scale, now
    corrected. For the other 22 candidates (before 2025-07-24), **the
    caveat still applies, for a different and still-open reason**: PR #338
    rematerialized only `data/replay_corpus_v1`, not the older
    `data/replay_polygon`/`data/replay_polygon_5m` caches this study's
    candidate/replay pipeline actually reads — those caches have no
    `reconstructed_*` field at all to rematerialize from, so no canonical
    tag can be produced for them without a separate regeneration effort
    (out of scope here). Using the **legacy** `market_condition` field for
    those 22 dates (and, for comparability, restated across the full
    34-candidate set): 28 tagged legacy-`TRENDING`, 6 tagged
    legacy-`CHOPPY` — this strategy's setups predominantly occur in
    legacy-labeled trending conditions, unsurprising for an outside-bar/
    breakout pattern, but for the pre-2025-07-24 majority of the sample this
    remains directional/diagnostic only, not canonical. No regime filter was
    applied anywhere in candidate generation or scoring; this section is
    read-only diagnostics per the operator's explicit instruction not to
    introduce a gate 3-2-2's own rules never had. Full per-date tags:
    `docs/strategy-rules/evidence_322/group1_extended_metrics.json` ->
    `regime_tags`.
11. **Does any evidence suggest the original 32-trade sample was unusually
    favorable?** No new leave-out comparison is possible (the exact original
    32-date list was never committed, only aggregate figures). What can be
    said: the corrected 34-candidate cohort contains the same top-3 winners
    the abandoned branch's own diagnostic named (2025-09-05 SHORT, 2026-05-13
    LONG, 2024-10-10 LONG, matching amounts to the cent) plus 2 additional
    near-boundary trades, and shows the same concentration profile (~35-54%
    from the top 3-5 trades) the original study already flagged. There is no
    evidence the *correction itself* introduced favorable bias — if anything
    it removed a fantasy loss (§2) — but the underlying sample remains small
    (20 resolved fills) and its own internal diagnostics (LONG's perfect
    11-0 record, 54% top-5 concentration) are exactly the kind of pattern
    that should raise suspicion of a favorable-so-far small sample rather
    than a proven edge.

---

## 6. Classification: PROMISING BUT UNPROVEN

Consistent with the abandoned branch's own prior conclusion ("PAPER PROOF" —
its internal term for what this repo's current taxonomy calls PROMISING BUT
UNPROVEN), and unchanged by this independent re-verification:

- **For:** positive in both halves, both directions, 6 of 8 quarters, and
  all three years; survives 1-4 tick slippage with PF staying above 9.9;
  drawdown is a single, isolated trade; the corrected EOD fix removed a
  fantasy loss rather than manufacturing a win (i.e. the correction made the
  result *more* honest, not more favorable by construction).
- **Against VALIDATED:** sample size did not and could not materially expand
  this study (§3, OOS EXPANSION BLOCKED BY DATA COVERAGE); 20 resolved fills
  over ~2 years is thin; LONG's perfect 11-0 record and 54% top-5
  concentration are unresolved small-sample-luck concerns; gap-open handling
  has zero live confirmation; Robustness Question #10 cannot be answered
  cleanly system-wide due to the shared market-condition parity blocker (it
  is fully answerable for this specific detector because the detector
  doesn't use that field, but the broader regime-dependency question remains
  partially diagnostic-only).

None of VALIDATED's required conditions (materially expanded sample,
temporal robustness beyond what a ~2-year/34-candidate cohort can show,
freedom from winner concentration, no unresolved parity issue anywhere in the
picture) are met. The realistic ceiling here, as anticipated going in, is
**PROMISING BUT UNPROVEN** — equivalently, WAIT FOR MORE DATA.

---

## 7. Reproduction

Requires Python 3.11+ (developed/tested on 3.13) and this repo's
`requirements.txt`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# unit tests
python3 -m pytest tests/test_detector_322_first_live.py \
  tests/test_reconcile_322_first_live.py \
  tests/test_replay_322_honest_fill.py -v

# full corrected study (writes docs/strategy-rules/evidence_322/*.json)
python3 -m research.run_322_expanded_evidence
python3 -m research.report_322_extended_metrics
```

**Data dependency (read this before trying to reproduce):** this study reads
three git-ignored local JSONL bar caches that are **not present in a fresh
clone**:

- `data/replay_polygon/MNQ/MNQ_<date>.jsonl` (15-minute, full day,
  2024-07-01..2026-06-26)
- `data/replay_polygon_5m/MNQ/MNQ_<date>.jsonl` (5-minute, RTH-only,
  2024-07-02..2026-06-26)
- `data/replay_corpus_v1/MNQ/MNQ_<date>.jsonl` (15-minute, full day,
  2025-07-24..2026-07-23 — used only for the Question #10 Pine-faithful
  regime tags, not for candidate/replay generation)

A future operator needs to either (a) regenerate these from the same Polygon
source pipeline this repo's other research modules already use
(`scripts/polygon_to_replay.py` and the corpus-v1 equivalent — not modified
by this study), or (b) obtain a copy of the caches from another machine/branch
where they already exist (as this study did, by symlinking
`data/replay_corpus_v1` in read-only from the adjacent main checkout).
Without these caches, `research.run_322_expanded_evidence` will run to
completion but silently produce zero candidates (missing-day bars are treated
as "no data for this date," fail-closed, matching the detector's own
missing-bar semantics — it will not error, it will just find nothing).

---

## 8. File manifest

New files (this branch):
- `research/detector_322_first_live.py` — ported verbatim (+ provenance docstring)
- `research/reconcile_322_first_live.py` — ported verbatim (+ provenance docstring)
- `research/replay_322_honest_fill.py` — ported + `EOD_BAR_MISSING` fix (§2)
- `research/bars_322_polygon_loader.py` — new: 15m→60m ET resampler, 5m loader
- `research/run_322_expanded_evidence.py` — new: study driver (Groups 1/2/3)
- `research/report_322_extended_metrics.py` — new: monthly/yearly/quarterly/top-N/gap-open/regime analysis
- `tests/test_detector_322_first_live.py` — ported verbatim
- `tests/test_reconcile_322_first_live.py` — ported verbatim
- `tests/test_replay_322_honest_fill.py` — ported + renamed EOD test + 4 new `EOD_BAR_MISSING` tests
- `docs/strategy-rules/evidence_322/group1_corrected_baseline.json` — evidence artifact
- `docs/strategy-rules/evidence_322/group2_oos_expansion.json` — evidence artifact (empty, reasoned)
- `docs/strategy-rules/evidence_322/group3_combined.json` — evidence artifact
- `docs/strategy-rules/evidence_322/group1_extended_metrics.json` — evidence artifact
- `docs/strategy-rules/60M_322_EXPANDED_EVIDENCE_2026-07-26.md` — this document

No runtime, config, risk, broker, or deployment file was touched. No file in
`Strategy_Inventory.md`/`README.md`'s existing "WAIT — build detector" entry
was edited by this study (the detector now exists in research form only;
promoting the strategy's documented verdict is an operator decision, out of
scope here).
