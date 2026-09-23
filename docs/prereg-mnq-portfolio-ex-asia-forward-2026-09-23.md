# MNQ Five-Family Shared-Account Portfolio (ex-Asia D+EMA), Forward — Preregistration (2026-09-23)

**RESEARCH / AUDIT ONLY. NO EXECUTION AUTHORITY.** Nothing in this document
enables, disables, promotes, demotes, or re-parameterizes any strategy, lane,
risk rule, broker path, or deployment. The live runtime stays PAPER / OBSERVE
only and `LIVE_TRADING_ENABLED=false` is not in scope.

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
