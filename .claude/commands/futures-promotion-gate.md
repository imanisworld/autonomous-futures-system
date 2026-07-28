# /futures-promotion-gate

Purpose:
Prevent a standalone research result (a detector + an offline replay script)
from being mistaken for evidence that the strategy actually works through the
system that would trade it. This is the gate between "detector produced
promising numbers" and "paper-ready" — narrower than `/futures-strategy-audit`
(which classifies edge given a trusted baseline) and narrower than
`/futures-paper-readiness` (which checks whether the *system* is safe to run
in paper mode at all, not whether *this strategy's* evidence is real).

Core rule: No proof, no run. A strategy may not be classified paper-ready from
detector/research output alone. It must show evidence that a historical
candidate set survives, in order, through:

```
historical candidate set
  -> ReplayEngine        (replay/replay_engine.py)
  -> DecisionEngine       (strategy/signal_engine.py — DecisionEngine)
  -> RiskEngine           (risk/risk_engine.py)
  -> PaperBroker          (execution/paper_broker.py)
  -> configured entry fill model   (ENTRY_FILL_MODEL / entry_fill_model, e.g. ioc_limit)
  -> configured account risk controls   (risk_rules.yaml daily/account limits,
                                          e.g. the 20% drawdown breaker)
```

A candidate set that only ever ran through a standalone research module
(`research/*.py` calling its own bespoke detector + a hand-rolled fill
simulator) has not demonstrated this — no matter how clean its numbers look.

## Step 0 — evidence discovery pass (required, before any new analysis or recommending any replay)

Do this before touching `research/*.py`, before proposing a `ReplayEngine`
run, and before trusting `Strategy_Inventory.md`'s label at face value.
`Strategy_Inventory.md` is written by hand and lags real work — this repo's
own history shows evidence-closure PRs sitting open, unmerged, for days
while the inventory still shows the old label (see the self-correction
example in Precedent below, which is exactly that failure caught in the
wild).

1. **Search PRs** — open, merged, and closed-unmerged — for the strategy
   name, its canonical ID (e.g. `strat_322_first_live`), and known aliases
   (e.g. "60M 3-2-2", "3-2-2 First Live"). Use the GitHub MCP tools
   (`search_pull_requests`, `list_pull_requests` with `state=all`) or `gh pr
   list --state all --search <term>`. Do not stop at merged PRs — this
   repo routinely does expensive, decisive evidence work in PRs that stay
   open for operator review before merge (see #365/#366/#367 below); a
   search scoped to merged-only will miss exactly the newest, most
   authoritative evidence.
2. **Search docs**: `Strategy_Inventory.md`, the relevant strategy README,
   everything under `docs/strategy-rules/`, and any `archive/*`-tagged
   historical evidence relevant to this strategy or its aliases.
3. **Order every source chronologically** (by PR/commit date, not by
   filename) and identify the **latest valid study** — "valid" meaning its
   methodology is sound on its own terms, not merely most recent.
4. **Never recommend repeating an experiment a newer evidence package
   already completed.** If a PR already ran the exact `ReplayEngine ->
   DecisionEngine -> RiskEngine -> PaperBroker` closure this gate would
   otherwise ask for, that closure is the answer — proposing it again as a
   "next step" is itself a finding of a broken gate, not a legitimate
   safe-next-step.
5. **When sources disagree, apply this precedence, strongest first:**
   ```
   PAPER FORWARD EVIDENCE
     > full executable runtime-parity evidence (ReplayEngine -> DecisionEngine
       -> RiskEngine -> PaperBroker actually run, with reported attrition)
     > isolated/standalone research (research/*.py, hand-rolled fill sim)
     > historical prose/docs (external studies, manual samples, old inventory rows)
   ```
   A later date does not override this order by itself — a fresh standalone
   research re-run does not outrank an older full-pipeline closure.
6. **Report, before anything else in this gate's output**:
   - `LATEST EVIDENCE FOUND`: what it is
   - `PR / REFERENCE`: number and title
   - `PR STATE`: open / merged / closed-unmerged, plus date — state matters,
     do not present an open, unreviewed PR's content as if it were already
     settled `main` state; report both what the evidence says and whether
     `main` has actually absorbed it yet
   - `DATE`
   - `METHODOLOGY`: one line
   - `VERDICT` as that source states it
   - Whether this **supersedes** `Strategy_Inventory.md`'s current label,
     and whether that inventory update has actually merged to `main` or is
     itself still sitting in the same unmerged PR (check `main` directly —
     do not assume a PR's stated intent to update the inventory means it
     already has)
7. **If a newer study already closes the requested proof gap: STOP.** Report
   that result instead of proposing another pipeline run. The only
   legitimate next step at that point is (a) getting the existing PR
   reviewed/merged, and/or (b) flagging the stale inventory label via
   `/daily-reconciliation`'s STRATEGY SOURCE OF TRUTH section — not new
   analysis.

## Precedent (cite, do not re-litigate)

**12HR Miyagi** and **60M 3-2-2 First Live** are the standing example of why
this gate exists — in two layers now, not one:

**Layer 1 (why the gate was built):** both had standalone historical
evidence that read as positive (Miyagi: MNQ 15 candidates / 8 resolved
fills / 7W-1L / PF 2.81; 3-2-2: 34 candidates / 21 fills / 18W-2L / PF
10.36) that came from `research/detector_*.py` + `research/replay_*_honest_
fill.py`, not from the production `DecisionEngine -> RiskEngine ->
PaperBroker` path. Separately, the isolated honest-fill passes for
`orb_breakout` and `vwap_reclaim` found that running a strategy through its
own fresh paper account (so its own account-level 20% drawdown breaker
reflects only its own P&L) can itself halt/reject a population well before
the historical window ends — a legitimate risk-control outcome, not a bug.

**Layer 2 (why Step 0 above exists):** both strategies have since had that
exact full-pipeline closure actually run, in **PR #366 (Miyagi)** and **PR
#367 (60M 3-2-2)** — both dated 2026-07-27, both titled "evidence closure,"
both concluding **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** (Miyagi:
0/8 MNQ and 2/10 MES causal-stop candidates fit inside `max_stop_ticks`;
3-2-2: 0/34 candidates reach a fill even in the most favorable hypothetical,
27/34 failing `max_stop_ticks` and 5/34 failing `min_confluence_grade` once
proven parity defects are removed). **Both PRs were open and unmerged** as
of this writing, and `main`'s `Strategy_Inventory.md` still read "PROMISING
BUT UNPROVEN" for both. A run of this exact gate against 3-2-2 on
2026-07-28 — before Step 0 existed — searched strategy files and the
(stale) inventory, found neither PR, and recommended running the closure
that PR #367 had already run the day before. That miss is the reason Step 0
is mandatory now, not optional: the gate must search PRs (including open,
unmerged ones) before recommending any new work, every time.

## Required evidence, by pipeline stage

For each stage, report the population count in and out, and account for the
delta:

- **Raw candidate count**: from the historical detector/scan, before any
  pipeline stage runs
- **Candidate identity parity**: do `ReplayEngine`'s candidates match the
  standalone research detector's candidates 1:1 (same bar, same instrument,
  same direction)? Report exact match count and any candidates present in one
  set but not the other
- **Direction parity**: LONG/SHORT assignment identical between the research
  detector and the production `DecisionEngine` for the same candidate
- **Candidates removed by each gate**: `ReplayEngine` (data/lookahead
  rejects), `DecisionEngine` (session/regime/permission gates), `RiskEngine`
  (per-check rejection counts — not just a total), entry fill model
  (no-fill/IOC-cap rejects), account risk controls (breaker trips, daily loss
  limit, position caps) — report each stage's removal count separately, not
  netted into one number
- **Candidates reaching RiskEngine**: count and % of raw
- **Candidates approved** (post-RiskEngine): count and % of raw
- **Entry attempts**: orders actually submitted to `PaperBroker`
- **Fills**: filled attempts, and fill rate against entry attempts
- **No-fills**: count, and taxonomy breakdown if available
  (`execution/no_fill_taxonomy.py`)
- **Resolved outcomes**: WIN/LOSS/BREAKEVEN count, and anything still open
- **Net P&L, PF, expectancy (per fill and per raw candidate — report both,
  they can disagree sharply), win rate**
- **H1/H2 walk-forward split**
- **Instrument split** (MES/MNQ) — do not let one instrument's result launder
  the other's into a combined verdict
- **LONG/SHORT split**
- **Recent-period behavior** (does the edge hold in the most recent quarter,
  or only historically)
- **Slippage sensitivity** where applicable (1-4 tick, per
  `/futures-live-replay-parity-audit`'s method)
- **Max drawdown** on the strategy's own trade sequence, not netted against
  other strategies
- **Top-winner concentration** (e.g. top-5 winners as % of net P&L — this
  system has repeatedly found 50-70%+ concentration on thin samples; report
  it even when not asked)
- **Exact risk controls that eliminate candidates** — name the specific
  `risk_rules.yaml` check/threshold and how many candidates it removed, not
  "risk rejected some trades"

If a strategy reaches **zero executable fills** at any stage, say so plainly
as the headline finding — do not bury it under partial-population statistics
computed on the candidates that did survive.

## Evidence classification (apply to every number cited)

Every metric in this report must be tagged with exactly one of:

- **RESEARCH RESULT** — produced by a standalone `research/*.py` detector
  and/or a hand-rolled fill simulator, not the production pipeline. Context
  only. Never sufficient for PROMISING BUT UNPROVEN or better.
- **RUNTIME PARITY** — the candidate set has been run through the actual
  `DecisionEngine -> RiskEngine -> PaperBroker` path (via `ReplayEngine` or
  live/demo), proving the strategy's logic and risk exposure match what would
  really execute, but not yet accumulating forward paper evidence.
- **PAPER FORWARD EVIDENCE** — actual paper/demo trades from the live-running
  system (journal-derived, per `ops/build_honest_baseline.py` /
  `ops/evidence_readiness.py`), accumulated after the strategy went live in
  paper mode.

A number's classification does not change no matter how favorable it looks —
do not upgrade a RESEARCH RESULT to RUNTIME PARITY by assumption because the
strategy name and formula match.

## Required checks (reuse, do not reimplement)

- **Step 0's PR/doc discovery pass** — always first, see above. Not optional,
  not skippable because the strategy "looks familiar."
- `/futures-proof-baseline-audit` — confirm the honest baseline this gate
  will cite is itself trustworthy first
- `/futures-strategy-audit` — the per-strategy classification mechanics
  (expectancy per fill vs per decision, drawdown, sample-size bar) this gate
  layers evidence-provenance on top of
- `/futures-live-replay-parity-audit` — the mechanism-level check for whether
  `ReplayEngine` and the live path share the same formulas/fill assumptions;
  run this before trusting any RUNTIME PARITY claim
- `/futures-forward-measurement-gate` — the pinned thresholds for whether
  enough PAPER FORWARD EVIDENCE has accumulated to trust it
- `ops/evidence_readiness.py` — read-only scorecard of what evidence streams
  are actually collecting vs. disabled vs. data-quality-blocked
- `ops/build_honest_baseline.py` — the honest live/demo P&L and fill
  accounting this gate's PAPER FORWARD EVIDENCE numbers must trace to
- `docs/strategy-rules/Strategy_Inventory.md` — current per-strategy
  dimension table (rules/detector/replay parity/honest fills/walk-
  forward/slippage/sample/verdict); this gate must not contradict it without
  citing what changed

## Final strategy classification (pick exactly one)

- `VALIDATED` — PAPER FORWARD EVIDENCE exists, adequate sample, RUNTIME
  PARITY confirmed, all pipeline-stage counts accounted for, no unresolved
  parity defect
- `PROMISING BUT UNPROVEN` — RUNTIME PARITY confirmed with a positive
  full-pipeline result, but PAPER FORWARD EVIDENCE is thin, absent, or not
  yet gated by `/futures-forward-measurement-gate`
- `BROKEN` — full-pipeline result is negative expectancy with adequate
  sample, or a structural defect (incomplete bracket, wrong session, lookahead)
- `OVERFIT` — RESEARCH RESULT edge that shrinks or inverts once run through
  `DecisionEngine -> RiskEngine -> PaperBroker` with the configured entry fill
  model (the exact failure mode `/futures-live-replay-parity-audit` exists to
  catch)
- `UNSAFE` — the strategy can reach `PaperBroker` without a complete
  stop/target, or bypasses a `RiskEngine` check
- `WAIT` — RESEARCH RESULT only, full pipeline not yet run, or too little
  data to classify either way with no structural red flag

## Forbidden actions

- Do not tune, rescue, or produce an alternate/adjusted variant of the
  strategy during the same validation pass — a rescue attempt is a new,
  separately-gated validation, not a rerun of this one.
- Do not silently exempt any legitimate `risk_rules.yaml` account risk
  control (daily loss limit, drawdown breaker, position caps) to reproduce
  the research numbers — if a real risk control eliminates the population,
  that is the finding, not an obstacle to work around.
- Do not classify a parity defect (the pipeline doing something wrong) and a
  correctly-functioning risk control (the pipeline doing exactly what it's
  configured to do) as the same kind of finding — separate them explicitly.
  "RiskEngine rejected 90% of candidates because the daily loss limit is
  calibrated for a different position size" is a parity/config finding;
  "RiskEngine rejected 90% of candidates because they were genuinely bad
  trades" is the strategy failing on its own risk-adjusted merits.
- Do not auto-merge, auto-enable, or auto-deploy anything as a result of this
  gate. Promotion (enabling in `risk_rules.yaml`, wiring into
  `signal_engine.py`) is always a separate, explicit, human-reviewed PR.
- Do not commit, push, or edit strategy/risk/broker code as part of running
  this gate — it is a read-only audit.
- Do not report a RESEARCH RESULT number without its tag, and do not let an
  untagged number quietly read as if it were RUNTIME PARITY or PAPER FORWARD
  EVIDENCE.

## Required output format

```
STRATEGY:
INSTRUMENT(S):

EVIDENCE DISCOVERY (Step 0 — always reported first):
  LATEST EVIDENCE FOUND:
  PR / REFERENCE:
  PR STATE:               open / merged / closed-unmerged (+ date)
  METHODOLOGY:
  VERDICT (as that source states it):
  SUPERSEDES CURRENT INVENTORY LABEL?  yes/no — and has `main` actually
                                        absorbed it, or is the inventory
                                        update itself still unmerged?
  IF NEWER EVIDENCE ALREADY CLOSES THIS GATE'S QUESTION: STOP HERE — report
  that result below instead of proposing a new pipeline run.

VERDICT: VALIDATED / PROMISING BUT UNPROVEN / BROKEN / OVERFIT / UNSAFE / WAIT

EVIDENCE PROVENANCE SUMMARY:
  RESEARCH RESULT:        <what exists, source files>
  RUNTIME PARITY:         <what exists, or "not yet run">
  PAPER FORWARD EVIDENCE: <what exists, or "none">

PIPELINE ATTRITION:
  raw candidates:            N
  candidate identity parity: N/N matched (mismatches: ...)
  direction parity:          N/N matched
  removed by ReplayEngine:   N (reason)
  removed by DecisionEngine: N (reason)
  removed by RiskEngine:     N (reason, per-check breakdown)
  reaching RiskEngine:       N
  approved:                  N
  entry attempts:            N
  fills:                     N   (fill rate: X%)
  no-fills:                  N   (taxonomy: ...)
  resolved outcomes:         N (W/L/BE)

PERFORMANCE (tag each figure RESEARCH RESULT / RUNTIME PARITY / PAPER FORWARD EVIDENCE):
  net P&L:
  PF:
  expectancy (per fill / per raw candidate):
  win rate:
  H1/H2:
  instrument split:
  LONG/SHORT split:
  recent-period behavior:
  slippage sensitivity:
  max drawdown:
  top-winner concentration:

RISK CONTROLS THAT ELIMINATED CANDIDATES:
  <exact check, threshold, count removed>

PARITY DEFECTS FOUND (separate from intended risk behavior):
  <or "none found">

BLOCKERS:
SAFE NEXT STEP:
```

Safety gates:
- Any classification above `WAIT` requires at minimum a full RUNTIME PARITY
  run with pipeline attrition accounted for at every stage — a RESEARCH
  RESULT alone caps the verdict at `WAIT`.
- `VALIDATED` requires PAPER FORWARD EVIDENCE meeting
  `/futures-forward-measurement-gate`'s thresholds — RUNTIME PARITY alone
  caps the verdict at `PROMISING BUT UNPROVEN`.
- Zero executable fills at any pipeline stage is reported as the headline
  finding, and caps the verdict at `WAIT` or `BROKEN` (never `PROMISING BUT
  UNPROVEN`) — an empty population proves nothing positive.
- A verdict may not rely on a rescue/tuning variant produced in the same
  pass — if the as-configured strategy fails, that is the verdict; a
  different configuration is a new, separate gate run.
- **Skipping Step 0 invalidates the run.** A verdict of `WAIT` or a
  recommendation to run the full pipeline is not trustworthy unless Step 0's
  PR/doc discovery pass was actually performed and reported — "I checked the
  strategy files and the inventory" is not a discovery pass; it must include
  an actual PR search (`state=all`, not just merged).
- If Step 0 finds a newer full-executable-path study (per the precedence
  order) that already answers this gate's question, the verdict must match
  that study's finding, not default to `WAIT` because "no new analysis was
  done in this run" — citing existing evidence correctly is this gate doing
  its job, not a shortcut.

Safe next step:
If Step 0 finds no prior full-pipeline closure, the safe next step is
building/running one (`ReplayEngine` through `PaperBroker` with the
configured fill model), not citing more research-only numbers. If Step 0
finds an existing closure, the safe next step is getting that PR reviewed
and merged (and flagging the stale inventory label via
`/daily-reconciliation` if `main` hasn't absorbed it yet) — not re-running
the closure. If `PROMISING BUT UNPROVEN`, the safe next step is accumulating
PAPER FORWARD EVIDENCE and re-checking against
`/futures-forward-measurement-gate` — never promoting on RUNTIME PARITY
alone. If `BROKEN`, `OVERFIT`, or `UNSAFE`, name the exact stage and count
where the population died or the defect was found, and name the PR it came
from; do not generalize to "the strategy doesn't work" without pointing at
the specific pipeline stage and its source.
