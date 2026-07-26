"""Honest-fill replay for the 12HR Miyagi 1-3-1 reversal strategy.

Research-only: consumes already-detected signal dicts and cached 5-minute
bars; it does not call runtime strategy, broker, or configuration code, for
the same reason `research/replay_322_honest_fill.py` gives (its
`execution/day_only_exit.py` counterpart imports `execution.broker_interface`
/ `execution.paper_broker`, runtime code this research module must not
depend on) -- the day-only-exit vocabulary below (`DAY_ONLY_EXIT_REASON`,
`EOD_BAR_MISSING`) intentionally matches that shared module's constants so
the same vocabulary is used across replay and runtime, without a code import.

FILL MODEL -- deliberately simpler than the 3-2-2 precedent's IOC-with-limit
model. `docs/strategy-rules/12HR_Miyagi_Rules.md` section 12 ("Enter on the
hit -- no 50% breach rule applies to Miyagi") and its hard-rules section 15
("Do NOT apply the 50% breach rule to Miyagi") together with the task brief
that scoped this build both establish that Miyagi has no documented IOC
tolerance / limit-price cap. Per that brief's explicit fallback instruction
("use a plain 'fills at the trigger price when crossed, plus adverse
slippage' model ... don't add speculative limit/IOC machinery beyond what's
written"), entries here ALWAYS fill at the exact trigger price adjusted by
adverse slippage, whenever a 5-minute bar's high/low crosses the trigger --
never at that bar's open/close, and never cancelled for "gapping too far."
This is a deliberate simplification, not an oversight: at the moment the
detector confirms a setup (9:30 AM ET), price is already strictly beyond
Bar C's boundary on the far side of the trigger from the detector's own
Step 6 (`price_at_open > bar_c_high` for SHORT / `< bar_c_low` for LONG), so
a "gap-through-the-trigger-at-open" scenario -- the only case the 3-2-2
precedent's gap-open branch exists to handle -- is structurally impossible
for Miyagi's very first bar of the entry window; a later 5-minute bar
gapping past the trigger mid-day is treated the same as a bar merely
touching it, per the brief's plain-fill-model instruction.

DAY-ONLY EXIT CONTRACT -- reused faithfully from
`12HR_Miyagi_Rules.md` section 8 ("Common Day-Only Exit -- 4:00 PM ET"),
which states the identical contract `60M_322_FirstLive_Rules.md` and PR #318
/ main@14e2af2 established: resolve stop/T1 first on the 15:55-16:00 ET bar
if reached; otherwise flatten at that bar's close with reason
`DAY_ONLY_FLATTEN`; if that exact bar is missing, record `EOD_BAR_MISSING`
as unresolved evidence (no price estimate, no WIN/LOSS/BREAKEVEN counted).

SINGLE-CONTRACT / T1-ONLY SCOPE -- `12HR_Miyagi_Rules.md` section 8 and hard
rule #15 both pin this strategy's current (only validated) management mode to
"Testing phase: 1 contract only, 100% exit at T1" and explicitly forbid a
T2-only or T2-inclusive exit on a single contract. This replay engine
therefore resolves every trade against the FIXED stop and T1 ONLY; `target_2`
is carried through in the signal/row data for transparency but never used to
resolve an exit, matching the canonical single-contract rule exactly (not a
missing feature -- using T2 here would violate an explicit hard rule).

NO-SAME-BAR-RESOLVES-OWN-BRACKET -- mirroring the 3-2-2 precedent's stated
honest-fill principle, the bar whose trigger-touch fills the entry is
excluded from that trade's own stop/T1 resolution (its unresolved intrabar
path after the touch is unknowable from OHLC alone). The sole documented
exception is the day-only-exit contract's own carve-out for the exact
15:55-16:00 ET bar: if entry happens to fill on that specific bar, this
module resolves that trade's stop/T1 on that same bar per the contract's
express text ("Stop/target resolution has precedence over the day-only exit
on that bar") rather than raising -- a data-availability edge case, not a
new leniency, and event-count-quantified in the evidence report if it ever
fires.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
TICK_SIZE = 0.25
POINT_VALUE = {"MNQ": 2.0, "MES": 5.0}
ROUND_TRIP_COMMISSION = 1.24

DAY_ONLY_EXIT_REASON = "DAY_ONLY_FLATTEN"
EOD_BAR_MISSING = "EOD_BAR_MISSING"
TRIGGER_NOT_HIT = "TRIGGER_NOT_HIT"
_EOD_BAR_START = time(15, 55)
_ENTRY_WINDOW_OPEN = time(9, 30)
_DAY_CLOSE = time(16, 0)


class ReplayInputError(ValueError):
    """Raised when private replay inputs violate the resolved data contract."""


def recover_entry(signal: dict, day_bars: list) -> Optional[dict]:
    """Find the first 5-minute bar in [9:30, 16:00) ET whose range crosses the
    trigger. Returns None if the trigger is never reached (TRIGGER_NOT_HIT).

    `day_bars` timestamps must already be ET-tz-aware (as produced by
    `bars_12hr_miyagi_loader.load_5m_day`); boundaries are always
    constructed in ET explicitly here regardless of the bars' own tzinfo,
    so callers passing UTC-tz bars would silently misalign -- this is a
    documented input contract, not a defensive fallback.
    """
    day = signal["date"]
    start = datetime.combine(day, _ENTRY_WINDOW_OPEN, ET)
    end = datetime.combine(day, _DAY_CLOSE, ET)
    window = sorted(
        (bar for bar in day_bars if start <= bar["ts"] < end),
        key=lambda bar: bar["ts"],
    )
    direction = signal["direction"]
    trigger = signal["entry_trigger"]
    for bar in window:
        crossed = bar["high"] >= trigger if direction == "LONG" else bar["low"] <= trigger
        if crossed:
            return {"bar": bar}
    return None


def _resolve_exit(
    signal: dict,
    day_bars: list,
    entry_bar: dict,
) -> dict:
    close_time = datetime.combine(signal["date"], _DAY_CLOSE, ET)
    direction = signal["direction"]

    entry_is_eod_bar = entry_bar["ts"].timetz().replace(tzinfo=None) == _EOD_BAR_START
    if entry_is_eod_bar:
        # Documented day-only-exit-contract carve-out: entry and the mandatory
        # flatten bar coincide. Resolve stop/T1 on this same bar before
        # falling back to DAY_ONLY_FLATTEN at its close.
        eligible = [entry_bar]
    else:
        eligible = sorted(
            (bar for bar in day_bars if bar["ts"] > entry_bar["ts"] and bar["ts"] < close_time),
            key=lambda bar: bar["ts"],
        )

    for bar in eligible:
        stop_hit = (
            bar["low"] <= signal["stop"] if direction == "LONG" else bar["high"] >= signal["stop"]
        )
        target_hit = (
            bar["high"] >= signal["target"] if direction == "LONG" else bar["low"] <= signal["target"]
        )
        if stop_hit:
            # Same-bar stop+target ambiguity: stop wins.
            return {"reason": "STOP", "base_exit": signal["stop"], "bar": bar}
        if target_hit:
            return {"reason": "TARGET", "base_exit": signal["target"], "bar": bar}

    exact_bar = next(
        (bar for bar in day_bars if bar["ts"].timetz().replace(tzinfo=None) == _EOD_BAR_START),
        None,
    )
    if exact_bar is not None:
        return {
            "reason": DAY_ONLY_EXIT_REASON,
            "base_exit": exact_bar["close"],
            "bar": exact_bar,
        }
    return {
        "reason": EOD_BAR_MISSING,
        "base_exit": None,
        "bar": eligible[-1] if eligible else entry_bar,
    }


def replay_signal(signal: dict, day_bars: list, *, slippage_ticks: float = 2.0) -> dict:
    instrument = signal["instrument"]
    if instrument not in POINT_VALUE:
        raise ReplayInputError(f"unknown instrument for point value: {instrument!r}")
    point_value = POINT_VALUE[instrument]
    direction = signal["direction"]
    slip = slippage_ticks * TICK_SIZE

    entry = recover_entry(signal, day_bars)
    base = {
        "date": signal["date"].isoformat(),
        "instrument": instrument,
        "direction": direction,
        "trigger": signal["entry_trigger"],
        "stop": signal["stop"],
        "target": signal["target"],
        "target_2": signal.get("target_2"),
        "slippage_ticks": slippage_ticks,
    }
    if entry is None:
        return {
            **base,
            "filled": False,
            "result": "NO_FILL",
            "exit_reason": TRIGGER_NOT_HIT,
            "entry_bar_ts": None,
            "base_entry_price": None,
            "fill_entry_price": None,
            "base_exit_price": None,
            "fill_exit_price": None,
            "exit_bar_ts": None,
            "gross_pnl": 0.0,
            "slippage_cost": 0.0,
            "commission": 0.0,
            "total_costs": 0.0,
            "net_pnl": 0.0,
        }

    entry_bar = entry["bar"]
    trigger = signal["entry_trigger"]
    base_fill = trigger
    fill_price = trigger + slip if direction == "LONG" else trigger - slip

    stop_wrong_side = (
        signal["stop"] >= fill_price if direction == "LONG" else signal["stop"] <= fill_price
    )
    if stop_wrong_side:
        return {
            **base,
            "filled": False,
            "entry_bar_ts": entry_bar["ts"].isoformat(),
            "result": "CANCELLED",
            "exit_reason": "POST_FILL_INVALID_STOP",
            "base_entry_price": base_fill,
            "fill_entry_price": fill_price,
            "base_exit_price": None,
            "fill_exit_price": None,
            "exit_bar_ts": None,
            "gross_pnl": 0.0,
            "slippage_cost": 0.0,
            "commission": 0.0,
            "total_costs": 0.0,
            "net_pnl": 0.0,
        }

    exit_info = _resolve_exit(signal, day_bars, entry_bar)

    if exit_info["reason"] == EOD_BAR_MISSING:
        return {
            **base,
            "filled": True,
            "entry_bar_ts": entry_bar["ts"].isoformat(),
            "result": "UNRESOLVED",
            "exit_reason": EOD_BAR_MISSING,
            "base_entry_price": base_fill,
            "fill_entry_price": fill_price,
            "base_exit_price": None,
            "fill_exit_price": None,
            "exit_bar_ts": exit_info["bar"]["ts"].isoformat() if exit_info["bar"] else None,
            "gross_pnl": None,
            "slippage_cost": None,
            "commission": None,
            "total_costs": None,
            "net_pnl": None,
        }

    base_exit = exit_info["base_exit"]
    signed = 1.0 if direction == "LONG" else -1.0
    actual_exit = base_exit - slip if direction == "LONG" else base_exit + slip

    gross_pnl = signed * (base_exit - base_fill) * point_value
    entry_slippage_cost = signed * (fill_price - base_fill) * point_value
    exit_slippage_cost = signed * (base_exit - actual_exit) * point_value
    slippage_cost = entry_slippage_cost + exit_slippage_cost
    total_costs = slippage_cost + ROUND_TRIP_COMMISSION
    net_pnl = gross_pnl - total_costs

    return {
        **base,
        "filled": True,
        "entry_bar_ts": entry_bar["ts"].isoformat(),
        "result": "WIN" if net_pnl > 0 else "LOSS" if net_pnl < 0 else "BREAKEVEN",
        "exit_reason": exit_info["reason"],
        "base_entry_price": base_fill,
        "fill_entry_price": fill_price,
        "base_exit_price": base_exit,
        "fill_exit_price": actual_exit,
        "exit_bar_ts": exit_info["bar"]["ts"].isoformat(),
        "gross_pnl": gross_pnl,
        "slippage_cost": slippage_cost,
        "commission": ROUND_TRIP_COMMISSION,
        "total_costs": total_costs,
        "net_pnl": net_pnl,
    }


def _sum_pnl(rows: list) -> float:
    return sum(row["net_pnl"] for row in rows if row["net_pnl"] is not None)


def _streaks(rows_in_order: list) -> tuple:
    max_wins = cur_wins = 0
    max_losses = cur_losses = 0
    for row in rows_in_order:
        if row["net_pnl"] is None:
            continue
        if row["net_pnl"] > 0:
            cur_wins += 1
            cur_losses = 0
        elif row["net_pnl"] < 0:
            cur_losses += 1
            cur_wins = 0
        else:
            cur_wins = 0
            cur_losses = 0
        max_wins = max(max_wins, cur_wins)
        max_losses = max(max_losses, cur_losses)
    return max_wins, max_losses


def _metrics(rows: list) -> dict:
    signals = len(rows)
    fills = [row for row in rows if row["filled"]]
    eod_bar_missing_rows = [row for row in fills if row["exit_reason"] == EOD_BAR_MISSING]
    resolved_fills = [row for row in fills if row["exit_reason"] != EOD_BAR_MISSING]
    wins = [row for row in resolved_fills if row["net_pnl"] > 0]
    losses = [row for row in resolved_fills if row["net_pnl"] < 0]
    gross_profit = sum(row["net_pnl"] for row in wins)
    gross_loss = -sum(row["net_pnl"] for row in losses)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for row in rows:
        equity += row["net_pnl"] if row["net_pnl"] is not None else 0.0
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    net = _sum_pnl(rows)
    max_consec_wins, max_consec_losses = _streaks(rows)
    sorted_wins = sorted(wins, key=lambda r: r["net_pnl"], reverse=True)
    sorted_losses = sorted(losses, key=lambda r: r["net_pnl"])
    return {
        "n": signals,
        "fills": len(fills),
        "resolved_fills": len(resolved_fills),
        "eod_bar_missing": len(eod_bar_missing_rows),
        "no_fill": sum(1 for row in rows if row["exit_reason"] == TRIGGER_NOT_HIT),
        "cancellations": signals - len(fills),
        "fill_rate": len(fills) / signals if signals else None,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(resolved_fills) if resolved_fills else None,
        "gross_pnl": sum(row["gross_pnl"] for row in rows if row["gross_pnl"] is not None),
        "slippage_cost": sum(row["slippage_cost"] for row in rows if row["slippage_cost"] is not None),
        "commission_cost": sum(row["commission"] for row in rows if row["commission"] is not None),
        "total_costs": sum(row["total_costs"] for row in rows if row["total_costs"] is not None),
        "net_pnl": net,
        "expectancy_per_signal": net / signals if signals else None,
        "expectancy_per_fill": _sum_pnl(resolved_fills) / len(resolved_fills) if resolved_fills else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "avg_win": sum(row["net_pnl"] for row in wins) / len(wins) if wins else None,
        "avg_loss": sum(row["net_pnl"] for row in losses) / len(losses) if losses else None,
        "largest_win": max((row["net_pnl"] for row in wins), default=None),
        "largest_loss": min((row["net_pnl"] for row in losses), default=None),
        "max_consecutive_wins": max_consec_wins,
        "max_consecutive_losses": max_consec_losses,
        "max_drawdown": max_dd,
        "top1_win_share_of_gross_profit": (
            sorted_wins[0]["net_pnl"] / gross_profit if sorted_wins and gross_profit else None
        ),
        "top3_win_share_of_gross_profit": (
            sum(r["net_pnl"] for r in sorted_wins[:3]) / gross_profit if sorted_wins and gross_profit else None
        ),
        "top5_win_share_of_gross_profit": (
            sum(r["net_pnl"] for r in sorted_wins[:5]) / gross_profit if sorted_wins and gross_profit else None
        ),
        "exit_reasons": {
            reason: sum(row.get("exit_reason") == reason for row in rows)
            for reason in (
                "TARGET",
                "STOP",
                DAY_ONLY_EXIT_REASON,
                EOD_BAR_MISSING,
                TRIGGER_NOT_HIT,
                "POST_FILL_INVALID_STOP",
            )
        },
    }


def run_replay(
    signals: list,
    bars_5m: list,
    *,
    study_start: date,
    study_end: date,
    slippage_ticks: float = 2.0,
) -> dict:
    by_date = defaultdict(list)
    for bar in bars_5m:
        by_date[bar["ts"].date()].append(bar)
    rows = [
        replay_signal(signal, by_date[signal["date"]], slippage_ticks=slippage_ticks)
        for signal in signals
    ]
    midpoint = study_start + (study_end - study_start) / 2
    h1 = [row for row in rows if date.fromisoformat(row["date"]) <= midpoint]
    h2 = [row for row in rows if date.fromisoformat(row["date"]) > midpoint]

    by_month: dict = defaultdict(list)
    by_year: dict = defaultdict(list)
    for row in rows:
        d = date.fromisoformat(row["date"])
        by_month[f"{d.year:04d}-{d.month:02d}"].append(row)
        by_year[str(d.year)].append(row)

    return {
        "schema_version": 1,
        "strategy": "12HR_MIYAGI",
        "model": {
            "tick_size": TICK_SIZE,
            "point_value": POINT_VALUE,
            "entry_fill_model": "TRIGGER_PRICE_PLUS_ADVERSE_SLIPPAGE_NO_IOC_NO_CAP",
            "entry_slippage_ticks": slippage_ticks,
            "exit_slippage_ticks": slippage_ticks,
            "round_trip_commission": ROUND_TRIP_COMMISSION,
            "same_bar_ambiguity": "STOP_FIRST",
            "entry_bar_exit_eligibility": "NEXT_5M_BAR_EXCEPT_EOD_BAR_CARVEOUT",
            "management_mode": "SINGLE_CONTRACT_T1_ONLY",
            "post_fill_wrong_side_stop": "FAIL_CLOSED",
            "unresolved_exit": "EXACT_15_55_ET_BAR_CLOSE_OR_EOD_BAR_MISSING",
        },
        "study_range": {
            "start": study_start.isoformat(),
            "end": study_end.isoformat(),
            "midpoint": midpoint.isoformat(),
            "h1": f"{study_start.isoformat()} through {midpoint.isoformat()}",
            "h2": f"after {midpoint.isoformat()} through {study_end.isoformat()}",
        },
        "overall": _metrics(rows),
        "halves": {"H1": _metrics(h1), "H2": _metrics(h2)},
        "directions": {
            direction: _metrics([row for row in rows if row["direction"] == direction])
            for direction in ("LONG", "SHORT")
        },
        "by_month": {month: _metrics(month_rows) for month, month_rows in sorted(by_month.items())},
        "by_year": {year: _metrics(year_rows) for year, year_rows in sorted(by_year.items())},
        "trades": rows,
    }


def build_sensitivity(
    signals: list,
    bars_5m: list,
    *,
    study_start: date,
    study_end: date,
) -> dict:
    return {
        str(ticks): run_replay(
            signals,
            bars_5m,
            study_start=study_start,
            study_end=study_end,
            slippage_ticks=float(ticks),
        )
        for ticks in (1, 2, 3, 4)
    }
