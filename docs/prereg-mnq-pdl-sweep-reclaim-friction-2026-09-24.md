# Prereg — MNQ true PDL sweep→reclaim friction viability (2026-09-24)

**Trial ID:** `T-2026-09-24-prereg-mnq-pdl-sweep-reclaim-friction-2026-09-24-01`  
**Status:** RESEARCH ONLY / OFFLINE. NO EXECUTION AUTHORITY.

## Why this trial exists

The existing strategy name `PDL_RECLAIM_SHORT` is not a reclaim; it is a break/close below PDL.
The already-run `fns-v0.1` study separately defined the genuine sweep→reclaim event as
`PDL_REJECTION_LONG`:

- previous completed 5m close >= prior RTH session low (PDL);
- current 5m low < PDL;
- current completed 5m close > PDL.

That raw event was already exposed on MNQ and was positive in both historical halves:
H1 n=125 median +5.43 bps; H2 n=86 median +3.34 bps at the 60m close horizon.
This trial does **not** rerun that question as though it were new evidence. It asks the next
narrow question: does the already-exposed raw directional effect remain economically positive
under a causal entry and a conservative all-in MNQ friction stress?

The symmetric PDH sweep→reclaim SHORT does not advance here because its prior MNQ raw-signal
medians were negative in both halves. No rescue test is authorized for it.

## Frozen population

- Instrument: MNQ only.
- Source: existing Polygon 5m RTH replay corpus under `data/replay_polygon_5m/MNQ`.
- Session: 09:30–16:00 America/New_York.
- Complete 78-bar RTH sessions only.
- Reuse `research.futures_non_strat_coverage.roll_excluded_sessions`.
- Reuse the exact `alert_ranker.non_strat_coverage.observe_session` event definition.
- Episode handling: first event of each `PDL_REJECTION_LONG` episode only.
- Half split: H1 < 2025-09-01; H2 >= 2025-09-01.

## Frozen causal trade measurement

This is a friction-viability screen, **not** a bracket backtest.

- Decision: close of the completed 5m sweep→reclaim event bar.
- Entry: open of the immediately following 5m bar.
- Direction: LONG.
- Exit: close exactly 60 elapsed minutes after entry, using 12 completed 5m bars including the entry bar.
- If the next bar or full 60m path is unavailable before RTH close, the episode is unresolved and excluded.
- Quantity: 1 MNQ.
- Gross points = exit close - entry open.
- Report gross dollars using $2.00 per MNQ point.
- Fixed all-in friction stresses:
  - 2.0 points round trip;
  - 3.0 points round trip.
- Net points = gross points - friction.
- No stop, target, R:R, IOC tolerance, volume gate, ATR gate, trend gate, time-of-day filter, or news filter.

## Advancement rule

The event is eligible for a separately preregistered bracket/fill study only if, at the
**3.0-point** friction stress:

1. at least 50 resolved episodes exist in each half;
2. mean net points > 0 in H1 and H2;
3. median net points > 0 in H1 and H2;
4. no causality, timestamp, roll, session, or episode-identity defect is found.

A pass is **PROMISING BUT UNPROVEN** only. It cannot activate a strategy.

If any criterion fails, classify this event definition WAIT or BROKEN and stop. Do not rescue
it with volume, ATR, session, trend, stop, or target filters.

## Later filters

Volume, ATR, and time-of-day may be studied only in a later preregistered contribution test
if this friction screen passes. Practitioner thresholds such as 1.5x volume or 0.2x ATR are
not part of this trial.

## Prohibited

- no runtime/strategy/risk/broker/config/Pine changes;
- no paper/DEMO/live activation;
- no stop/target optimization;
- no volume/ATR filter mining;
- no reuse of PDH SHORT after its raw-signal failure.
