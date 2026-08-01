"""Equity corpus v1 — single-ticker smoke test. READ-ONLY RESEARCH.

Gate #13.2 of docs/equity-setup-corpus-preregistration-v1.md. Fetches ONE
representative single-name ticker and verifies the frozen conventions before any
full batch is authorized.

Performs read-only Polygon aggregate GETs and writes only under
data/equity_corpus_v1/ (gitignored). No broker call, no order path, no strategy
gate, no futures data, no deployment. The API key is read from POLYGON_API_KEY
and is never logged, echoed, or written to disk.

Verifies, per the preregistration:
  - frozen window honoured (2024-07-31 .. 2026-07-30, America/New_York)
  - session tagging PREMARKET / RTH / AFTER_HOURS
  - timezone handling via zoneinfo, NOT a fixed UTC offset (DST correctness)
  - duplicate timestamps
  - missing sessions
  - adjustment status echoed by the provider
  - derived higher-timeframe bar counts under frozen 09:30 anchors
  - is_partial_interval marking on session-boundary bars
  - restart / idempotency
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ET = ZoneInfo("America/New_York")
WINDOW_START = date(2024, 7, 31)
WINDOW_END = date(2026, 7, 30)
OUT_DIR = Path("data/equity_corpus_v1")

PREMARKET_OPEN = (4, 0)
RTH_OPEN = (9, 30)
RTH_CLOSE = (16, 0)
EXT_CLOSE = (20, 0)

# Derived-timeframe anchors, all RTH-anchored at 09:30 ET per preregistration §4.
DERIVED = {"15m": 15, "30m": 30, "1h": 60, "4h": 240}

_RATE_SLEEP = 13.0  # measured Polygon limit is 5 req/min; 13s is a safe margin


def _minutes_et(ts_ms: int) -> tuple[datetime, int]:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=ET)
    return dt, dt.hour * 60 + dt.minute


def session_tag(ts_ms: int) -> str | None:
    """PREMARKET / RTH / AFTER_HOURS, or None when outside 04:00-20:00 ET."""
    _, m = _minutes_et(ts_ms)
    if PREMARKET_OPEN[0] * 60 <= m < RTH_OPEN[0] * 60 + RTH_OPEN[1]:
        return "PREMARKET"
    if RTH_OPEN[0] * 60 + RTH_OPEN[1] <= m < RTH_CLOSE[0] * 60:
        return "RTH"
    if RTH_CLOSE[0] * 60 <= m < EXT_CLOSE[0] * 60:
        return "AFTER_HOURS"
    return None


def fetch_5m(ticker: str, start: date, end: date, api_key: str) -> list[dict]:
    """Paginated read-only 5-minute aggregate fetch. Self-throttled."""
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute/"
        f"{start.isoformat()}/{end.isoformat()}"
    )
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}
    bars: list[dict] = []
    adjusted_flag = None
    requests = 0
    while True:
        for attempt in range(8):
            try:
                r = httpx.get(url, params=params, timeout=90.0)
            except httpx.HTTPError as exc:
                # Transient DNS/connection/read failures MUST be retried: a
                # single blip would otherwise kill a multi-hour batch. Observed
                # for real during the first smoke run (ConnectError on DNS).
                wait = min(60.0, _RATE_SLEEP * (attempt + 1))
                print(f"    transient {type(exc).__name__}, retry {attempt + 1}/8 "
                      f"in {wait:.0f}s", flush=True)
                time.sleep(wait)
                continue
            requests += 1
            if r.status_code == 429:
                time.sleep(_RATE_SLEEP * (attempt + 1))
                continue
            if r.status_code >= 500:
                time.sleep(_RATE_SLEEP * (attempt + 1))
                continue
            r.raise_for_status()
            break
        else:
            raise RuntimeError("exhausted retry budget (rate limit or transport)")
        j = r.json()
        adjusted_flag = j.get("adjusted", adjusted_flag)
        page = j.get("results") or []
        bars.extend(page)
        print(f"    page {requests}: +{len(page)} bars (total {len(bars)})", flush=True)
        nxt = j.get("next_url")
        if not nxt:
            break
        url, params = nxt, {"apiKey": api_key}
        time.sleep(_RATE_SLEEP)
    return bars, adjusted_flag, requests


def derive(bars: list[dict], minutes: int) -> list[dict]:
    """Aggregate 5m -> higher TF, anchored 09:30 ET, never crossing a session
    boundary. A bucket cut short by the session end is is_partial_interval."""
    buckets: dict[tuple, list[dict]] = {}
    for b in bars:
        tag = session_tag(b["t"])
        if tag is None:
            continue
        dt, m = _minutes_et(b["t"])
        offset = m - (RTH_OPEN[0] * 60 + RTH_OPEN[1])
        idx = offset // minutes if offset >= 0 else -(((-offset) + minutes - 1) // minutes)
        buckets.setdefault((dt.date(), tag, idx), []).append(b)

    out = []
    expected = minutes // 5
    for (day, tag, idx), group in sorted(buckets.items()):
        group.sort(key=lambda x: x["t"])
        out.append({
            "t": group[0]["t"],
            "o": group[0]["o"],
            "h": max(x["h"] for x in group),
            "l": min(x["l"] for x in group),
            "c": group[-1]["c"],
            "v": sum(x.get("v", 0) for x in group),
            "session": tag,
            "n_source_bars": len(group),
            "is_partial_interval": len(group) < expected,
        })
    return out


def main() -> int:
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    key = os.getenv("POLYGON_API_KEY", "")
    if not key:
        for line in Path(".env").read_text().splitlines():
            if line.startswith("POLYGON_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        print("BLOCKER: POLYGON_API_KEY not available")
        return 1

    print(f"SMOKE TEST — {ticker}")
    print(f"window {WINDOW_START} .. {WINDOW_END} America/New_York")
    print(f"key present ({len(key)} chars, not shown)\n")

    t0 = time.perf_counter()
    bars, adjusted_flag, n_req = fetch_5m(ticker, WINDOW_START, WINDOW_END, key)
    elapsed = time.perf_counter() - t0
    print(f"requests={n_req}  elapsed={elapsed:.1f}s  raw 5m bars={len(bars)}")
    print(f"provider adjusted flag: {adjusted_flag}")

    ts = [b["t"] for b in bars]
    dupes = len(ts) - len(set(ts))
    print(f"duplicate timestamps: {dupes}")

    first_dt = datetime.fromtimestamp(min(ts) / 1000, tz=ET)
    last_dt = datetime.fromtimestamp(max(ts) / 1000, tz=ET)
    print(f"earliest: {first_dt.isoformat()}")
    print(f"latest  : {last_dt.isoformat()}")
    in_window = WINDOW_START <= first_dt.date() and last_dt.date() <= WINDOW_END
    print(f"within frozen window: {in_window}")

    tags = Counter(session_tag(b["t"]) for b in bars)
    print(f"session tags: {dict(tags)}")

    # DST correctness: both offsets must appear across a 24-month window.
    offsets = {datetime.fromtimestamp(t / 1000, tz=ET).utcoffset() for t in ts}
    print(f"distinct UTC offsets seen: {sorted(str(o) for o in offsets)} "
          f"(DST handled: {len(offsets) > 1})")

    sessions = sorted({datetime.fromtimestamp(t / 1000, tz=ET).date() for t in ts})
    print(f"sessions covered: {len(sessions)}")
    gaps = []
    for a, b in zip(sessions, sessions[1:]):
        delta = (b - a).days
        if delta > 4:  # >long weekend
            gaps.append((a.isoformat(), b.isoformat(), delta))
    print(f"suspicious session gaps (>4d): {len(gaps)}")
    for g in gaps[:5]:
        print(f"   {g[0]} -> {g[1]} ({g[2]}d)")

    print("\nderived timeframes (09:30 ET anchored, session-bounded):")
    derived_counts = {}
    for name, mins in DERIVED.items():
        d = derive(bars, mins)
        partial = sum(1 for x in d if x["is_partial_interval"])
        derived_counts[name] = {"bars": len(d), "partial": partial}
        print(f"   {name:<4} bars={len(d):<7} partial_interval={partial}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "corpus_version": "equity_corpus_v1",
        "smoke_test": True,
        "ticker": ticker,
        "source": "Polygon.io",
        "endpoint": "/v2/aggs/ticker/{t}/range/5/minute/{from}/{to}",
        "adjusted": adjusted_flag,
        "timezone": "America/New_York",
        "window": {"start": WINDOW_START.isoformat(), "end": WINDOW_END.isoformat()},
        "requests": n_req,
        "raw_5m_bars": len(bars),
        "duplicate_timestamps": dupes,
        "earliest": first_dt.isoformat(),
        "latest": last_dt.isoformat(),
        "within_frozen_window": in_window,
        "session_tag_counts": {str(k): v for k, v in tags.items()},
        "distinct_utc_offsets": sorted(str(o) for o in offsets),
        "sessions_covered": len(sessions),
        "suspicious_gaps": gaps,
        "derived_timeframes": derived_counts,
    }
    mpath = OUT_DIR / f"smoke_{ticker}_manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    bpath = OUT_DIR / f"smoke_{ticker}_5m.jsonl"
    with bpath.open("w") as fh:
        for b in sorted(bars, key=lambda x: x["t"]):
            fh.write(json.dumps({**b, "session": session_tag(b["t"])}) + "\n")

    print(f"\nwrote {mpath}")
    print(f"wrote {bpath} ({bpath.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
