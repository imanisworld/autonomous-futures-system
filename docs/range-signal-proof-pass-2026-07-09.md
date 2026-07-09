# MES `range_signal` Proof Pass — Verification of the 2026-07-09 Consolidated Finding

## Verdict

**PROMISING BUT UNPROVEN. HOLD / AUDIT ONLY.** The original finding
(`docs/consolidated-regime-routing-test-plan-2026-07-09.md`, Section A) survives most of
this proof pass, but this audit found a real problem the original report missed: **the
90-trade MES sample is not 90 independent observations.** A single day (2026-07-08)
contributes 22 of the 90 trades (24%) and $1,858.55 of the $2,241.45 net P&L (83%), and 5 of
the 8 trading days in the sample show a literal 100% win rate — a pattern consistent with
this signal re-firing repeatedly on consecutive bars during one sustained intraday move, not
90 separately-earned wins. The effective sample size for statistical purposes is closer to
**8 trading days (6 net-positive, 2 net-negative)** than 90 trades. This does not kill the
finding — MES still nets positive even excluding the dominant day — but it substantially
weakens the "walk-forward consistent" claim the original report made, since that claim was
itself contaminated by the same clustering. No config, routing, or execution change is
appropriate on this evidence. No such change was made or is being proposed.

## 1. Exact data source, files, and row counts

- **Source**: `/root/afs-shared/logs/journal_2026-*.jsonl` on the Hetzner box, synced via
  `ssh`/`tar` to local scratchpad for analysis (not committed — real trading data, public
  repo).
- **Files used**: 22 files, `journal_2026-06-15.jsonl` through `journal_2026-07-09.jsonl`
  (missing 06-20, 06-27, 07-04 — all Saturdays, market closed, expected). 8,809 total journal
  lines.
- **Freshness verified**: re-fetched and `md5sum`-compared 3 sample files
  (`journal_2026-06-29.jsonl`, `journal_2026-07-02.jsonl`, `journal_2026-07-09.jsonl`)
  against the live box copies — byte-identical for the two completed days; 07-09 continues
  to grow (expected, it's the current day). Not stale, not tampered.
- **All 90 MES outcomes are genuine resolved real box journal outcomes**: every
  `SHADOW_OUTCOME` row in the `range_signal` lane has `final: true`, and every
  `candidate_key` appears in exactly one `SHADOW_OUTCOME` row (checked all 389 range_signal
  outcome rows in the file set) — zero revisions, zero non-final/provisional rows counted.

## 2. Duplicate-candidate / duplicate-outcome check

Re-ran the join between `RANGE_BOUND`-originating candidates and their `SHADOW_OUTCOME`
resolution, this time tracking every raw match (not deduplicated) before counting. Result:
**234 raw matches, 234 unique `candidate_key`s, zero keys matched more than once.** The
n=90 MES / n=144 MNQ counts are not inflated by the same signal being re-armed and
re-counted across consecutive bars — each is a genuinely distinct candidate (unique
instrument + decision timestamp + strategy + direction + entry price).

## 3. Observe-only confirmation (did this ever affect execution?)

Read the source directly rather than inferring from behavior:

- `webhook/runner.py`, the block building `range_signal` is explicitly commented "Range
  observation (journal-only, no effect on decisions)" and "fail-soft — a build hiccup must
  never affect ingestion, the decision, or risk" — wrapped in a bare `try/except` that only
  logs on failure.
- `context/range_signal.py`'s own module docstring: "JOURNAL-ONLY. No effect on trade
  decisions, risk gates, sizing, or execution."
- The resolver (`strategy/shadow_resolver.py`, `resolve_pending_shadow_outcomes`) runs after
  the live decision has already been made and journaled; it only appends `SHADOW_OUTCOME`
  evidence rows, never mutates `decision`/`risk`/`fill`.

Confirmed architecturally, not just empirically: this signal has never had, and cannot have
under the current code, any path to affecting a real order.

## 4. Entry/stop/target provenance (hindsight check)

Read `context/range_signal.py:_build_range_signal`. `entry_candidate` is `rs.price` — the
current bar's price from the same `MarketState` used for the live (blocked) decision.
`stop_candidate` is derived from the broken wall's own recorded level (a structural price
already known before this bar). `target_candidate` uses the next-known resistance/support
level from `wall_ctx`, or a symmetric R-multiple if none exists. All three values are
computable from data available at the bar's own close — no forward/future bars are used to
construct the theoretical setup itself. Forward bars are used only afterward, by the
resolver, to determine whether/how it would have filled — which is the correct and expected
way any shadow evaluation works, not a hindsight leak.

## 5. Fill-realism assumptions

Read `strategy/shadow_setups.py:resolve_shadow_candidate` directly:

- **Entry-fill test**: the entry only "fills" when a forward bar's range actually trades
  through it (`low <= entry <= high`) — not an always-fills assumption. `NO_FILL` is a real,
  possible outcome (43 of 389 total range_signal candidates in the full file set resolved
  `NO_FILL`, confirming this isn't vacuous).
- **Same-bar ambiguity**: `if target_hit and stop_hit: won = not pessimistic_both_hit` with
  `pessimistic_both_hit=True` by default — **stop wins on ambiguous same-bar straddles**,
  exactly as required.
- **Resolution starts the bar after fill**, not the fill bar itself — no look-ahead on the
  entry bar.
- **Slippage and commissions are NOT modeled.** Exit price is exactly the theoretical
  `target` or `stop` level. This is a real, honest gap — every dollar figure in this report
  and the original finding is a frictionless number. This is consistent with every other
  shadow-lane result cited this session (PR #237/#238/#240 all carry the same caveat), not a
  new problem specific to this signal, but it means the true expectancy is lower than
  reported, direction and magnitude unknown without a slippage model.

## 6. Breakdowns (this is where the real finding is)

**By week**: wk27 (06-29–07-02) n=51, exp=+$9.10/trade. wk28 (07-06–07-09) n=39,
exp=+$45.57/trade — looks like second-half improvement, **but this is exactly where the
07-08 cluster lives** (see below).

**By direction**: LONG n=60, WR=81.7%, exp=+$1.33/trade (barely positive — essentially
carried by win-rate alone against a poor win/loss size ratio). **SHORT n=30, WR=100.0%,
exp=+$72.06/trade** — a 100% win rate over 30 trades is the single biggest red flag in this
audit and was investigated directly (below).

**By day** (the check that actually mattered):

| Day | n | WR | Net |
|---|---|---|---|
| 06-29 | 12 | 100.0% | +$335.05 |
| 06-30 | 11 | 100.0% | +$163.75 |
| 07-01 | 15 | 80.0% | +$339.85 |
| 07-02 | 13 | 69.2% | -$374.35 |
| 07-06 | 5 | 100.0% | +$65.00 |
| 07-07 | 7 | 42.9% | -$182.05 |
| **07-08** | **22** | **100.0%** | **+$1,858.55** |
| 07-09 | 5 | 100.0% | +$35.65 |

**5 of 8 days show a literal 100% win rate.** All 22 of the SHORT trades sit on just 3 days
(07-02: 5, 07-07: 3, 07-08: 22) — the "SHORT is 100% WR" finding is almost entirely the
single 07-08 event. Pulling the raw 07-08 trades: a dense run of `RANGE_BREAK_CLOSE` SHORT
signals firing on consecutive 15-minute bars between 11:00 and 13:30 UTC, each with rising
`pnl_ticks` (43, 32, 92, 96, 132, 149, 150, 122, 156, 128...) — consistent with one
sustained intraday down-move that the signal kept re-triggering on as price kept falling.
These are not 22 independent bets; they are heavily autocorrelated draws from what is
effectively one trading event.

**Day-level reframing**: 6 of 8 days net-positive, 2 net-negative. Excluding 07-08 entirely:
n=68, WR=83.8%, net=+$382.90, exp=+$5.63/trade — still positive, materially smaller, and
itself still contains other 100%-WR days (06-29, 06-30, 07-06, 07-09) likely carrying their
own smaller-scale version of the same within-day clustering.

**Trade-level risk stats** (for the full n=90, with the clustering caveat above in mind):
avg win +$46.33, avg loss -$102.02 (losses run ~2.2x the size of wins — this strategy leans
entirely on win rate, not a favorable win/loss size ratio), largest win +$195.00, largest
loss -$166.40, max drawdown on the trade-sequence equity curve -$1,123.70 (roughly half the
total net gain), max consecutive-trade losing streak 4. Top-3-trade dollar share of net:
25.4% (this check, done in the original report, does not catch clustering — it measures
per-trade dollar dominance, not temporal correlation across trades, which is why it looked
clean while the real problem sat undetected).

**MAE/MFE**: not tracked by the `range_signal` resolver's `ShadowOutcome` (only
`result`/`exit_price`/`pnl_ticks`/`bars_to_fill`/`bars_to_exit`) — unlike
`mes_mnq_mechanical_research.py`'s `_simulate`, which does track it. Not computed in this
pass; would require reconstructing forward candle ranges independently. Named as a real gap,
not silently skipped.

## 7. Comparison against baselines

- **No-trade baseline**: $0 by definition.
- **Existing trend-strategies admitted on RANGE_BOUND** (from the original report,
  re-confirmed here): n=327, net -$472.63, expectancy -$1.45/trade — `BAD_STRATEGY`. MES
  `range_signal` clearly beats this, even after the clustering correction.
- **MNQ `range_signal`**: n=144, WR=54.2%, net -$12,524.98, exp=-$86.98/trade — re-confirmed
  unchanged by this pass. Applying the same day-level lens as a sanity check: MNQ's losses
  are not concentrated in one anomalous day the way MES's gains are (not shown in full here,
  out of scope for a "confirm it stays rejected" check) — MNQ remains `BAD_STRATEGY` and this
  audit found nothing to soften that.

## 8. Blockers to any routing/config change

1. Effective sample size is closer to 8 trading days than 90 trades — not enough to trust
   walk-forward or session-consistency claims at face value.
2. No slippage/commission model — real expectancy is unknown, only bounded above by the
   frictionless number reported.
3. Win/loss size ratio is unfavorable (avg loss ~2.2x avg win) — the strategy is entirely
   win-rate-dependent, and the win rate itself is now shown to be inflated by within-day
   autocorrelation.
4. Only 3 weeks of real data (06-29 earliest range_signal row through 07-09) — far short of
   the 622-day depth this codebase uses to validate anything else (e.g. MES `orb_reclaim`).
5. MAE/MFE not available for this lane — cannot assess how much adverse excursion these
   trades tolerated before winning.

## 9. Safe next step

Not a routing or config change. If the operator wants to pursue this further: let the
already-running observe-only `range_signal` lane keep accumulating real evidence
(zero code change — it already runs unconditionally), and specifically **build the missing
day-level / cluster-aware statistics as a standing measurement** (not a one-off check) so
that future evaluation of this lane doesn't have to be manually re-derived the way this audit
just did. Do not build `proof_builder`/`demo_proof` around this candidate until the
per-day-independent picture, not the per-trade picture, clears the same bar used elsewhere
in this codebase.

## Final posture (matches the operator's own framing)

- **MES range routing = `PROMISING_BUT_UNPROVEN`** (downgraded from the original report's
  implicit framing, specifically because of the clustering finding in Section 6).
- **MNQ range routing = REJECT** (`BAD_STRATEGY`, unchanged, re-confirmed).
- **Loosening the trend gate generally inside RANGE_BOUND = REJECT** (re-confirmed: the
  non-range-specific trend-strategies lose money on RANGE_BOUND bars, -$1.45/trade).
- **System posture = HOLD / OBSERVATION ONLY.**

## Scope

Docs-only. No script added (same reasoning as the original report — real box journal data,
not reproducible/committable). Zero `execution/`/`risk/`/`config/`/`webhook/`/`broker*`/
`strategy/`/`main.py`/`risk_rules.yaml` diff. No `proof_builder`/`demo_proof`. No config
change, no routing change, no live execution, no new strategy work.
