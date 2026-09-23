"""Cross-market paper admission evaluator (prereg 2026-09-23).

RESEARCH ONLY. Implements docs/prereg-cross-market-paper-admission-2026-09-23.md
exactly; where this module and the prereg disagree, the prereg wins. It reads
the observation lane's OUTCOME rows and reports, per (market, setup) pair, the
Stage A screen, the Stage B fresh-trade confirmation and the pair status. It
never changes runtime state and grants no execution authority.

Standard library only, so it can run on the box against the live log.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

PREREG_DOC = "docs/prereg-cross-market-paper-admission-2026-09-23.md"
EPOCH = "62546883+2026-09-16T12:17:19Z"
MARKETS = ("M2K", "MGC", "MCL", "MBT")
SETUPS = (
    "strat_212",
    "strat_122",
    "strat_22_continuation_observed",
    "strat_22_reversal_observed",
    "strat_312_observed",
    "strat_322_reversal_observed",
    "impulse_first_pullback_observed",
    "trend_consolidation_break_observed",
    "transition_failed_breakdown_reclaim",
)
TERMINAL_RESULTS = frozenset({"WIN", "LOSS", "EXPIRED"})

# §3 round-trip commission + fees, and tick value for the 2-tick slippage.
COMMISSION_ROUND_TRIP = {"M2K": 1.48, "MGC": 2.00, "MCL": 2.00, "MBT": 6.00}
TICK_VALUE = {"M2K": 0.50, "MGC": 1.00, "MCL": 1.00, "MBT": 0.50}
SLIPPAGE_TICKS_ROUND_TRIP = 2


def cost_per_trade(market: str) -> float:
    return round(COMMISSION_ROUND_TRIP[market] + SLIPPAGE_TICKS_ROUND_TRIP * TICK_VALUE[market], 2)


# §4 / §5 / §6
FIRST_CHECKPOINT = date(2026, 9, 25)  # Friday
CHECKPOINT_TIME_ET = time(17, 0)
DEADLINE = date(2027, 3, 31)
A_MIN_TRADES = 40
A_MIN_DAYS = 15
PF_HURDLE = 1.94
TOP3_SHARE_MAX = 0.60
MAX_DRAWDOWN = 1750.0
B_TRADES = 40

STATUS_SCREENING = "SCREENING"
STATUS_CONFIRMING = "CONFIRMING"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_REJECTED = "REJECTED"
STATUS_NOT_ADMITTED = "NOT_ADMITTED"
STATUS_INSUFFICIENT = "INSUFFICIENT_CONFIRMATION"


def _ts(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class Trade:
    market: str
    setup: str
    signal_ts: datetime
    exit_ts: datetime
    trade_date: str
    gross: float
    net: float
    result: str
    candidate_id: str


def trade_from_row(row: dict) -> Optional[Trade]:
    """§2: a terminal, filled, in-scope OUTCOME row of the registered epoch."""
    if row.get("record_type") != "OUTCOME" or row.get("evidence_epoch") != EPOCH:
        return None
    market, setup = row.get("instrument"), row.get("strategy")
    if market not in MARKETS or setup not in SETUPS:
        return None
    if row.get("entry_filled") is not True or row.get("result") not in TERMINAL_RESULTS:
        return None
    gross = row.get("gross_pnl_dollars_1_contract")
    if not isinstance(gross, (int, float)) or isinstance(gross, bool):
        return None
    signal_ts = _ts(row.get("signal_timestamp"))
    exit_ts = _ts(row.get("exit_timestamp")) or _ts(row.get("resolved_at_bar_ts"))
    if signal_ts is None or exit_ts is None:
        return None
    trade_date = str(row.get("trading_date") or row.get("observation_date") or signal_ts.astimezone(ET).date())
    return Trade(
        market=market,
        setup=setup,
        signal_ts=signal_ts,
        exit_ts=exit_ts,
        trade_date=trade_date,
        gross=float(gross),
        net=round(float(gross) - cost_per_trade(market), 2),
        result=str(row["result"]),
        candidate_id=str(row.get("candidate_id") or ""),
    )


def load_trades(rows: Iterable[dict]) -> dict[tuple[str, str], list[Trade]]:
    """Group terminal trades by pair, ordered by exit; duplicate candidates keep the first."""
    out: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    seen: set[str] = set()
    for row in rows:
        t = trade_from_row(row)
        if t is None:
            continue
        if t.candidate_id and t.candidate_id in seen:
            continue
        if t.candidate_id:
            seen.add(t.candidate_id)
        out[(t.market, t.setup)].append(t)
    for trades in out.values():
        trades.sort(key=lambda t: (t.exit_ts, t.signal_ts, t.candidate_id))
    return out


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


# ─── metrics ─────────────────────────────────────────────────────────────────

def _pf(nets: list[float]) -> Optional[float]:
    wins = sum(n for n in nets if n > 0)
    losses = -sum(n for n in nets if n < 0)
    if losses == 0:
        return None if wins == 0 else float("inf")
    return wins / losses


def _max_drawdown(nets: list[float]) -> float:
    peak = equity = dd = 0.0
    for n in nets:
        equity += n
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return round(dd, 2)


def _top3_share(trades: list[Trade]) -> Optional[float]:
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t.trade_date] += t.net
    net = sum(by_day.values())
    if net <= 0:
        return None
    return sum(sorted(by_day.values(), reverse=True)[:3]) / net


def _fmt(x):
    if x is None:
        return None
    if x == float("inf"):
        return "inf"
    return round(x, 4)


def stage_a_metrics(trades: list[Trade]) -> dict:
    nets = [t.net for t in trades]
    half = len(trades) // 2
    first, second = nets[:half], nets[half:]
    pf = _pf(nets)
    share = _top3_share(trades)
    dd = _max_drawdown(nets)
    days = len({t.trade_date for t in trades})
    net = round(sum(nets), 2)
    criteria = {
        "A1_trades_ge_40": len(trades) >= A_MIN_TRADES,
        "A2_days_ge_15": days >= A_MIN_DAYS,
        "A3_net_gt_0": net > 0,
        "A4_pf_ge_1_94": pf is not None and pf >= PF_HURDLE,
        "A5_both_halves_net_gt_0": bool(first) and bool(second) and sum(first) > 0 and sum(second) > 0,
        "A6_top3_days_le_60pct": share is not None and share <= TOP3_SHARE_MAX,
        "A7_max_drawdown_le_1750": dd <= MAX_DRAWDOWN,
    }
    return {
        "trades": len(trades),
        "trade_dates": days,
        "net": net,
        "pf": _fmt(pf),
        "first_half_net": round(sum(first), 2),
        "second_half_net": round(sum(second), 2),
        "top3_day_share": _fmt(share),
        "max_drawdown": dd,
        "criteria": criteria,
        "pass": all(criteria.values()),
    }


def stage_b_metrics(trades: list[Trade]) -> dict:
    nets = [t.net for t in trades]
    half = len(nets) // 2
    pf = _pf(nets)
    net = round(sum(nets), 2)
    criteria = {
        "B_net_gt_0": net > 0,
        "B_pf_ge_1_94": pf is not None and pf >= PF_HURDLE,
        "B_both_halves_net_gt_0": sum(nets[:half]) > 0 and sum(nets[half:]) > 0,
    }
    return {
        "trades": len(trades),
        "net": net,
        "pf": _fmt(pf),
        "first_half_net": round(sum(nets[:half]), 2),
        "second_half_net": round(sum(nets[half:]), 2),
        "criteria": criteria,
        "pass": all(criteria.values()),
    }


# ─── checkpoints + status ────────────────────────────────────────────────────

def checkpoint_instant(day: date) -> datetime:
    return datetime.combine(day, CHECKPOINT_TIME_ET, tzinfo=ET).astimezone(UTC)


def checkpoints_through(as_of: datetime) -> list[date]:
    """Fridays from FIRST_CHECKPOINT whose 17:00 ET has passed, capped at the deadline."""
    out = []
    d = FIRST_CHECKPOINT
    while checkpoint_instant(d) <= as_of and d <= DEADLINE:
        out.append(d)
        d += timedelta(days=7)
    return out


def evaluate_pair(trades: list[Trade], as_of: datetime) -> dict:
    """Deterministic §4–§6 status for one pair as of ``as_of``."""
    d_pass: Optional[date] = None
    a_at_pass: Optional[dict] = None
    for cp in checkpoints_through(as_of):
        sample = [t for t in trades if t.exit_ts <= checkpoint_instant(cp)]
        m = stage_a_metrics(sample)
        if m["pass"]:
            d_pass, a_at_pass = cp, m
            break
    latest = stage_a_metrics([t for t in trades if t.exit_ts <= as_of])
    deadline_passed = as_of.astimezone(ET).date() > DEADLINE
    if d_pass is None:
        return {
            "status": STATUS_NOT_ADMITTED if deadline_passed else STATUS_SCREENING,
            "stage_a_pass_checkpoint": None,
            "stage_a_latest": latest,
            "stage_b": None,
        }
    cut = checkpoint_instant(d_pass)
    fresh = [t for t in trades if t.signal_ts > cut and t.exit_ts <= as_of]
    fresh.sort(key=lambda t: (t.exit_ts, t.signal_ts, t.candidate_id))
    if len(fresh) < B_TRADES:
        status = STATUS_INSUFFICIENT if deadline_passed else STATUS_CONFIRMING
        stage_b = {"trades_so_far": len(fresh), "needed": B_TRADES}
    else:
        b = stage_b_metrics(fresh[:B_TRADES])
        status = STATUS_CONFIRMED if b["pass"] else STATUS_REJECTED
        stage_b = b
    return {
        "status": status,
        "stage_a_pass_checkpoint": d_pass.isoformat(),
        "stage_a_at_pass": a_at_pass,
        "stage_a_latest": latest,
        "stage_b": stage_b,
    }


def evaluate(rows: Iterable[dict], as_of: datetime) -> dict:
    by_pair = load_trades(rows)
    pairs = {}
    for market in MARKETS:
        for setup in SETUPS:
            pairs[f"{market}:{setup}"] = evaluate_pair(by_pair.get((market, setup), []), as_of)
    counts: dict[str, int] = defaultdict(int)
    for p in pairs.values():
        counts[p["status"]] += 1
    return {
        "prereg": PREREG_DOC,
        "epoch": EPOCH,
        "as_of": as_of.isoformat(),
        "costs_per_trade": {m: cost_per_trade(m) for m in MARKETS},
        "checkpoints_passed": [d.isoformat() for d in checkpoints_through(as_of)],
        "status_counts": dict(sorted(counts.items())),
        "pairs": pairs,
        "authority": "Research only. CONFIRMED makes a pair eligible for a proposal; it is not a GO.",
    }
