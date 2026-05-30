#!/bin/bash
# Test the Railway webhook with a realistic MNQ orb_reclaim setup.
# Usage: WEBHOOK_SECRET=your_secret bash scripts/test_webhook.sh

SECRET="${WEBHOOK_SECRET:-}"
if [ -z "$SECRET" ]; then
  echo "Error: set WEBHOOK_SECRET before running"
  echo "  WEBHOOK_SECRET=your_secret bash scripts/test_webhook.sh"
  exit 1
fi

URL="https://autonomous-futures-system-production.up.railway.app/webhook/alert?secret=${SECRET}"

curl -s -X POST "$URL" \
  -H "Content-Type: application/json" \
  -d '{
    "ticker": "MNQ1!",
    "timestamp": "2026-05-30T14:45:00+00:00",
    "instrument": "MNQ",
    "open": 21180.0,
    "high": 21210.0,
    "low": 21175.0,
    "close": 21205.0,
    "volume": 4800,
    "avg_volume": 3500,
    "vwap": 21190.0,
    "price_vs_vwap": "above",
    "orb_high": 21200.0,
    "orb_low": 21155.0,
    "orb_status": "reclaimed_high",
    "market_condition": "TRENDING",
    "trend_direction": "UP",
    "trend_strength": "MODERATE",
    "previous_day_high": 21250.0,
    "previous_day_low": 21100.0,
    "previous_day_close": 21200.0,
    "price_vs_pdh": "below",
    "price_vs_pdl": "above",
    "timeframe": "5m",
    "current_bar_type": "two_up",
    "previous_bar_type": "inside_bar",
    "two_bars_back_type": "two_up",
    "strat_sequence": "strat_212",
    "strat_trigger": "continuation",
    "strat_direction": "LONG",
    "previous_bar_high": 21195.0,
    "previous_bar_low": 21178.0,
    "supply_zone_high": 21260.0,
    "supply_zone_low": 21240.0,
    "demand_zone_high": 21170.0,
    "demand_zone_low": 21150.0,
    "zone_type": "demand",
    "zone_state": "fresh",
    "gex_flip": 21200.0,
    "call_wall": 21300.0,
    "put_wall": 21050.0,
    "signa_grade": "A",
    "signa_weekly_direction": "UP"
  }' | python3 -m json.tool
