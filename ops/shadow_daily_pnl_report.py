#!/usr/bin/env python3
"""Daily shadow P&L report (read-only, evidence only).

Adds up one trading day of SHADOW_OUTCOME rows from the engine journal
(`journal_<UTC date>.jsonl`) into dollars: per market and per strategy,
before and after costs. Answers "how did the shadow do today?" without a
hand-run script.

Never grants execution eligibility. Never changes a rule. Reads journals only.

Day = the row's `candidate_day` (the trading day the candidate belongs to), so
the evening Asia session that starts on the previous UTC date is included.
Rows are de-duplicated by `candidate_key` (last row wins).

Costs: MNQ/MES use the proven forward-campaign assumptions (1 tick slippage,
$1.48 commission per round trip) — same model as gate_condition_report.
Other instruments are reported gross only. OPEN (unresolved at end of day) and
NO_FILL rows are counted but carry no dollars.

Late rows: the resolver only finalizes a day's NO_FILL/OPEN rows on the first
bar of a LATER UTC day (and a dead feed can delay WIN/LOSS rows by days), so the
same-evening run misses some. `--final` re-runs a past day, writes
`shadow_daily_pnl_<day>_final.json`, and says what changed since the first pass.

Stacking: the shadow lane is uncapped, so one setup can open a new trade while
an earlier trade of the same market + strategy + direction is still live. The
report also shows a one-at-a-time view that leaves those stacked trades out.
"Left out" counts closed trades only, so kept + left out equals the market's
trade count; overlapping trades still open are reported separately.
Fill/exit times come from the stored bar files (the same bars the resolver
used); without them it falls back to 15-minute bars and flags the count.

Your limits (operator rule, 2026-09-29): the same shadow trades replayed under
at most 3 FILLED trades in the night session (18:00-09:30 ET) and 3 in the day
session (09:30-17:00 ET), reset at 18:00 ET (CME trading date), one position at
a time — once for the whole account and once for each market separately. Trades
are taken in fill order; one that fills while the account (or market) is still
in a trade, or after its session already has 3, is skipped. A trade the
resolver left open is played forward on the stored bars (stop checked before
target) and otherwise closed at the 17:00 ET close of its trading date. Shown
for the report's trading date and the week so far, next to every signal under
the same pricing. Raw collection is untouched: this is a view of the same rows.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.futures_contracts import TICK_SIZE, TICK_VALUE  # noqa: E402
from context.bar_history import _parse_dt  # noqa: E402
from notifications import plain_english as pe  # noqa: E402
from ops.gate_condition_report import (  # noqa: E402
    COMMISSION_DOLLARS,
    COSTED_INSTRUMENTS,
    SLIPPAGE_TICKS,
    _post_discord,
    net_dollars,
)
from strategy.shadow_resolver import LOOKBACK_DAYS  # noqa: E402

ET = ZoneInfo("America/New_York")
REPORT_INSTRUMENTS = ("MNQ", "MES", "M2K", "MBT", "MCL", "MGC")
# The resolver runs on the 15M ingestion path only; used when bar files are missing.
FALLBACK_BAR_MINUTES = 15


def _empty() -> dict:
    return {"closed": 0, "wins": 0, "losses": 0, "open": 0, "no_fill": 0, "gross_usd": 0.0, "net_usd": None}


def load_outcomes(log_dir: str | Path, day: date) -> list[dict]:
    """SHADOW_OUTCOME rows whose candidate_day == day, de-duplicated by candidate_key.

    Reads ahead as far as the resolver's lookback: a row is journaled on the UTC
    date it resolved, which can be several days after its candidate day.
    """
    return load_outcomes_range(log_dir, day, day)


def load_outcomes_range(log_dir: str | Path, first: date, last: date) -> list[dict]:
    """load_outcomes for every candidate day first..last, reading each journal once."""
    wanted = {(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)}
    latest: dict[str, dict] = {}
    for offset in range(-1, (last - first).days + LOOKBACK_DAYS):
        path = Path(log_dir) / f"journal_{(first + timedelta(days=offset)).isoformat()}.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") != "SHADOW_OUTCOME" or row.get("candidate_day") not in wanted:
                    continue
                key = row.get("candidate_key") or row.get("event_id") or line
                latest[key] = row
    return list(latest.values())


def _add(bucket: dict, inst: str, result: str, ticks: float | None) -> None:
    if result == "OPEN":
        bucket["open"] += 1
        return
    if result not in ("WIN", "LOSS") or ticks is None:
        bucket["no_fill"] += 1
        return
    bucket["closed"] += 1
    bucket["wins" if result == "WIN" else "losses"] += 1
    tick_value = TICK_VALUE.get(inst)
    gross = float(ticks) * tick_value if tick_value is not None else 0.0
    bucket["gross_usd"] = round(bucket["gross_usd"] + gross, 2)
    net = net_dollars(inst, gross, tick_value)
    if net is not None:
        bucket["net_usd"] = round((bucket["net_usd"] or 0.0) + net, 2)


def _load_bars(log_dir: str | Path | None, inst: str, day_iso: str, cache: dict) -> list[dict]:
    key = (inst, day_iso)
    if key not in cache:
        bars: list[dict] = []
        path = Path(log_dir) / f"bars_{inst}_{day_iso}.jsonl" if log_dir is not None else None
        if path is not None and path.exists():
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        bars.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        cache[key] = bars
    return cache[key]


def _fill_exit_times(row: dict, log_dir: str | Path | None, cache: dict) -> tuple[datetime, datetime | None, bool] | None:
    """(fill time, exit time or None while OPEN, approximate?) for a filled row.

    Rebuilds the resolver's forward window — the candidate day's stored bars after
    the candidate bar, up to the resolving bar, with a usable high/low — so
    bars_to_fill / bars_to_exit index the same bars. Falls back to 15-minute
    steps from the candidate bar when the bars can't reproduce the window.
    """
    outcome = row.get("shadow_outcome") or {}
    n_fill, n_exit = outcome.get("bars_to_fill"), outcome.get("bars_to_exit")
    cand_dt = _parse_dt(str(row.get("candidate_bar_ts") or ""))
    if cand_dt is None or not isinstance(n_fill, int) or n_fill < 1:
        return None
    end_dt = _parse_dt(str(row.get("resolved_at_bar_ts") or ""))
    forward: list[datetime] = []
    for bar in _load_bars(log_dir, str(row.get("instrument")), str(row.get("candidate_day")), cache):
        bar_dt = _parse_dt(str(bar.get("ts") or ""))
        if bar_dt is None or bar_dt <= cand_dt or (end_dt is not None and bar_dt > end_dt):
            continue
        try:
            float(bar["high"]), float(bar["low"])
        except (KeyError, TypeError, ValueError):
            continue
        forward.append(bar_dt)
    last = n_exit if isinstance(n_exit, int) else n_fill
    if len(forward) >= last:
        exit_dt = forward[n_exit - 1] if isinstance(n_exit, int) else None
        return forward[n_fill - 1], exit_dt, False
    step = timedelta(minutes=FALLBACK_BAR_MINUTES)
    exit_dt = cand_dt + n_exit * step if isinstance(n_exit, int) else None
    return cand_dt + n_fill * step, exit_dt, True


def mark_stacked(rows: list[dict], log_dir: str | Path | None = None) -> dict[int, dict]:
    """One-at-a-time view per market + strategy + direction.

    Walk filled trades in fill order; a trade that fills on or before the exit
    bar of the trade currently held for its key is "stacked" (an uncapped lane
    extra), otherwise it becomes the held trade. A same-bar fill/exit counts as
    stacked because OHLC can't order them. OPEN trades hold to the end of the
    day. Returns {id(row): {"stacked": bool, "approx": bool}} for filled rows.
    """
    cache: dict = {}
    filled = []
    for row in rows:
        result = str((row.get("shadow_outcome") or {}).get("result") or "")
        if result not in ("WIN", "LOSS", "OPEN"):
            continue
        times = _fill_exit_times(row, log_dir, cache)
        if times is None:
            continue
        filled.append((times[0], times[1], times[2], row))
    filled.sort(key=lambda t: (t[0], str(t[3].get("candidate_key"))))
    held_until: dict[tuple, datetime | None] = {}
    flags: dict[int, dict] = {}
    for fill_dt, exit_dt, approx, row in filled:
        key = (row.get("instrument"), row.get("strategy"), row.get("direction"))
        busy = key in held_until and (held_until[key] is None or fill_dt <= held_until[key])
        if not busy:
            held_until[key] = exit_dt
        flags[id(row)] = {"stacked": busy, "approx": approx}
    return flags


SESSION_CAP = 3
DAY_OPEN, DAY_CLOSE, NIGHT_OPEN = time(9, 30), time(17, 0), time(18, 0)
CAP_SCOPES = ("account", "per_market")


def trading_session(fill_dt: datetime) -> tuple[date, str]:
    """(CME trading date, 'night' | 'day' | 'halt') for a fill time; 18:00 ET starts the next date."""
    et = fill_dt.astimezone(ET)
    trading_date = (et + timedelta(hours=6)).date()
    t = et.time()
    if DAY_OPEN <= t < DAY_CLOSE:
        return trading_date, "day"
    if t >= NIGHT_OPEN or t < DAY_OPEN:
        return trading_date, "night"
    return trading_date, "halt"


def _settle_open(row: dict, trading_date: date, log_dir: str | Path | None, cache: dict) -> tuple[datetime, float, str] | None:
    """Play a resolver-OPEN trade forward on the stored bars after its last resolved bar:
    stop (checked first), then target, else the last close before 17:00 ET of its
    trading date. None when no stored bar covers it."""
    try:
        stop, target = float(row["stop"]), float(row["target"])
    except (KeyError, TypeError, ValueError):
        return None
    long_side = row.get("direction") == "LONG"
    close_dt = datetime.combine(trading_date, DAY_CLOSE, ET)
    seen = _parse_dt(str(row.get("resolved_at_bar_ts") or "")) or _parse_dt(str(row.get("candidate_bar_ts") or ""))
    start = date.fromisoformat(str(row.get("candidate_day")))
    last_close = None
    for offset in range((close_dt.astimezone(ZoneInfo("UTC")).date() - start).days + 1):
        day_iso = (start + timedelta(days=offset)).isoformat()
        for bar in sorted(_load_bars(log_dir, str(row.get("instrument")), day_iso, cache), key=lambda b: str(b.get("ts"))):
            bar_dt = _parse_dt(str(bar.get("ts") or ""))
            if bar_dt is None or bar_dt >= close_dt:
                continue
            try:
                high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
            except (KeyError, TypeError, ValueError):
                continue
            if seen is not None and bar_dt <= seen:
                last_close = close  # the resolver already saw no stop/target here
                continue
            if (low <= stop) if long_side else (high >= stop):
                return bar_dt, stop, "stop"
            if (high >= target) if long_side else (low <= target):
                return bar_dt, target, "target"
            last_close = close
    if last_close is None:
        return None
    return close_dt, last_close, "close"


def capped_trades(rows: list[dict], log_dir: str | Path | None = None) -> list[dict]:
    """Filled shadow trades with fill/exit time, CME session and dollars after costs."""
    cache: dict = {}
    trades = []
    for row in rows:
        outcome = row.get("shadow_outcome") or {}
        result = str(outcome.get("result") or "")
        if result not in ("WIN", "LOSS", "OPEN"):
            continue
        times = _fill_exit_times(row, log_dir, cache)
        if times is None:
            continue
        inst = str(row.get("instrument") or "?")
        tick_value, tick_size = TICK_VALUE.get(inst), TICK_SIZE.get(inst)
        trading_date, session = trading_session(times[0])
        exit_dt, ticks, how = times[1], outcome.get("pnl_ticks"), result.lower()
        if result == "OPEN":
            settled = _settle_open(row, trading_date, log_dir, cache)
            ticks, how = None, "unpriced"
            exit_dt = datetime.combine(trading_date, DAY_CLOSE, ET)
            if settled is not None and tick_size and row.get("entry") is not None:
                exit_dt, price, how = settled
                sign = 1 if row.get("direction") == "LONG" else -1
                ticks = sign * (price - float(row["entry"])) / tick_size
        net = None
        if ticks is not None and tick_value is not None:
            net = net_dollars(inst, float(ticks) * tick_value, tick_value)
        trades.append({
            "key": str(row.get("candidate_key")), "instrument": inst, "fill": times[0],
            "exit": exit_dt or times[0], "trading_date": trading_date, "session": session,
            "how": how, "net_usd": net,
            "won": result == "WIN" or (result == "OPEN" and net is not None and net > 0),
        })
    trades.sort(key=lambda t: (t["fill"], t["key"]))
    return trades


def apply_session_cap(trades: list[dict], scope: str, cap: int = SESSION_CAP) -> list[dict]:
    """Trades kept under the cap, in fill order; scope 'account' or 'per_market'."""
    taken_per_session: dict[tuple, int] = defaultdict(int)
    busy_until: dict[str, datetime] = {}
    kept = []
    for t in trades:
        group = "account" if scope == "account" else t["instrument"]
        if t["session"] == "halt":
            continue
        slot = (group, t["trading_date"], t["session"])
        if taken_per_session[slot] >= cap:
            continue
        if group in busy_until and t["fill"] <= busy_until[group]:
            continue  # same bar as the previous exit counts as still in it (OHLC can't order them)
        taken_per_session[slot] += 1
        busy_until[group] = t["exit"]
        kept.append(t)
    return kept


def _cap_summary(trades: list[dict]) -> dict:
    priced = [t for t in trades if t["net_usd"] is not None]
    pnl = peak = drawdown = 0.0
    for t in sorted(priced, key=lambda t: (t["exit"], t["key"])):
        pnl += t["net_usd"]
        peak = max(peak, pnl)
        drawdown = max(drawdown, peak - pnl)
    return {
        "trades": len(trades),
        "wins": sum(t["won"] for t in priced),
        "losses": sum(not t["won"] for t in priced),
        "net_usd": round(pnl, 2) if priced else None,
        "max_drawdown_usd": round(drawdown, 2),
        "closed_at_5pm": sum(t["how"] in ("stop", "target", "close") for t in trades),
        "unpriced": len(trades) - len(priced),
        "day_session": sum(t["session"] == "day" for t in trades),
        "night_session": sum(t["session"] == "night" for t in trades),
    }


def capped_report(log_dir: str | Path, day: date, cap: int = SESSION_CAP) -> dict:
    """Your-limits view for trading date `day` and its week so far (Monday..day)."""
    monday = day - timedelta(days=day.weekday())
    # Sunday-evening candidates open Monday's trading date; Saturday covers any carry-in.
    trades = capped_trades(load_outcomes_range(log_dir, monday - timedelta(days=2), day), log_dir)
    views = {"every_signal": trades, **{s: apply_session_cap(trades, s, cap) for s in CAP_SCOPES}}
    out = {"rule": {"per_session": cap, "day": "09:30-17:00 ET", "night": "18:00-09:30 ET",
                    "reset": "18:00 ET", "one_position": True}, "trading_date": day.isoformat(),
           "week_start": monday.isoformat()}
    for label, lo in (("today", day), ("week_to_date", monday)):
        out[label] = {
            name: _cap_summary([t for t in kept if lo <= t["trading_date"] <= day])
            for name, kept in views.items()
        }
    return out


def build_report(rows: list[dict], day: date, *, log_dir: str | Path | None = None) -> dict:
    by_inst: dict[str, dict] = defaultdict(_empty)
    by_strat: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(_empty))
    total = _empty()
    first_by_inst: dict[str, dict] = defaultdict(_empty)
    first_total = _empty()
    # Overlapping trades left out, bucketed like the totals: "closed" is what the
    # one-at-a-time line leaves out, so kept + left out == the market's trades.
    stacked_by_inst: dict[str, dict] = defaultdict(_empty)
    flags = mark_stacked(rows, log_dir)
    approx = 0
    for row in rows:
        inst = str(row.get("instrument") or "?")
        outcome = row.get("shadow_outcome") or {}
        result = str(outcome.get("result") or "")
        ticks = outcome.get("pnl_ticks")
        strat = str(row.get("strategy") or "?")
        for bucket in (by_inst[inst], by_strat[inst][strat], total):
            _add(bucket, inst, result, ticks)
        flag = flags.get(id(row))
        approx += bool(flag and flag["approx"])
        if flag and flag["stacked"]:
            _add(stacked_by_inst[inst], inst, result, ticks)
            continue
        for bucket in (first_by_inst[inst], first_total):
            _add(bucket, inst, result, ticks)
    # Always surface the full six-market observation universe, including zero-evidence markets.
    for inst in REPORT_INSTRUMENTS:
        by_inst[inst]
        by_strat[inst]
    # Total net only counts costed markets; say so rather than mixing.
    return {
        "generated_at": datetime.now(ET).isoformat(),
        "day": day.isoformat(),
        "pass": "first",
        "candidates": len(rows),
        "total": total,
        "by_instrument": {k: by_inst[k] for k in sorted(by_inst)},
        "by_strategy": {k: {s: v[s] for s in sorted(v)} for k, v in sorted(by_strat.items())},
        # One-at-a-time view: stacked trades (filled while the same market +
        # strategy + direction was still in a trade) left out.
        # "stacked" counts closed trades only; overlapping trades still open
        # are "stacked_open" (they are already in the market's "still open").
        "first_signal": {
            "total": first_total,
            "stacked": sum(b["closed"] for b in stacked_by_inst.values()),
            "stacked_open": sum(b["open"] for b in stacked_by_inst.values()),
            "by_instrument": {
                k: {
                    **first_by_inst[k],
                    "stacked": stacked_by_inst[k]["closed"],
                    "stacked_open": stacked_by_inst[k]["open"],
                }
                for k in sorted(by_inst)
            },
            "timing_approx": approx,
        },
        "cost_model": {
            "instruments": list(COSTED_INSTRUMENTS),
            "commission_dollars": COMMISSION_DOLLARS,
            "slippage_ticks": SLIPPAGE_TICKS,
            "note": "total net_usd covers costed instruments only; others gross only",
        },
        "authority": "evidence_only",
        **({"capped": capped_report(log_dir, day)} if log_dir is not None else {}),
    }


def _dollars(b: dict) -> str:
    if b["net_usd"] is not None:
        return f"{pe.money(round(b['net_usd']))[:-3]} after costs"
    return f"{pe.money(round(b['gross_usd']))[:-3]} before costs"


def _line(b: dict) -> str:
    # Open / never-filled always shown: a zero is information (late rows land after the first pass).
    return (
        f"{b['closed']} trades, {b['wins']} won, {b['losses']} lost, {_dollars(b)}"
        f" ({b['open']} still open, {b['no_fill']} never filled)"
    )


def _first_signal_line(label: str, first: dict, stacked: int, stacked_open: int = 0) -> str:
    # The card keeps a "<label> one at a time:" line as its own field under its
    # market only while the key is five words or fewer; longer keys are pooled
    # into the card description, away from their market.
    still_open = f", {stacked_open} overlapping trades still open" if stacked_open else ""
    return (
        f"{label} one at a time: {first['closed']} trades, {first['wins']} won,"
        f" {first['losses']} lost, {_dollars(first)}"
        f" ({stacked} overlapping trades left out{still_open})"
    )


def compare_passes(first: dict, final: dict) -> dict:
    """What the final pass added to the all-markets total since the first pass."""
    a, b = first.get("total") or _empty(), final["total"]
    delta = {k: b[k] - a.get(k, 0) for k in ("closed", "wins", "losses", "open", "no_fill")}
    delta["candidates"] = final["candidates"] - int(first.get("candidates") or 0)
    delta["gross_usd"] = round(b["gross_usd"] - float(a.get("gross_usd") or 0.0), 2)
    delta["net_usd"] = round((b["net_usd"] or 0.0) - float(a.get("net_usd") or 0.0), 2)
    delta["first_generated_at"] = first.get("generated_at")
    delta["first"] = {k: a.get(k) for k in ("closed", "open", "no_fill")}
    return delta


def _change_line(change: dict | None) -> str:
    if change is None:
        return "Final pass: no first-pass report on file to compare."
    if not any(change[k] for k in ("candidates", "closed", "open", "no_fill")):
        return "Final pass: no change since the first report."
    first = change["first"]
    money = pe.money(round(change["net_usd"]))[:-3]
    return (
        f"Final pass: {change['closed']:+d} trades, {money} after costs since the first report;"
        f" still open {first['open']} → {first['open'] + change['open']},"
        f" never filled {first['no_fill']} → {first['no_fill'] + change['no_fill']}"
    )


def _strategy_value(bucket: dict) -> float:
    value = bucket["net_usd"] if bucket["net_usd"] is not None else bucket["gross_usd"]
    return float(value or 0.0)


def _strategy_extremes(report: dict, inst: str) -> tuple[str | None, str | None]:
    ranked = [
        (_strategy_value(bucket), strategy)
        for strategy, bucket in (report.get("by_strategy", {}).get(inst) or {}).items()
        if bucket["closed"]
    ]
    if not ranked:
        return None, None
    ranked.sort()
    worst_value, worst_strategy = ranked[0]
    best_value, best_strategy = ranked[-1]
    best = f"{best_strategy} {pe.money(round(best_value))[:-3]}" if best_value > 0 else None
    worst = f"{worst_strategy} {pe.money(round(worst_value))[:-3]}" if worst_value < 0 else None
    return best, worst


def format_digest(report: dict, *, top: int = 3) -> str:
    final = report.get("pass") == "final"
    title = f"🧮 **Shadow P&L · all 6 futures · {pe.et_date(report['day'])}{' · final' if final else ''}**"
    lines = [title]
    if final:
        lines.append(_change_line(report.get("change_since_first_pass")))
    first_signal = report.get("first_signal") or {}
    if not report["candidates"]:
        lines.append("No shadow outcomes recorded for this day; all six markets are shown below.")
    else:
        lines.append(f"All markets: {_line(report['total'])}")
        # Only when closed trades were left out; otherwise the view equals the total.
        if first_signal.get("stacked"):
            lines.append(_first_signal_line(
                "Total", first_signal["total"], first_signal.get("stacked", 0),
                first_signal.get("stacked_open", 0),
            ))
    for inst in REPORT_INSTRUMENTS:
        bucket = report["by_instrument"].get(inst) or _empty()
        if not (bucket["closed"] or bucket["open"] or bucket["no_fill"]):
            lines.append(f"**{pe.market(inst)}** — no shadow outcomes recorded")
            continue
        lines.append(f"**{pe.market(inst)}** — {_line(bucket)}")
        inst_first = (first_signal.get("by_instrument") or {}).get(inst) or {}
        if inst_first.get("stacked"):
            lines.append(_first_signal_line(
                inst, inst_first, inst_first.get("stacked", 0), inst_first.get("stacked_open", 0),
            ))
        best, worst = _strategy_extremes(report, inst)
        if best or worst:
            lines.append(
                f"Best: {best or 'none positive'} · Worst: {worst or 'none negative'}"
            )
    if first_signal.get("timing_approx"):
        lines.append(
            f"(overlap timing estimated for {first_signal['timing_approx']} trades — bar files missing)"
        )
    lines.extend(_capped_lines(report.get("capped")))
    lines.append("[shadow only · 1 contract each · no orders · no rule change]")
    return "\n".join(lines)


_CAP_LABELS = (("account", "whole account"), ("per_market", "each market separately"),
               ("every_signal", "every signal, no limits"))


def _cap_line(label: str, s: dict) -> str:
    if not s["trades"]:
        return f"{label}: no trades"
    text = f"{label}: {s['trades']} trades, {s['wins']} won, {s['losses']} lost"
    if s["net_usd"] is not None:
        text += f", {pe.money(round(s['net_usd']))[:-3]} after costs"
        text += f", deepest drop {pe.money(round(s['max_drawdown_usd']), signed=False)[:-3]}"
    if s["unpriced"]:
        text += f" ({s['unpriced']} could not be priced)"
    return text


def _capped_lines(capped: dict | None) -> list[str]:
    if not capped:
        return []
    lines = [f"**Your limits** — 3 trades in the day session + 3 in the night session,"
             f" one position at a time, reset 6:00 PM ET · {pe.et_date(capped['trading_date'])}"]
    for name, label in _CAP_LABELS:
        lines.append(_cap_line(f"Today, {label}", capped["today"][name]))
    week = capped["week_to_date"]
    lines.append(f"Week so far (since {pe.et_date(capped['week_start'])}):")
    for name, label in _CAP_LABELS:
        lines.append(_cap_line(f"Week, {label}", week[name]))
    marked = week["account"]["closed_at_5pm"] + week["per_market"]["closed_at_5pm"]
    if marked:
        lines.append("(trades still open at the end of tracking were played out on stored prices,"
                     " closing at 5:00 PM ET at the latest)")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"))
    parser.add_argument("--day", default=None, help="trading day YYYY-MM-DD (default: today in New York; yesterday with --final)")
    parser.add_argument(
        "--final",
        action="store_true",
        help="re-run a PAST day after late rows land; writes shadow_daily_pnl_<day>_final.json and compares to the first pass",
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of the digest")
    parser.add_argument("--discord", action="store_true", help="post the digest to DISCORD_ROUTE_DAILY_REPORT / DISCORD_WEBHOOK_URL")
    args = parser.parse_args(argv)

    today = datetime.now(ET).date()
    day = date.fromisoformat(args.day) if args.day else today - timedelta(days=1 if args.final else 0)
    if args.final and day >= today:
        parser.error("--final is for a past day (late rows land after the day ends)")
    report = build_report(load_outcomes(args.log_dir, day), day, log_dir=args.log_dir)
    out_dir = Path(args.log_dir)
    first_path = out_dir / f"shadow_daily_pnl_{day.isoformat()}.json"
    if args.final:
        report["pass"] = "final"
        try:
            first = json.loads(first_path.read_text())
        except (OSError, json.JSONDecodeError):
            first = None
        report["change_since_first_pass"] = compare_passes(first, report) if first else None
    try:
        text = json.dumps(report, indent=2)
        if args.final:
            (out_dir / f"shadow_daily_pnl_{day.isoformat()}_final.json").write_text(text)
        else:
            first_path.write_text(text)
            (out_dir / "shadow_daily_pnl_latest.json").write_text(text)
    except OSError:
        pass
    digest = format_digest(report)
    print(json.dumps(report, indent=2) if args.json else digest)
    if args.discord:
        url = os.getenv("DISCORD_ROUTE_DAILY_REPORT") or os.getenv("DISCORD_WEBHOOK_URL")
        if url:
            print("discord:", "ok" if _post_discord(url, digest) else "FAILED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
