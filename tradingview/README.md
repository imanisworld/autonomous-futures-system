# TradingView Alert Setup

This folder contains paste-ready alert message templates for TradingView.

The webhook URL should be:

```text
https://YOUR-PUBLIC-TUNNEL/webhook/alert?secret=YOUR_LOCAL_SECRET
```

Use `smoke_test_alert_message.json.tpl` first. It should produce `NO_TRADE`.

Then move to `full_context_alert_message.json.tpl` after your TradingView
indicator can provide real VWAP, ORB, previous-day, trend, and Strat values.

Important:

- Do not send fake `0` values for context fields during real testing.
- Omit fields that are not computed yet.
- Live trading remains disabled; this feeds the paper engine only.
- If the tunnel restarts, paste the new tunnel URL into TradingView.
