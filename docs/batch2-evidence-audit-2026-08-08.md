# Batch 2 research — evidence-system audit

**Date:** 2026-08-08
**Scope:** three questions only — multiple-testing validity, replay field parity, IOC fill realism.
**Boundary honoured:** no runtime, strategy, risk, broker, config, execution, deployment, or
merge-state change. Everything added is new evidence tooling under `research/`.

---

## VERDICT

**The measuring instrument is in worse shape than the strategies it measures.**

Two of the three questions returned proven defects in the evidence system itself. The third
returned the opposite of what was expected and **removes** a standing blocker.

| | Question | Result |
|---|---|---|
| Q1 | Multiple-testing / null-baseline validity | **PROVEN INSUFFICIENT.** The null band answers a question nobody is asking. |
| Q2 | Replay observability / field parity | **PROVEN DEFECTIVE.** 11 verdict-relevant fields and 3 record types are unreachable in replay; 5 more are unreachable everywhere. |
| Q3 | IOC / same-bar fill realism | **MIXED, and BLOCKER CLEARED.** The fill model is conservative on path risk and optimistic on cost. The "destroyed" #354–#356 evidence regenerates *exactly*. |

The single most consequential finding is Q3's: the corrected-IOC corpus evidence was
regenerated today and matched the committed result **row for row**. The standing
"reproducibility BLOCKED" hold rested on the loss of *output logs*. The *inputs* were never
lost.

---

## Q1 — Multiple-testing / null-baseline validity

### What was verified

**The repo contains no multiple-testing tooling of any kind.** Zero matches across all 2,726
Python files for `deflated`, `bonferroni`, `false.discovery`, `null_baseline`, `multiple.test`,
or Monte-Carlo trial correction. The BINDING rule *"never report a PF without its null band"*
has no implementation; the band is a recorded number, not a reusable procedure.

**One methodology document exists and it is good** —
`docs/prereg-context-permission-layer-analysis-plan-2026-07-16.md`. Its §12 commits to
Holm–Bonferroni at family-wise α = 0.05 over exactly 22 pre-committed tests, permutation
p-values (10,000 permutations, shuffled within session × strategy strata), mandatory
walk-forward sign agreement, and an out-of-sample window with a materially-different-regime
requirement. That is a stricter design than most published quant work.

**It was never implemented, and it covers one study.** It does not cover the 41,750-candidate
shadow lane, the strategy-family evidence passes, or any of the parameter sweeps in
`research/` (19 scripts) and `scripts/` (76 scripts).

### The actual defect: the null band answers the wrong question

The recorded band — p5 1.02 / median 1.47 / p95 1.94, with 91.8% of random runs profitable —
describes **one** random run:

> P(a single random run has PF ≥ x)

After searching M strategy variants and reporting the best, the question that governs is:

> P(the **best** of M random runs has PF ≥ x)

Under independence these are related exactly, with no distributional assumption:

    P(max of N ≤ x) = F(x)^N

so a family-wise false-positive rate of α requires clearing the single-run quantile
q = (1 − α)^(1/N). Computed by `research/multiple_testing.py`:

| N independent trials | P(≥1 null run beats the recorded p95) | Single-run quantile needed for FWER 5% |
|---:|---:|---:|
| 1 | 5.00% | 95.000000% |
| 5 | 22.62% | 98.979378% |
| 10 | 40.13% | 99.488380% |
| **22** | **67.65%** | **99.767120%** |
| 50 | 92.31% | 99.897466% |
| 100 | 99.41% | 99.948720% |
| 41,750 | ~100% | 99.999877% |

At **22** trials — the prereg's own family size, the most charitable count available — a pure
noise process beats the p95 more often than not. The threshold that would actually control
error is the band's 99.767th percentile. **The band was never measured that far into its own
tail, so the correct threshold is not merely unmet; it is unmeasured.**

Trials are of course correlated. Bailey & López de Prado's Appendix A.3 Eq. (9) handles that:

    N̂ = ρ̄ + (1 − ρ̄)·M

Even at ρ̄ = 0.99 — every shadow family behaving as essentially one strategy — 41,750
candidates still imply **418 independent trials**, requiring the 99.988th percentile.
There is no plausible correlation assumption under which the recorded p95 is the right bar.

### Sources used

| Ref | Source | Provenance |
|---|---|---|
| S1 | Bailey & López de Prado (2014), *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality*, JPM 40(5). `davidhbailey.com/dhbpapers/deflated-sharpe.pdf` | 1,048,118 bytes / 22 pp / SHA-256 `ca4a1e834a2954b84eb560776e903329cd9cef318d458aadf6d67bc8166d10b5` |
| S2 | Bailey, Borwein, López de Prado & Zhu (2015), *The Probability of Backtest Overfitting*. `davidhbailey.com/dhbpapers/backtest-prob.pdf` | 1,145,522 bytes / 34 pp / SHA-256 `d8bfbadaaedb430d9dec929646d92f3580fdf18238f34121ae7738fa1c69a63d` |

Formulas taken verbatim from S1: Eq. (1) expected maximum Sharpe after N independent trials;
Eq. (2) DSR; Appendix A.3 Eq. (9) implied independent trials. S1's own framing of the
statistic: *"DSR is a PSR where the rejection threshold is adjusted to reflect the multiplicity
of trials."* S2 supplies PBO/CSCV — it requires a matrix of per-configuration performance time
series, which this repo does not currently export, so PBO is **applicable in principle,
not runnable today**.

Harvey & Liu (2015), *Backtesting* — the third canonical reference — could not be retrieved.
Every mirror attempted (CME education PDF, two Duke faculty paths) returned a transport
failure or HTML. **Its specific numeric hurdles are therefore NOT cited here.** The Q1
conclusion does not depend on it.

### Repo files inspected

`docs/prereg-context-permission-layer-analysis-plan-2026-07-16.md` (full),
plus repo-wide searches over `research/`, `scripts/`, `ops/`, `strategy/`.

### Reproductions run

`research/multiple_testing.py` — 7 independent verifications, all passing: Holm and
Benjamini–Hochberg against hand-computed adjusted p-values; PSR reduced to the plain
t-statistic under Normality; Eq. (1) recomputed by hand at N = 10 (agreement to 1e-12);
monotonicity in N; exact round-trip of the quantile/FWER pair; both limits of Eq. (9);
MinTRL returning +inf when SR ≤ benchmark.

### Proven defects

1. **The null band is a single-run distribution used as a best-of-M threshold.** This is
   textbook selection bias — the error DSR exists to correct.
2. **No trial ledger exists.** M is not recorded anywhere, so the correction cannot be applied
   retroactively even now. This is the binding blocker for Q1, not the missing math.
3. **The prereg's own correction was never implemented** and is scoped to a study that appears
   never to have run.

### Invalidated assumptions

- *"Beat the null band's p95 and the result is meaningful."* False beyond a handful of trials.
- *"The null-band rule is a multiple-testing control."* It is not. It is a single-trial
  sanity check. Both are needed; only one exists, and it is the weaker one.

### Evidence classifications affected

**No classification flips to a worse verdict.** Every strategy currently carries BROKEN,
WAIT, REJECT, or UNPROVEN — none is asserted as a proven edge, so there is nothing for a
stricter threshold to demote. Concretely:

- The shadow lane (pooled PF 0.944, ceiling 1.02 pre-cost) is *below* the null median of 1.47.
  Multiple-testing correction makes an already-negative result more negative. No change.
- MES 2-1-2's best-ever PF sat at the 50th percentile of chance. No change.
- **What is affected is any FUTURE promotion.** The correction must be in place before
  anything is promoted, not after.

### Tooling fixes justified / made

**Made:** `research/multiple_testing.py` — Holm–Bonferroni, Benjamini–Hochberg, PSR, DSR,
expected-maximum-Sharpe, MinTRL, implied-independent-trials, and the null-band order-statistic
bridge. Pure stdlib; imports nothing from runtime. The order-statistic functions apply to the
corpus **as it stands today** and need no new simulation.

**Justified but NOT made** (needs a decision, not just code): a **trial ledger** — every
evidence script recording what it searched and how many variants. Without it, M is
unrecoverable and Q1 stays permanently un-closable.

### Remaining blockers

- **BLOCKED BY DATA — M is unrecorded.** The corrections are implemented; the input is not.
- **BLOCKED BY DATA — DSR/PSR need a return series.** The repo reports profit factor.
  Bridging requires exporting per-trade R (the raw-trades JSONL already carries per-trade P&L,
  so this is small work, not new evidence).

---

## Q2 — Replay observability / field parity

### What was verified

Both known instances were confirmed and **precisely re-characterised**, and new ones were
found. `research/replay_field_parity.py` performs the audit statically over all 33 journal
write sites (21 live-lane, 7 replay-lane, 5 shared).

**Written by live, never by replay — 11 fields:**
`broker_status_raw`, `cancel_timestamp`, `exit_mode`, `no_fill_reason`, `order_ids`,
`order_type`, `requested_entry`, `seconds_until_cancel`, `signal_timestamp`, `stop`,
`submit_timestamp`.

`replay/replay_engine.py:774` calls `journal.log_outcome()` with twelve arguments and none of
the above — even though `PaperBroker` has already computed `no_fill_reason` and `order_type`
onto the `Fill`. **Replay computes them and discards them at the journal boundary.**

**Read by evidence code, written only on the live lane — 3 record types:**
`SHADOW_OUTCOME` (`strategy/shadow_resolver.py:224,242`), `ORDER_IDS`
(`webhook/runner.py:1300,2599`), `BAR_CLAIM` (`webhook/runner.py:880`).

**Declared in the schema but never given a value on any path, live included — 5 fields:**
`last_price_at_submit`, `last_price_at_cancel`, `best_bid_at_submit`, `best_ask_at_submit`,
`ticks_moved_from_entry`. `webhook/runner.py:2461-2465` passes each as a literal `None` with
an honest comment; `tests/test_no_fill_journal_fields.py:88` asserts they stay null.

### The three failure modes are distinct, and only one is "blindness"

**1. Structural mismatch — `SHADOW_OUTCOME`.** The prior record said replay never writes it.
That is not quite right and the correction matters. `replay/replay_engine.py:488` **does**
resolve shadow candidates via `resolve_shadow_candidate()`, storing the result at
`decision["shadow_candidates"][i]["outcome"]`. Every reader —
`ops/evidence_report.py:168`, `ops/evidence_readiness.py:67`, `webhook/app.py:1859` —
filters on `record["type"] == "SHADOW_OUTCOME"`. Same data, different shape, invisible.
This is recoverable by an analysis-layer reshape; no new collection is needed.

**2. Genuine blindness — `pine_has_bracket`.** `strategy/signal_engine.py:1060` derives it
from `state.raw`, the TradingView webhook payload. Replay synthesises state from candles, so
`raw` never carries `entry`/`stop`/`target`, and the entire Pine-bracket override branch
(`_apply_advisory_bracket`, called at `:1499`) is unreachable. Replay therefore evaluates a
**different bracket** than live for any Pine-bracketed strategy. This is the mechanism behind
the MNQ inverse-ORB `ENTRY_DETACHED` finding, and it is not recoverable from existing data.

**3. False confidence — `entry_status`.** This is the worst of the three because it does not
look empty. `execution/paper_broker.py:335` hardcodes `entry_status="dead"`, so replay's
`no_fill_reason` is **always** `NO_FILL_PRICE_MOVED_AWAY` and `NO_FILL_LIMIT_TOO_PASSIVE` is
structurally unreachable. Live Tradovate polls real order status
(`execution/tradovate_broker.py:1135-1183`) and can produce either. A no-fill taxonomy computed
over replay journals is measuring a constant while presenting as a distribution.
`execution/no_fill_taxonomy.py` is itself honest about this — it maps unknowns to
`NO_FILL_UNKNOWN` rather than guessing — but the *caller* asserts a certainty it does not have.

### Sources used

None external. This question is answered entirely from the repo; no outside authority could
settle what this codebase does. The static-analysis approach (writer/reader differencing) is
standard test-double-fidelity practice, not a cited result.

### Repo files inspected

`journal/journal_logger.py`, `replay/replay_engine.py`, `webhook/runner.py`,
`execution/paper_broker.py`, `execution/no_fill_taxonomy.py`,
`execution/tradovate_broker.py`, `strategy/signal_engine.py`, `strategy/shadow_resolver.py`,
`ops/evidence_report.py`, `ops/evidence_readiness.py`, `webhook/app.py`.

### Reproductions run

`research/replay_field_parity.py` — independently rediscovered every hand-audited field and
found four the hand audit missed (`exit_mode`, `order_ids`, `stop`, `requested_entry`). One
false positive it initially produced (`BAR_CLAIM` reported as never written) was traced to the
tool's own lane-attribution rule and fixed before this report; the corrected tool attributes
record types to the lanes that **call** the writer method rather than to `journal_logger.py`,
which merely defines them.

### Proven defects

1. Replay discards `no_fill_reason` / `order_type` it has already computed.
2. Replay's shadow outcomes are unreadable by every shadow reader (shape, not absence).
3. Replay's no-fill cause is a hardcoded constant presenting as a measurement.
4. The Pine-bracket path is unreachable in replay, so brackets can differ live vs replay.
5. Five microstructure fields are schema-only and have never held a value anywhere.

### Invalidated assumptions

- *"Replay and live journals are the same shape, so a reader works on both."* False for at
  least 11 fields and 3 record types.
- *"An empty result from a replay-derived evidence reader means no events occurred."*
  It may mean the reader can never match.
- *"`SHADOW_OUTCOME` is unearnable from replay."* Too strong. The data exists; the schema
  differs. Recoverable at the analysis layer.

### Evidence classifications affected

- **Shadow lane** ([[shadow families]], 41,750 candidates): the "readers BLIND" characterisation
  should be amended from *unearnable* to *unreadable-as-shaped*. The verdict (no edge) does not
  change — it was reached from the candidates directly, not through the readers.
- **Any no-fill-cause statistic computed over replay journals** is void: it is a constant.
- **Any Pine-bracketed strategy evaluated only in replay** carries an unquantified bracket
  divergence. This is consistent with the existing MNQ inverse-ORB NON-EXECUTABLE finding and
  reinforces it.

### Tooling fixes justified / made

**Made:** `research/replay_field_parity.py`, with its limits printed on every run — it cannot
see semantic divergence (the `entry_status` case) or structural nesting (the `SHADOW_OUTCOME`
case), and a clean run is explicitly *not* proof of parity.

**Justified but NOT made** (each touches runtime or a canonical artifact):
- Pass the fields replay already has (`no_fill_reason`, `order_type`, `signal_timestamp`)
  through `replay/replay_engine.py`'s `log_outcome` calls. Pure additive journaling —
  but it is a runtime file, so it is frozen.
- Have replay emit real `SHADOW_OUTCOME` rows, or add a reshaping adapter at the reader.
  The adapter is the freeze-safe option.
- Make `PaperBroker` emit `entry_status=None` (→ `NO_FILL_UNKNOWN`) instead of asserting
  `"dead"`. Honest, one line, and still a runtime change.

### Remaining blockers

- **FROZEN, not blocked.** Every repair is small and known. The standing directive through
  2026-09-30 forbids the runtime edits; the reader-side adapter is the one piece that could
  proceed under evidence-tooling scope, and it needs an operator decision on whether reshaping
  replay output counts as evidence tooling or as changing the evidence.

---

## Q3 — IOC / same-bar fill realism

### What was verified

Reproduced in `research/fill_model_audit.py`, which constructs `PaperBroker` in memory and
prints each observed number.

**Conservative (correct, and better than expected):**

| | Behaviour |
|---|---|
| C3 | A bar straddling both stop and target resolves as the **stop**. `config/settings.py:189` defaults `fill_pessimistic_both_hit = True`. Verified both branches. |
| — | Adverse slippage on every market fill; `fill_slippage_ticks` defaults to **1.0**, not 0. |
| — | An unmarketable IOC books `CANCELLED` / `ENTRY_NOT_FILLED` with no position and no session-budget consumption — the same way live books it. |
| — | A `stop_market` entry with no next-bar open fails closed (`ENTRY_OPEN_UNAVAILABLE`) rather than assuming a fill. |

**Optimistic:**

| | Behaviour |
|---|---|
| C1 | **No commission or exchange fees anywhere in `PaperBroker`.** `pnl_dollars = pnl_ticks × tick_value × contracts`, full stop. A 4-point MNQ winner books $8.00 gross and $8.00 net. A CME micro round turn is ~$1.24–$1.48 — **15–19% of that winner, and 62–74% of a $2.00 one**. Only two standalone research scripts model commission; every `PaperBroker`-derived number does not. |
| C2 | **A resting target fills on touch.** `next_bar.high >= target` books a clean `WIN` at exactly the target with zero slippage. CME Globex is price-time FIFO — per CME's own matching-algorithm documentation, *"resting orders are matched in timestamp order only"*, and orders lose priority on any price, size, or account change. A limit at a price the market merely touches fills only if the queue ahead of it clears. **Queue position is not modelled.** Stops are unaffected — those correctly slip. |
| C5 | **Zero submit latency.** `ioc_limit` treats the decision bar's **close** as the market on arrival (`replay/replay_engine.py:693` passes `candle.close`). Live, the order is constructed, risk-checked, and routed after that close. The tolerance cap bounds the damage; the fill price is still the close. Magnitude **unquantified** — see C6. |

**A causality error, biased the other way (C4):** for the `stop_market` model,
`resolve_position()` activates the pending entry and then falls through to resolve against
**the same bar** — including the portion that preceded the trigger. A long stop-entry at 100.00
on a bar that opens at 97.00, bottoms at 97.00, and rallies to 101.00 books a `STOP_HIT` loss
that could not have occurred. Direction is **pessimistic**, but it is still wrong and it is not
symmetric with live.

**Stop-before-target when the path is unknown is handled correctly** — that is C3, and it is
the single most important conservatism in the model.

### The validation gap (C6)

The fill model **cannot be validated against live behaviour**, because the live journal records
no market state at submit or cancel time. `last_price_at_submit`, `last_price_at_cancel`,
`best_bid_at_submit`, `best_ask_at_submit`, `ticks_moved_from_entry` are literal `None` on
every path. **Q3's validation is blocked by Q2's defect.** The two questions are not
independent.

### Can the destroyed #354–#356 evidence be rebuilt? — YES, PROVEN TODAY

This is the finding that changes the most.

The lost artifacts were the **run logs** under `/private/tmp`. The **inputs** were never lost:

| Input | Status |
|---|---|
| `data/replay_corpus_v1_market_condition_fixed` | **626 files, tree SHA-256 `4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4` — byte-identical to the value recorded in the canonical results meta.** |
| `risk_rules.yaml` @ `56677a0ab37bbf6277a895fd7ddb37351f8c2436c4e48debe9c9acfa3361d2e3` | Present at commit `69ec77f`, an ancestor of `origin/main`. |
| `scripts/corrected_ioc_corpus_evidence.py` | Committed at `69ec77f`. |
| `scripts/corrected_ioc_corpus_results.json`, `..._raw_trades.jsonl`, `docs/corrected-ioc-corpus-evidence-2026-07-26.md` | All committed and present. |

**Regenerated from `69ec77f` on 2026-08-08. Result:**

- `results.json` — **entire non-`meta` payload identical.** The only meta difference is
  `main_sha` (`69ec77f` vs the recorded `e8f2fe23`, which was main at the time of the original
  run while the script itself lived on the PR branch).
- `raw_trades.jsonl` — **all 165 rows identical** after dropping `paper_order_id`, a
  per-run `uuid4`.
- `report.md` — differs in exactly one line, the pinned-code SHA.

**The "#354–#356 reproducibility BLOCKED" hold is cleared.** Reproduction is deterministic and
verified. Its headline numbers stand as regenerated: net after commission −$802.28,
PF 0.752958 after commission (0.790381 before), expectancy −$8.27/trade, max drawdown
$1,073.61, 165 attempts.

### The trap that made this look blocked — and a real tooling defect

`scripts/corrected_ioc_corpus_evidence.py` records `risk_rules_sha256_before/after` but its
only internal guard is `before == after` **within one run**. It never checks either hash
against the run being reproduced.

PR #376 changed `risk_rules.yaml` from `56677a0a…` to `0325eefe…` (v1.2.0, isolating the MNQ
ORB Breakout inverse lane as repo default). **A re-run at today's HEAD passes every assertion
in that script while replaying a different `enabled_concepts` set — and would report success.**
That is how a reproduction attempt silently becomes a new, different experiment.

### Sources used

| Ref | Source | Status |
|---|---|---|
| S3 | CME Group Client Systems Wiki, *CME Globex Matching Algorithm Steps* (`cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457218521`) | Retrieved 2026-08-08. Quoted: *"During FIFO, resting orders are matched in timestamp order only"*; orders lose priority on increased quantity, price change, or account change; Display-Quantity orders return to the end of the queue after each match. |

**NOT retrieved, and therefore NOT relied on:** the CME *iLink — Order Qualifiers* page
(FAK/FOK semantics, tag 59 TimeInForce, minimum-quantity behaviour). Three retrieval paths
were attempted — direct fetch of the Confluence page, the `autocert-ilink` webhelp page, and
the in-app browser — and all returned navigation shells or transport timeouts. **The specific
claim "CME FAK ≡ the IOC semantics this repo simulates" is UNVERIFIED.** The Q3 conclusions
above do not depend on it; they rest on the FIFO/queue-priority facts in S3, which were
retrieved, and on the repo's own code.

### Repo files inspected

`execution/paper_broker.py` (full), `ops/fill_realism.py` (full),
`execution/no_fill_taxonomy.py` (full), `replay/replay_engine.py` (fill paths),
`config/settings.py` (fill-model defaults and loader),
`scripts/corrected_ioc_corpus_evidence.py`, `webhook/runner.py` (execution-failure journaling).

### Reproductions run

1. `research/fill_model_audit.py` — six checks, each printing its observed number.
2. Full regeneration of the corrected-IOC corpus evidence from `69ec77f`, diffed against the
   committed artifacts (results above).
3. `research/verify_ioc_corpus_reproducibility.py` — exercised in **both** directions:
   exit 1 with the correct diagnosis from a HEAD-tree worktree, exit 0 from the pinned one.

### Proven defects

1. **No commission/fee model in the core paper/replay fill path.** Material at micro-contract
   scale.
2. **Target fills on touch with no queue position.** Upper-bound optimistic on every win.
3. **`stop_market` entry-bar look-back** resolves against pre-trigger price (pessimistic, but
   acausal).
4. **The reproduction script does not pin the provenance hashes it records**, so a re-run at
   the wrong commit silently succeeds as a different experiment.
5. **Submit-time market state is never captured**, so none of the above can be measured
   against live.

### Invalidated assumptions

- *"The corrected-IOC evidence is unreproducible and no parity claim may be made until it is
  rebuilt."* The premise was wrong. It is rebuilt, and it matches.
- *"`ops/fill_realism.py` is the fill model."* It is a read-only journal reporter with no model
  in it. The model is `execution/paper_broker.py`.
- *"The fill model is uniformly conservative."* It is conservative on **path** risk and
  optimistic on **cost** and **queue**.

### Evidence classifications affected

- **Corrected IOC Corpus v1 (#346) — edge BROKEN under honest IOC:** unchanged, and now
  **reproducible on demand**. Its status improves from *unverifiable* to *verified*.
- **Every `PaperBroker`-derived PF in the corpus is overstated by the omitted commission.**
  Directionally this makes already-failing strategies worse; it cannot rescue one. Two research
  scripts that do model commission ($1.24–$1.48 round turn) are the exception.
- **Any win-rate or PF that leans on target-touch fills is an upper bound**, not an estimate.

### Remaining blockers

- **NOT blocked by data.** Q3(c) is closed affirmatively.
- **Blocked by instrumentation** for latency and queue effects: quantifying C2 and C5 requires
  submit-time market state, which is the Q2 defect. Nothing can be measured here until that is
  fixed.

---

## Which question most changes confidence in the evidence base

**Q3 — and in the direction opposite to what was expected.**

Q1 and Q2 both say "the instrument is unreliable," but neither changes a single existing
verdict. Everything in the corpus is already BROKEN, WAIT, REJECT, or UNPROVEN. A stricter
significance bar and a leakier replay both make negative results *more* negative. They matter
enormously for the *next* promotion and not at all for the current ledger.

Q3 changed something concrete today: a standing hold was resting on a false premise. Evidence
believed destroyed was regenerated and matched row for row. That is the only finding in this
batch that moves a decision.

The second-order lesson is worth as much: the loss was of **outputs**, and outputs are cheap.
The corpus, the pinned config, the orchestration script, and the recorded provenance hashes
were all preserved — so the result was never actually at risk. What *was* at risk, and remains
so, is the ability to *notice* a failed reproduction: the script records provenance without
enforcing it.

---

## Which existing strategy classifications need re-review

**None require a verdict change.** Three require an annotation:

1. **Shadow lane** — amend "readers BLIND / gate unearnable from replay" to
   "replay resolves outcomes but stores them under a shape no reader matches." The no-edge
   verdict is unaffected; the recoverability assessment changes from impossible to
   analysis-layer.
2. **Corrected IOC Corpus v1 (#346)** — promote from *evidence unverifiable* to
   *evidence verified, regenerated 2026-08-08*, with the reproduction recipe pinned to
   `69ec77f` and the corpus tree hash recorded.
3. **Every PaperBroker-derived PF** — annotate as pre-commission. Not a re-review; a caveat
   that should travel with the number.

Deliberately **not** re-opened: the shadow lane's no-edge verdict, MES 2-1-2, and the MNQ
inverse-ORB NON-EXECUTABLE finding. Q1 and Q2 make each of those *more* firmly negative.

---

## Exact next research question

> **What is M?** Build a trial ledger: for every evidence artifact in the repo, recover how
> many strategy/parameter/instrument/timeframe variants were searched before that result was
> reported — and where the count is unrecoverable, record it as unrecoverable rather than as
> one.

This is the binding blocker Q1 surfaced and could not resolve. The corrections are implemented
and verified; the input they consume does not exist. Until M is known, no multiple-testing
correction can be applied to anything already in the corpus, and Q1 stays permanently open.

It is deliberately narrower than "apply multiple-testing correction to the corpus." That larger
task is unexecutable until this one is done.

**Deferred to Batch 3 as previously ranked, unchanged:** greeks/IV provenance and maximum
defensible age; partial-interval HTF bar contamination.

---

## Tooling added by this batch

All under `research/`. Read-only, stdlib-only where possible, importing no runtime module
except `execution/paper_broker.py` (constructed in memory, never connected).

| File | Purpose |
|---|---|
| `research/multiple_testing.py` | Holm–Bonferroni, BH, PSR, DSR, expected-max-Sharpe, MinTRL, implied independent trials, and the null-band order-statistic bridge. |
| `research/replay_field_parity.py` | Static writer/reader differencing of journal fields and record types across the live and replay lanes. Prints its own limits. |
| `research/fill_model_audit.py` | Reproduces each fill-model conservatism/optimism claim with observed numbers. |
| `research/verify_ioc_corpus_reproducibility.py` | Preflight that refuses a reproduction attempt from the wrong tree and names the commit to use. |

No runtime, strategy, risk, broker, config, execution, or deployment file was modified.
No merge, deploy, or PR was made.
