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
    args = parser.parse_args(argv)

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
