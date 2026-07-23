# DETECTOR SPECIFICATIONS

Behavioral specs for coded strategy detectors. These specs tell Claude Code exactly what each detector must do. Build and reconcile one detector at a time. Do not build all three simultaneously.

**Build order: 4HR → reconcile → Miyagi → reconcile → 3-2-2 → reconcile**

---

## General Requirements (all detectors)

Every detector must:

- Be a pure, stateless function — no global state, no side effects
- Take only historical bars as input (no lookahead)
- Return a structured signal object or `None`
- Handle all edge cases explicitly — no silent failures
- Be testable against the manual sample dates

### Input format (all detectors)

```python
bars: list[dict]  # ordered oldest to newest
# Each bar has:
# {
#   "ts": datetime,      # bar open timestamp, ET-aware
#   "open": float,
#   "high": float,
#   "low": float,
#   "close": float,
#   "volume": float,
#   "timeframe": str     # "4H", "12H", "60M", "5M", "1H"
# }
```

### Output format (all detectors)

```python
{
  "signal": bool,                      # True = setup detected
  "direction": str,                    # "LONG" (calls) or "SHORT" (puts)
  "entry_trigger": float,              # exact price level that triggers entry
  "stop_reference": float,             # stop price at 9:30 AM reference time
                                       # NOTE: actual executable stop depends on
                                       # real entry time — replay engine must
                                       # recalculate using actual entry bar
  "stop_reference_bar_ts": datetime,   # which 1H bar produced the reference stop
  "target": float,                     # target price
  "setup_bar_ts": datetime,            # timestamp of the bar that completes the setup
  "entry_window_open": datetime,       # earliest valid entry time
  "entry_window_close": datetime,      # latest valid entry time (setup void after)
  "reference_candle_high": float,      # prior 4PM candle high (or equivalent)
  "reference_candle_low": float,       # prior 4PM candle low (or equivalent)
  "invalidation": str | None,          # reason setup was invalidated, or None
}
```

---

## Detector 1 — 4HR Re-Trigger

Build this first. Reconcile against manual samples before building Detector 2.

### Function signature

```python
def detect_4hr_retrigger(
    bars_4h: list[dict],      # 4-hour bars, ET-anchored (4AM/8AM/12PM/4PM windows)
    bars_5m: list[dict],      # 5-minute bars for retrace confirmation
    bars_1h: list[dict],      # 1-hour bars for stop reference calculation
    eval_date: date,          # the trading date being evaluated
    instrument: str,          # "MNQ", "MES", or "QQQ"
) -> dict | None
```

### Step-by-step logic

**Step 1 — Identify the prior 4PM candle**

- For MNQ/MES on Tuesday–Friday: prior 4PM candle = the bar opening at 4:00 PM ET on the previous calendar trading day
- For MNQ/MES on Monday: prior 4PM candle = the bar opening at 4:00 PM ET on Sunday
- For QQQ on Tuesday–Friday: prior 4PM candle = the bar opening at 4:00 PM ET on the previous calendar trading day
- For QQQ on Monday: prior 4PM candle = the bar opening at 4:00 PM ET on the prior Friday (no Sunday session)
- If prior 4PM candle does not exist in `bars_4h` → return `None`

**Step 2 — Identify the 4AM candle**

- The 4AM candle = bar with open timestamp at `eval_date` 4:00 AM ET
- If this bar does not exist → return `None`

**Step 3 — Classify the 4AM candle**

- CALLS setup (2DOWN): 4AM high ≤ prior 4PM high AND 4AM low < prior 4PM low
- PUTS setup (2UP): 4AM low ≥ prior 4PM low AND 4AM high > prior 4PM high
- Inside bar (high ≤ prior high AND low ≥ prior low) → return `None`
- Outside bar (high > prior high AND low < prior low) → return `None` (ambiguous)
- Any other case → return `None`

**Step 4 — Identify the 8AM candle**

- The 8AM candle = bar with open timestamp at `eval_date` 8:00 AM ET
- If this bar does not exist → return `None`

**Step 5 — Classify the 8AM candle**

- For CALLS: 8AM high must break ABOVE the 4AM high (8AM is 2UP vs 4AM)
- For PUTS: 8AM low must break BELOW the 4AM low (8AM is 2DOWN vs 4AM)
- If condition not met → return `None`

**Step 6 — Confirm retrace via 5-minute bars**

- Filter `bars_5m` to bars where `ts >= eval_date 8:00 AM ET AND ts < eval_date 9:30 AM ET`
- For CALLS: find the first 5-minute bar whose CLOSE is below the 4AM high
- For PUTS: find the first 5-minute bar whose CLOSE is above the 4AM low
- Intrabar touches do not count — close only
- If no such bar exists in the window → return `None`

**Step 7 — Check 9:30 AM state**

- Find the 5-minute bar whose open timestamp = `eval_date` 9:30 AM ET
- For CALLS: bar open must be below the 4AM high
- For PUTS: bar open must be above the 4AM low
- If condition not met → return `{signal: False, invalidation: "PRICE_THROUGH_TRIGGER_AT_OPEN"}`

**Step 8 — Calculate stop reference**

- Filter `bars_1h` to bars where `ts < eval_date 9:30 AM ET`
- The stop reference bar = the last bar in that filtered list (most recently completed 1H bar before 9:30 AM)
- At 9:30 AM this will be the 8:00–9:00 AM bar
- Stop reference for CALLS = low of that 1H bar
- Stop reference for PUTS = high of that 1H bar
- Record `stop_reference_bar_ts` so the replay engine knows which bar was used

**Step 9 — Return signal**

```python
return {
    "signal": True,
    "direction": "LONG" if calls else "SHORT",
    "entry_trigger": four_am_high if calls else four_am_low,
    "stop_reference": stop_price,
    "stop_reference_bar_ts": stop_bar_ts,
    "target": prior_4pm_high if calls else prior_4pm_low,
    "setup_bar_ts": eight_am_bar_ts,
    "entry_window_open": eval_date_9_30_am_et,
    "entry_window_close": eval_date_11_00_am_et,
    "reference_candle_high": prior_4pm_high,
    "reference_candle_low": prior_4pm_low,
    "invalidation": None,
}
```

### Stop note for replay engine

The detector returns `stop_reference` based on the 9:30 AM anchor. The replay engine MUST recalculate the actual stop based on the real entry time:

- Entry between 9:30–10:00 AM → stop = low/high of 8:00–9:00 AM bar (same as reference)
- Entry between 10:00–11:00 AM → stop = low/high of 9:00–10:00 AM bar
- Do NOT use `stop_reference` as the executable stop without checking entry time

### Edge cases

- 4AM bar missing → return `None`
- 8AM bar not yet closed → return `None`
- Multiple 5-min bars retrace before 9:30 AM → use the FIRST one only
- Sunday session missing for MNQ Monday → return `None`, do not fall back to Friday
- 4AM bar is outside bar → return `None`

---

## Detector 2 — 12HR Miyagi

Build only after 4HR detector is built AND reconciled. Do not start this until reconciliation passes.

### Function signature

```python
def detect_12hr_miyagi(
    bars_12h: list[dict],     # 12-hour bars, ET-anchored (4AM–4PM, 4PM–4AM windows)
    bars_5m: list[dict],      # 5-minute bars for 9:30 AM state check and invalidation
    bars_60m: list[dict],     # 60-minute bars for stop calculation
    eval_date: date,          # the trading date being evaluated
    instrument: str,          # "MNQ", "MES", or "QQQ"
) -> dict | None
```

### Candle identification

The 1-3-1 pattern uses four consecutive 12-hour bars. Name them in chronological order:

```
Bar A  →  Bar B  →  Bar C  →  Bar D (live)
```

- Bar D (live): 12H bar opening at `eval_date` 4:00 AM ET
- Bar C (inside bar, Candle 3): 12H bar opening at prior day 4:00 PM ET
- Bar B (outside bar, Candle 2): 12H bar opening at prior day 4:00 AM ET
- Bar A (first inside bar, Candle 1): 12H bar opening two days prior at 4:00 PM ET

If any of Bar A, B, C, D do not exist in `bars_12h` → return `None`

### Step-by-step logic

**Step 1 — Verify Bar C is inside Bar B (Candle 3 is inside Candle 2)**

- Bar C high ≤ Bar B high AND Bar C low ≥ Bar B low
- If not → return `None`

**Step 2 — Verify Bar B is outside Bar A (Candle 2 is outside Candle 1)**

- Bar B high > Bar A high AND Bar B low < Bar A low
- If not → return `None`

**Step 3 — Verify Bar A is inside the bar before it (Candle 1 is inside its prior bar)**

- Identify Bar Z: the 12H bar immediately before Bar A (opening at two days prior 4:00 AM ET)
- If Bar Z does not exist → return `None`
- Bar A high ≤ Bar Z high AND Bar A low ≥ Bar Z low
- If not → return `None`

**Step 4 — Calculate trigger level**

- Trigger = (Bar C high + Bar C low) / 2

**Step 5 — Check Bar C integrity before 9:30 AM using 5-minute bars**

- Filter `bars_5m` to bars where `ts >= eval_date 4:00 AM ET AND ts < eval_date 9:30 AM ET`
- For each 5-minute bar in this window: check if high > Bar C high AND low < Bar C low
- If any single 5-minute bar satisfies both conditions → Bar C became an outside bar
- Return `{signal: False, invalidation: "CANDLE3_BECAME_OUTSIDE_BAR"}`

**Step 6 — Confirm Bar D direction at 9:30 AM using 5-minute bars**

- Find the 5-minute bar with open timestamp = `eval_date` 9:30 AM ET
- If this bar does not exist → return `None`
- Price at 9:30 AM = that bar's open
- Price > Bar C high → Bar D is 2U → direction = SHORT (puts)
- Price < Bar C low → Bar D is 2D → direction = LONG (calls)
- Price between Bar C high and Bar C low → return `None` (no setup)

**Step 7 — Calculate stop reference using 60-minute bars**

- Filter `bars_60m` to bars where `ts < eval_date 9:30 AM ET`
- Stop reference bar = last bar in that filtered list (most recently completed 60-min bar before 9:30 AM)
- At 9:30 AM this will be the 8:00–9:00 AM bar
- Stop for LONG (calls) = low of that bar
- Stop for SHORT (puts) = high of that bar
- Stop is FIXED — does not update as trade progresses
- Record `stop_reference_bar_ts`

**Step 8 — Set targets**

- T1: Bar C high (LONG) or Bar C low (SHORT)
- T2: Bar B high (LONG) or Bar B low (SHORT)

**Step 9 — Return signal**

```python
return {
    "signal": True,
    "direction": "LONG" or "SHORT",
    "entry_trigger": trigger,
    "stop_reference": stop_price,
    "stop_reference_bar_ts": stop_bar_ts,
    "target": t1_price,
    "target_2": t2_price,
    "setup_bar_ts": bar_d_ts,
    "entry_window_open": eval_date_9_30_am_et,
    "entry_window_close": None,
    "reference_candle_high": bar_b_high,
    "reference_candle_low": bar_b_low,
    "bar_c_high": bar_c_high,
    "bar_c_low": bar_c_low,
    "invalidation": None,
}
```

### Edge cases

- Bar Z (bar before Bar A) missing → return `None`
- Bar C becomes outside bar before 9:30 AM → invalidated
- Price exactly equals Bar C high or low at 9:30 AM → return `None` (ambiguous)
- 12H bar alignment: bars must be anchored at 4:00 AM ET and 4:00 PM ET exactly

---

## Detector 3 — 60M 3-2-2 First Live

Build only after Miyagi detector is built AND reconciled.

### Function signature

```python
def detect_322_first_live(
    bars_60m: list[dict],     # 60-minute bars, ET-anchored
    eval_date: date,          # the trading date being evaluated
    instrument: str,          # "MNQ" only — return None for all other instruments
) -> dict | None
```

### Step-by-step logic

**Step 0 — Instrument check**

- If `instrument != "MNQ"` → return `None` (not validated for other instruments)

**Step 1 — Identify the candles**

- 7AM candle: bar with open timestamp at `eval_date` 7:00 AM ET
- 8AM candle: bar with open timestamp at `eval_date` 8:00 AM ET
- 9AM candle: bar with open timestamp at `eval_date` 9:00 AM ET
- If any of these do not exist → return `None`
- 10AM candle checked during entry phase only

**Step 2 — Verify 8AM is outside bar relative to 7AM**

- 8AM high > 7AM high AND 8AM low < 7AM low
- If not → return `None`

**Step 3 — Classify 9AM candle direction**

- 9AM candle closes at 10:00 AM ET — classification uses the completed 9AM bar
- PUTS setup (9AM is 2UP): 9AM high > 8AM high AND 9AM low ≥ 8AM low
- CALLS setup (9AM is 2DOWN): 9AM low < 8AM low AND 9AM high ≤ 8AM high
- Inside bar → return `None`
- Outside bar → return `None`
- Any other case → return `None`

**Step 4 — Set trigger and stop**

- For PUTS: `entry_trigger` = 9AM low, stop = 9AM high
- For CALLS: `entry_trigger` = 9AM high, stop = 9AM low

**Step 5 — Check 10AM candle for first live break**

- The 10AM candle opens at `eval_date` 10:00 AM ET
- If 10AM bar does not exist → return `None`
- For PUTS:
  - If 10AM open < 9AM low (gap down) → valid, entry_price = 10AM open
  - Else if 10AM low < 9AM low at any point → valid, entry_price = 9AM low
  - Else → return `{signal: False, invalidation: "NO_BREAK_BY_11AM"}`
- For CALLS:
  - If 10AM open > 9AM high (gap up) → valid, entry_price = 10AM open
  - Else if 10AM high > 9AM high at any point → valid, entry_price = 9AM high
  - Else → return `{signal: False, invalidation: "NO_BREAK_BY_11AM"}`

**Step 6 — Return signal**

```python
return {
    "signal": True,
    "direction": "SHORT" if puts else "LONG",
    "entry_trigger": nine_am_low if puts else nine_am_high,
    "stop_reference": nine_am_high if puts else nine_am_low,
    "stop_reference_bar_ts": nine_am_bar_ts,
    "target": eight_am_low if puts else eight_am_high,
    "setup_bar_ts": nine_am_bar_ts,
    "entry_window_open": eval_date_10_00_am_et,
    "entry_window_close": eval_date_11_00_am_et,
    "nine_am_range_points": nine_am_high - nine_am_low,  # context field
    "reference_candle_high": eight_am_high,
    "reference_candle_low": eight_am_low,
    "invalidation": None,
}
```

### Edge cases

- Instrument not MNQ → return `None` immediately
- Gap open at 10AM through trigger → valid, use 10AM open as entry price
- 10AM candle opens exactly at trigger → first tick through = entry at trigger price
- 9AM range is zero (flat bar) → return `None`
- 9AM bar not yet closed at eval time → do not classify until 10:00 AM

---

## Reconciliation Protocol

Run this after each detector is built. Do not proceed to the next detector until this passes.

### Protocol

1. Run detector on the full date range used in the manual study
2. Collect all dates where detector returns `signal=True`
3. Compare against the manual sample dates from the external researcher
4. Compute: dates in both (true positives), dates in detector only (false positives), dates in manual only (false negatives)

### Pass criteria

- True positive rate ≥ 95% (detector finds ≥95% of manually identified entries)
- False positive rate ≤ 10% (detector does not fire excessively on dates manual study rejected)

### On fail

- Do not proceed to backtest
- Identify each discrepancy: data difference, rule interpretation difference, or bar alignment difference
- Fix the detector or the spec, not the manual sample
- Re-run reconciliation until pass criteria are met

---

## Replay Engine Requirements

After reconciliation passes, the replay engine must:

1. Use IOC-faithful fills — not always-fills
2. Apply 2-tick adverse slippage on entry AND exit separately
3. Deduct $1.24 round-trip commission (MNQ/MES) or equivalent
4. For same-bar stop and target touch: stop wins (pessimistic)
5. For 4HR Re-Trigger specifically: recalculate stop using actual entry bar, not `stop_reference`. Find the last completed 1H bar before actual entry timestamp. Use its low (LONG) or high (SHORT).
6. Walk-forward: split at exact chronological midpoint of the date range
7. Report per run: n, fill rate, win rate, gross P&L, total costs, net P&L, expectancy per signal, expectancy per fill, PF, avg win, avg loss, max DD, H1 and H2 separately, LONG vs SHORT split
8. Slippage sensitivity: run at 1-tick, 2-tick, 3-tick, 4-tick adverse
9. One position at a time — no overlapping trades on the same instrument
