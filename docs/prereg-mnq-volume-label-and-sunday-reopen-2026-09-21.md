# Pre-registration — MNQ market-condition volume filter & Sunday-reopen window

**Registered 2026-09-21 04:50Z, before any further data is examined. Evidence
lanes only. No rule, gate, config, or runtime change is authorized by this
document. Any change is a separate post-2026-09-30 decision that must cite the
outcome recorded in §6.**

Companion evidence: `counterfactual-gates-off-2026-09-21.md`,
`counterfactual-sunday-reopen-2026-09-21.md` (+ addendum).

## 1. What we already know (frozen as of registration)

- The MNQ label is Pine-computed on the 15m chart: TRENDING requires the
  EMA9/21/55 stack ordered **and** `rel_vol ≥ 0.80` where
  `rel_vol = volume / sma(volume, 20)`. DEAD/CHOPPY are pure volume thresholds.
- On 2026-09-20 the EMA trend was UP on every bar 22:00–01:15Z; the label
  flipped away from TRENDING only because `rel_vol` was 0.29–0.62, since the
  20-bar SMA at a Sunday reopen is Friday RTH volume.
- On the 2026-09-16 → 09-21 tape (MNQ shadow OUTCOME rows): label TRENDING
  26/67 +$360; **EMA-aligned-but-volume-blocked 6/30 −$690**; EMA sideways
  24/69 +$59. Across 8 Sundays, every bucket lost inside Sun 22:00Z–Mon 01:00Z.
- Conclusion so far: the volume filter is the most valuable part of the label;
  loosening it is unsupported. The only surviving candidate is a tightening.

Both facts above are *in-sample for the purpose of this document*. Nothing
below may be scored on data observed before 2026-09-21 04:50Z.

## 2. Hypotheses (fixed)

**H1 — Sunday-reopen no-entry (tightening).**
Blocking all MNQ entries Sun 22:00Z – Mon 01:00Z improves net $ per week
versus the current rules, on forward data.

**H2 — Session-aware volume denominator (loosening, expected to FAIL).**
Replacing `sma(volume, 20)` with a same-session-of-week baseline (volume ÷
median volume of the same 15m slot over the prior 4 weeks) would relabel some
"aligned-but-thin" bars as TRENDING. The trades that *only* H2 admits are net
positive after costs.

**H3 — 1m volume acceleration inside the 15m bar (loosening, exploratory).**
Among aligned-but-thin 15m bars, the subset where the last 5 one-minute bars'
volume exceeds the first 10 one-minute bars' volume (of the same 15m bar) is net
positive after costs.

**H4 — Fast label for fast triggers (coupling test; added 2026-09-21 05:30Z,
before any forward data was examined).**
If entries ever move to the 1m trigger lanes, the label available at trigger
time is the *last closed* 15m bar's — up to 14 minutes stale. H4 asks whether a
regime read that updates at trigger speed does better than carrying the stale
15m label. Population: MNQ triggers logged by the observation-only 1m lanes
(#723 4HR trigger, #749 3-2-2 First Live observer). Each trigger is scored
two ways — (a) "carry": the 15m Pine label of the last closed bar; (b) "fast":
the fast-regime score from `research-fast-regime-from-5m-2026-09-21.md` §"Fast-
regime score" (six 5m bars ending at the last 5m close before the trigger;
score ≥ 3 and direction agrees ⇒ TRENDING). That score is adopted verbatim —
no re-tuning. The comparison is between the two *filters*, not between fast
and slow entry: does gating 1m triggers on (b) beat gating them on (a)?

**H5 — Regime gate (`BLOCK_RESTRICTED_REGIME=true`, reason `REGIME_NOT_FULL`)
is blocking the profitable bucket (loosening; added 2026-09-21 06:05Z, before
any forward data was examined).**
Back-tape motivation (in-sample, not scored): of 67 MNQ shadow setups labelled
TRENDING since 09-16, the live bot took **0**; 27 were blocked solely by the
regime gate and resolved 11/27, **+$642 gross**; 20 were "no qualifying setup"
(executable detector did not fire), −$182; 14 had the label flip by decision
time, +$116. Tonight's 22:00Z and 01:00Z TRENDING bars were regime-blocked.
H5: among MNQ setups that pass the market-condition rule (label TRENDING) and
the executable detector, those the regime gate blocks (`RESTRICTED`) are net
positive after costs on forward data — i.e. the gate removes more profit than
loss. Population is the *executable* candidate, not the observer family, to
avoid the detector-boundary mismatch above.

**H6 — 2-2 continuation, Asia session, EMA-aligned, 1.5R (added 2026-09-21
05:30Z from the 313-day grid; the grid result is in-sample and is NOT scored).**
In-sample motivation: 313-day replay grid (25,119 candidates × 7 exits × 3
filters × 5 sessions, honest fill, proven costs): `strat_22_continuation`,
session `asian`, EMA9/21/55 stack aligned with the trade direction with **no**
`rel_vol` requirement, fixed 1.5R target → n=1,049, 47% W, +$9,208, PF 1.25,
beating 200/200 random-direction permutations (null max 1.10). The live slice
(all sessions, volume-filtered label, 2R) is nowhere near the top of the grid.
H6: on forward data the same slice is net positive after costs.

**H7 — 2-2 continuation, Sunday reopen, no filter, 1R (added 05:30Z).**
In-sample: same grid, session `sunday_reopen` (Sun 22:00Z–Mon 01:00Z), no
label/EMA filter, fixed 1R → n=139, 66% W, +$3,742, PF 1.75, beating 200/200
permutations (null max 1.68, tighter). Supersedes the 8-Sunday journal study
(n=9) that concluded the opposite. H7: on forward data the slice is net
positive after costs. **H1 (Sunday no-entry) is now expected to FAIL; it stays
registered and is scored honestly.**

**Robustness note on H6/H7 (in-sample, other thread, 2026-09-21 ~05:50Z; NOT
scored):** same corpus, simulator and costs, but ONE position at a time (what
the lane can actually take), then split. H6: n=553 PF 1.20 +$4.3k (halves 1.35 /
1.12; 8/13 months positive; +1 tick/side → PF 1.17) — real but thin and decaying
in the second half, ≈ +$330/month/contract. H7: n=85 PF 1.54 +$1.9k (halves
1.69 / 1.47; 10/13 months; +1 tick → 1.52) — the cleaner cell. H6+H7 as one
book: n=625 PF 1.26 +$6.3k, max DD $1,145. NY 2-2 reversal (PF 0.95 → 1.63
by half) is suspect and is deliberately NOT registered. The TRENDING-label 3R
Asia cell drops to PF 1.11 one-at-a-time, reinforcing that the EMA-only
version is the better one. Known limits: touch-based fills at the entry level;
the EMA-aligned filter was chosen after seeing the grid. Expected forward
volume ≈ 50 trades/month combined, so H6 reaches its floor in ~2 months and H7
in ~6 Sundays. The forward thresholds in §4 are read against THESE numbers.

## 3. Data (forward only)

- Candidates: `cross_instrument_observation_v1.jsonl` CANDIDATE/OUTCOME rows,
  MNQ, `signal_timestamp > 2026-09-21T04:50:00Z`, all shadow families.
- Bars: `logs/bars_MNQ_*.jsonl` (15m) for EMA/`rel_vol` reconstruction;
  `logs/tf1m/bars_MNQ_*.jsonl` (1m, collected since 09-18) for H3.
- 1m-lane triggers for H4: the observer lanes' own evidence files
  (`logs/…` written by #723/#749), joined to `logs/tf5m/bars_MNQ_*.jsonl` for
  the fast score and to the 15m journal for the carried label.
- H5 population: MNQ 15m journal decision rows where `market_condition ==
  TRENDING`, the executable detector produced a setup, and the sole failed gate
  is the regime gate (`failed_gates`/`reason` contains `REGIME`); geometry from
  the row's own `shadow_candidates` entry for that setup; resolved against
  `bars_MNQ_*.jsonl` with the campaign resolver rules.
- H6/H7 population: `cross_instrument_observation_v1.jsonl` MNQ CANDIDATE rows
  with `strategy == strat_22_continuation_observed`, `signal_timestamp` after
  registration; session from the candidate's decision bar (`asian` per corpus
  session rules; `sunday_reopen` = Sun 22:00Z–Mon 01:00Z); EMA alignment for
  H6 computed from `bars_MNQ_*.jsonl` (close > ema9 > ema21 > ema55 for LONG,
  mirrored for SHORT; EMAs on 15m closes with ≥60 bars warm-up). Outcomes
  re-resolved at the registered target (1.5R / 1R) against the same bars with
  the campaign's fill rules — the campaign's own 2R OUTCOME rows are NOT the
  scoring series for H6/H7.
- Costs: `execution/forward_evidence_campaign.py` constants
  (`SLIPPAGE_TICKS=1.0`, `COMMISSION_DOLLARS=1.48`), 1 contract.
- Resolution: the campaign's own OUTCOME resolver (stop-first ties). No new
  fill logic.

## 4. Minimum sample and decision rules (fixed)

| Hyp. | Population scored | Min n | PASS if | FAIL if |
|---|---|---|---|---|
| H1 | MNQ demo-eligible setups (label TRENDING, regime FULL) inside the window vs. outside, same weeks | ≥ 6 Sundays **and** ≥ 20 inside-window rows | inside-window net $ ≤ 0 **and** removing them raises weekly net $ in ≥ 4 of 6 weeks | otherwise |
| H2 | Rows admitted by H2 only (EMA aligned, `rel_vol < 0.80`, session-relative ≥ 0.80) | ≥ 30 rows | net $ > 0 after costs **and** win-rate ≥ TRENDING bucket's same-period win-rate | otherwise |
| H3 | Aligned-but-thin rows with 1m acceleration | ≥ 30 rows | net $ > 0 after costs **and** win-rate ≥ TRENDING bucket's | otherwise |
| H4 | 1m-lane MNQ triggers, scored under filter (a) carry-15m vs (b) fast-5m | ≥ 40 triggers **and** ≥ 15 where (a) and (b) disagree | on the disagreement set, (b)-admitted net $ > (a)-admitted net $ **and** (b)-admitted net $ > 0 | otherwise |
| H5 | TRENDING + executable setup, blocked only by regime gate | ≥ 25 rows | net $ > 0 after costs **and** win-rate ≥ 35% **and** max single-loss ≤ 1.5R | otherwise |
| H6 | 2-2 con, asian, EMA-aligned (no rel_vol), fixed 1.5R | ≥ 80 rows | net $ > 0 after costs **and** PF ≥ 1.10 **and** win-rate ≥ 40% | otherwise |
| H7 | 2-2 con, sunday_reopen, no filter, fixed 1R | ≥ 6 Sundays **and** ≥ 30 rows | net $ > 0 after costs **and** PF ≥ 1.20 **and** win-rate ≥ 55% | otherwise |

- One look, at the first weekly gate-report run after the minimum n is met.
  No interim peeks that inform a decision. The daily 22:20Z gate line may
  continue to print aggregate counts; it does not score H1–H3.
- Multiple-comparison note: seven hypotheses, one look each. H6/H7 were
  selected as the top of a 1,253-cell grid, so their in-sample PF is inflated;
  the forward thresholds above are deliberately below the in-sample values
  (1.10 vs 1.25; 1.20 vs 1.75). If exactly one of H2/H3/H4/H5/H6/H7 passes at
  the margin, treat as PROVISIONAL and require a second,
  non-overlapping sample of equal size before any proposal.
- H4 is a *coupling* result: a PASS means "if 1m entries are ever enabled,
  they must carry the fast label, not the 15m one." It says nothing about
  whether 1m entries should be enabled — that is the 1m lanes' own question.
- H5 is the only loosening with back-tape support. A PASS authorizes a
  proposal to set `BLOCK_RESTRICTED_REGIME=false` (env) for MNQ only, with the
  demo lane as the first forward test — still a gate change, still post-09-30
  or an explicit operator ruling.
- H6/H7 PASS authorizes a proposal for a **session-scoped rule** (Asia:
  EMA-only condition + 1.5R; Sunday reopen: no condition gate + 1R) with the
  demo lane as first live test — a runtime change (session-scoped gate
  overrides + per-session R target), post-09-30 or explicit operator ruling.
  Slippage sensitivity must be re-checked on live demo fills before any
  live-money discussion: at 1R targets one extra tick per side moves PF
  materially.
- A PASS on H2, H3, H4, H5, H6 or H7 does **not** authorize a change; it authorizes a proposal
  for the post-09-30 review with the numbers attached.

## 5. What is NOT allowed

- Re-slicing by family, direction, hour, or instrument after seeing forward
  results to rescue a FAIL. The populations above are the populations.
- Scoring on any row with `signal_timestamp` before registration time.
- Changing the cost constants, the resolver, or the label formula mid-study.
- Using the demo ledger (mixed-era, mixed-sizing) as the scoring series.
- Re-tuning the fast-regime score (weights, threshold, bar count) for H4.
- Scoring H5 on observer-family geometry instead of the executable setup.
- Scoring H6/H7 on the campaign's 2R OUTCOME rows instead of re-resolving at
  the registered target; changing the target, session boundary, or EMA
  definition after registration; adding a filter to H7.

## 6. Outcome (to be filled once, at the look)

| Hyp. | Look date | n | Net $ | Win % | Result | Follow-up |
|---|---|---|---|---|---|---|
| H1 | | | | | | |
| H2 | | | | | | |
| H3 | | | | | | |
| H4 | | | | | | |
| H5 | | | | | | |
| H6 | | | | | | |
| H7 | | | | | | |

## 7. Ownership

Scoring is mechanical; whoever runs it appends §6 and links the run. Proposals,
if any, go to the operator with this document attached. The operator's standing
ruling (PAPER/OBSERVE only, freeze to 2026-09-30) is unchanged by anything here.
