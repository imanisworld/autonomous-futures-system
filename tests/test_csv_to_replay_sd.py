"""Guard that scripts/csv_to_replay carries supply/demand zone columns from the
TradingView CSV export into the replay JSONL.

S&D zones travel CSV -> JSONL (this converter) -> ReplayCandle (candle_loader)
-> state.sd (replay_engine). The converter was the missing link: candle_loader
and replay_engine already read/build the fields, but the CSV columns were never
mapped, so historical replays had state.sd = None. This locks that link.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.csv_to_replay import convert

# Minimal TradingView-style CSV: two NY-session 15m bars with ORB (bars without
# ORB are intentionally skipped by the converter) plus the S&D zone columns.
_CSV = (
    "time,open,high,low,close,Volume,EMA 9,EMA 21,EMA 55,EMA 200,VWAP,HOD,LOD,"
    "NY ORB High,NY ORB Low,Supply Top,Supply Bottom,Demand Top,Demand Bottom,"
    "Bar Type 1 Label,Bar Type 2 Label,Bar Type 3 Label\n"
    "2026-05-23T09:45:00-04:00,19480,19510,19475,19505,4200,19500,19490,19470,19410,19495,19510,19475,"
    "19498,19462,19530,19522,19478,19470,2U,2U,2U\n"
    "2026-05-23T10:00:00-04:00,19505,19515,19500,19512,3900,19503,19492,19471,19411,19498,19515,19475,"
    "19498,19462,19532,19524,19480,19472,2U,2U,2U\n"
)


def test_convert_carries_supply_demand_zones(tmp_path):
    csv_path = tmp_path / "MNQ_15.csv"
    csv_path.write_text(_CSV, encoding="utf-8")
    out_dir = tmp_path / "out"

    jsonl_path = convert(csv_path, out_dir)
    candles = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line.strip()]

    assert candles, "converter produced no candles"
    # Every emitted candle must carry the four S&D zone fields, non-null.
    for c in candles:
        for field in ("supply_top", "supply_bottom", "demand_top", "demand_bottom"):
            assert c.get(field) is not None, f"candle missing {field}"

    # Values must map straight through from the CSV (first bar).
    first = candles[0]
    assert first["supply_top"] == 19530.0
    assert first["supply_bottom"] == 19522.0
    assert first["demand_top"] == 19478.0
    assert first["demand_bottom"] == 19470.0
