"""MGC 4H wide-geometry forward observation evaluator.

OBSERVATION / RESEARCH ONLY. Implements docs/prereg-mgc-4h-wide-forward-2026-09-23.md;
where this module and the prereg disagree, the prereg wins. Scored offline from
fresh Polygon bars. Places no orders and changes no runtime state.

Blind by default: ``counts_report`` never emits P&L-shaped keys. ``look_report``
is the single look and refuses unless the step-0 parity report passed and the
minimum sample (or the deadline) is reached.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
import time as _time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
REPO = Path(__file__).resolve().parents[1]

PREREG_DOC = "docs/prereg-mgc-4h-wide-forward-2026-09-23.md"
PREREG_ID = "mgc-4h-wide-forward-2026-09-23"
MARKET = "MGC"
MONTH_CODES = "GJMQVZ"
TF_MINUTES = 240
SCORING_START = datetime(2026, 9, 24, 22, 0, tzinfo=UTC)
DEADLINE = date(2027, 6, 30)
WARMUP_DAYS = 90
SETTLE_HOURS = 24
COST_PER_TRADE = 4.00
ATR_MULT = 2.0
MIN_TERMINAL_TRADES = 150
MIN_OBS_DAYS = 60
PF_MIN = 1.30
TOP3_SHARE_MAX = 0.50
MAX_DRAWDOWN = 6000.0
GAP_DAY_SHARE_MAX = 0.10
NULL_A_DRAWS = 500
NULL_B_DRAWS = 200
SEED = 947
PARITY_REPRODUCED_MIN = 0.90
PARITY_GEOMETRY_MIN = 0.95


# ─── data: dated contracts -> causal front chain ─────────────────────────────

def contract_tickers(start: date, end: date) -> list[str]:
    """Dated MGC tickers ordered by expiry (year, month)."""
    out = []
    for y in range(start.year - 1, end.year + 2):
        for m in MONTH_CODES:
            out.append(f"{MARKET}{m}{y % 10}")
    return out


def _expiry_rank(ticker: str, start_year: int) -> tuple[int, int]:
    m, y = ticker[len(MARKET)], int(ticker[len(MARKET) + 1:])
    decade = start_year - start_year % 10
    year = decade + y
    if year < start_year - 2:
        year += 10
    return year, MONTH_CODES.index(m)


def front_chain(volume_by_day: dict[date, dict[str, float]], start_year: int) -> dict[date, str]:
    """Front per UTC day = highest volume on the PREVIOUS UTC day; never roll backwards."""
    days = sorted(volume_by_day)
    front: dict[date, str] = {}
    prev = None
    cur = None
    for d in days:
        src = volume_by_day[prev] if prev is not None else volume_by_day[d]
        pick = max(src, key=src.get)
        if cur is not None and _expiry_rank(pick, start_year) < _expiry_rank(cur, start_year):
            pick = cur
        front[d] = pick
        cur = pick
        prev = d
    return front


def raw_sha256(bars: list[dict]) -> str:
    h = hashlib.sha256()
    for b in bars:
        h.update(json.dumps([b["ts"], b["open"], b["high"], b["low"], b["close"], b["volume"]],
                            separators=(",", ":")).encode() + b"\n")
    return h.hexdigest()


def settled_cutoff(fetched_at: datetime) -> datetime:
    from research.prereg929_forward_corpus import settled_cutoff as _cut

    return _cut(fetched_at)


def fetch_front_bars(start: date, end: date, *, client=None) -> dict:
    """Fresh Polygon 15m fetch of every dated MGC contract, spliced on the causal chain."""
    from sources.polygon_client import PolygonError, PolygonFuturesClient

    if client is None:
        try:
            from dotenv import load_dotenv

            load_dotenv(REPO / ".env")
        except ImportError:
            pass
        client = PolygonFuturesClient(min_request_interval=0.5)
    if not getattr(client, "configured", True):
        raise RuntimeError("POLYGON_API_KEY not set (local .env only)")
    by_ticker: dict[str, list] = {}
    skipped = []
    for t in contract_tickers(start, end):
        bars = None
        for attempt in range(6):
            try:
                bars = client.fetch_bars(t, start, end, 15)
                break
            except PolygonError as exc:
                if "429" in str(exc) and attempt < 5:
                    _time.sleep(30 * (attempt + 1))
                    continue
                skipped.append([t, str(exc)[:160]])
                break
        if bars:
            by_ticker[t] = bars
    vol: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for t, bars in by_ticker.items():
        for b in bars:
            vol[b.ts.date()][t] += float(b.volume or 0)
    front = front_chain(vol, start.year)
    raw = []
    for t, bars in by_ticker.items():
        for b in bars:
            if front.get(b.ts.date()) == t:
                raw.append({"ts": int(b.ts.timestamp()), "open": float(b.open), "high": float(b.high),
                            "low": float(b.low), "close": float(b.close), "volume": float(b.volume or 0)})
    raw.sort(key=lambda r: r["ts"])
    chain = []
    for d in sorted(front):
        if not chain or chain[-1][0] != front[d]:
            chain.append([front[d], d.isoformat()])
    front_tickers = {t for t, _ in chain}
    blocking = [s for s in skipped if s[0] in front_tickers]
    return {"raw15": raw, "chain": chain, "skipped": skipped, "skipped_front": blocking}


def resample_session(raw15: list[dict], tf: int) -> list[dict]:
    """Session-anchored buckets (18:00 ET roll) of the same 15m bars."""
    out, cur, key = [], None, None
    for r in raw15:
        et = datetime.fromtimestamp(r["ts"], ET)
        d = et.date() if et.time() >= time(18) else et.date() - timedelta(days=1)
        s0 = datetime.combine(d, time(18), tzinfo=ET).timestamp()
        k = (d, int((r["ts"] - s0) // (tf * 60)))
        if k != key:
            if cur:
                out.append(cur)
            key = k
            cur = {"ts": int(s0 + k[1] * tf * 60), "open": r["open"], "high": r["high"], "low": r["low"],
                   "close": r["close"], "volume": r["volume"]}
        else:
            cur["high"] = max(cur["high"], r["high"]); cur["low"] = min(cur["low"], r["low"])
            cur["close"] = r["close"]; cur["volume"] += r["volume"]
    if cur:
        out.append(cur)
    return out


def derive(raw: list[dict], tf: int) -> list[dict]:
    from scripts.polygon_to_replay import derive_candles

    return derive_candles(raw, MARKET, tf)


# ─── gap days (15m) ──────────────────────────────────────────────────────────

def gap_days(raw15: list[dict], first_day: date, last_day: date) -> list[dict]:
    from research.prereg929_forward_corpus import _reduced_schedule, _runs, _slots, obs_day_window

    have = {r["ts"] for r in raw15}
    out = []
    d = first_day
    while d <= last_day:
        if d.weekday() < 5:
            lo, hi = obs_day_window(d)
            slots = _slots(lo, hi, 900)
            reduced = _reduced_schedule(d)
            present = [s for s in slots if s in have]
            if reduced:
                slots = [s for s in slots if present and present[0] <= s <= present[-1]]
            missing = [s for s in slots if s not in have]
            if missing:
                out.append({"obs_day": d.isoformat(), "reduced_schedule": reduced, "missing_15m": _runs(missing, 900)})
        d += timedelta(days=1)
    return out


def obs_day_window_utc(day: str) -> tuple[datetime, datetime]:
    d = date.fromisoformat(day)
    lo = datetime.combine(d - timedelta(days=1), time(18), tzinfo=ET).astimezone(UTC)
    hi = datetime.combine(d, time(18), tzinfo=ET).astimezone(UTC)
    return lo, hi


# ─── detection (production functions, unchanged) ─────────────────────────────

def structural_setups() -> set[str]:
    cfg = json.loads((REPO / "config" / "cross_instrument_observation.json").read_text())
    return {p["strategy"] for p in cfg["populations"]
            if p["collection_mode"] == "structural_outcome" and MARKET in p["instruments"]}


def to_replay_candles(rows: list[dict]):
    from replay.candle_loader import ReplayCandleLoader

    return [ReplayCandleLoader._parse(r) for r in rows]


def detect(candles, tf: int) -> list[dict]:
    """Run the production structural detectors over a candle stream."""
    import execution.cross_instrument_observation as cio
    from replay.replay_engine import ReplayEngine
    from strategy.shadow_setups import evaluate_shadow_setups

    import tempfile

    engine = ReplayEngine(log_dir=tempfile.mkdtemp(prefix="mgc4h-"))
    keep = structural_setups()
    prev = prevprev = None
    research: deque = deque(maxlen=32)
    camp = {"strat_212_122": {}}
    out = []
    for c in candles:
        research.append({"ts": c.timestamp, "open": c.open, "high": c.high, "low": c.low,
                         "close": c.close, "volume": c.volume, "timeframe": str(tf)})
        state = engine._market_state_from_candle(c, prev, prevprev)
        prevprev, prev = prev, c
        cur = datetime.fromisoformat(c.timestamp).date()
        recent = [b for b in research if 0 <= (cur - datetime.fromisoformat(b["ts"]).date()).days < 3][-8:]
        try:
            day = cio.observation_day(MARKET, c.timestamp).isoformat()
        except Exception:
            continue
        found = [x.to_dict() for x in evaluate_shadow_setups(state, recent, engine.config,
                                                             include_canonical_observers=False)]
        canon = cio._strat_212_122_candidate(state, camp, MARKET, day, PREREG_ID)
        if canon:
            found.append(canon)
        atr = (c.source or {}).get("reconstructed_atr14")
        for cd in found:
            if cd.get("strategy") not in keep:
                continue
            d = str(cd.get("direction") or "").upper()
            try:
                e, s, t = float(cd["entry"]), float(cd["stop"]), float(cd["target"])
            except (KeyError, TypeError, ValueError):
                continue
            if d not in ("LONG", "SHORT") or not math.isfinite(e) or abs(e - s) <= 0:
                continue
            out.append({"strategy": cd["strategy"], "direction": d, "entry": e, "stop": s, "target": t,
                        "signal_ts": c.timestamp, "day": day, "atr": atr,
                        "filled_on_signal_bar": bool(cd.get("filled_on_signal_bar")),
                        "pre_resolved": cd.get("pre_resolved")})
    return out


# ─── resolution ──────────────────────────────────────────────────────────────

class Path15:
    def __init__(self, bars15: list[dict]):
        import execution.cross_instrument_observation as cio

        self.bars = bars15
        self.ts = [b["ts"] for b in bars15]
        self.day = {}
        for b in bars15:
            try:
                self.day[b["ts"]] = cio.observation_day(MARKET, b["ts"]).isoformat()
            except Exception:
                self.day[b["ts"]] = None

    def forward(self, after_ts: str, day: str) -> list[dict]:
        i = bisect.bisect_left(self.ts, after_ts)
        out = []
        while i < len(self.bars) and self.day.get(self.ts[i]) == day:
            out.append(self.bars[i]); i += 1
        return out


def resolve_wide(direction: str, entry: float, atr: Optional[float], filled_on_signal_bar: bool,
                 fwd: list[dict]) -> Optional[dict]:
    """§3 wide geometry. Returns gross dollars, result, exit ts; None = no fill / no ATR."""
    from config.futures_contracts import contract_economics

    tick, tv = contract_economics(MARKET)
    if not atr or not math.isfinite(float(atr)) or float(atr) <= 0:
        return None
    sign = 1 if direction == "LONG" else -1
    risk = ATR_MULT * float(atr)
    stop = entry - sign * risk
    filled = filled_on_signal_bar
    best = entry
    armed = False
    next_stop = None
    last = None

    def money(px):
        return (px - entry) * sign / tick * tv

    for b in fwd:
        hi, lo = float(b["high"]), float(b["low"])
        last = b
        if not filled:
            if lo <= entry <= hi:
                filled = True
                if (lo <= stop) if sign > 0 else (hi >= stop):
                    return {"gross": money(stop), "result": "LOSS", "exit_ts": b["ts"], "path_end": b["ts"]}
                best = max(entry, hi) if sign > 0 else min(entry, lo)
                if (best - entry) * sign >= risk:
                    armed = True
                    next_stop = max(best - risk, entry) if sign > 0 else min(best + risk, entry)
            continue
        if next_stop is not None:
            stop = max(stop, next_stop) if sign > 0 else min(stop, next_stop)
        if (lo <= stop) if sign > 0 else (hi >= stop):
            g = money(stop)
            return {"gross": g, "result": "WIN" if g > 0 else ("LOSS" if g < 0 else "BREAKEVEN"),
                    "exit_ts": b["ts"], "path_end": b["ts"]}
        best = max(best, hi) if sign > 0 else min(best, lo)
        if (best - entry) * sign >= risk:
            armed = True
        if armed:
            next_stop = max(best - risk, entry) if sign > 0 else min(best + risk, entry)
    if not filled or last is None:
        return None
    return {"gross": money(float(last["close"])), "result": "EXPIRED", "exit_ts": last["ts"], "path_end": last["ts"]}


# ─── the run ─────────────────────────────────────────────────────────────────

@dataclass
class Run:
    fetched_at: str
    cutoff: str
    chain: list
    skipped_front: list
    raw_sha256: str
    gap_days: list
    trades: list = field(default_factory=list)
    voided: list = field(default_factory=list)
    scoring_candles: list = field(default_factory=list)
    healthy: bool = True
    error: Optional[str] = None


def build_run(*, fetched_at: Optional[datetime] = None, client=None, fetch=None) -> Run:
    """Fetch, settle, derive, detect, resolve. ``fetch`` lets tests inject bars."""
    fetched_at = (fetched_at or datetime.now(UTC)).astimezone(UTC)
    cutoff = settled_cutoff(fetched_at)
    start = SCORING_START.date() - timedelta(days=WARMUP_DAYS)
    end = cutoff.astimezone(ET).date()
    data = fetch(start, end) if fetch else fetch_front_bars(start, end, client=client)
    lim = int(cutoff.timestamp())
    raw15 = [r for r in data["raw15"] if r["ts"] + 900 <= lim]
    run = Run(fetched_at=fetched_at.isoformat(), cutoff=cutoff.isoformat(), chain=data.get("chain", []),
              skipped_front=data.get("skipped_front", []), raw_sha256=raw_sha256(raw15), gap_days=[])
    if run.skipped_front:
        run.healthy, run.error = False, "a front-month contract could not be fetched"
        return run
    first_obs = (SCORING_START + timedelta(hours=6)).astimezone(ET).date()
    run.gap_days = gap_days(raw15, first_obs, end) if end >= first_obs else []
    gap_set = {g["obs_day"] for g in run.gap_days}
    rows15 = derive(raw15, 15)
    rows240 = derive(resample_session(raw15, TF_MINUTES), TF_MINUTES)
    bars15 = [{"ts": r["timestamp"], "open": r["open"], "high": r["high"], "low": r["low"],
               "close": r["close"], "timeframe": "15"} for r in rows15]
    candles = to_replay_candles(rows240)
    run.scoring_candles = [{"ts": c.timestamp, "close": c.close,
                            "atr": (c.source or {}).get("reconstructed_atr14")}
                           for c in candles if c.timestamp >= SCORING_START.isoformat()]
    path = Path15(bars15)
    for cd in detect(candles, TF_MINUTES):
        if cd["signal_ts"] < SCORING_START.isoformat():
            continue
        fwd = path.forward(_plus(cd["signal_ts"], TF_MINUTES), cd["day"])
        r = resolve_wide(cd["direction"], cd["entry"], cd["atr"], cd["filled_on_signal_bar"], fwd)
        if r is None:
            continue
        mr = resolve_wide("SHORT" if cd["direction"] == "LONG" else "LONG", cd["entry"], cd["atr"],
                          cd["filled_on_signal_bar"], fwd)
        trade = {"strategy": cd["strategy"], "direction": cd["direction"], "signal_ts": cd["signal_ts"],
                 "day": cd["day"], "result": r["result"], "exit_ts": r["exit_ts"],
                 "net": round(r["gross"] - COST_PER_TRADE, 2),
                 "mirror_net": round(mr["gross"] - COST_PER_TRADE, 2) if mr else None}
        if _touches_gap(cd["signal_ts"], r["exit_ts"], gap_set):
            run.voided.append(trade)
        else:
            run.trades.append(trade)
    run.trades.sort(key=lambda t: (t["exit_ts"], t["signal_ts"]))
    run._path = path  # for null B
    return run


def _plus(ts: str, minutes: int) -> str:
    return (datetime.fromisoformat(ts) + timedelta(minutes=minutes)).isoformat()


def _touches_gap(signal_ts: str, exit_ts: str, gap_set: set[str]) -> bool:
    lo, hi = datetime.fromisoformat(signal_ts), datetime.fromisoformat(exit_ts)
    for d in gap_set:
        a, b = obs_day_window_utc(d)
        if lo < b and a <= hi:
            return True
    return False


# ─── counts (blind) ──────────────────────────────────────────────────────────

FORBIDDEN_KEY_FRAGMENTS = ("net", "pnl", "pf", "profit", "drawdown", "dd", "win", "loss", "breakeven",
                           "expectancy", "equity", "result", "exit", "entry", "stop", "target", "return")


def assert_blind(obj, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            low = str(k).lower()
            for frag in FORBIDDEN_KEY_FRAGMENTS:
                if frag in low:
                    raise AssertionError(f"blind violation: key {path}/{k} contains {frag!r}")
            assert_blind(v, f"{path}/{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_blind(v, f"{path}[{i}]")


def _candle_days(run: Run) -> set[str]:
    import execution.cross_instrument_observation as cio

    out = set()
    cutoff = datetime.fromisoformat(run.cutoff)
    for c in run.scoring_candles:
        ts = datetime.fromisoformat(c["ts"])
        if ts + timedelta(minutes=TF_MINUTES) > cutoff:
            continue
        try:
            d = cio.observation_day(MARKET, c["ts"]).isoformat()
        except Exception:
            continue
        if d not in {g["obs_day"] for g in run.gap_days}:
            out.add(d)
    return out


def sample_status(n: int, days: int, today: date) -> dict:
    met = n >= MIN_TERMINAL_TRADES and days >= MIN_OBS_DAYS
    return {"minimum_sample_met": met, "deadline_reached": today > DEADLINE, "look_allowed": met or today > DEADLINE,
            "min_terminal_trades": MIN_TERMINAL_TRADES, "min_observation_days": MIN_OBS_DAYS,
            "deadline": DEADLINE.isoformat()}


def gap_status(run: Run, days: int) -> dict:
    n_gap = len(run.gap_days)
    share = n_gap / (days + n_gap) if (days + n_gap) else 0.0
    return {"gap_day_count": n_gap, "gap_day_share": round(share, 6), "gap_day_share_cap": GAP_DAY_SHARE_MAX,
            "cap_exceeded": share > GAP_DAY_SHARE_MAX, "gap_days": [g["obs_day"] for g in run.gap_days]}


def counts_report(run: Run, *, as_of: datetime) -> dict:
    days = len(_candle_days(run))
    out = {
        "prereg": PREREG_ID, "mode": "counts", "as_of": as_of.isoformat(),
        "scoring_start": SCORING_START.isoformat(), "fetched_at": run.fetched_at, "settled_cutoff": run.cutoff,
        "raw_sha256": run.raw_sha256, "contract_chain": run.chain, "pipeline_healthy": run.healthy,
        "pipeline_error": run.error,
        "terminal_trades": len(run.trades),
        "terminal_trades_by_setup": dict(sorted(Counter(t["strategy"] for t in run.trades).items())),
        "void_gap_day_trades": len(run.voided),
        "observation_days": days,
        "gaps": gap_status(run, days),
        "sample": sample_status(len(run.trades), days, as_of.date()),
    }
    assert_blind(out)
    return out


# ─── the single look ─────────────────────────────────────────────────────────

class LookRefused(RuntimeError):
    pass


def pf(v: list[float]) -> float:
    gp = sum(x for x in v if x > 0); gl = -sum(x for x in v if x < 0)
    return gp / gl if gl > 0 else (math.inf if gp > 0 else 0.0)


def max_drawdown(v: list[float]) -> float:
    eq = pk = dd = 0.0
    for x in v:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    return round(dd, 2)


def q95(xs: list[float]) -> Optional[float]:
    xs = sorted(x for x in xs if not math.isnan(x))
    return xs[int(0.95 * (len(xs) - 1))] if xs else None


def null_random_direction(trades: list[dict], rng: random.Random) -> list[float]:
    pairs = [(t["net"], t["mirror_net"]) for t in trades if t.get("mirror_net") is not None]
    return [pf([a if rng.random() < 0.5 else b for a, b in pairs]) for _ in range(NULL_A_DRAWS)] if pairs else []


def null_random_time(run: Run, trades: list[dict], rng: random.Random) -> list[float]:
    import execution.cross_instrument_observation as cio

    pool = [c for c in run.scoring_candles if c["atr"]]
    dirs = [t["direction"] for t in trades] or ["LONG", "SHORT"]
    out = []
    n = len(trades)
    if not pool or not n:
        return out
    for _ in range(NULL_B_DRAWS):
        v = []
        tries = 0
        while len(v) < n and tries < 20 * n:
            tries += 1
            c = rng.choice(pool)
            try:
                day = cio.observation_day(MARKET, c["ts"]).isoformat()
            except Exception:
                continue
            fwd = run._path.forward(_plus(c["ts"], TF_MINUTES), day)
            r = resolve_wide(rng.choice(dirs), float(c["close"]), c["atr"], False, fwd)
            if r:
                v.append(r["gross"] - COST_PER_TRADE)
        if len(v) >= 20:
            out.append(pf(v))
    return out


def validate_step0(path: Optional[Path]) -> dict:
    if path is None or not Path(path).is_file():
        raise LookRefused("look requires a step-0 parity report (--step0-report)")
    rep = json.loads(Path(path).read_text())
    if rep.get("prereg") != PREREG_ID or rep.get("kind") != "step0_parity":
        raise LookRefused("step-0 report is not for this prereg")
    if rep.get("verdict") != "PASS":
        raise LookRefused(f"step-0 parity did not PASS ({rep.get('verdict')}) -> BLOCKED")
    return rep


def look_report(run: Run, *, as_of: datetime, step0_path: Optional[Path]) -> dict:
    step0 = validate_step0(step0_path)
    if not run.healthy:
        raise LookRefused(f"pipeline unhealthy: {run.error}")
    trades = run.trades
    days = len(_candle_days(run))
    sample = sample_status(len(trades), days, as_of.date())
    if not sample["look_allowed"]:
        raise LookRefused(f"minimum sample not met ({len(trades)}/{MIN_TERMINAL_TRADES} trades, "
                          f"{days}/{MIN_OBS_DAYS} days) and the deadline has not passed")
    rng = random.Random(SEED)
    v = [t["net"] for t in trades]
    h = len(v) // 2
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t["day"]] += t["net"]
    net = round(sum(v), 2)
    top3 = sum(sorted(by_day.values(), reverse=True)[:3]) / net if net > 0 else None
    na, nb = null_random_direction(trades, rng), null_random_time(run, trades, rng)
    qa, qb = q95(na), q95(nb)
    p = pf(v)
    gaps = gap_status(run, days)
    crit = {
        "1_sample": {"value": [len(trades), days], "pass": sample["minimum_sample_met"]},
        "2_net_gt_0": {"value": net, "pass": net > 0},
        "3_pf_ge_1_30": {"value": round(p, 4) if p != math.inf else "inf", "pass": p >= PF_MIN},
        "4_both_halves_gt_0": {"value": [round(sum(v[:h]), 2), round(sum(v[h:]), 2)],
                               "pass": sum(v[:h]) > 0 and sum(v[h:]) > 0},
        "5_top3_days_le_50pct": {"value": round(top3, 4) if top3 is not None else None,
                                 "pass": top3 is not None and top3 <= TOP3_SHARE_MAX},
        "6_max_drawdown_le_6000": {"value": max_drawdown(v), "pass": max_drawdown(v) <= MAX_DRAWDOWN},
        "7_beats_random_direction_95": {"value": qa, "pass": qa is not None and p > qa},
        "8_beats_random_time_95": {"value": qb, "pass": qb is not None and p > qb},
    }
    if gaps["cap_exceeded"]:
        verdict = "INSUFFICIENT_DATA"
    elif not sample["minimum_sample_met"]:
        verdict = "INSUFFICIENT_SAMPLE"
    elif all(c["pass"] for c in crit.values()):
        verdict = "FORWARD_EVIDENCE"
    else:
        verdict = "REJECTED"
    by = defaultdict(list)
    for t in trades:
        by[t["direction"]].append(t["net"])
    setups = defaultdict(list)
    for t in trades:
        setups[t["strategy"]].append(t["net"])
    monthly = defaultdict(float)
    for t in trades:
        monthly[t["day"][:7]] += t["net"]
    return {
        "prereg": PREREG_ID, "mode": "look", "as_of": as_of.isoformat(), "verdict": verdict,
        "criteria": crit, "gaps": gaps, "sample": sample, "step0": {"generated_at": step0.get("generated_at")},
        "fetched_at": run.fetched_at, "raw_sha256": run.raw_sha256, "contract_chain": run.chain,
        "descriptive": {
            "direction": {k: {"n": len(x), "net": round(sum(x), 2), "pf": round(pf(x), 3) if pf(x) != math.inf else "inf"} for k, x in by.items()},
            "setup": {k: {"n": len(x), "net": round(sum(x), 2)} for k, x in sorted(setups.items())},
            "monthly_net": {k: round(x, 2) for k, x in sorted(monthly.items())},
            "void_gap_day_trades": len(run.voided),
        },
        "authority": "Observation/research only. FORWARD_EVIDENCE permits a separate proposal, never a GO.",
    }


# ─── step 0: parity vs the live observation lane ─────────────────────────────

def step0_parity(live_obs_path: Path, *, fetched_at: Optional[datetime] = None, client=None, fetch=None) -> dict:
    """Production 15m detectors on the Polygon bars vs live MGC CANDIDATE rows, scoring window."""
    from config.futures_contracts import contract_economics

    fetched_at = (fetched_at or datetime.now(UTC)).astimezone(UTC)
    cutoff = settled_cutoff(fetched_at)
    start = SCORING_START.date() - timedelta(days=WARMUP_DAYS)
    end = cutoff.astimezone(ET).date()
    data = fetch(start, end) if fetch else fetch_front_bars(start, end, client=client)
    lim = int(cutoff.timestamp())
    raw15 = [r for r in data["raw15"] if r["ts"] + 900 <= lim]
    cands = detect(to_replay_candles(derive(raw15, 15)), 15)
    tick, _ = contract_economics(MARKET)
    lo_ts, hi_ts = SCORING_START.timestamp(), cutoff.timestamp()

    def key(strategy, ts, d):
        return (strategy, datetime.fromisoformat(ts).timestamp(), d)

    mine = {key(c["strategy"], c["signal_ts"], c["direction"]): c for c in cands}
    live = {}
    for line in Path(live_obs_path).read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (r.get("record_type") == "CANDIDATE" and r.get("instrument") == MARKET
                and r.get("collection_mode") == "structural_outcome"):
            k = key(r["strategy"], r["signal_timestamp"], r["direction"])
            if lo_ts <= k[1] < hi_ts - 15 * 60:
                live[k] = r
    hit = [k for k in live if k in mine]
    geo = [k for k in hit if all(abs(float(live[k][f]) - float(mine[k][f])) <= tick + 1e-9
                                 for f in ("entry", "stop", "target"))]
    rate = len(hit) / len(live) if live else 0.0
    grate = len(geo) / len(hit) if hit else 0.0
    verdict = "PASS" if live and rate >= PARITY_REPRODUCED_MIN and grate >= PARITY_GEOMETRY_MIN else "FAIL"
    return {"prereg": PREREG_ID, "kind": "step0_parity", "generated_at": datetime.now(UTC).isoformat(),
            "live_candidates": len(live), "reproduced": len(hit), "geometry_ok": len(geo),
            "reproduced_rate": round(rate, 4), "geometry_rate": round(grate, 4),
            "thresholds": [PARITY_REPRODUCED_MIN, PARITY_GEOMETRY_MIN], "verdict": verdict,
            "missing_examples": [[k[0], datetime.fromtimestamp(k[1], UTC).isoformat(), k[2]]
                                 for k in live if k not in mine][:20]}
