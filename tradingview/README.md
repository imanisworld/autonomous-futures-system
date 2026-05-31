# TradingView Alert Setup

## Quick start

1. Open `risksentinel_context.pine` in TradingView's Pine Editor and add it to
   your chart (MNQ/MES/MGC/MCL, 3m or 5m timeframe).
2. Create one alert:
   - **Condition** → `RiskSentinel — Full Context` → `Any alert() function call`
   - **Webhook URL** → `https://YOUR-TUNNEL/webhook/alert`
     (add `?secret=YOUR_SECRET` or set header `X-Webhook-Secret`)
   - **Message** → leave blank — the script builds the full JSON body
   - **Expiry** → open-ended
3. The indicator fires once per confirmed bar close.  No fields need to be
   filled in manually.

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
