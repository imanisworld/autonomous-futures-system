# Current-Week MES/MNQ Behavior Regression Audit — 2026-07-09

## Question being answered

Distinct from the (closed) 6-contract regression audit: what changed in the last 1-2 weeks
that caused MES to show "invalid setup" behavior and MNQ to stop qualifying for trades,
despite both instruments still being evaluated at normal volume?

Constraints honored: no `demo_proof`/`proof_builder` built, no production behavior changed,
no gates loosened, no stops widened, no strategy posture changed.

## Step 1 — pinning the windows

Pulled the box's real journal (`/root/afs-shared/logs/journal_2026-*.jsonl`, 06-15 through
07-09) to compare a confirmed-healthy stretch against the flagged one:

- **Healthy baseline**: 2026-06-22 through 2026-07-01 (8 trading days) — MES 3.12
  `TRADE`/day, MNQ 1.75 `TRADE`/day.
- **Flagged window**: 2026-07-06 through 2026-07-08 (3 trading days) — MES 2.33
  `TRADE`/day (still trading, across 4 strategies), **MNQ exactly 0.00 `TRADE`/day, three
  days straight**, despite `NO_TRADE` evaluation volume staying normal-to-elevated (89/day
  vs the 82/day baseline — MNQ was being evaluated *more*, not less).

Both windows were deliberately chosen to sit entirely after 2026-06-19 (see Step 2) so the
comparison isn't confounded by an older, unrelated gate change.

## Step 2 — a real lead that turned out NOT to be a regression (important correction)

Mid-investigation, comparing `RANGE_BOUND`-labeled bars in an earlier (wider) before/after
split surfaced something dramatic: before a certain point, `RANGE_BOUND`-labeled bars
sometimes passed through to deeper gates (trend-strength, EMA-stack, volume) and
occasionally even resulted in a `TRADE`; after that point, **100% of `RANGE_BOUND` bars are
blocked by a single gate, `MARKET_CONDITION_NOT_TRENDING`, with zero exceptions, every day
since**. Pinned the exact flip precisely: 0% pure-block rate 06-15 through 06-18, 96.8% on
06-19, 100% every day 06-21 onward through 07-09 today.

**This is real, but it is not a regression.** Traced it to commit `07ca20d` ("TRENDING-only
gate + configurable ORB stop + limit-entry option", PR #59, merged 2026-06-18 21:08 ET,
live on the box from 2026-06-19 ~01:07 UTC) — a deliberate, evidence-backed change, not an
accidental short-circuit:

- Root-caused from the 2026-06-18 losing day (4 stop-outs, -$170, 0 wins).
- Project memory (`project_trending_gate_deploy.md`) records it was **live-validated with
  real P&L before being kept on**: "on the box, ~44% of trades fired in RANGE_BOUND and that
  bucket was net -$146 vs TRENDING +$288 over ~2wk."
- It is a real ~30-44% trade-volume cut, deliberately accepted as the cost of eliminating a
  bucket that was losing money.

Also checked whether the *config value* driving this (`require_trending_condition`, env
override `REQUIRE_TRENDING_CONDITION`) had been toggled anywhere — it hasn't: the env var has
never been set in any dated `.env` backup on the box (checked backups from 06-16 through
today), and it isn't set in `risk_rules.yaml` at any point in git history either. The
pre-06-19 partial-enforcement behavior was because **the gate didn't exist in the code yet**,
not because a flag was flipped. Once it shipped, its (always-true) default applied
immediately.

**Why this doesn't explain the current window**: it's three weeks old, and both the
"healthy" (06-22 to 07-01) and "flagged" (07-06 to 07-08) comparison windows sit entirely
inside this same enforced-gate era. It structurally caps trade *volume* below what it was
before 06-19, but it does not distinguish a healthy week from an unhealthy one within the
post-06-19 period — both get the identical treatment. Classification for this specific
finding: **EXPECTED_STRICTNESS**, not a regression. Correcting course here rather than
declaring victory on an exciting-looking lead that the evidence doesn't actually support.

## Step 3 — clean before/after gate-distribution comparison (both windows post-06-19)

| Gate | MES before/day | MES after/day | MNQ before/day | MNQ after/day |
|---|---|---|---|---|
| MARKET_CONDITION_NOT_TRENDING | 38.25 | 41.33 | 41.38 | 42.00 |
| MARKET_CONDITION_NOT_TRADABLE | 23.00 | 21.33 | 17.75 | 19.67 |
| REGIME_NOT_FULL | 6.12 | 10.00 | 7.75 | 10.67 |
| REGIME_RESTRICTED | 3.12 | 2.00 | 5.12 | 3.00 |
| WEAK_BAR_CLOSE | 5.12 | 1.67 | 6.75 | 5.67 |
| ENTRY_DETACHED_FROM_PRICE | 3.62 | 1.00 | 8.00 | 8.00 |
| EMA_STACK_NOT_ALIGNED | 1.88 | 0.00 | 1.75 | 0.00 |

No gate shows a dramatic multi-fold shift once the comparison is confined to the
post-06-19 era. `REGIME_NOT_FULL` ticks up modestly for both instruments (+3-4/day); nothing
else moves by more than a few events per day. `NO_TRADE` total volume is essentially flat
(MES 79.9→81.7/day, MNQ 82.0→89.0/day). This directly contradicts a "the gates got
stricter this week" story — the gate mix that actually fires is stable.

Searched `reason` text and `risk_check.failed_rule` broadly (not just `failed_gates` codes)
for anything stop/R:R/invalid-setup-shaped in both windows — no new failure mode appears.
`ENTRY_DETACHED_FROM_PRICE`-style reasons ("Entry X detached from price Y...") are frequent
in both windows at similar rates — a pre-existing, unchanged behavior, not new this week.
`RISK_REJECTED` volume is negligible in both windows (2 and 0 events respectively).

## Step 4 — ruled out, with evidence, not assumption

- **PR #239 (MES narrowed to `orb_reclaim` only)**: merged 2026-07-09T11:43:10Z — *after*
  the entire flagged window. Confirmed the flagged-window MES `TRADE` rows actually used
  `orb_breakout`/`orb_rejection` (strategies PR #239 later disables for MES), consistent
  with the narrowing not being live yet. Does not explain anything in this window.
- **PR #142 ("validate ORB retests and unify runner exits", merged 2026-07-02)**: lands
  squarely in the boundary and touches ORB-retest-adjacent code
  (`context/five_min_feed.py`, `strategy/shadow_setups.py`, `execution/tradovate_broker.py`
  `runner_live` exit path) — a real candidate on paper. Checked whether it's wired into the
  live decision path: `record_five_min`/`triggered_armed_setup`/`arm_fifteen_min_setup` are
  all gated behind `five_min_enabled()`, and the box's live `.env` has
  `FIVE_MIN_FEED_ENABLED=false` (also pinned via the `EXPECTED_PROOF_FIVE_MIN_FEED_ENABLED`
  drift gate, i.e. deliberately locked off). Inert in production. Ruled out.
- **CONFIG_BLOCKED alert misconfiguration** (real, separate issue found along the way):
  1,178 rows across 06-29, 07-02, 07-03, 07-05, 07-06 with reason "Live alert misconfigured:
  expected 15m chart, received 5m" — but under instrument names `MES1!`/`MNQ1!` (raw
  TradingView continuous-contract ticker with the `1!` suffix), **not** the canonical
  `MES`/`MNQ` names the rest of the system uses. Checked whether this contaminated the real
  decision stream: every canonical `MES`/`MNQ` decision row's `context.timeframe` field reads
  `"15"` consistently across all 21 days sampled, zero exceptions — clean. This is a real,
  separate TradingView-side alert hygiene issue (something is sending 5-minute bars under a
  wrong symbol format) worth fixing on its own, but it does not touch the decision pipeline
  being audited here and does not explain the qualification drop.

## Step 5 — what remains unexplained

MES's modest `TRADE`-rate dip (3.12 → 2.33/day) is not dramatic — it kept trading across 4
distinct strategies in the flagged window and the gate-distribution comparison shows no
supporting shift. This is consistent with ordinary variance for a low-frequency signal.

**MNQ's exact zero-for-three-days streak remains the real, striking anomaly**, and this audit
did not find a mechanical cause for it. Everything checked — gate-distribution mix, PRs
merged in the boundary window, feature-flag state, alert-configuration contamination, the
older TRENDING-gate change — comes back clean or ruled out. A rough back-of-envelope
(treating daily MNQ trade count as Poisson with the healthy-window mean of 1.75/day) puts
three consecutive zero-trade days at roughly 0.5% probability under pure chance — unusual
enough that this audit does not want to wave it off as ordinary variance, but not so unusual
that three days of real data can distinguish "genuinely quiet market" from "an
as-yet-unidentified mechanism." A fourth quiet MNQ day, or a repeat of this pattern next
week, would be the next real signal to re-open this.

## Classification

**MES: `NORMAL_VARIANCE`** — the distributional check requested (not just "processed
roughly the same count," the actual decision-type and gate-code mix) supports this
conclusion rather than contradicting it.

**MNQ: `INSUFFICIENT_DATA`** — real, statistically unusual anomaly; no gate-distribution
shift, config change, or merged-PR mechanism found despite a thorough search; 3 days is not
enough to separate "unlucky market window" from "an undiscovered cause."

**RANGE_BOUND/`MARKET_CONDITION_NOT_TRENDING` structural finding: `EXPECTED_STRICTNESS`** —
real, three weeks old, deliberate, live-P&L-validated. Not this week's cause, but flagged
here since it was a live lead this audit chased down and closed with evidence rather than
just asserting a conclusion.

## Recommendation (not executed — read-only audit)

1. No production change from this audit — per the operator's own constraints.
2. Worth a follow-up, separately: identify and fix/disable whatever TradingView alert is
   sending 5-minute bars under `MES1!`/`MNQ1!` — real noise, harmless to the current
   pipeline, but pointless load and log noise.
3. Let MNQ accumulate another few days of real data before concluding anything further —
   this audit could not responsibly force a conclusion on 3 days of evidence, and the
   operator's own standing rule is not to conclude "normal variance" without distributional
   proof — the same rule cuts the other way here: 3 days also isn't enough to declare a
   regression.

## Scope

Read-only. Zero `execution/`/`risk/`/`config/`/`webhook/`/`broker*`/`strategy/`/`main.py`/
`risk_rules.yaml` diff. No `proof_builder`/`demo_proof` code. No gates loosened, no stops
widened, no strategy posture changed. Forensic report built from box SSH journal reads
(`/root/afs-shared/logs/`) and `git log`/`git show` archaeology on already-merged commits.
