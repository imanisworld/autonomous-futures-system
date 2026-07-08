# /futures-live-replay-parity-audit

Purpose:
Audit whether `replay/replay_engine.py` (backtest) and the live/demo path (`webhook/runner.py` → `execution/tradovate_broker.py` or `execution/paper_broker.py`) use the same strategy logic and the same fill assumptions. This is the mechanism-level check behind `/futures-strategy-audit`'s OVERFIT classification — a strategy that looks good in replay but has never proven itself under honest live fills is not validated, it is potentially a fill-model artifact (the exact failure mode this system already found once: the legacy backtest edge was proven to be an artifact of an unrealistic fill model, not real strategy edge, per the honest-baseline work this session).

Core rule: No proof, no run. Parity is not assumed from the two paths sharing a name (e.g. both calling something named "orb_reclaim") — it must be verified by comparing the actual formulas, timeframe handling, and fill logic each path executes.

Required checks:
- Strategy formulas: confirm `strategy/strat_classifier.py`, `strategy/signal_engine.py`, and any strategy-specific modules are the SAME code called by both `replay/replay_engine.py` and the live webhook path (`webhook/runner.py` → `webhook/state_builder.py`) — not a parallel reimplementation that could silently drift
- Timeframe handling: confirm replay's candle cadence matches live's actual alert cadence (the live engine is 15M-only per prior findings — confirm replay is not silently testing on a different timeframe mix)
- EMA/session/ORB/PDH/VWAP logic: spot-check that key-level and regime calculations (`_market_state_from_candle`, `_htf_context_for`, `_key_levels_from_candle`, `_strat_context_from_candle` in replay vs. their live equivalents in `webhook/state_builder.py`) produce the same field lineage from the same raw candle
- Stop/target/invalidation handling: confirm replay and live compute stop/target the same way, not independently re-derived
- Same-bar ambiguity: when a bar's range contains both the stop and the target, confirm replay's resolution rule (`fill_pessimistic_both_hit` in `execution/paper_broker.py` — pessimistic mode picks STOP as the worst case) matches what live/demo actually does when this occurs, and that this is documented, not incidental
- Slippage/commission assumptions: `execution/paper_broker.py`'s `slippage_ticks` (adverse, applied to MARKET fills) and `_entry_tolerance_ticks` vs. whatever the live broker path (`execution/tradovate_broker.py`) actually experiences — confirm replay's slippage assumption is not more generous than reality
- IOC/limit fill assumptions: confirm replay's fill model for capped-limit entries matches the live IOC-cap marketability rule used throughout the phantom-clear and plain-CANCELLED audits (`entry ± ENTRY_SLIPPAGE_TOLERANCE_TICKS_{MES,MNQ} * 0.25pt`) — a replay that fills more optimistically than this cap allows will show inflated edge
- Target-first bias: confirm replay does not default to assuming the target was hit first in an ambiguous bar unless `pessimistic_both_hit` is explicitly configured to do so — an unstated target-first bias is a known way backtests overstate edge
- Divergence check: for any strategy with both a replay result and an honest live/demo result (via `ops/build_honest_baseline.py`), compare the two directly — a replay P&L/win-rate that is meaningfully more favorable than the honest live/demo number for the same strategy is the signature of a parity gap, not necessarily a code bug, but it must be explained

Forbidden actions:
- Do not modify `replay/replay_engine.py`, `execution/paper_broker.py`, `execution/tradovate_broker.py`, or any strategy file.
- Do not commit or push.
- Do not run a live/demo test order as part of this audit.
- Do not assume parity because both paths reference the same strategy name — verify the actual code path.
- Do not generate or commit a report containing strategy names, replay P&L, or live P&L comparisons to this public repo — write it to `private/` (gitignored).

Required output format:

Verdict:
APPROVE / REJECT / HOLD / AUDIT ONLY

Parity Classification:
CONSISTENT / OVERFIT / FILL ARTIFACT / INCONCLUSIVE

Why:
2-5 decisive reasons.

What I Verified:
- strategy code path confirmed shared (not reimplemented) between replay and live
- timeframe/cadence match confirmed
- same-bar ambiguity resolution rule confirmed identical
- slippage/IOC-cap fill assumptions compared
- replay vs. honest live/demo result compared per strategy, where both exist

Problems Found:
Separate:
- blockers
- warnings
- minor cleanup

Required Fixes:
- must-fix before trusting any replay-derived edge claim
- should-fix later
- do-not-touch items

Safe Next Step:
Smallest safe action only.

Safety gates:
- If replay's fill model is more optimistic than the live IOC-cap marketability rule (e.g. no slippage tolerance cap, or a resting-limit assumption live does not use), classify OVERFIT / FILL ARTIFACT — this is precisely the failure mode already found once in this system's history (the legacy replay edge proven to be a fill-model artifact under honest fills).
- If the two paths call genuinely different code for the same named strategy, classify INCONCLUSIVE and treat it as a blocker, not a minor finding — the replay result says nothing about the live strategy in that case.
- A strategy with no live/demo trades yet to compare against replay is INCONCLUSIVE for that strategy specifically, not CONSISTENT by default.

Safe next step:
If FILL ARTIFACT or OVERFIT is found for a strategy, the safe next step is to flag it directly in that strategy's `/futures-strategy-audit` entry and exclude replay-only numbers from any go-live argument for it. If CONSISTENT, the safe next step is to note that replay results for that strategy carry more weight going forward, but still subject to `/futures-strategy-audit`'s sample-size bar.
