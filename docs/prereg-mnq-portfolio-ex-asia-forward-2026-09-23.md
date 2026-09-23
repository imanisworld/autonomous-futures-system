# MNQ Five-Family Shared-Account Portfolio (ex-Asia D+EMA), Forward — Preregistration (2026-09-23)

**RESEARCH / AUDIT ONLY. NO EXECUTION AUTHORITY.** Nothing in this document
enables, disables, promotes, demotes, or re-parameterizes any strategy, lane,
risk rule, broker path, or deployment. The live runtime stays PAPER / OBSERVE
only and `LIVE_TRADING_ENABLED=false` is not in scope.

> **Amended 2026-09-23, before the scoring start (see §9).** The forward bar
> source, gap handling, warm-up and three evaluator mechanics changed. The
> hypotheses, family set, shared-account rules, thresholds, minimum sample,
> deadline and single look are unchanged. Where §9 and an earlier section
> disagree, §9 wins. The original text is kept for the audit trail.

## 1. What we already know (frozen as of registration)

PR #915 (closed, audit-only; preserved as
`archive/pr915-mnq-combined-portfolio-audit-5a9f14b`, SHA `5a9f14baf714947b98a38a19b45f04a8d18fb365`)
replayed six frozen MNQ families in one chronological shared account
(one open position, 3 fills per CME observation day, $5,000 normalization)
over the 290-day common window **2025-07-24 → 2026-06-26**:

| Portfolio | Fills | Net | PF | Max DD |
|---|---|---|---|---|
| All six families | 488 | +$5,138 | 1.18 | $2,984 |
| &nbsp;&nbsp;H1 / H2 (by fill count) | 242 / 242 | +$6,323 / **−$1,184** | 1.77 / **0.94** | |
| Leave out Asia D+EMA | 60 | +$9,373 | **2.08** | $1,773 |

Asia D+EMA took 445 of 488 fills and caused 1,554 busy-skips of other
families. Removing it improved net by $4,234. Daily 2-2 completed-close
contributed most (+$2,926 leave-one-out delta; standalone +$7,847, PF 2.04,
n=17).

**The ex-Asia row is a post-hoc selection.** It was chosen after seeing the
leave-one-out table, it rests on 60 fills, and its PF sits barely above the
frozen null hurdle (null p95 PF 1.94). It is the *motivation* for this
document, not evidence for it. **Nothing observed before the scoring start in
§3 is scored.**

## 2. Hypotheses (fixed)

**H1 (primary).** The five-family MNQ portfolio below, run in one shared
chronological account under the frozen #915 rules, clears the standard
futures evidence hurdle on forward data it has never seen.

Families (identities frozen exactly as in #915; do not re-implement, retune,
or harmonize brackets):

1. `4HR_RETRIGGER`
2. `60M_322_FIRST_LIVE`
3. `DAILY_22_COMPLETED_CLOSE`
4. `12HR_MIYAGI`
5. `SUSTAINED_TREND_V1` (frozen at #912 tip `cd50f42`)

**H2 (secondary, cannibalization).** On the same forward window, the ex-Asia
five-family portfolio earns more net P&L than the six-family portfolio that
also includes `ASIA_D_EMA`. This tests whether the #915 cannibalization finding
repeats out of sample. H2 is descriptive of portfolio construction only; it is
not a verdict on the Asia D+EMA forward paper cohort, which keeps its own
ledger and its own evaluation.

## 3. Data (forward only)

**Scoring start:** the first completed MNQ 5m bar with timestamp
**≥ 2026-09-23T22:00:00Z** (CME observation day beginning 18:00 ET Wednesday
2026-09-23). A portfolio fill counts only if its causal signal *and* its fill
are both at or after this instant.

**Historical window 2026-06-27 → 2026-07-23 is NOT scored.** It lies outside
the #915 common window, but #911, #912 and the 313-day grid have already
examined those bars. It would also yield only about 4 expected ex-Asia fills.

**Bar sources (box, collected forward):**

- 5m: `logs/tf5m/bars_MNQ_*.jsonl` (collected since 2026-07-01)
- 15m: `logs/bars_MNQ_*.jsonl` (collected since 2026-06-05)

The #915 family adapters read enriched replay-corpus rows. The forward
population is those box bars converted into the same corpus row format, using
the **existing** corpus enrichment path, never a new re-implementation. If the
existing path cannot rebuild corpus rows from raw bars, the study fails closed
as `INSUFFICIENT_DATA_PIPELINE` and no P&L is computed.

**Step 0: parity gate. It must PASS before any forward row is scored, and it
never looks at forward P&L.**

The box bars overlap the frozen historical corpora for
2026-07-01 → 2026-07-23 (5m) and 2026-06-05 → 2026-07-23 (15m).

| Check | PASS if |
|---|---|
| 0a. OHLC parity, 5m, box bars vs `replay_corpus_v1_5m` | ≥ 99.5% of overlapping bars match O/H/L/C within 1 tick; no RTH gap > 2 consecutive bars in the box series that the corpus does not also have |
| 0b. OHLC parity, 15m, box bars vs `replay_corpus_v1_market_condition_fixed` | same thresholds as 0a |
| 0c. Candidate parity. Run each frozen #915 family adapter over the rebuilt overlap corpus and over the frozen corpus. | Identical candidate identities, signal timestamps, directions, entry, stop and target for every family. Every mismatch is listed; any unexplained mismatch fails the gate. |
| 0d. 4HR-audit lineage. The 4HR, 3-2-2, Miyagi and Daily adapters historically read `replay_corpus_v1_5m_4hr_audit`, which ends 2026-06-26 and never overlaps the box bars. Run those four adapters over `replay_corpus_v1_5m` and over `replay_corpus_v1_5m_4hr_audit` on their shared dates. | Identical candidate streams, or every difference explained by a documented corpus-construction change that does not favor either result |

If step 0 fails, fix the *pipeline* (not a family) and re-run step 0. Every
attempt is recorded in §6. Step 0 may be re-run before the look; the look
itself happens once.

**Evaluator.** Reuse `research/mnq_combined_portfolio_audit.py` and
`scripts/mnq_combined_portfolio_audit.py` from `5a9f14b`. The only permitted
changes are:

- accept a corpus directory and a date range as inputs;
- drop `ASIA_D_EMA` from the family set for H1;
- the tie order below.

Portfolio mechanics, fill models, costs, and family adapters stay unchanged.
The implementation lives under `research/` and `scripts/` only, lands as its
own PR, and must pass step 0 in CI-reproducible form before its first look.

## 4. Shared-account rules (frozen; identical to #915 except the family set)

- MNQ only; one contract per fill.
- **One open MNQ position total** across all families.
- **Maximum 3 filled trades per CME observation day** across all families.
- A signal arriving while a position is open → `SKIPPED_BUSY_PORTFOLIO`. A
  signal arriving after the third fill of the day → `SKIPPED_MAX_TRADES_PORTFOLIO`.
- Earliest causal fill timestamp wins. Exact-timestamp ties use the fixed
  administrative order
  `4HR_RETRIGGER → 60M_322_FIRST_LIVE → DAILY_22_COMPLETED_CLOSE → 12HR_MIYAGI → SUSTAINED_TREND_V1`
  (the #915 order with Asia removed). For H2's six-family comparator,
  `ASIA_D_EMA` returns to its #915 position.
- Each family keeps its own frozen signal timing, entry, stop, target, fill
  model, slippage, commission, holding horizon and EOD behavior. Daily 2-2 may
  hold across sessions and occupy the account.
- $5,000 starting balance, used for drawdown normalization only.
- No standalone P&Ls are added together. Only the chronological shared-account
  stream is the result.

## 5. Minimum sample and decision rules (fixed)

**Expected pace.** #915's ex-Asia row gave 60 fills in 290 trading days, about
5 per month. The minimum sample below is therefore expected around
**May 2027**. That pace is stated now so a slow accumulation is not later read
as a reason to relax anything.

**Single look.** The P&L look happens once, when **both** of these hold:

- ≥ **40 terminal portfolio fills** (resolved to stop, target, time or EOD
  exit; open positions do not count), **and**
- ≥ **120 CME observation days** since the scoring start.

Before the look, only operational counts may be read: fills by family, skips,
days, and pipeline health. Nobody reads net, PF, drawdown, or win/loss before
the look. Operational counts are allowed because the minimum sample depends on
them.

**Deadline.** If the minimum sample is not reached by **2027-09-30**, the look
happens then and H1 is `INSUFFICIENT_SAMPLE`, whatever the P&L.

**H1 decision:**

| # | Criterion (all required for PASS) |
|---|---|
| 1 | Net P&L after each family's frozen costs > $0 |
| 2 | Profit factor ≥ **1.94** (frozen null p95) |
| 3 | Both chronological halves (split by terminal-fill count) net > $0 |
| 4 | Max drawdown ≤ **$1,750** (35% of $5,000) |
| 5 | The three best filled days contribute ≤ **60%** of net P&L |

- **PASS:** all five hold → `FORWARD_PORTFOLIO_EVIDENCE`
- **FAIL:** net ≤ $0, *or* PF < 1.94, *or* any of criteria 3–5 fails →
  `FORWARD_PORTFOLIO_REJECTED`
- **INSUFFICIENT_SAMPLE:** deadline reached with fewer than 40 terminal fills
  or 120 days

**H2 decision** (same look, same window):

- **CONFIRMED** if ex-Asia net > six-family net **and** the six-family run
  shows `ASIA_D_EMA` busy-skipping at least one fill that the ex-Asia run took.
- **NOT CONFIRMED** otherwise.

**Reported regardless of outcome (descriptive only, never gates):**

- per-family fills, busy-skips, max-trades skips;
- each family's leave-one-out delta inside the five-family portfolio;
- Daily 2-2 share of net P&L and of occupied account-hours;
- monthly net/PF and max consecutive losses;
- the net P&L if the whole fill stream took +1 tick of adverse slippage per
  side.

## 6. What is NOT allowed

- Scoring any bar or fill before 2026-09-23T22:00:00Z.
- Changing any family's detector, bracket, fill model, costs, or tie order.
  Removing a second family after the look is also forbidden: that would be a
  new post-hoc selection and needs its own prereg.
- Reading P&L, PF, drawdown, or win/loss before the single look.
- Moving the thresholds in §5, the minimum sample, or the deadline.
- Substituting another corpus, fetching new vendor data, or backfilling box gaps
  to rescue a failed step 0. A gap that step 0 cannot bridge fails closed.
- Treating any H1 or H2 result as runtime authority. A PASS permits only a
  separate, operator-approved proposal under the standing change rule:
  prereg, evidence, staged rollback, explicit GO.

## 7. Outcome (to be filled once, at the look)

- Step 0 attempts (date, result, mismatches):
- Look date / terminal fills / CME days:
- H1 criteria 1–5 values:
- H1 verdict:
- H2 values / verdict:
- Descriptive table:

## 8. Ownership

Registered by Claude (auditor lane) on operator instruction, 2026-09-23. The
evaluator adapter is a separate follow-up PR under `research/` + `scripts/`.
No merge, deploy, restart, paper activation, DEMO activation, or broker action
is authorized by this document.

**No proof, no run.**

## 9. Amendment 1 (registered 2026-09-23, before the scoring start)

**Why.** The evaluator was built and step 0 was run on the box bars. It
failed: 0a 97.65%, 0b 90.86%, 0c 271 mismatches; 0d passed. The cause was
the bar source, not the families:

- The box 5m lane was switched off on purpose from 2026-07-03 to 07-12.
- Whole-feed outages lost bars on 07-28, 08-14, 09-14 and 09-21.
- The TradingView 5m alert did not fire on 09-03.
- About 2% of live-captured closes are 2–4 ticks off the settled close.

Under the original §6 the study could not survive the next outage.

**What was seen before this amendment.** Only data-quality facts: bar
coverage, OHLC agreement, and which enriched fields differ between sources.
The blind counts run showed 0 scored fills, because scoring has not started.
**No forward P&L, PF, drawdown, win/loss, entry, stop or target has been
computed or read.**

### 9.1 Forward bar source (replaces the "Bar sources" paragraph of §3)

- Forward bars come from the **Polygon futures API**, fetched with the
  unchanged `scripts/polygon_to_replay.py` path. That path uses
  `PolygonFuturesClient.fetch_continuous`, the continuous front contract with
  `DEFAULT_ROLL_DAYS = 8`, and `derive_candles`.
- This is the same source and code path that built the frozen `replay_corpus_v1*`
  corpora. On the step-0 overlap, a fresh fetch matched the frozen corpora
  exactly: 5m 4,644/4,644 bars and 15m 3,188/3,188 bars identical in O/H/L/C,
  with 0 corpus bars missing.
- The box's live-captured bars (`logs/tf5m/`, `logs/bars_MNQ_*`) are **no longer
  scored**. They stay the paper bot's runtime input, which this study does not
  evaluate.
- **Settlement delay:** a CME observation day is used only if it ended at least
  24 hours before the fetch.
- **Every run re-fetches the whole window fresh.** Earlier fetches are never
  mixed in or cached across runs.
- The look records the fetch time and the SHA-256 of the raw bar files it used.
- 5m and 15m are fetched separately at their native resolution. Neither is
  resampled from the other.

### 9.2 Warm-up (replaces the open `--corpus-start` question)

- The loaded corpus starts **60 calendar days** before the first day it scores.
  Candles are then derived as `polygon_to_replay` does, with its own 10-day
  pre-roll on top.
- For the forward run the corpus starts **2026-07-25**. Candidates before
  2026-09-23T22:00:00Z are still dropped before the shared-account replay (§3).
- For step 0 the rebuilt overlap uses `--warmup-days 60`. With that setting
  every EMA field matches the frozen corpora exactly. With the 10-day default,
  `ema_200` drifted until 07-13.

### 9.3 Gap days (new, fixed; no filling of any kind)

- A **gap day** is a CME observation day (18:00 ET roll) where the Polygon 5m
  or 15m series is missing a bar that the CME Globex MNQ schedule says should
  trade.
  - The schedule is Sunday–Friday 18:00–17:00 ET with the daily 17:00–18:00
    break.
  - Exchange holidays and early closes use the calendar in
    `scripts/csv_to_replay.detect_day_boundaries` / `cme_trading_day`.
  - A 5m/15m coverage disagreement is also a gap.
- Gap days are detected mechanically before any adapter runs. They are listed
  with their missing windows. Example known today: Fri 2026-09-11, 13:00–15:00
  and 16:00–17:00 ET, missing in both MNQU6 and MNQZ6.
- **Handling:**
  - Candles are derived over all delivered bars. Nothing is filled,
    interpolated, or taken from another source (box, TradingView, other
    vendor).
  - The gap day's candles are then removed from **both** the 5m and 15m
    corpora, over the whole loaded window including warm-up. The families
    see it like a market closure.
  - Any portfolio fill is `VOID_GAP_DAY` if its signal, fill or exit falls on
    a gap day, or it is open across one.
  - `VOID_GAP_DAY` fills are not terminal and are excluded from every §5
    number. They are reported with their count and family, but their P&L is
    never shown. Skips they caused stay as replayed.
  - Gap days do not count toward the 120 CME days.
- **Fail-closed cap:** if more than **10%** of the CME observation days in the
  scoring window are gap days at the look, H1 is `INSUFFICIENT_DATA`, whatever
  the P&L. H2 is then `NOT CONFIRMED`.

### 9.4 Step 0 (thresholds unchanged)

- **0a and 0b:** same windows and same thresholds (≥ 99.5% within 1 tick; the
  RTH gap rule). They now compare a fresh Polygon fetch (§9.1, §9.2) with the
  frozen corpora, not the box bars.
- **0c:** unchanged, except for one pre-declared explanation class.
  - The current `derive_candles` sets the day boundary differently from the
    code that built the frozen corpora. This shows on the CME days just after
    an exchange holiday or early close: 2026-06-21..06-23 after Juneteenth,
    and 2026-07-05..07-07 after July 3/4.
  - A 0c mismatch is explained by this class only if **both** hold:
    - it lies on those days;
    - it traces to the day-boundary fields `previous_day_*`, `daily_*`, `vwap`,
      `hod`, `lod`, `price_vs_pdh/pdl/vwap` or `ftfc_*`.
  - Such mismatches must still be listed one by one. Any other mismatch stays
    unexplained and fails 0c.
  - The class was identified from field-level diffs only, before any candidate
    comparison was run on Polygon data.
- **0d:** add the two lineages the evaluator found, with the same "identical, or
  documented construction change" test:
  - the 3-2-2 15m detector: `replay_polygon` vs
    `replay_corpus_v1_market_condition_fixed`;
  - the Miyagi fill corpus: `replay_polygon_5m` vs `replay_corpus_v1_5m`.

### 9.5 Evaluator mechanics (additions to the permitted changes in §3)

1. **12HR Miyagi candidates:** off the frozen corpora, candidates come from the
   unchanged detector `research/run_12hr_miyagi_evidence.detect_candidates`,
   pointed at the forward corpus. It reproduces the frozen #915 candidate file
   15/15.
2. **#915 control assertions:** these assert the frozen-corpus totals (for
   example "4HR fills == 80"). They are no-ops off the frozen corpora, and the
   control-only summaries are `None`-safe. Every other fail-closed guard still
   runs. That includes the 3-2-2 crosscheck and the #912 5m coverage guard,
   which §9.3 now satisfies by removing gap days from both timeframes.
3. The evaluator reads Polygon-rebuilt corpora (§9.1) instead of box bars.

### 9.6 §6 changes

- "Fetching new vendor data" is now allowed **only** as §9.1 describes. It is
  the registered forward source, not a rescue.
- "Backfilling box gaps" stays forbidden, and §9.3 extends that to any gap in
  any source.
- Every other line of §6 stands.
- Further amendments are forbidden after the scoring start.
