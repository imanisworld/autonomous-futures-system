# Shadow / Adaptive observation layer

Read-only machinery for **observing** trade opportunities the live system does NOT
take — so we can study what the gates cost us without risking a dollar. Nothing in
here ever places an order.

There are two complementary pieces:

## 1. Shadow setups (`strategy/shadow_setups.py`)
Stateless detectors for fade/reclaim/range opportunities outside the live strategy
set: `orb_false_break_fade` (validated keeper), `ovn_high/low_sweep_reclaim`,
`gap_fill`, `ema_pullback_trend`. `evaluate_shadow_setups(state)` returns scored
candidates with a `risk_tier` + `size_multiplier`.

Wired (fail-soft, read-only) into both `webhook/runner.py` (live) and
`replay/replay_engine.py` (backtest): when a bar produces shadow candidates they are
attached to the result and written to the journal under `shadow_candidates`. They are
NEVER executed.

Tally them across replay journals:
```
python scripts/shadow_candidate_counts.py --log-dir logs/<replay_run>
```

## 2. Schedule modes + shadow runner (`adaptive/`)
`schedule_mode` (config `schedule.mode`, or `SCHEDULE_MODE` env) controls gate
enforcement:
- `current` (default) — normal: all session/schedule gates enforced.
- `always_on_shadow` — evaluate every session, **never submit** (pure observation).
- `always_on_paper` — evaluate every session, paper orders only (live broker rejects).

`schedule_mode` is forbidden to be anything but `current` when `live_trading_enabled`
is true (enforced in `config/settings.py`).

`shadow_runner.evaluate_with_shadow(state, daily_state, config)` runs the decision
engine **twice** — gates enforced (`current`) vs bypassed (`always_on_shadow`) — and
emits the delta, i.e. exactly which gate blocked a setup. `opportunity_tracker.py`
accumulates these into an opportunity ledger; `execution_gate.py` is the structural
chokepoint guaranteeing a non-`current` mode can never reach order placement.

## Status
Consolidated 2026-06-17 onto one branch from the stranded `claude/adaptive-shadow`
(framework) + `codex/futures-shadow-setup-tests` (detectors). `codex/range-fade-wip`
was fully subsumed (its `opportunity_tracker.py` was identical). Observation-only;
not yet enabled in production config (`schedule_mode` stays `current` on the box).
