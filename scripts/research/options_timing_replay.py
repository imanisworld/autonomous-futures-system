"""Options scanner detection-timing replay -- STEP 0 (parity) ONLY.

trial_id: T-2026-09-24-prereg-options-scanner-timing-2026-09-24-01
prereg:   docs/prereg-options-scanner-timing-2026-09-24.md

OFFLINE / READ-ONLY. Replays variant ``v0_baseline_sip_960_close_confirm`` (the
current production scanner) over the Step 0 parity window and compares the
replayed suppression-reason CLASS of every row with the row production
recorded. No outcome, P&L or expectancy is computed here, and no other
variant is implemented: v1/v2/v3 and the scoring window need separate GO.

The replay drives the real wrapped ``alert_ranker.scanner.OptionsScanner``
unmodified. Only its inputs are substituted:

* bars   -- Alpaca SIP 1-minute history aggregated to 30Min, served causally
            (only bars fully closed by each scan's information cutoff);
* quote  -- close of the last completed SIP 1-minute bar at the scan time
            (the prereg's stated proxy for the live quote);
* chain  -- unavailable (historical option quotes are not replayable); this
            can only affect rows that already passed the late-entry gate, which
            are compared as one class;
* Discord-- no webhook and a transport that refuses any request.

Subcommands::

    pull   --out DIR --first-scan ISO   fetch SIP 1m bars (Step 0 window only)
    probe-iex                           one read-only IEX entitlement probe
    step0  --bars DIR --prod JSON --out JSON   replay + parity report
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import gzip
import json
import os
import sys
import tempfile
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRIAL_ID = "T-2026-09-24-prereg-options-scanner-timing-2026-09-24-01"
VARIANT = "v0_baseline_sip_960_close_confirm"
STEP0_START = datetime(2026, 9, 15, 16, 50, 0, tzinfo=timezone.utc)
# Start of the #949 forward cohort. Nothing at or after this instant may be
# pulled, replayed or scored by this trial.
EXCLUDED_FROM = datetime(2026, 9, 23, 14, 7, 8, tzinfo=timezone.utc)
PASS_THRESHOLD = 0.95
LOOKBACK_DAYS = 10
DELAY_BUFFER = timedelta(seconds=960)

WATCHLIST = (
    "AAPL,MSFT,NVDA,TSLA,SPY,QQQ,AMZN,GOOGL,PLTR,INTC,"
    "IWM,TLT,JPM,BAC,COIN,XOM,MRK,WMT,NFLX,GE"
).split(",")
SYMBOLS = tuple(dict.fromkeys([*WATCHLIST, "SPY", "QQQ"]))

# Production scanner env (non-secret values read from the live process on
# 2026-09-24). Credentials are replaced by placeholders: no provider is called.
REPLAY_ENV = {
    "OPTIONS_SCANNER_ENABLED": "true",
    "OPTIONS_MARKET_DATA_PROVIDER": "public",
    "PUBLIC_API_KEY": "replay-placeholder",
    "PUBLIC_ACCOUNT_ID": "replay-placeholder",
    "ALPACA_KEY": "replay-placeholder",
    "ALPACA_SECRET": "replay-placeholder",
    "OPTIONS_BAR_CONTEXT_ENABLED": "true",
    "OPTIONS_BAR_CONTEXT_FEED": "sip",
    "OPTIONS_BAR_CONTEXT_LOOKBACK_DAYS": str(LOOKBACK_DAYS),
    "OPTIONS_BAR_CONTEXT_TIMEFRAME": "30Min",
    "OPTIONS_SIP_DELAY_BUFFER_SECONDS": str(int(DELAY_BUFFER.total_seconds())),
    "OPTIONS_SCANNER_WATCHLIST": ",".join(WATCHLIST),
    "OPTIONS_SCANNER_DISCORD_WEBHOOK_URL": "",
    "OPTIONS_PAPER_V1_COLLECTION_ENABLED": "true",
    "OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS": "1000",
    "OPTIONS_MANAGER_RISK_MIN_DTE_DAYS": "14",
    "OPTIONS_COMPANION_ENABLED": "false",
    # Signa is display-only (scorer contribution 0); the replay has no Signa.
    "SIGNA_API_ENABLED": "false",
    "OPTIONS_SIGNA_CONTEXT_PULL_ENABLED": "false",
    "OPTIONS_SIGNA_V2_OBSERVE_ENABLED": "false",
}


# --------------------------------------------------------------------------
# Reason classes (frozen before any comparison was run)
# --------------------------------------------------------------------------

_BAR_CONTEXT_PREFIXES = (
    "missing_bars", "stale_market_data", "missing_context", "no_completed_bars",
    "bar_context", "provider_", "missing_symbol", "feed_not_consolidated",
    "no_session", "insufficient_history", "unsupported_timeframe", "calendar",
    "pagination_truncated", "invalid_window", "naive_timestamp", "missing_inputs:vwap",
)
_LEVEL_INVALID = (
    "DATA_INVALID:direction_unknown",
    "DATA_INVALID:underlying_invalidation_missing",
    "DATA_INVALID:target_missing",
)


def reason_class(reason: str | None, alert_sent: bool = False) -> str:
    """Map a raw ``alert_suppression_reason`` to its Step 0 comparison class.

    Everything downstream of the late-entry gate (contract, risk, score,
    delivery) is one class: option chains cannot be replayed historically.
    """
    if alert_sent:
        return "PAST_LATE_GATE"
    r = (reason or "").strip()
    if not r:
        return "PAST_LATE_GATE"
    if r == "session_not_started":
        return "SESSION_NOT_STARTED"
    if r == "no_session_bars":
        return "NO_SESSION_BARS"
    if r == "ENTRY_LATE:episode_blocked_after_entry_late":
        return "ENTRY_LATE_BLOCKED"
    if r.startswith("ENTRY_LATE:") or r.startswith("entry_late_counterfactual_only:"):
        return "ENTRY_LATE_FIRST"
    if r == "counterfactual_observer_only":
        return "OBSERVER"
    if r.startswith("no_setup"):
        return "NO_SETUP"
    if r.startswith("setup_forming"):
        return "SETUP_FORMING"
    if r.startswith("setup_proof_incomplete"):
        return "SETUP_PROOF_INCOMPLETE"
    if r.startswith(_LEVEL_INVALID):
        return "LEVELS_INVALID"
    if r.startswith(_BAR_CONTEXT_PREFIXES):
        return "BAR_CONTEXT_OTHER"
    return "PAST_LATE_GATE"


# --------------------------------------------------------------------------
# Bars
# --------------------------------------------------------------------------

def _guard_before_exclusion(moment: datetime, what: str) -> None:
    if moment >= EXCLUDED_FROM:
        raise ValueError(f"{what} {moment.isoformat()} is at/after the excluded boundary")


def aggregate_30m(minute_bars: Sequence[Any]) -> list[Any]:
    """Aggregate 1Min bars into clock-aligned 30Min bars (Alpaca alignment)."""
    from alert_ranker.causal_bars import Bar

    buckets: dict[datetime, list[Any]] = collections.OrderedDict()
    for bar in sorted(minute_bars, key=lambda b: b.start_utc):
        s = bar.start_utc
        key = s.replace(minute=(s.minute // 30) * 30, second=0, microsecond=0)
        buckets.setdefault(key, []).append(bar)
    out = []
    for start, group in buckets.items():
        volume = sum(b.volume for b in group)
        weighted = [(b.vwap, b.volume) for b in group if b.vwap is not None]
        vwap = (
            sum(v * w for v, w in weighted) / sum(w for _, w in weighted)
            if weighted and sum(w for _, w in weighted) > 0
            else None
        )
        out.append(
            Bar(
                start=start,
                open=group[0].open,
                high=max(b.high for b in group),
                low=min(b.low for b in group),
                close=group[-1].close,
                volume=volume,
                vwap=vwap,
            )
        )
    return out


class HistoricalBarProvider:
    """Serves only bars fully closed by ``end`` (strictly causal)."""

    feed = "sip"

    def __init__(self, bars_30m: dict[str, list[Any]]):
        self._bars = {k.upper(): sorted(v, key=lambda b: b.start_utc) for k, v in bars_30m.items()}

    async def fetch_bars(self, symbols, timeframe, start, end):
        from alert_ranker.bar_provider import BarProviderError

        if timeframe.name != "30Min":
            raise BarProviderError("provider_error", f"replay serves 30Min only, got {timeframe.name}")
        _guard_before_exclusion(end, "bar request end")
        out: dict[str, list[Any]] = {}
        for symbol in symbols:
            key = symbol.strip().upper()
            rows = [
                b for b in self._bars.get(key, [])
                if b.start_utc >= start and b.start_utc + timeframe.delta <= end
            ]
            out[key] = rows
        missing = sorted(k for k, v in out.items() if not v)
        if missing:
            raise BarProviderError("missing_symbol", ",".join(missing))
        return out


class LocalCalendar:
    async def session_for(self, day: date):
        from alert_ranker.session_calendar import nyse_session_for

        return nyse_session_for(day)


class ReplayChainUnavailable(RuntimeError):
    pass


class ReplayMarketData:
    """Quote proxy from SIP 1-minute closes; option chains are unavailable."""

    def __init__(self, minute_bars: dict[str, list[Any]]):
        self._bars = {k.upper(): sorted(v, key=lambda b: b.start_utc) for k, v in minute_bars.items()}
        self.now: datetime | None = None

    def _completed(self, ticker: str) -> list[Any]:
        assert self.now is not None
        now = self.now.astimezone(timezone.utc)
        return [b for b in self._bars.get(ticker.upper(), []) if b.start_utc + timedelta(minutes=1) <= now]

    async def fetch_market_snapshot(self, ticker: str):
        from alert_ranker.tastytrade_client import MarketSnapshot

        done = self._completed(ticker)
        if not done:
            return MarketSnapshot(ticker=ticker.upper(), error="replay_no_quote")
        last = done[-1]
        session_day = self.now.astimezone(timezone.utc).date()
        day_volume = sum(b.volume for b in done if b.start_utc.date() == session_day)
        return MarketSnapshot(
            ticker=ticker.upper(),
            price=last.close,
            volume=day_volume,
            quote_timestamp=(last.start_utc + timedelta(minutes=1)).isoformat(),
            raw={"replay_quote": "sip_1m_close"},
        )

    async def fetch_option_expirations(self, ticker: str):
        raise ReplayChainUnavailable("option chains are not replayable")

    async def fetch_option_chain(self, ticker: str, expiration: str):
        raise ReplayChainUnavailable("option chains are not replayable")


def _refusing_transport():
    import httpx

    def handler(request):  # pragma: no cover - reached only on a bug
        raise RuntimeError(f"replay refuses network: {request.url}")

    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------
# Cache I/O
# --------------------------------------------------------------------------

def save_minute_bars(path: Path, bars: dict[str, list[Any]]) -> None:
    payload = {
        sym: [
            [b.start_utc.isoformat(), b.open, b.high, b.low, b.close, b.volume, b.vwap]
            for b in rows
        ]
        for sym, rows in bars.items()
    }
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)


def load_minute_bars(path: Path) -> dict[str, list[Any]]:
    from alert_ranker.causal_bars import Bar

    with gzip.open(path, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    out = {}
    for sym, rows in payload.items():
        out[sym] = [
            Bar(start=datetime.fromisoformat(r[0]), open=r[1], high=r[2], low=r[3],
                close=r[4], volume=r[5], vwap=r[6])
            for r in rows
        ]
    return out


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------

def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def row_key(row: dict[str, Any], with_direction: bool = True) -> tuple[str, str, str, str, str]:
    return (
        _parse_ts(row["timestamp"]).isoformat(),
        str(row["ticker"]).upper(),
        str(row["source"]),
        str(row["pattern"] or ""),
        str(row["direction"] or "") if with_direction else "",
    )


async def replay_cycles(
    cycles: Sequence[tuple[datetime, Sequence[str]]],
    minute_bars: dict[str, list[Any]],
    seed_blocks: Iterable[dict[str, Any]],
    workdir: Path,
) -> list[dict[str, Any]]:
    """Run the production scanner at each (scan time, tickers) cycle."""
    for key, value in REPLAY_ENV.items():
        os.environ[key] = value
    import httpx

    from alert_ranker.bar_context import BarContextBuilder
    from alert_ranker.causal_bars import MINUTE_30
    from alert_ranker.config import load_config
    from alert_ranker.discord import DiscordAlerter
    from alert_ranker.scanner import OptionsScanner
    from alert_ranker.storage import ScanStorage

    cfg = load_config()
    storage = ScanStorage(workdir / "replay_scanner.sqlite")
    for block in seed_blocks:
        storage.block_episode(
            block["ticker"], block["episode_key"], block["reason"],
            blocked_at=_parse_ts(block["blocked_at"]),
        )
    bars_30m = {sym: aggregate_30m(rows) for sym, rows in minute_bars.items()}
    builder = BarContextBuilder(
        provider=HistoricalBarProvider(bars_30m),
        calendar=LocalCalendar(),
        timeframe=MINUTE_30,
        delay_buffer=DELAY_BUFFER,
        lookback_days=LOOKBACK_DAYS,
        exchange_timezone=cfg.timezone,
    )
    market = ReplayMarketData(minute_bars)
    async with httpx.AsyncClient(transport=_refusing_transport()) as client:
        alerter = DiscordAlerter(cfg, storage, client=client)
        scanner = OptionsScanner(
            config=cfg, market_data=market, storage=storage, discord=alerter,
            signa_client=None, bar_context=builder,
        )
        for now, tickers in cycles:
            _guard_before_exclusion(now, "scan time")
            market.now = now
            scanner.config = replace(cfg, watchlist=list(tickers))
            await scanner.scan_watchlist(source="scheduled", now=now)

    import sqlite3

    with sqlite3.connect(storage.path) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(r) for r in conn.execute(
                "select timestamp,ticker,source,pattern,direction,alert_sent,"
                "alert_suppression_reason as reason from scans order by id"
            )
        ]


def compare(
    prod: Sequence[dict[str, Any]],
    replay: Sequence[dict[str, Any]],
    *,
    with_direction: bool = True,
) -> dict[str, Any]:
    """Row-level parity keyed on (scan time, ticker, source, pattern[, direction]).

    ``with_direction=True`` is the verdict metric (stricter than the prereg's
    (ticker, 30m bucket, pattern, class) key). ``False`` drops the scorer's
    VWAP/EMA direction, which is the prereg's stated granularity.
    """
    def index(rows):
        grouped: dict[tuple, list[str]] = collections.defaultdict(list)
        for r in rows:
            grouped[row_key(r, with_direction)].append(reason_class(r.get("reason"), bool(r.get("alert_sent"))))
        return grouped

    p, q = index(prod), index(replay)
    agree = 0
    total = 0
    confusion: collections.Counter = collections.Counter()
    for key in set(p) | set(q):
        pc = sorted(p.get(key, []))
        qc = sorted(q.get(key, []))
        n = max(len(pc), len(qc))
        total += n
        for i in range(n):
            a = pc[i] if i < len(pc) else "<absent>"
            b = qc[i] if i < len(qc) else "<absent>"
            if a == b:
                agree += 1
            else:
                confusion[(a, b)] += 1
    return {
        "rows_compared": total,
        "rows_agree": agree,
        "agreement": (agree / total) if total else 0.0,
        "prod_rows": len(prod),
        "replay_rows": len(replay),
        "disagreements_prod_to_replay": {f"{a} -> {b}": n for (a, b), n in confusion.most_common()},
    }


def cycles_from_prod(prod_rows: Sequence[dict[str, Any]]) -> list[tuple[datetime, list[str]]]:
    by_time: dict[datetime, list[str]] = collections.OrderedDict()
    for r in prod_rows:
        if r["source"] != "scheduled":
            continue
        by_time.setdefault(_parse_ts(r["timestamp"]), [])
        if r["ticker"].upper() not in by_time[_parse_ts(r["timestamp"])]:
            by_time[_parse_ts(r["timestamp"])].append(r["ticker"].upper())
    return sorted(by_time.items())


# Decision-equivalent code window: every scanner release from dd3aa9d
# (restart 2026-09-21T04:21:21Z) to the deployed 5b7be0f7 has byte-identical
# decision modules except discord.py message text and a session_calendar
# Easter-date fix. Rows before it were produced by code that pre-dates #869.
CODE_EQUIVALENT_FROM = datetime(2026, 9, 21, 4, 21, 21, tzinfo=timezone.utc)


def run_step0(bars_path: Path, prod_path: Path) -> dict[str, Any]:
    prod_doc = json.loads(prod_path.read_text())
    prod = [
        r for r in prod_doc["rows"]
        if STEP0_START <= _parse_ts(r["timestamp"]) < EXCLUDED_FROM
        and str(r["source"]).startswith("scheduled")
    ]
    seed = [b for b in prod_doc["episode_blocks"] if _parse_ts(b["blocked_at"]) < STEP0_START]
    minute_bars = load_minute_bars(bars_path)
    cycles = cycles_from_prod(prod)
    with tempfile.TemporaryDirectory() as tmp:
        replay = asyncio.run(replay_cycles(cycles, minute_bars, seed, Path(tmp)))

    def segment(rows, lo, hi):
        return [r for r in rows if lo <= _parse_ts(r["timestamp"]) < hi]

    full = compare(prod, replay)
    equiv = compare(
        segment(prod, CODE_EQUIVALENT_FROM, EXCLUDED_FROM),
        segment(replay, CODE_EQUIVALENT_FROM, EXCLUDED_FROM),
    )
    earlier = compare(
        segment(prod, STEP0_START, CODE_EQUIVALENT_FROM),
        segment(replay, STEP0_START, CODE_EQUIVALENT_FROM),
    )
    no_dir_full = compare(prod, replay, with_direction=False)
    no_dir_equiv = compare(
        segment(prod, CODE_EQUIVALENT_FROM, EXCLUDED_FROM),
        segment(replay, CODE_EQUIVALENT_FROM, EXCLUDED_FROM),
        with_direction=False,
    )
    verdict = "PASS" if full["agreement"] >= PASS_THRESHOLD else "BLOCKED"
    return {
        "verdict": verdict,
        "verdict_metric": "full_window.agreement (direction-keyed)",
        "prereg_key_full_window": no_dir_full,
        "prereg_key_code_equivalent_segment": no_dir_equiv,
        "trial_id": TRIAL_ID,
        "variant": VARIANT,
        "window": [STEP0_START.isoformat(), EXCLUDED_FROM.isoformat()],
        "cycles": len(cycles),
        "pass_threshold": PASS_THRESHOLD,
        "full_window": full,
        "code_equivalent_segment": {"from": CODE_EQUIVALENT_FROM.isoformat(), **equiv},
        "pre_equivalent_segment": earlier,
        "class_counts_prod": dict(collections.Counter(
            reason_class(r.get("reason"), bool(r.get("alert_sent"))) for r in prod)),
        "class_counts_replay": dict(collections.Counter(
            reason_class(r.get("reason"), bool(r.get("alert_sent"))) for r in replay)),
    }


# --------------------------------------------------------------------------
# Network subcommands (read-only)
# --------------------------------------------------------------------------

def _provider(feed: str):
    from alert_ranker.bar_provider import AlpacaBarProvider
    from alert_ranker.config import resolve_alpaca_credentials

    key, secret = resolve_alpaca_credentials()
    if not key or not secret:
        raise SystemExit("Alpaca credentials not found in environment")
    return AlpacaBarProvider(
        base_url=os.environ.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"),
        api_key=key, secret_key=secret, feed=feed,
    )


def pull_start(first_scan: datetime) -> datetime:
    """Earliest 30Min bucket any Step 0 scan can request."""
    start = first_scan - DELAY_BUFFER - timedelta(days=LOOKBACK_DAYS)
    bucket = start.replace(minute=(start.minute // 30) * 30, second=0, microsecond=0)
    return bucket if bucket >= start else bucket + timedelta(minutes=30)


async def _pull(out_dir: Path, first_scan: datetime) -> dict[str, Any]:
    from alert_ranker.causal_bars import MINUTE_1

    _guard_before_exclusion(first_scan, "first scan")
    start, end = pull_start(first_scan), EXCLUDED_FROM - timedelta(seconds=1)
    provider = _provider("sip")
    bars: dict[str, list[Any]] = {}
    for symbol in SYMBOLS:
        got = await provider.fetch_bars([symbol], MINUTE_1, start, end)
        bars[symbol] = [b for b in got[symbol] if b.start_utc + timedelta(minutes=1) <= EXCLUDED_FROM]
    out_dir.mkdir(parents=True, exist_ok=True)
    save_minute_bars(out_dir / "sip_1m.json.gz", bars)
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "symbols": len(bars), "bars": {k: len(v) for k, v in bars.items()},
    }


async def _probe_iex() -> dict[str, Any]:
    """One request: IEX 1Min bars for SPY over the last 24h up to now.

    A 200 on a window ending *now* proves real-time IEX query entitlement (SIP
    on this plan returns an entitlement error for the last 15 minutes).
    Freshness of the newest bar is reported but only meaningful in RTH.
    """
    from alert_ranker.bar_provider import BarProviderError
    from alert_ranker.causal_bars import MINUTE_1

    now = datetime.now(timezone.utc).replace(microsecond=0)
    provider = _provider("iex")
    try:
        got = await provider.fetch_bars(["SPY"], MINUTE_1, now - timedelta(hours=24), now)
    except BarProviderError as exc:
        return {"entitled": exc.reason not in ("provider_entitlement",), "reason": exc.reason,
                "detail": exc.detail[:200], "probed_at": now.isoformat()}
    newest = got["SPY"][-1].start_utc if got["SPY"] else None
    return {"entitled": True, "probed_at": now.isoformat(), "bars": len(got["SPY"]),
            "newest_bar_start": newest.isoformat() if newest else None}


# ==========================================================================
# SCORING STAGE (operator GO 2026-09-24). Frozen by the prereg; the choices
# below that the prereg leaves open are fixed here BEFORE the single read and
# are restated in the result document.
# ==========================================================================

VARIANTS = (
    "v0_baseline_sip_960_close_confirm",
    "v1_iex_feed_60s_close_confirm",
    "v2_daily_at_open_live_cross",
    "v3_30m_intrabar_live_cross",
)
SCORE_FIRST_SESSION = date(2025, 10, 1)
SCORE_LAST_SESSION = date(2026, 8, 31)
SCORE_PULL_END = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
IEX_BUFFER = timedelta(seconds=60)
SCAN_OFFSET = timedelta(seconds=45)       # production cycles land ~44-57 s past
SCAN_CADENCE = timedelta(minutes=5)       # each 5-minute mark; unchanged in all variants
OUTCOME_HORIZON = timedelta(days=14)      # shortest DTE V1 may select
NULL_DRAWS = 500
NULL_SEED = 20260924
MIN_TOTAL_ELIGIBLE = 60
ACTIVE_TIMEFRAMES = {"30M": "30m", "1D": "1D"}
DISPOSITION = {"SUPPORTS": "PROMISING_BUT_UNPROVEN", "NO IMPROVEMENT": "RETIRE", "BLOCKED": "NOT_RUN"}


# ---------------------------------------------------------------- data layer

class MinuteSeries:
    """Compact 1-minute bars (numpy), minute-start epoch seconds."""

    def __init__(self, t, o, h, l, c, v, vw):
        import numpy as np

        order = np.argsort(t, kind="stable")
        self.t = np.asarray(t, dtype="int64")[order]
        self.o = np.asarray(o, dtype="float64")[order]
        self.h = np.asarray(h, dtype="float64")[order]
        self.l = np.asarray(l, dtype="float64")[order]
        self.c = np.asarray(c, dtype="float64")[order]
        self.v = np.asarray(v, dtype="float64")[order]
        self.vw = np.asarray(vw, dtype="float64")[order]

    @classmethod
    def from_bars(cls, bars: Sequence[Any]) -> "MinuteSeries":
        return cls(
            [int(b.start_utc.timestamp()) for b in bars],
            [b.open for b in bars], [b.high for b in bars], [b.low for b in bars],
            [b.close for b in bars], [b.volume for b in bars],
            [b.vwap if b.vwap is not None else float("nan") for b in bars],
        )

    def __len__(self) -> int:
        return len(self.t)

    def price_at(self, now: datetime) -> float | None:
        """Close of the last minute fully completed at ``now``."""
        import numpy as np

        idx = int(np.searchsorted(self.t, int(now.timestamp()) - 60, side="right")) - 1
        return float(self.c[idx]) if idx >= 0 else None

    def window(self, start: datetime, end_exclusive_completed: datetime):
        """Indices of minutes starting >= start and completed by the given time."""
        import numpy as np

        lo = int(np.searchsorted(self.t, int(start.timestamp()), side="left"))
        hi = int(np.searchsorted(self.t, int(end_exclusive_completed.timestamp()) - 60, side="right"))
        return lo, max(lo, hi)

    def to_30m_bars(self) -> list[Any]:
        import numpy as np

        from alert_ranker.causal_bars import Bar

        if not len(self.t):
            return []
        keys = self.t // 1800
        starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
        ends = np.r_[starts[1:], len(keys)]
        highs = np.maximum.reduceat(self.h, starts)
        lows = np.minimum.reduceat(self.l, starts)
        vols = np.add.reduceat(self.v, starts)
        ok = ~np.isnan(self.vw)
        wsum = np.add.reduceat(np.where(ok, self.vw * self.v, 0.0), starts)
        wv = np.add.reduceat(np.where(ok, self.v, 0.0), starts)
        out = []
        for i, (s, e) in enumerate(zip(starts, ends)):
            out.append(Bar(
                start=datetime.fromtimestamp(int(keys[s]) * 1800, tz=timezone.utc),
                open=float(self.o[s]), high=float(highs[i]), low=float(lows[i]),
                close=float(self.c[e - 1]), volume=float(vols[i]),
                vwap=float(wsum[i] / wv[i]) if wv[i] > 0 else None,
            ))
        return out


def save_series(path: Path, s: MinuteSeries) -> None:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, t=s.t, o=s.o, h=s.h, l=s.l, c=s.c, v=s.v, vw=s.vw)


def load_series(path: Path) -> MinuteSeries:
    import numpy as np

    z = np.load(path)
    return MinuteSeries(z["t"], z["o"], z["h"], z["l"], z["c"], z["v"], z["vw"])


def load_feed(data_dir: Path, feed: str) -> dict[str, MinuteSeries]:
    return {sym: load_series(data_dir / feed / f"{sym}.npz") for sym in SYMBOLS}


class IndexedBarProvider:
    """Causal 30Min provider over pre-aggregated bars, bisect-indexed.

    ``feed`` is the label the production guard checks; the v1/v3 IEX
    provider carries the consolidated label because substituting the feed IS
    the variant under test (prereg v1/v3), not a silent fallback.
    """

    feed = "sip"

    def __init__(self, bars_30m: dict[str, list[Any]], guard_end: datetime):
        import bisect

        self._bisect = bisect
        self._bars = {k.upper(): v for k, v in bars_30m.items()}
        self._starts = {k: [b.start_utc for b in v] for k, v in self._bars.items()}
        self._guard_end = guard_end

    async def fetch_bars(self, symbols, timeframe, start, end):
        from alert_ranker.bar_provider import BarProviderError

        if timeframe.name != "30Min":
            raise BarProviderError("provider_error", f"replay serves 30Min only, got {timeframe.name}")
        if end > self._guard_end:
            raise ValueError(f"bar request end {end.isoformat()} beyond the approved data window")
        out = {}
        for symbol in symbols:
            key = symbol.strip().upper()
            starts = self._starts.get(key, [])
            lo = self._bisect.bisect_left(starts, start)
            hi = self._bisect.bisect_right(starts, end - timeframe.delta)
            out[key] = self._bars.get(key, [])[lo:hi]
        missing = sorted(k for k, v in out.items() if not v)
        if missing:
            raise BarProviderError("missing_symbol", ",".join(missing))
        return out


class SeriesMarketData(ReplayMarketData):
    """Quote proxy from SIP 1-minute closes (numpy); no option chains."""

    def __init__(self, sip: dict[str, MinuteSeries]):
        self._sip = sip
        self.now: datetime | None = None

    async def fetch_market_snapshot(self, ticker: str):
        from alert_ranker.tastytrade_client import MarketSnapshot

        series = self._sip.get(ticker.upper())
        price = series.price_at(self.now) if series is not None else None
        if price is None:
            return MarketSnapshot(ticker=ticker.upper(), error="replay_no_quote")
        day0 = datetime.combine(self.now.astimezone(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc)
        lo, hi = series.window(day0, self.now)
        return MarketSnapshot(
            ticker=ticker.upper(), price=price, volume=float(series.v[lo:hi].sum()),
            quote_timestamp=self.now.isoformat(), raw={"replay_quote": "sip_1m_close"},
        )


def scan_grid(session) -> list[datetime]:
    out, t = [], session.open + SCAN_OFFSET
    while t < session.close:
        out.append(t)
        t += SCAN_CADENCE
    return out


def scoring_sessions() -> list[Any]:
    from alert_ranker.session_calendar import nyse_session_for

    out, day = [], SCORE_FIRST_SESSION
    while day <= SCORE_LAST_SESSION:
        s = nyse_session_for(day)
        if s is not None:
            out.append(s)
        day += timedelta(days=1)
    return out


# ------------------------------------------------------------ variant layers

def _is_daily_212(candidate: dict[str, Any]) -> bool:
    return str(candidate.get("setup_type") or "").upper().startswith("DAILY_212")


def _v2_layer(base_cls, sip: dict[str, MinuteSeries]):
    """Daily 2-2 / 3-2-2 evaluated from 09:30 ET on live-observed prices.

    Everything except the Daily candidate is the v0 path. The Daily candidate
    is produced by production ``_daily_candidate_from_raw``; the only change is
    its inputs: the current-day candle also includes the live prices the
    scanner observed at its own 5-minute scans after the last completed
    (SIP, 960 s) bar, and alignment contexts use the latest causally
    available session when the current one has no completed bar yet.
    """
    from alert_ranker.causal_bars import Bar, completed_bars, session_bars

    class V2DailyAtOpen(base_cls):
        async def _fetch_bar_context(self, ticker, now):
            fields = await super()._fetch_bar_context(ticker, now)
            # Prereg scope is Daily 2-2 / 3-2-2: Daily 2-1-2 stays on the v0 path.
            keep = [c for c in fields.get("paper_setup_candidates") or []
                    if str(c.get("setup_timeframe") or "").upper() != "1D"
                    or _is_daily_212(c)]
            daily = await self._v2_daily(ticker, now)
            if daily is not None and not _is_daily_212(daily):
                keep.append({"bar_context_available": True, "bar_context_reason": "", **daily})
            fields["paper_setup_candidates"] = keep
            fields["paper_setup_candidate_count"] = len(keep)
            return fields

        async def _v2_daily(self, ticker, now):
            builder = self.bar_context
            symbol = ticker.strip().upper()
            session = await builder._session_for(now)
            if session is None or now < session.open:
                return None
            cutoff = now.astimezone(timezone.utc) - builder.delay_buffer
            wanted = [symbol, *[s for s in builder.index_symbols if s != symbol]]
            start = cutoff - timedelta(days=builder.lookback_days)
            required = await builder._sessions_in_window(start, session)
            raw = await builder.provider.fetch_bars(wanted, builder.timeframe, start, cutoff)
            current = session_bars(completed_bars(raw.get(symbol, []), builder.timeframe, cutoff),
                                   builder.timeframe, session.open, session.close)
            if current:
                ctx_session, ctx_cutoff, ctx_required, ctx_raw = session, cutoff, required, raw
            else:
                prev = None
                day = session.date - timedelta(days=1)
                for _ in range(10):
                    prev = await builder._session_for_day(day)
                    if prev is not None:
                        break
                    day -= timedelta(days=1)
                if prev is None:
                    return None
                ctx_cutoff = prev.close
                ctx_start = ctx_cutoff - timedelta(days=builder.lookback_days)
                ctx_required = await builder._sessions_in_window(ctx_start, prev)
                ctx_raw = await builder.provider.fetch_bars(wanted, builder.timeframe, ctx_start, ctx_cutoff)
                ctx_session = prev
            contexts = {n: builder._symbol_context(n, ctx_raw.get(n, []), ctx_session, ctx_required, ctx_cutoff)
                        for n in wanted}
            if not all(c.available for c in contexts.values()):
                return None  # a missing input fails closed
            ticker_ctx, spy, qqq = contexts[symbol], contexts.get("SPY"), contexts.get("QQQ")

            last_end = (current[-1].start_utc + builder.timeframe.delta) if current else session.open
            series = sip.get(symbol)
            observed = [p for g in scan_grid(session) if last_end < g <= now
                        for p in [series.price_at(g) if series is not None else None] if p is not None]
            bars = list(raw.get(symbol, []))
            synth_cutoff = cutoff
            if observed:
                bars.append(Bar(start=last_end, open=observed[0], high=max(observed),
                                low=min(observed), close=observed[-1], volume=0.0, vwap=None))
                synth_cutoff = last_end + builder.timeframe.delta
            return self._daily_candidate_from_raw(
                builder, bars, session, required, synth_cutoff, ticker_ctx, spy, qqq
            )

    V2DailyAtOpen.__name__ = base_cls.__name__
    return V2DailyAtOpen


def _intrabar_builder_cls():
    from alert_ranker.bar_context import BarContextBuilder
    from alert_ranker.causal_bars import Bar, completed_bars, session_bars
    from strategy.strat_classifier import INSIDE_BAR, TWO_DOWN, TWO_UP, StratBar, classify_bar

    class IntrabarBuilder(BarContextBuilder):
        """IEX 60 s context; for the scanned symbol only, a completed inside
        bar whose next slot the live price has already broken gets one
        synthetic forming bar so the unchanged production verdict can call it."""

        def _symbol_context(self, symbol, bars, session, required, cutoff):
            ctx = super()._symbol_context(symbol, bars, session, required, cutoff)
            state = getattr(self, "_v3", None)
            if not state or symbol != state["symbol"]:
                return ctx
            now, sip, iex = state["now"], state["sip"], state["iex"]
            regular = []
            for s in required:
                regular.extend(session_bars(completed_bars(bars, self.timeframe, cutoff),
                                            self.timeframe, s.open, s.close))
            if len(regular) < 3:
                return ctx
            three_back, two_back, inside = regular[-3:]
            sb = lambda b: StratBar(high=b.high, low=b.low)  # noqa: E731
            if classify_bar(sb(inside), sb(two_back)) != INSIDE_BAR:
                return ctx
            if classify_bar(sb(two_back), sb(three_back)) not in {TWO_UP, TWO_DOWN}:
                return ctx
            slot = inside.start_utc + self.timeframe.delta
            if not (session.open <= slot < session.close) or slot > now:
                return ctx
            if slot + self.timeframe.delta <= cutoff:
                return ctx  # the breakout bar is already complete: ordinary path
            price = sip.price_at(now) if sip is not None else None
            if price is None or inside.low <= price <= inside.high:
                return ctx
            highs, lows, opens, vols, vwap = [price], [price], [], 0.0, price
            if iex is not None:
                lo, hi = iex.window(slot, now - IEX_BUFFER)
                if hi > lo:
                    import numpy as np

                    highs.append(float(iex.h[lo:hi].max()))
                    lows.append(float(iex.l[lo:hi].min()))
                    opens.append(float(iex.o[lo]))
                    vols = float(iex.v[lo:hi].sum())
                    ok = ~np.isnan(iex.vw[lo:hi])
                    w = float(iex.v[lo:hi][ok].sum())
                    if w > 0:
                        vwap = float((iex.vw[lo:hi][ok] * iex.v[lo:hi][ok]).sum() / w)
            # Production session VWAP fails closed on a bar without vwap; the
            # forming bar's causal vwap is its IEX minutes', else the live price.
            synth = Bar(start=slot, open=opens[0] if opens else price, high=max(highs),
                        low=min(lows), close=price, volume=max(vols, 1.0), vwap=vwap)
            return super()._symbol_context(symbol, [*bars, synth], session, required,
                                           slot + self.timeframe.delta)

    return IntrabarBuilder


def _v3_layer(base_cls, v3_builder, sip, iex):
    class V330mIntrabar(base_cls):
        async def _fetch_bar_context(self, ticker, now):
            v0_fields = await super()._fetch_bar_context(ticker, now)
            saved = self.bar_context
            self.bar_context = v3_builder
            v3_builder._v3 = {"symbol": ticker.strip().upper(), "now": now,
                              "sip": sip.get(ticker.upper()), "iex": iex.get(ticker.upper())}
            try:
                fields = await super()._fetch_bar_context(ticker, now)
            finally:
                v3_builder._v3 = None
                self.bar_context = saved
            override = {"bar_context_available": v0_fields.get("bar_context_available"),
                        "bar_context_reason": v0_fields.get("bar_context_reason")}
            daily = [{**override, **c} for c in v0_fields.get("paper_setup_candidates") or []]
            fields["paper_setup_candidates"] = daily
            fields["paper_setup_candidate_count"] = len(daily)
            return fields

    V330mIntrabar.__name__ = base_cls.__name__
    return V330mIntrabar


def build_scanner_class(variant: str, *, sip=None, iex=None, v3_builder=None):
    """Production chain (see alert_ranker/scanner.py) with an optional replay
    layer between the multi-setup collector and the Signa/V1 wrappers."""
    from alert_ranker import scanner_legacy
    from alert_ranker.multisetup_scanner import build_multisetup_scanner
    from alert_ranker.signa_v2_observer import build_signa_v2_observer
    from alert_ranker.v1_diagnostics import build_v1_diagnostics_capture
    from alert_ranker.v1_evidence_hardening import build_v1_evidence_hardening
    from alert_ranker.v1_runtime_preflight import build_v1_runtime_preflight

    cls = build_multisetup_scanner(scanner_legacy.OptionsScanner)
    if variant == VARIANTS[2]:
        cls = _v2_layer(cls, sip)
    elif variant == VARIANTS[3]:
        cls = _v3_layer(cls, v3_builder, sip, iex)
    cls = build_signa_v2_observer(cls)
    cls = build_v1_evidence_hardening(cls)
    cls = build_v1_runtime_preflight(cls)
    return build_v1_diagnostics_capture(cls)


# ------------------------------------------------------------------- replay

_RAW_KEEP = (
    "setup_status", "mechanical_signal_status", "counterfactual_observer", "setup_type",
    "setup_timeframe", "setup_direction", "setup_entry_trigger", "underlying_invalidation",
    "stop", "target_1", "price", "paper_entry_remaining_rr", "paper_policy_status",
    "paper_policy_reason", "latest_completed_bar_start", "session_open", "entry_late_reason",
)


def _extract_and_prune(storage) -> list[dict[str, Any]]:
    import sqlite3

    with sqlite3.connect(storage.path) as conn:
        rows = conn.execute(
            "select id,timestamp,ticker,source,pattern,direction,alert_sent,"
            "alert_suppression_reason,raw_json from scans order by id"
        ).fetchall()
        if rows:
            conn.execute("delete from scans where id <= ?", (rows[-1][0],))
            for table in ("options_v1_diagnostic_snapshots", "options_selector_evidence"):
                try:
                    conn.execute(f"delete from {table}")
                except sqlite3.OperationalError:
                    pass
    out = []
    for _id, ts, ticker, source, pattern, direction, sent, reason, raw_json in rows:
        raw = json.loads(raw_json or "{}")
        rec = {"timestamp": ts, "ticker": ticker, "source": source, "pattern": pattern,
               "direction": direction, "alert_sent": sent, "reason": reason}
        rec.update({k: raw.get(k) for k in _RAW_KEEP})
        out.append(rec)
    return out


async def replay_scoring_variant(variant: str, data_dir: Path, out_path: Path, workdir: Path,
                                 sessions: Sequence[Any] | None = None) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise SystemExit(f"unknown variant {variant}")
    for key, value in REPLAY_ENV.items():
        os.environ[key] = value
    import httpx

    from alert_ranker.bar_context import BarContextBuilder
    from alert_ranker.causal_bars import MINUTE_30
    from alert_ranker.config import load_config
    from alert_ranker.discord import DiscordAlerter
    from alert_ranker.storage import ScanStorage

    sessions = list(sessions if sessions is not None else scoring_sessions())
    guard = SCORE_PULL_END
    for s in sessions:
        _guard_before_exclusion(s.close, "session")
    sip = load_feed(data_dir, "sip")
    iex = load_feed(data_dir, "iex") if variant in (VARIANTS[1], VARIANTS[3]) else {}
    cfg = load_config()

    def builder(feed_series, buffer, cls=BarContextBuilder):
        provider = IndexedBarProvider({k: v.to_30m_bars() for k, v in feed_series.items()}, guard)
        return cls(provider=provider, calendar=LocalCalendar(), timeframe=MINUTE_30,
                   delay_buffer=buffer, lookback_days=LOOKBACK_DAYS, exchange_timezone=cfg.timezone)

    v3_builder = None
    if variant == VARIANTS[1]:
        main_builder = builder(iex, IEX_BUFFER)
    else:
        main_builder = builder(sip, DELAY_BUFFER)
    if variant == VARIANTS[3]:
        v3_builder = builder(iex, IEX_BUFFER, cls=_intrabar_builder_cls())
    cls = build_scanner_class(variant, sip=sip, iex=iex, v3_builder=v3_builder)
    storage = ScanStorage(workdir / f"replay_{variant}.sqlite")
    market = SeriesMarketData(sip)
    rows = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(transport=_refusing_transport()) as client:
        alerter = DiscordAlerter(cfg, storage, client=client)
        scanner = cls(config=cfg, market_data=market, storage=storage, discord=alerter,
                      signa_client=None, bar_context=main_builder)
        with open(out_path, "w", encoding="utf-8") as fh:
            for n, session in enumerate(sessions, 1):
                print(f"{variant} session {n}/{len(sessions)} {session.date} rows={rows}",
                      file=sys.stderr, flush=True)
                for now in scan_grid(session):
                    market.now = now
                    await scanner.scan_watchlist(source="scheduled", now=now)
                    for rec in _extract_and_prune(storage):
                        fh.write(json.dumps(rec) + "\n")
                        rows += 1
    return {"variant": variant, "sessions": len(sessions), "rows": rows}


# ------------------------------------------------------------------ metrics

def _norm_tf(value: Any) -> str | None:
    return ACTIVE_TIMEFRAMES.get(str(value or "").strip().upper())


def _long_short(setup_direction: Any, direction: Any) -> str | None:
    d = str(setup_direction or "").upper()
    if d in ("CALL", "LONG"):
        return "LONG"
    if d in ("PUT", "SHORT"):
        return "SHORT"
    d = str(direction or "").upper()
    return d if d in ("LONG", "SHORT") else None


def episodes_from_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One episode per (ticker, timeframe, setup_type, direction, trigger, NY date).

    Detection = first ACTIVE-lane row that reached the late-entry decision
    (eligible, first-late, or episode-blocked). Eligible = the episode had at
    least one row that passed the late-entry gate; its alert/entry is the
    first such row.
    """
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    eps: dict[tuple, dict[str, Any]] = {}
    for r in records:
        tf = _norm_tf(r.get("setup_timeframe"))
        if tf is None:
            continue
        cls = reason_class(r.get("reason"), bool(r.get("alert_sent")))
        eligible = (cls == "PAST_LATE_GATE" and not r.get("counterfactual_observer")
                    and str(r.get("setup_status") or "").upper() == "TRIGGERED")
        if not (eligible or cls in ("ENTRY_LATE_FIRST", "ENTRY_LATE_BLOCKED")):
            continue
        direction = _long_short(r.get("setup_direction"), r.get("direction"))
        trigger = r.get("setup_entry_trigger")
        if direction is None or trigger is None:
            continue
        ts = _parse_ts(r["timestamp"])
        key = (r["ticker"].upper(), tf, str(r.get("setup_type") or "").upper(), direction,
               round(float(trigger), 4), ts.astimezone(ny).date().isoformat())
        row = {"time": ts, "price": r.get("price"), "reason": r.get("reason"), "cls": cls,
               "stop": r.get("underlying_invalidation") or r.get("stop"),
               "target": r.get("target_1"), "bar_start": r.get("latest_completed_bar_start")}
        ep = eps.get(key)
        if ep is None:
            ep = eps[key] = {"key": key, "ticker": key[0], "tf": tf, "setup_type": key[2],
                             "direction": direction, "trigger": float(trigger), "date": key[5],
                             "first": row, "first_eligible": None}
        if eligible and ep["first_eligible"] is None:
            ep["first_eligible"] = row
    return list(eps.values())


def _rr(direction, price, stop, target):
    from alert_ranker.paper_v1 import remaining_reward_to_risk

    try:
        return remaining_reward_to_risk(direction, float(price), float(stop), float(target))
    except (TypeError, ValueError):
        return None


class RthPaths:
    """Regular-session SIP minutes per symbol, for crosses and outcomes."""

    def __init__(self, sip: dict[str, MinuteSeries]):
        import numpy as np

        from alert_ranker.session_calendar import nyse_session_for

        self.paths = {}
        for sym, s in sip.items():
            if not len(s):
                self.paths[sym] = s
                continue
            days = sorted({datetime.fromtimestamp(int(x), tz=timezone.utc).date() for x in s.t[:: 60]}
                          | {datetime.fromtimestamp(int(s.t[-1]), tz=timezone.utc).date()})
            mask = np.zeros(len(s.t), dtype=bool)
            for d in days:
                sess = nyse_session_for(d)
                if sess is None:
                    continue
                lo = np.searchsorted(s.t, int(sess.open.timestamp()), side="left")
                hi = np.searchsorted(s.t, int(sess.close.timestamp()) - 60, side="right")
                mask[lo:hi] = True
            self.paths[sym] = MinuteSeries(s.t[mask], s.o[mask], s.h[mask], s.l[mask],
                                           s.c[mask], s.v[mask], s.vw[mask])
        self.data_end = SCORE_PULL_END

    def first_cross(self, sym, direction, trigger, armed: datetime, until: datetime):
        import numpy as np

        p = self.paths[sym]
        lo = int(np.searchsorted(p.t, int(armed.timestamp()), side="left"))
        hi = int(np.searchsorted(p.t, int(until.timestamp()) - 60, side="right"))
        if hi <= lo:
            return None
        hit = p.h[lo:hi] >= trigger if direction == "LONG" else p.l[lo:hi] <= trigger
        idx = np.flatnonzero(hit)
        return datetime.fromtimestamp(int(p.t[lo + idx[0]]), tz=timezone.utc) if len(idx) else None

    def touched_between(self, sym, direction, level, start: datetime, end: datetime) -> bool:
        import numpy as np

        p = self.paths[sym]
        lo = int(np.searchsorted(p.t, int(start.timestamp()), side="left"))
        hi = int(np.searchsorted(p.t, int(end.timestamp()) - 60, side="right"))
        if hi <= lo:
            return False
        return bool((p.h[lo:hi] >= level).any() if direction == "LONG" else (p.l[lo:hi] <= level).any())

    def outcome_r(self, sym, direction, entry, stop, target, entry_time: datetime):
        """First touch of target/stop after entry (stop first on a shared minute).

        Returns (R, resolution). Unresolved at the 14-day horizon or the end of
        the approved data window is marked to the last regular-session close.
        """
        import numpy as np

        p = self.paths[sym]
        risk = (entry - stop) if direction == "LONG" else (stop - entry)
        reward = (target - entry) if direction == "LONG" else (entry - target)
        if risk <= 0:
            return None, "invalid_geometry"
        end = min(entry_time + OUTCOME_HORIZON, self.data_end)
        lo = int(np.searchsorted(p.t, int(entry_time.timestamp()), side="left"))
        hi = int(np.searchsorted(p.t, int(end.timestamp()) - 60, side="right"))
        if hi <= lo:
            return 0.0, "no_path"
        if direction == "LONG":
            hs, ht = p.l[lo:hi] <= stop, p.h[lo:hi] >= target
        else:
            hs, ht = p.h[lo:hi] >= stop, p.l[lo:hi] <= target
        either = np.flatnonzero(hs | ht)
        if len(either):
            i = either[0]
            return (-1.0, "stop") if hs[i] else (reward / risk, "target")
        last = float(p.c[hi - 1])
        move = (last - entry) if direction == "LONG" else (entry - last)
        return move / risk, "horizon" if entry_time + OUTCOME_HORIZON <= self.data_end else "data_end"


def _false_positive(ep, sip_30m, direction) -> bool | None:
    """SIP completed-bar check of the variant's trigger (v1/v3).

    30m: the SIP (two-back, inside, trigger) bars must be 2-1-2 with the
    trigger bar a 2 in the setup's direction (an outside bar is a false
    positive). Daily (v1): production ``evaluate_daily_setup`` on SIP bars
    completed by the same time must be TRIGGERED in the same direction.
    """
    import bisect

    from alert_ranker.causal_bars import build_session_candle, completed_bars, session_bars
    from alert_ranker.causal_bars import MINUTE_30
    from alert_ranker.daily_strat import evaluate_daily_setup
    from alert_ranker.session_calendar import nyse_session_for
    from strategy.strat_classifier import INSIDE_BAR, TWO_DOWN, TWO_UP, StratBar, classify_bar

    bars = sip_30m.get(ep["ticker"], [])
    sb = lambda b: StratBar(high=b.high, low=b.low)  # noqa: E731
    want = TWO_UP if direction == "LONG" else TWO_DOWN
    if ep["tf"] == "30m":
        start = ep["first"].get("bar_start")
        if not start:
            return None
        t = _parse_ts(start)
        # regular-session series up to and including the trigger bar
        starts = [b.start_utc for b in bars]
        i = bisect.bisect_left(starts, t)
        if i >= len(bars) or bars[i].start_utc != t:
            return True
        seq = []
        j = i
        while j >= 0 and len(seq) < 4:
            b = bars[j]
            s = nyse_session_for(b.start_utc.astimezone(timezone.utc).date())
            if s is not None and s.open <= b.start_utc < s.close:
                seq.append(b)
            j -= 1
        if len(seq) < 4:
            return True
        trig, inside, two_back, three_back = seq
        if classify_bar(sb(inside), sb(two_back)) != INSIDE_BAR:
            return True
        if classify_bar(sb(two_back), sb(three_back)) not in {TWO_UP, TWO_DOWN}:
            return True
        return classify_bar(sb(trig), sb(inside)) != want
    # Daily
    when = ep["first"]["time"] - IEX_BUFFER
    closed = completed_bars(bars, MINUTE_30, when)
    day = date.fromisoformat(ep["date"])
    candles, current, d, seen = [], None, day, 0
    sess = nyse_session_for(day)
    if sess is None:
        return None
    current = build_session_candle(session_bars(closed, MINUTE_30, sess.open, sess.close))
    while len(candles) < 3 and seen < 15:
        d -= timedelta(days=1)
        seen += 1
        s = nyse_session_for(d)
        if s is None:
            continue
        c = build_session_candle(session_bars(closed, MINUTE_30, s.open, s.close))
        if c is not None:
            candles.insert(0, c)
    verdict = evaluate_daily_setup(candles, current)
    if not verdict.triggered:
        return True
    return ("LONG" if verdict.direction == "CALL" else "SHORT") != direction


def _quantiles(values):
    import numpy as np

    if not values:
        return None
    a = np.asarray(values, dtype="float64")
    return {"n": int(len(a)), "p25": float(np.percentile(a, 25)), "median": float(np.median(a)),
            "p75": float(np.percentile(a, 75))}


def score_variant(variant, episodes, paths: RthPaths, sip_30m, halves) -> dict[str, Any]:
    import numpy as np

    from alert_ranker.session_calendar import nyse_session_for

    out: dict[str, Any] = {}
    rng = np.random.default_rng(NULL_SEED)
    for half_name, dates in halves.items():
        eps = [e for e in episodes if e["date"] in dates]
        detected = eps
        eligible = [e for e in eps if e["first_eligible"] is not None]
        late = [e for e in eps if e["first_eligible"] is None]
        late_split = collections.Counter()
        for e in late:
            reason = str(e["first"]["reason"] or "")
            late_split["past_target" if "past_target" in reason else
                       "past_stop" if "past_stop" in reason else
                       "remaining_rr_below_1" if "remaining_rr" in reason else "other"] += 1
        rr_first, latency, tbd, fp = [], [], 0, []
        for e in detected:
            f = e["first"]
            rr = _rr(e["direction"], f["price"], f["stop"], f["target"])
            if rr is not None:
                rr_first.append(rr)
            sess = nyse_session_for(date.fromisoformat(e["date"]))
            armed = sess.open if e["tf"] == "1D" else (_parse_ts(f["bar_start"]) if f.get("bar_start") else sess.open)
            cross = paths.first_cross(e["ticker"], e["direction"], e["trigger"], armed, f["time"])
            if cross is not None:
                latency.append((f["time"] - cross).total_seconds() / 60.0)
                if f["target"] is not None and paths.touched_between(
                        e["ticker"], e["direction"], float(f["target"]), cross, f["time"]):
                    tbd += 1
            if variant in (VARIANTS[1], VARIANTS[3]) and (variant == VARIANTS[1] or e["tf"] == "30m"):
                flag = _false_positive(e, sip_30m, e["direction"])
                if flag is not None:
                    fp.append(flag)
        rs, resolutions, geo = [], collections.Counter(), []
        for e in eligible:
            f = e["first_eligible"]
            try:
                entry, stop, target = float(f["price"]), float(f["stop"]), float(f["target"])
            except (TypeError, ValueError):
                resolutions["invalid_levels"] += 1
                continue
            r, how = paths.outcome_r(e["ticker"], e["direction"], entry, stop, target, f["time"])
            resolutions[how] += 1
            if r is not None:
                rs.append(r)
                geo.append((e, entry, stop, target))
        null_means = []
        if geo:
            for _ in range(NULL_DRAWS):
                draw = []
                for e, entry, stop, target in geo:
                    sess = nyse_session_for(date.fromisoformat(e["date"]))
                    minutes = int((sess.close - sess.open).total_seconds() // 60)
                    t = sess.open + timedelta(minutes=int(rng.integers(1, minutes)))
                    p = paths.paths[e["ticker"]].price_at(t)
                    if p is None:
                        continue
                    sign = 1.0 if e["direction"] == "LONG" else -1.0
                    risk, reward = abs(entry - stop), abs(target - entry)
                    r, _how = paths.outcome_r(e["ticker"], e["direction"], p, p - sign * risk,
                                              p + sign * reward, t)
                    if r is not None:
                        draw.append(r)
                if draw:
                    null_means.append(float(np.mean(draw)))
        out[half_name] = {
            "detected_episodes": len(detected),
            "eligible_episodes": len(eligible),
            "late_episodes": len(late),
            "late_rate": (len(late) / len(detected)) if detected else None,
            "late_split": dict(late_split),
            "r_remaining_at_first_detection": _quantiles(rr_first),
            "detection_latency_minutes": _quantiles(latency),
            "target_before_detection": tbd,
            "target_before_detection_rate": (tbd / len(detected)) if detected else None,
            "false_positive_checked": len(fp),
            "false_positive_rate": (sum(fp) / len(fp)) if fp else None,
            "underlying_structural_expectancy_R": float(np.mean(rs)) if rs else None,
            "outcomes_n": len(rs),
            "resolutions": dict(resolutions),
            "null_p95_expectancy_R": float(np.percentile(null_means, 95)) if null_means else None,
            "null_draws": len(null_means),
        }
    return out


def classify_variant(variant, metrics, v0) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    total = sum(metrics[h]["eligible_episodes"] for h in ("H1", "H2"))
    checks["eligible_total_ge_60"] = total >= MIN_TOTAL_ELIGIBLE
    for h in ("H1", "H2"):
        m, b = metrics[h], v0[h]
        rr = m["r_remaining_at_first_detection"]
        exp, base = m["underlying_structural_expectancy_R"], b["underlying_structural_expectancy_R"]
        base = base if base is not None else 0.0
        checks[f"{h}_eligible_ge_2x_v0"] = m["eligible_episodes"] >= 2 * b["eligible_episodes"]
        checks[f"{h}_median_rr_ge_1"] = rr is not None and rr["median"] >= 1.0
        checks[f"{h}_target_before_detection_le_10pct"] = (
            m["target_before_detection_rate"] is not None and m["target_before_detection_rate"] <= 0.10)
        if variant in (VARIANTS[1], VARIANTS[3]):
            checks[f"{h}_false_positive_le_15pct"] = (
                m["false_positive_rate"] is not None and m["false_positive_rate"] <= 0.15)
        checks[f"{h}_expectancy_gt_0"] = exp is not None and exp > 0
        checks[f"{h}_expectancy_ge_v0_minus_0.05"] = exp is not None and exp >= base - 0.05
        checks[f"{h}_expectancy_gt_null_p95"] = (
            exp is not None and m["null_p95_expectancy_R"] is not None and exp > m["null_p95_expectancy_R"])
    verdict = "SUPPORTS" if all(checks.values()) else "NO IMPROVEMENT"
    return {"verdict": verdict, "checks": checks, "eligible_total": total}


def run_scoring(data_dir: Path, records_dir: Path) -> dict[str, Any]:
    sip = load_feed(data_dir, "sip")
    paths = RthPaths(sip)
    sip_30m = {k: v.to_30m_bars() for k, v in sip.items()}
    sessions = scoring_sessions()
    mid = len(sessions) // 2
    halves = {"H1": {s.date.isoformat() for s in sessions[:mid]},
              "H2": {s.date.isoformat() for s in sessions[mid:]}}
    metrics = {}
    for variant in VARIANTS:
        path = records_dir / f"{variant}.jsonl"
        with open(path, encoding="utf-8") as fh:
            episodes = episodes_from_records(json.loads(line) for line in fh)
        metrics[variant] = score_variant(variant, episodes, paths, sip_30m, halves)
    verdicts = {v: classify_variant(v, metrics[v], metrics[VARIANTS[0]]) for v in VARIANTS[1:]}
    overall = "SUPPORTS" if any(x["verdict"] == "SUPPORTS" for x in verdicts.values()) else "NO IMPROVEMENT"
    return {
        "trial_id": TRIAL_ID,
        "scoring_window": [SCORE_FIRST_SESSION.isoformat(), SCORE_LAST_SESSION.isoformat()],
        "sessions": len(sessions),
        "halves": {"H1": [sessions[0].date.isoformat(), sessions[mid - 1].date.isoformat()],
                   "H2": [sessions[mid].date.isoformat(), sessions[-1].date.isoformat()]},
        "metrics": metrics,
        "verdicts": verdicts,
        "overall": overall,
        "disposition": DISPOSITION[overall],
    }


# ----------------------------------------------------------------- pulling

async def _pull_scoring(out_dir: Path) -> dict[str, Any]:
    from alert_ranker.bar_provider import BarProviderError
    from alert_ranker.causal_bars import MINUTE_1

    first_scan = datetime(2025, 10, 1, 13, 30, tzinfo=timezone.utc) + SCAN_OFFSET
    start = pull_start(first_scan)
    end = SCORE_PULL_END
    _guard_before_exclusion(end, "pull end")
    summary: dict[str, Any] = {"start": start.isoformat(), "end": end.isoformat(), "bars": {}}
    for feed in ("sip", "iex"):
        provider = _provider(feed)
        for symbol in SYMBOLS:
            chunks = []
            lo = start
            while lo < end:
                hi = min(lo + timedelta(days=31), end)
                for attempt in range(6):
                    try:
                        got = await provider.fetch_bars([symbol], MINUTE_1, lo, hi)
                        chunks.extend(got[symbol])
                        break
                    except BarProviderError as exc:
                        if exc.reason == "missing_symbol":
                            break
                        if attempt == 5:
                            raise
                        await asyncio.sleep(2 ** attempt * 3)
                lo = hi
            dedup = {b.start_utc: b for b in chunks if b.start_utc + timedelta(minutes=1) <= end}
            series = MinuteSeries.from_bars([dedup[k] for k in sorted(dedup)])
            save_series(out_dir / feed / f"{symbol}.npz", series)
            summary["bars"][f"{feed}:{symbol}"] = len(series)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_pull = sub.add_parser("pull")
    p_pull.add_argument("--out", type=Path, required=True)
    p_pull.add_argument("--first-scan", required=True)
    sub.add_parser("probe-iex")
    p_step0 = sub.add_parser("step0")
    p_step0.add_argument("--bars", type=Path, required=True)
    p_step0.add_argument("--prod", type=Path, required=True)
    p_step0.add_argument("--out", type=Path, required=True)
    p_step0.add_argument("--variant", default=VARIANT)
    p_ps = sub.add_parser("pull-scoring")
    p_ps.add_argument("--out", type=Path, required=True)
    p_rs = sub.add_parser("replay-scoring")
    p_rs.add_argument("--variant", required=True, choices=VARIANTS)
    p_rs.add_argument("--data", type=Path, required=True)
    p_rs.add_argument("--out", type=Path, required=True)
    p_sc = sub.add_parser("score")
    p_sc.add_argument("--data", type=Path, required=True)
    p_sc.add_argument("--records", type=Path, required=True)
    p_sc.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.cmd == "pull-scoring":
        print(json.dumps(asyncio.run(_pull_scoring(args.out)), indent=2))
        return 0
    if args.cmd == "replay-scoring":
        with tempfile.TemporaryDirectory() as tmp:
            summary = asyncio.run(replay_scoring_variant(
                args.variant, args.data, args.out / f"{args.variant}.jsonl", Path(tmp)))
        print(json.dumps(summary))
        return 0
    if args.cmd == "score":
        result = run_scoring(args.data, args.records)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True, default=str))
        print(json.dumps({"overall": result["overall"],
                          "verdicts": {k: v["verdict"] for k, v in result["verdicts"].items()}}))
        return 0
    if args.cmd == "pull":
        print(json.dumps(asyncio.run(_pull(args.out, _parse_ts(args.first_scan))), indent=2))
    elif args.cmd == "probe-iex":
        print(json.dumps(asyncio.run(_probe_iex()), indent=2))
    else:
        if args.variant != VARIANT:
            raise SystemExit(f"only {VARIANT} is approved for Step 0")
        report = run_step0(args.bars, args.prod)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(json.dumps({k: report[k] for k in ("verdict", "cycles", "full_window", "code_equivalent_segment", "prereg_key_full_window", "prereg_key_code_equivalent_segment")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
