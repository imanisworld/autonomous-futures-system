# Strategy Audit Handoff - 2026-07-08

Audit-only snapshot saved before context compaction. No strategy, config, broker,
or service changes were made as part of this audit.

## Current Verdict

PROMISING BUT UNPROVEN / WAIT

The system does not appear to have LONG/SHORT broker mapping flipped. The bigger
issue is that the executable model is thinner than the intended discretionary
model: mostly VWAP / ORB / PDH / PDL trend setups, with Strat and richer market
structure often used as context, scoring, or shadow evidence rather than as hard
trade authority.

## Runtime Config Observed

- `htf_direction_mode`: `off`
- `htf_direction_source`: `payload`
- `htf_conflict_policy`: `four_hour_confirmed`
- `strategy_selection_mode`: `ranked`
- `strategy_fallback_enabled`: `False`
- `require_trending_condition`: `True`
- `signa_gate_enforced`: `False`
- `gex_observe_enabled`: `True`
- `range_observe_enabled`: `False`
- `entry_fill_model`: `market`
- `fill_slippage_ticks`: `1.0`
- `fill_pessimistic_both_hit`: `True`
- `runner_mode`: `False`
- `exit_mode`: `static`
- `max_trades_per_day`: `9999`
- `max_consecutive_losses`: `9999`
- `max_daily_loss`: `150.0`
- `max_drawdown_percent`: `0.2`
- `news_blackout_mode`: `off`

Enabled executable concepts:

- `orb_breakout`
- `orb_reclaim`
- `orb_rejection`
- `vwap_reclaim`
- `vwap_rejection`
- `vwap_hold`
- `pdh_reclaim`
- `pdl_reclaim`

MES disables:

- `vwap_reclaim`
- `pdl_reclaim`

## Direction Mapping Findings

- Strat classifier maps `two_up -> LONG` and `two_down -> SHORT`.
- Tradovate broker maps `LONG -> Buy` and `SHORT -> Sell`.
- No global reverse/flip switch was found active.
- Risk remains that payload/context fields can disagree, especially explicit
  Pine-provided `strat_direction`, which `webhook/state_builder.py` trusts when
  present.

## Level Stack Status

- ORB high/low: implemented and hard-used.
- ORB midpoint: missing in executable logic.
- VWAP: implemented and hard-used.
- PDH/PDL: implemented and hard-used.
- Prior close: partial; used by live HTF direction if `HTF_DIRECTION_SOURCE=live`,
  but current runtime uses payload direction.
- HOD/LOD: scoring input, not hard gate.
- PWH/PWL: scoring/wall context, not hard gate.
- GEX walls/flips: partial hard gate via `strategy/gex_gate.py`; richer wall map
  is journal-only.
- Supply/demand zones: partial; represented and wall-context aware, but not a
  complete executable strategy authority.
- PMH/PML: missing.
- ONH/ONL sweeps: shadow-only in `strategy/shadow_setups.py`.
- Round numbers: missing.
- Liquidity sweep/reclaim: mostly shadow-only or missing.

## Direction Authority Status

- 15m trend: hard gate for most executable setups.
- EMA stack: hard/soft gate plus scoring depending on MODERATE admission.
- VWAP side: hard gate.
- ORB side: hard gate.
- Strat direction: confirmation/veto/scoring, but direct Strat strategies are
  disabled in runtime config.
- 1H/4H/daily: available but not controlling because `htf_direction_mode=off`.
- GEX: can hard-block red-light conditions, not full direction authority.
- Signa: mostly shadow/soft because enforcement is off.

## Executable Vs Non-Executable Strategy Status

Executable right now:

- `orb_breakout`
- `orb_reclaim`
- `orb_rejection`
- `vwap_reclaim`
- `vwap_rejection`
- `vwap_hold`
- `pdh_reclaim`
- `pdl_reclaim`

Not executable under current runtime config:

- `strat_212`
- `strat_122`
- `strat_322_reversal`
- `strat_inside_break`
- `strat_outside_continuation`
- faithful `4H retrigger`
- overnight/liquidity sweep reclaim

Some of the above exist as code paths or shadow candidates, but they are not in
current `enabled_concepts`.

## Candidate Selection Finding

Ranked mode is on. Candidate ranking uses confluence score, R:R, expectancy
bonus, and priority.

Important gap: `strategy_fallback_enabled=False`. If the top-ranked candidate
fails a later gate such as Strat conflict, stale entry, or R:R, the engine can
skip a valid second candidate.

## Market Condition Finding

`require_trending_condition=True` means the bot blocks non-`TRENDING` setups.
This can protect against chop but may miss early trend transitions, range breaks,
reclaims, and reversals before the classifier updates.

The engine trusts Pine `TRENDING`/`RANGE_BOUND` labels as-is. It can veto Pine
`CHOPPY` only when directional structure is clear.

## Replay / Live Parity

Partial, not proven identical.

Shared machinery:

- `DecisionEngine`
- `RiskEngine`
- `PaperBroker`
- confluence scoring
- many setup functions

Known parity gaps:

- Live uses `webhook/state_builder.py`.
- Replay rebuilds state separately in `replay/replay_engine.py`.
- Live trend can be recomputed from EMAs; replay mostly trusts candle trend
  fields.
- Runtime `entry_fill_model=market`; live Tradovate IOC behavior can differ.
- GEX/Signa availability may differ between live payloads and replay candles.

## Fill Realism

Implemented:

- pessimistic same-bar stop-vs-target handling
- adverse slippage
- next-bar resolution
- IOC/stop-entry no-fill machinery exists

Gaps:

- Runtime replay currently uses `entry_fill_model=market`, so fills are assumed
  unless run under another model.
- Commissions are not clearly part of core `PaperBroker` P&L.
- Webhook age is observed but not enforced because `log_alert_age_only=True`.
- Bar-close replay cannot fully know intrabar ordering beyond pessimistic
  stop-first assumptions.

## Risk Alignment

Runtime risk does not match the intended human-style rules:

- Intended: max 3 trades/day.
- Runtime: `max_trades_per_day=9999`.
- Intended: lockout after losses.
- Runtime: `max_consecutive_losses=9999`, circuit breaker off.
- Intended: news lockout.
- Runtime: `news_blackout_mode=off`.

Still enforced:

- one open position max
- required entry/stop/target
- min R:R
- max daily loss
- max drawdown
- max stop distance
- position sizing ladder

## Blockers

- HTF direction authority is off.
- Strat doctrine is mostly not executable.
- Runtime trade-count/loss-count/news throttles are intentionally disabled.
- Replay/live parity is not fully proven.
- Rich wall/level hierarchy is mostly journal-only, not decision authority.
- Candidate fallback is off, so valid second candidates can be skipped.

## Safe Next Step

Build a read-only strategy intent audit report over recent journal/replay rows:

- selected setup
- all candidates considered
- rank score and winner
- failed gates
- HTF/level context
- whether a shadow-only discretionary setup matched
- whether a valid second candidate was skipped

Do not enable new strategies, loosen gates, or change runtime risk until this
audit produces evidence.

