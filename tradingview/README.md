# TradingView Alert Setup

## Quick start

1. Open `risksentinel_context.pine`, replace the **entire** contents of your
   TradingView Pine Editor, save it, and add it to an MNQ or MES **15m** chart.
   Do not paste only part of the file; every input, including `i_orb_min`, is
   declared near the top.
2. Create one alert:
   - **Condition** → `RiskSentinel — Full Context` → `Any alert() function call`
   - **Webhook URL** → `https://YOUR-TUNNEL/webhook/alert`
     (add `?secret=YOUR_SECRET` or set header `X-Webhook-Secret`)
   - **Message** → leave blank — the script builds the full JSON body
   - **Expiry** → open-ended
3. The indicator fires once per confirmed 15m bar close. `Send alerts 24 hours`
   defaults to ON so overnight opportunities reach the backend. Turn it OFF to
   restrict alerts to the configured NY, London, and additional alert sessions.
4. After replacing or changing the Pine script, delete and recreate the MNQ and
   MES TradingView alerts. Existing alerts keep a snapshot of the old script.

## What the indicator computes

| Field | Method |
|---|---|
| OHLCV, ticker, timeframe | TradingView built-ins |
| VWAP | `ta.vwap` (session-anchored) |
| ORB high/low | First `i_orb_min` minutes of NY session (default 15) |
| ORB status | Bar-by-bar: inside / above / below / reclaimed_high / rejected_high / … |
| Trend direction/strength | EMA fast vs slow spread |
| Market condition | Volume + range + trend scoring → TRENDING / RANGE_BOUND / CHOPPY / DEAD |
| Previous day H/L/C | `request.security` daily |
| price_vs_pdh/pdl | Derived from close vs PDH/PDL |
| Strat bar types | Bar-by-bar high/low comparison (two_up / two_down / inside / outside) |
| Strat sequence | Three-bar classification: strat_212 / strat_122 / strat_inside_break / … |
| HTF context | Daily, 4H, and 1H bar type + direction via `request.security` |
| FTFC | `UP`, `DOWN`, `MIXED`, or `NEUTRAL`; passive unless config enables HTF gate |

## Alert Sessions

`Send alerts 24 hours` is enabled by default. The NY, London, and Additional
Alert Session inputs remain available for a restricted-session alert mode.

## Manual templates

Use `smoke_test_alert_message.json.tpl` for a minimal curl test (should
produce `NO_TRADE`).  `full_context_alert_message.json.tpl` documents the
full schema for reference.

## Notes

- Live trading remains disabled.  This feeds the paper engine only.
- If the tunnel restarts, update the webhook URL in TradingView.
- The indicator also plots EMA fast/slow, VWAP, and ORB levels on the chart
  as a visual reference.
- For MNQ, use the chart symbol you trade in TradingView. The webhook normalizes
  futures symbols like `MNQ1!` into `MNQ`.
