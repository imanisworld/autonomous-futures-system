# /futures-strategy-audit

Purpose:
Audit whether each futures strategy (`strategy/strat_classifier.py`, `strategy/signal_engine.py`, `strategy/shadow_setups.py`, and the named strategies they produce — e.g. `orb_reclaim`, `orb_breakout`, `vwap_hold`, `pdh_reclaim`, `strat_4hr_retrigger`) actually has edge, using the honest baseline, not the legacy/optimistic one.

Core rule: No proof, no run. A strategy is not VALIDATED because it looks good in `replay/` — it is VALIDATED only against realistic fills, adequate sample size, and honest live/demo P&L. Run `/futures-proof-baseline-audit` first if the baseline's trust state is not already known for this session.

Required checks, per strategy, per instrument:
- Win/loss count and net P&L from `ops/build_honest_baseline.py`'s `by_strategy` breakdown (current honest combined baseline: 30 filled trades, 11W/19L, net -$67.85 as of 2026-07-07 — re-verify, do not assume this is still current)
- Expectancy per fill (net P&L ÷ filled trades) and expectancy per decision (net P&L ÷ all decisions including no-fills) — these can disagree sharply; report both
- No-fill rate for this strategy specifically (via `/futures-cancelled-audit` + `ops/build_honest_baseline.py`'s cancelled counts) — a strategy that "wins" only because most of its signals never fill is not validated, it is unmeasured
- Sample size — state the raw count plainly; do not let a favorable win rate on 3-5 trades read as validated
- Drawdown within the sample — worst peak-to-trough on this strategy's own trade sequence, not smoothed by netting against other strategies
- Replay/live consistency — does this strategy's replay result (`replay/replay_engine.py`, `logs/replay_*`) predict its honest live/demo result, or diverge sharply (see `/futures-live-replay-parity-audit` for the mechanism-level check)
- Stop/target/invalidation logic — confirm the strategy always produces a complete bracket (entry+stop+target), no silent fallback to an unbracketed order
- Session/timeframe behavior — is this strategy's expected trigger window (`session_hours_et`, timeframe) actually where its trades occurred, or is it firing outside its intended regime
- Instrument split — does this strategy behave consistently across MNQ and MES, or is one instrument dragging/inflating the combined number
- Enabled/disabled/shadow-only status — confirm current status in the live config matches what's being audited (a disabled or shadow-only strategy should never be reported as if it were live-contributing)
- Lookahead check — confirm no input to the strategy's decision was only knowable after the decision bar closed (a common source of replay-only "edge")

Classification (pick exactly one per strategy per instrument):
- `VALIDATED` — multiple months of data, realistic (honest) fills, identical replay/live logic confirmed via `/futures-live-replay-parity-audit`, adequate sample size, controlled drawdown, no lookahead, clear invalidation rule. This bar is deliberately high — expect most or all strategies to fail to meet it today.
- `PROMISING BUT UNPROVEN` — positive expectancy so far but insufficient sample size, unconfirmed replay/live parity, or untested drawdown behavior
- `BROKEN` — negative expectancy with adequate sample size, or a structural defect (incomplete bracket, lookahead, wrong session)
- `OVERFIT` — replay/backtest edge that disappears or inverts under honest fills or out-of-sample data (see `/futures-live-replay-parity-audit`)
- `UNSAFE` — the strategy can produce an order without a complete stop/target, or otherwise bypass a risk gate
- `WAIT` — too little data to classify either way, and no structural red flag found

Forbidden actions:
- Do not modify strategy code, thresholds, or enabled/disabled state.
- Do not enable, disable, or promote any strategy as a result of this audit — that is a separate operator decision, not an automatic action here.
- Do not commit or push.
- Do not classify any strategy VALIDATED without explicitly confirming every item in the VALIDATED bar above — a strong recent run alone is PROMISING BUT UNPROVEN, not VALIDATED.
- Do not generate or commit a report containing strategy names, per-strategy P&L, or entry/stop/target levels to this public repo — write it to `private/` (gitignored).
- Do not let a single instrument's strong result (e.g. MNQ) launder a weak result on the other (e.g. MES) into a combined "the strategy works" verdict — report per-instrument.

Required output format:

Verdict:
APPROVE / REJECT / HOLD / AUDIT ONLY

Strategy Classification (per strategy, per instrument):
VALIDATED / PROMISING BUT UNPROVEN / BROKEN / OVERFIT / UNSAFE / WAIT

Why:
2-5 decisive reasons.

What I Verified:
- honest baseline re-checked (cite /futures-proof-baseline-audit run or timestamp)
- win/loss/P&L per strategy per instrument
- expectancy per fill vs. per decision
- no-fill rate per strategy
- sample size stated explicitly
- drawdown checked
- replay/live parity checked or flagged as not yet checked
- bracket completeness checked
- lookahead checked

Problems Found:
Separate:
- blockers (UNSAFE or BROKEN findings)
- warnings (OVERFIT or thin-sample PROMISING BUT UNPROVEN findings)
- minor cleanup

Required Fixes:
- must-fix before any strategy can be considered for live/scaling discussion
- should-fix later
- do-not-touch items

Safe Next Step:
Smallest safe action only.

Safety gates:
- No strategy may be classified VALIDATED without: adequate sample size (state the threshold used), honest (not legacy) fills, confirmed replay/live parity, controlled drawdown, no lookahead, and a clear invalidation rule. Missing any one caps the classification at PROMISING BUT UNPROVEN.
- A strategy with a materially unaudited no-fill rate (see `/futures-cancelled-audit` open findings) caps its classification at WAIT, not VALIDATED or BROKEN — the sample is not yet trustworthy either direction.
- Any strategy that can produce an order without a complete stop/target is UNSAFE regardless of its P&L.
- This audit never authorizes enabling, disabling, or tuning a strategy — it only classifies. Any resulting action is a separate, explicit operator decision.

Safe next step:
If any strategy is UNSAFE or BROKEN, name it and the specific defect — do not generalize to "the strategy set." If all strategies land at WAIT or PROMISING BUT UNPROVEN (the expected current state per the honest baseline), the safe next step is to keep collecting data under `/futures-forward-measurement-gate`'s thresholds, not to act on today's thin sample.
