# Prereg — MNQ delayed VWAP failed-reclaim Stage A (2026-09-24)

**Trial ID:** `T-2026-09-24-prereg-mnq-vwap-delayed-failed-reclaim-2026-09-24-01`  
**Status:** RESEARCH ONLY / RAW-SIGNAL SCREEN. NO EXECUTION AUTHORITY.

## Why this is a new question

The existing `fns-v0.1` family `VWAP_FAILED_RECLAIM_SHORT` already tested an **immediate**
failure: one completed 5m bar reclaims cumulative RTH VWAP and the very next completed 5m
bar closes back below VWAP. On MNQ that raw 60m signal did not hold across halves
(H1 median +3.56 bps; H2 median -1.84 bps), so that exact definition stays closed.

This trial does **not** rerun or rescue the immediate definition. It tests one materially
different, externally motivated hypothesis:

> After a causal VWAP reclaim, price survives at least one additional completed 5m bar
> without closing back below VWAP, then fails back below cumulative VWAP on bar 2 or 3
> after the reclaim. Does that delayed failure predict further downside?

No ATR, volume, trend, session, or news filter is part of the base signal.

## Frozen population

- Instrument: MNQ only.
- Existing Polygon 5m RTH replay corpus under `data/replay_polygon_5m/MNQ`.
- Session: 09:30–16:00 America/New_York.
- Complete 78-bar RTH sessions only.
- Reuse `research.futures_non_strat_coverage.roll_excluded_sessions`.
- Half split: H1 < 2025-09-01; H2 >= 2025-09-01.
- At most the first qualifying delayed failed-reclaim event per session is scored.

## Frozen causal event

For each completed 5m RTH bar:

1. A reclaim arms when prior close <= prior cumulative RTH VWAP and current close > current cumulative RTH VWAP.
2. The immediately following completed bar must **not** close below its current cumulative VWAP.
   - If it does, that is the already-exposed immediate failed-reclaim family and is excluded.
3. On bar 2 or bar 3 after the reclaim, the first completed bar that closes below its current cumulative RTH VWAP is the event.
4. If no such close occurs by bar 3, the arm expires.
5. Direction is SHORT.
6. A new reclaim may arm only after the prior arm resolves/expires.

## Raw outcome contract

Reference price = event bar close. No execution assumption is applied.

Measure signed SHORT:
- 15m close return;
- 30m close return;
- 60m close return;
- EOD close return;
- 60m MFE and MAE.

No stop, target, R:R, commission, slippage, IOC, volume, ATR, trend, time-of-day, or news filter.

## Advancement rule

Eligible for a separately preregistered friction/fill study only if:

1. >=80 resolved 60m events total;
2. >=30 resolved events in H1 and >=30 in H2;
3. mean signed 60m return > 0 in both halves;
4. median signed 60m return >= 0 in both halves;
5. median 60m MFE > median 60m MAE in both halves;
6. no causality, timestamp, roll, session, or population defect is found.

Passing means **PROMISING BUT UNPROVEN** only. Failing means WAIT/BROKEN and stop.
Do not rescue a failing result with volume, ATR, session, trend, stop or target filters.

## Later filters

Volume/ATR/time-of-day contribution may be tested only after a raw-signal pass, under a
separate preregistration. Practitioner thresholds are not baked into this signal.

## Prohibited

- no runtime, strategy, risk, broker, config, webhook, Pine or deployment changes;
- no paper/DEMO/live activation;
- no re-opening the immediate one-bar failed-reclaim definition;
- no post-result threshold changes.
