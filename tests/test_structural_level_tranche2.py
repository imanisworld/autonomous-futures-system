"""Tranche-2 (MGC / MCL / MBT) X0 source-probe tool — synthetic tests, no network."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import structural_level_tranche2_source_probe as t2  # noqa: E402
from sources.polygon_client import PolygonBar  # noqa: E402

UTC = timezone.utc


def test_contract_month_parsing():
    assert t2.contract_month("MGCZ5", "MGC") == (2025, 12)
    assert t2.contract_month("MCLF6", "MCL") == (2026, 1)
    assert t2.contract_month("MBTV6", "MBT") == (2026, 10)
    assert t2.contract_month("MGC", "MGC") is None and t2.contract_month("MGCZ25", "MGC") is None


class _Client:
    """Two monthly contracts: X5 dominant until 2025-10-19, Z5 from 2025-10-20 (volume crossover)."""
    configured = True
    base_url = "https://example.invalid"
    api_key = "x"

    def fetch_bars(self, ticker, s, e, tf=15):
        out = []
        t = datetime(s.year, s.month, s.day, tzinfo=UTC)
        end = datetime(e.year, e.month, e.day, 23, 45, tzinfo=UTC)
        while t <= end:
            if t.weekday() < 5:                      # weekday slots only (continuity accounting exercised)
                late = t.date() >= date(2025, 10, 20)
                vol = (10.0 if late else 100.0) if ticker == "MCLX5" else (100.0 if late else 10.0)
                px = 60.0 + (0.5 if ticker == "MCLZ5" else 0.0)
                out.append(PolygonBar(t, px, px + 0.1, px - 0.1, px, vol, ticker))
            t += timedelta(minutes=15)
        return out


def test_probe_builds_volume_front_chain_and_census():
    inv = [{"ticker": "MCLX5", "first_trade_date": "2024-11-01", "last_trade_date": "2025-10-21", "trading_venue": "XNYM", "active": False},
           {"ticker": "MCLZ5", "first_trade_date": "2024-12-01", "last_trade_date": "2025-11-20", "trading_venue": "XNYM", "active": False},
           {"ticker": "MCLZ9", "first_trade_date": "2020-01-01", "last_trade_date": "2029-11-20", "trading_venue": "XNYM", "active": True}]
    rep = t2.probe("MCL", _Client(), date(2025, 10, 13), date(2025, 10, 24), inventory=inv, raw_rows=9)
    assert rep["inventory"]["unique_tickers"] == 3 and rep["inventory"]["raw_rows"] == 9
    assert rep["inventory"]["contracts_in_window"] == ["MCLX5", "MCLZ5"]        # far-dated Z9 excluded by horizon
    assert rep["inventory"]["month_codes_listed"] == {"X": 1, "Z": 2}
    assert [c["ticker"] for c in rep["volume_front_chain"]] == ["MCLX5", "MCLZ5"]
    x = rep["crossovers"][0]
    assert (x["from"], x["to"], x["crossover_utc_day"]) == ("MCLX5", "MCLZ5", "2025-10-20")
    assert x["both_trading_on_day"] and x["from_bars_after"] > 0 and x["to_bars_before"] > 0
    assert rep["chain_causally_ordered_by_expiry"] is True and rep["chain_flip_flops"] == 0
    assert rep["retention"]["earliest_bar_served"].startswith("2025-10-13")
    cont = rep["continuity_front_contract_by_month"]["2025-10"]
    assert cont["expected_slots"] > 0 and cont["missing_slots"] >= 0


def test_probe_never_imports_runtime():
    imports = [l.strip() for l in (ROOT / "scripts/structural_level_tranche2_source_probe.py").read_text().splitlines()
               if l.startswith(("from ", "import "))]
    for line in imports:
        for bad in ("webhook", "execution", "broker", "replay", "risk", "tradovate", "adaptive", "strategy"):
            assert not line.startswith((f"from {bad}", f"import {bad}")), line


def test_list_contracts_drops_spreads_and_dedupes(monkeypatch):
    rows = [{"ticker": "MBTU6", "first_trade_date": "2025-03-31", "last_trade_date": "2026-09-25", "trading_venue": "XCME", "active": True},
            {"ticker": "MBTU6", "first_trade_date": "2026-01-01", "last_trade_date": "2026-09-25", "trading_venue": "XCME", "active": False},
            {"ticker": "MBTU6-MBTV6", "first_trade_date": "2026-04-27", "last_trade_date": "2026-09-25", "trading_venue": "XCME", "active": True},
            {"ticker": "MBTU6-MBTV6XXX", "first_trade_date": "2026-04-27", "last_trade_date": "2026-09-25", "trading_venue": "XCME", "active": True},
            {"ticker": "MBTV6", "first_trade_date": "2026-04-27", "last_trade_date": "2026-10-30", "trading_venue": "XCME", "active": True}]

    calls = {"n": 0}

    class _Resp:
        def __init__(self, code):
            self.status_code = code
            self.request = None

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": rows, "next_url": None}

    class _Http:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, params=None):
            calls["n"] += 1
            return _Resp(503 if calls["n"] < 3 else 200)      # two transient 5xx, then the page

    monkeypatch.setattr(t2.httpx, "Client", _Http)
    out, raw = t2.list_contracts(_Client(), "MBT", pace=0.0)
    assert calls["n"] == 3 and raw == 5                        # retried through the 503s
    assert [r["ticker"] for r in out] == ["MBTU6", "MBTV6"]              # spreads dropped, U6 de-duplicated
    assert out[0]["first_trade_date"] == "2025-03-31" and out[0]["active"] is True   # widest life kept
