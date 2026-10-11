#!/usr/bin/env python3
"""Underlying-price screen for three options research families.

Frozen before outcomes on 2026-10-11. Research output only.
Does not write journals, place orders, or change collectors.

Universe: the 66-name default watchlist on
cursor/options-scanner-universe-expand-5735 (CORE 23 + EXPANDED 42 +
CONDITIONAL KWEB). KWEB remains subject to the existing option liquidity gate
if a later options trial is run.

Window: NYSE sessions 2026-04-01 through 2026-09-08. This starts at the
existing ong-v0.1 study start and stops before the already published
2026-09-09..2026-09-15 strat scoring week.

Bars: Alpaca SIP 5Min, raw, regular session only.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from alert_ranker.bar_provider import AlpacaBarProvider, BarProviderError  # noqa: E402
from alert_ranker.causal_bars import MINUTE_5, Bar  # noqa: E402
from alert_ranker.config import resolve_alpaca_credentials  # noqa: E402
from alert_ranker.session_calendar import nyse_session_for  # noqa: E402

OUT = Path(__file__).resolve().parent / "results.json"
START = date(2026, 4, 1)
END = date(2026, 9, 8)
FETCH_START = datetime(2026, 3, 20, tzinfo=timezone.utc)
FETCH_END = datetime(2026, 9, 9, tzinfo=timezone.utc)
SLIP = 0.01
TRIGGER_BARS = 6
DEFINITION = "underlying-edge-v1-2026-10-11"

CORE = (
    "SPY", "QQQ", "IWM", "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "GOOGL",
    "AMD", "AVGO", "PLTR", "NFLX", "JPM", "BAC", "COIN", "HOOD", "TLT", "GLD",
    "SLV", "IBIT", "SMH",
)
EXPANDED = (
    "ORCL", "CRM", "MU", "QCOM", "ARM", "TSM", "SMCI", "INTC", "SOFI", "RIVN",
    "UBER", "ABNB", "DIS", "WMT", "COST", "HD", "NKE", "BA", "CAT", "GE",
    "F", "GM", "XOM", "CVX", "OXY", "COP", "C", "WFC", "GS", "MS",
    "SCHW", "LLY", "UNH", "PFE", "XLF", "XLK", "XLE", "XBI", "HYG", "EEM",
    "FXI", "DIA",
)
CONDITIONAL = ("KWEB",)
SYMBOLS = CORE + EXPANDED + CONDITIONAL

# Newly specified where the repository has no equity registration under the
# requested name. Levels for the failed-break family reuse ns-v0.1 PDH/PDL/ORB.
# Slippage, stop-first, and next-open entry reuse ong-v0.1.
RULES = {
    "version": DEFINITION,
    "newly_specified": [
        "htf pullback length 2..6 closes against the prior-session direction",
        "trigger must occur within 6 completed bars or the setup expires",
        "failed-break failure window is 6 completed bars after the break close",
        "compression is one strictly narrower inside bar; direction is the mother bar",
        "maximum hold is the same regular session; no overnight",
        "gap through the frozen stop, or an open already through +1R, is a missed entry",
    ],
    "reused": [
        "66-symbol CORE+EXPANDED universe",
        "ns-v0.1 PDH, PDL, and 30-minute opening-range levels",
        "ong-v0.1 next-bar-open entry, $0.01 adverse entry and stop slippage, stop-first",
        "nyse_session_for regular-session calendar",
    ],
    "baseline": "matched no-trigger: next open after the setup reference, setup direction, same frozen stop price",
    "promising_rule_frozen_before_outcomes": (
        "minimum 30 eligible episodes, 10 sessions, 5 symbols, valid causal test, "
        "and conditional 1R-before-stop above the matched baseline with stop-first under 50%"
    ),
}


def sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def rth_bars(rows: list[Bar], day: date) -> list[Bar] | None:
    session = nyse_session_for(day)
    if session is None:
        return None
    kept = [
        bar for bar in rows
        if session.open <= bar.start_utc < session.close
        and bar.high >= bar.low and bar.open > 0 and bar.close > 0
    ]
    kept.sort(key=lambda bar: bar.start_utc)
    expected = int((session.close - session.open).total_seconds() // 300)
    if len(kept) < expected - 2:
        return None
    return kept


def sessions_for(rows: list[Bar]) -> tuple[list[tuple[date, list[Bar]]], int]:
    grouped: dict[date, list[Bar]] = defaultdict(list)
    zone = __import__("zoneinfo").ZoneInfo("America/New_York")
    for bar in rows:
        grouped[bar.start.astimezone(zone).date()].append(bar)
    out = []
    incomplete = 0
    for day in sorted(grouped):
        bars = rth_bars(grouped[day], day)
        if bars:
            out.append((day, bars))
        elif nyse_session_for(day) is not None:
            incomplete += 1
    return out, incomplete


def walk(
    bars: list[Bar],
    entry_i: int,
    direction: int,
    structural_stop: float,
    decision_close: float,
    *,
    baseline: bool,
) -> dict:
    """Score one entry.

    Strategy risk is the distance from the actual fill to the frozen stop
    price. Baseline risk is that same pre-entry distance, measured from the
    baseline fill. A gap through the structural stop, or an open already
    through the pre-entry +1R level, is a miss rather than a fill.
    """
    if entry_i >= len(bars):
        return {"class": "MISSED", "reason": "NO_NEXT_BAR"}
    raw = bars[entry_i].open
    planned = (decision_close - structural_stop) * direction
    if planned <= 0:
        return {"class": "MISSED", "reason": "INVALID_RISK"}
    beyond = decision_close + direction * planned
    if (raw - structural_stop) * direction <= 0:
        return {"class": "MISSED", "reason": "GAP_THROUGH_STOP"}
    if (raw - beyond) * direction >= 0:
        return {"class": "MISSED", "reason": "MOVED_BEYOND_ENTRY"}
    entry = raw + SLIP * direction
    if baseline:
        risk = planned
        stop = entry - direction * planned
    else:
        stop = structural_stop
        risk = (entry - structural_stop) * direction
        if risk <= 0:
            return {"class": "MISSED", "reason": "GAP_THROUGH_STOP"}
    target1 = entry + direction * risk
    target2 = entry + direction * 2 * risk
    mae = 0.0
    mfe = 0.0
    for offset, probe in enumerate(bars[entry_i:], start=1):
        if direction > 0:
            mae = max(mae, (entry - probe.low) / risk)
            mfe = max(mfe, (probe.high - entry) / risk)
            stop_hit = probe.low <= stop
            hit1 = probe.high >= target1
            hit2 = probe.high >= target2
        else:
            mae = max(mae, (probe.high - entry) / risk)
            mfe = max(mfe, (entry - probe.low) / risk)
            stop_hit = probe.high >= stop
            hit1 = probe.low <= target1
            hit2 = probe.low <= target2
        outcome = None
        if stop_hit:
            outcome = "STOP"
        elif hit2:
            outcome = "R2"
        elif hit1:
            outcome = "R1"
        if outcome is not None:
            return {"class": "EXECUTED", "result": outcome, "bars": offset, "mae_r": mae, "mfe_r": mfe, "risk_price": risk}
    last = bars[-1].close - SLIP * direction
    return {
        "class": "EXECUTED",
        "result": "EOD",
        "bars": len(bars) - entry_i,
        "mae_r": mae,
        "mfe_r": max(mfe, (last - entry) * direction / risk),
        "risk_price": risk,
    }


def first_pullback(bars: list[Bar], direction: int) -> tuple[int, int] | None:
    for end in range(7, len(bars)):
        for length in range(2, 7):
            start = end - length + 1
            if start < 6:
                continue
            ok = True
            for idx in range(start, end + 1):
                against = bars[idx].close < bars[idx - 1].close if direction > 0 else bars[idx].close > bars[idx - 1].close
                if not against:
                    ok = False
                    break
            if ok:
                return start, end
    return None


def trigger_after(bars: list[Bar], start: int, end: int, direction: int, level: float, stop: float) -> tuple[str, int | None]:
    last = min(len(bars) - 1, end + TRIGGER_BARS)
    for idx in range(end + 1, last + 1):
        bar = bars[idx]
        if direction > 0:
            stopped = bar.low <= stop
            triggered = bar.close > level
        else:
            stopped = bar.high >= stop
            triggered = bar.close < level
        if stopped and triggered:
            return "INVALIDATED", None
        if stopped:
            return "INVALIDATED", None
        if triggered:
            return "TRIGGER", idx
    return "NO_TRIGGER", None


def htf_pullback(symbol: str, days: list[tuple[date, list[Bar]]]) -> tuple[list[dict], Counter]:
    episodes = []
    excluded = Counter()
    for pos in range(1, len(days)):
        day, bars = days[pos]
        if day < START or day > END:
            continue
        _prev_day, prev = days[pos - 1]
        if prev[-1].close == prev[0].open:
            excluded["flat_prior_session"] += 1
            continue
        direction = 1 if prev[-1].close > prev[0].open else -1
        found = first_pullback(bars, direction)
        if found is None:
            continue
        start, end = found
        chunk = bars[start:end + 1]
        if direction > 0:
            level = max(bar.high for bar in chunk)
            stop = min(bar.low for bar in chunk) - SLIP
        else:
            level = min(bar.low for bar in chunk)
            stop = max(bar.high for bar in chunk) + SLIP
        if (bars[end].close - stop) * direction <= 0:
            excluded["invalid_stop"] += 1
            continue
        state, trigger_i = trigger_after(bars, start, end, direction, level, stop)
        regime = "prior_up" if direction > 0 else "prior_down"
        episodes.append(_episode(
            symbol, day, "htf_pullback", direction, regime, bars, end, state, trigger_i, stop, direction,
        ))
    return episodes, excluded


def failed_break(symbol: str, days: list[tuple[date, list[Bar]]]) -> tuple[list[dict], Counter]:
    episodes = []
    excluded = Counter()
    for pos in range(1, len(days)):
        day, bars = days[pos]
        if day < START or day > END or len(bars) < 8:
            continue
        _prev_day, prev = days[pos - 1]
        pdh = max(bar.high for bar in prev)
        pdl = min(bar.low for bar in prev)
        orb_high = max(bar.high for bar in bars[:6])
        orb_low = min(bar.low for bar in bars[:6])
        levels = (("PDH", pdh, 1, 1), ("PDL", pdl, -1, 1), ("ORB_HIGH", orb_high, 1, 6), ("ORB_LOW", orb_low, -1, 6))
        regime = "prior_up" if prev[-1].close > prev[0].open else "prior_down" if prev[-1].close < prev[0].open else "prior_flat"
        for name, level, break_dir, first_idx in levels:
            break_i = None
            for idx in range(first_idx, len(bars)):
                prev_close = bars[idx - 1].close
                close = bars[idx].close
                broke = close > level and prev_close <= level if break_dir > 0 else close < level and prev_close >= level
                if broke:
                    break_i = idx
                    break
            if break_i is None:
                continue
            # Invalidation of the break is a close back through the level.
            # That price is known when the break bar completes. The break bar's
            # own extreme is on the favorable side and cannot be the stop.
            base_stop = level - SLIP if break_dir > 0 else level + SLIP
            if (bars[break_i].close - base_stop) * break_dir <= 0:
                excluded[f"invalid_stop_{name}"] += 1
                continue
            state = "NO_TRIGGER"
            trigger_i = None
            last = min(len(bars) - 1, break_i + TRIGGER_BARS)
            extreme = bars[break_i].high if break_dir > 0 else bars[break_i].low
            for idx in range(break_i + 1, last + 1):
                extreme = max(extreme, bars[idx].high) if break_dir > 0 else min(extreme, bars[idx].low)
                failed = bars[idx].close < level if break_dir > 0 else bars[idx].close > level
                if failed:
                    state = "TRIGGER"
                    trigger_i = idx
                    break
            trade_dir = -break_dir
            stop = (extreme + SLIP) if break_dir > 0 else (extreme - SLIP)
            # Baseline takes the break. Strategy takes the failure in the opposite direction.
            episode = _episode(
                symbol, day, "failed_break", trade_dir, regime, bars, break_i, state, trigger_i, stop, trade_dir,
            )
            episode["level"] = name
            episode["baseline"] = walk(
                bars, break_i + 1, break_dir, base_stop, bars[break_i].close, baseline=True,
            )
            episode["baseline_direction"] = "LONG" if break_dir > 0 else "SHORT"
            episodes.append(episode)
    return episodes, excluded


def compression(symbol: str, days: list[tuple[date, list[Bar]]]) -> tuple[list[dict], Counter]:
    episodes = []
    excluded = Counter()
    for day, bars in days:
        if day < START or day > END:
            continue
        chosen = None
        for idx in range(1, len(bars) - 1):
            mother, inside = bars[idx - 1], bars[idx]
            narrower = inside.high <= mother.high and inside.low >= mother.low and (
                inside.high < mother.high or inside.low > mother.low
            )
            if not narrower or mother.close == mother.open:
                continue
            direction = 1 if mother.close > mother.open else -1
            stop = mother.low - SLIP if direction > 0 else mother.high + SLIP
            level = mother.high if direction > 0 else mother.low
            if (inside.close - stop) * direction <= 0:
                excluded["invalid_stop"] += 1
                continue
            chosen = (idx, direction, stop, level)
            break
        if chosen is None:
            continue
        idx, direction, stop, level = chosen
        state, trigger_i = trigger_after(bars, idx, idx, direction, level, stop)
        regime = "mother_up" if direction > 0 else "mother_down"
        episodes.append(_episode(symbol, day, "compression_expansion", direction, regime, bars, idx, state, trigger_i, stop, direction))
    return episodes, excluded


def _episode(symbol, day, family, direction, regime, bars, ref_i, state, trigger_i, stop, trade_dir) -> dict:
    baseline = walk(bars, ref_i + 1, direction, stop, bars[ref_i].close, baseline=True)
    if state == "TRIGGER" and trigger_i is not None:
        strategy = walk(bars, trigger_i + 1, trade_dir, stop, bars[trigger_i].close, baseline=False)
        observed = True
    elif state == "INVALIDATED":
        strategy = {"class": "NO_TRIGGER", "reason": "INVALIDATED"}
        observed = False
    else:
        strategy = {"class": "NO_TRIGGER", "reason": "EXPIRED"}
        observed = False
    return {
        "symbol": symbol,
        "session": day.isoformat(),
        "family": family,
        "regime": regime,
        "direction": "LONG" if trade_dir > 0 else "SHORT",
        "observed_trigger": observed,
        "strategy": strategy,
        "baseline": baseline,
    }


def rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(100.0 * num / den, 1)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.median(values), 3)


def _risk_comparison(rows: list[dict]) -> dict:
    """Dollar risk is not shared by the two entries.

    Strategy R is the fill-to-stop distance. Baseline R is the pre-entry
    distance known at the setup close. A higher conditional 1R rate therefore
    does not by itself show better risk-adjusted expectancy.
    """
    strategy_risk = [row["strategy"]["risk_price"] for row in rows if row["strategy"].get("risk_price")]
    baseline_risk = [row["baseline"]["risk_price"] for row in rows if row["baseline"].get("risk_price")]
    paired = [
        row["strategy"]["risk_price"] / row["baseline"]["risk_price"]
        for row in rows
        if row["strategy"].get("risk_price") and row["baseline"].get("risk_price")
    ]
    return {
        "strategy_executed_with_risk": len(strategy_risk),
        "baseline_executed_with_risk": len(baseline_risk),
        "paired_executed": len(paired),
        "median_strategy_risk_price": median(strategy_risk),
        "median_baseline_risk_price": median(baseline_risk),
        "median_paired_strategy_over_baseline_risk": median(paired),
        "same_absolute_stop_distance": False,
    }


def summarize(name: str, rows: list[dict], excluded: Counter) -> dict:
    eligible = len(rows)
    observed = sum(1 for row in rows if row["observed_trigger"])
    executed = [row for row in rows if row["strategy"]["class"] == "EXECUTED"]
    missed = [row for row in rows if row["strategy"]["class"] == "MISSED"]
    no_trigger = [row for row in rows if row["strategy"]["class"] == "NO_TRIGGER"]
    missed_reasons = Counter(row["strategy"]["reason"] for row in missed)
    no_trigger_reasons = Counter(row["strategy"]["reason"] for row in no_trigger)
    def result_count(items, result):
        return sum(1 for row in items if row["strategy"].get("result") == result)
    r1 = result_count(executed, "R1") + result_count(executed, "R2")
    r2 = result_count(executed, "R2")
    stops = result_count(executed, "STOP")
    eod = result_count(executed, "EOD")
    base_exec = [row for row in rows if row["baseline"]["class"] == "EXECUTED"]
    def base_count(result):
        return sum(1 for row in base_exec if row["baseline"].get("result") == result)
    b1 = base_count("R1") + base_count("R2")
    b2 = base_count("R2")
    bstop = base_count("STOP")
    symbols = sorted({row["symbol"] for row in rows})
    sessions = sorted({row["session"] for row in rows})
    def slice_summary(subset: list[dict]) -> dict:
        sub_exec = [row for row in subset if row["strategy"]["class"] == "EXECUTED"]
        wins = sum(1 for row in sub_exec if row["strategy"].get("result") in {"R1", "R2"})
        stopped = sum(1 for row in sub_exec if row["strategy"].get("result") == "STOP")
        return {
            "eligible": len(subset),
            "executed": len(sub_exec),
            "r1_before_stop_pct": rate(wins, len(sub_exec)),
            "stop_first_pct": rate(stopped, len(sub_exec)),
        }

    regimes = {regime: slice_summary([row for row in rows if row["regime"] == regime]) for regime in sorted({row["regime"] for row in rows})}
    levels = sorted({row.get("level") for row in rows if row.get("level")})
    by_level = {level: slice_summary([row for row in rows if row.get("level") == level]) for level in levels}
    by_symbol = {symbol: slice_summary([row for row in rows if row["symbol"] == symbol]) for symbol in symbols}
    by_session = {session: slice_summary([row for row in rows if row["session"] == session]) for session in sessions}
    s1 = rate(r1, len(executed))
    base_s1 = rate(b1, len(base_exec))
    stop_pct = rate(stops, len(executed))
    enough = eligible >= 30 and len(sessions) >= 10 and len(symbols) >= 5
    if not rows:
        verdict = "BROKEN" if excluded else "INSUFFICIENT DATA"
    elif enough and s1 is not None and base_s1 is not None and s1 > base_s1 and (stop_pct or 100) < 50:
        verdict = "PROMISING BUT UNPROVEN"
    elif enough:
        verdict = "REJECT"
    else:
        verdict = "INSUFFICIENT DATA"
    return {
        "family": name,
        "eligible_episodes": eligible,
        "symbols": len(symbols),
        "sessions": len(sessions),
        "observed_trigger_pct": rate(observed, eligible),
        "actionable_trigger_pct": rate(len(executed), eligible),
        "missed_entry_pct": rate(len(missed), eligible),
        "missed_reasons": dict(missed_reasons),
        "no_trigger": len(no_trigger),
        "no_trigger_reasons": dict(no_trigger_reasons),
        "executed": len(executed),
        "r1_before_stop_pct": s1,
        "r2_before_stop_pct": rate(r2, len(executed)),
        "stop_first_pct": stop_pct,
        "eod_pct": rate(eod, len(executed)),
        "median_mae_r": median([row["strategy"]["mae_r"] for row in executed]),
        "median_mfe_r": median([row["strategy"]["mfe_r"] for row in executed]),
        "median_hold_bars": median([float(row["strategy"]["bars"]) for row in executed]),
        "baseline_comparability": _risk_comparison(rows),
        "baseline_executed": len(base_exec),
        "baseline_r1_before_stop_pct": base_s1,
        "baseline_r2_before_stop_pct": rate(b2, len(base_exec)),
        "baseline_stop_first_pct": rate(bstop, len(base_exec)),
        "r1_minus_baseline_pp": None if s1 is None or base_s1 is None else round(s1 - base_s1, 1),
        "per_opportunity_strategy_r1_pct": rate(r1, eligible),
        "per_opportunity_baseline_r1_pct": rate(b1, eligible),
        "regimes": regimes,
        "by_level": by_level,
        "by_symbol": by_symbol,
        "by_session": by_session,
        "exclusions": dict(excluded),
        "verdict": verdict,
    }


async def fetch_all() -> tuple[dict[str, list[Bar]], dict[str, str]]:
    load_dotenv(ROOT / ".env")
    key, secret = resolve_alpaca_credentials()
    if not key or not secret:
        raise SystemExit("alpaca_credentials_absent")
    provider = AlpacaBarProvider(
        base_url="https://data.alpaca.markets",
        api_key=key,
        secret_key=secret,
        timeout=30.0,
    )
    found: dict[str, list[Bar]] = {}
    errors: dict[str, str] = {}
    for offset in range(0, len(SYMBOLS), 3):
        batch = list(SYMBOLS[offset:offset + 3])
        try:
            got = await provider.fetch_bars(batch, MINUTE_5, FETCH_START, FETCH_END)
        except BarProviderError as exc:
            detail = getattr(exc, "args", ("", ""))
            code = str(detail[0]) if detail else type(exc).__name__
            for symbol in batch:
                try:
                    one = await provider.fetch_bars([symbol], MINUTE_5, FETCH_START, FETCH_END)
                    found[symbol] = one[symbol]
                except BarProviderError as one_exc:
                    errors[symbol] = str(one_exc.args[0]) if one_exc.args else type(one_exc).__name__
            continue
        found.update(got)
        print(f"fetched {offset + len(batch)}/{len(SYMBOLS)}", flush=True)
    return found, errors


def main() -> None:
    if len(SYMBOLS) != 66 or len(set(SYMBOLS)) != 66:
        raise SystemExit(f"universe_count {len(SYMBOLS)}")
    bars, errors = asyncio.run(fetch_all())
    families = {
        "htf_pullback": htf_pullback,
        "failed_break": failed_break,
        "compression_expansion": compression,
    }
    summaries = {}
    episode_rows: list[dict] = []
    prepared: dict[str, list[tuple[date, list[Bar]]]] = {}
    incomplete_sessions = 0
    for symbol, symbol_bars in bars.items():
        symbol_days, incomplete = sessions_for(symbol_bars)
        prepared[symbol] = symbol_days
        incomplete_sessions += incomplete
    for name, fn in families.items():
        rows = []
        excluded = Counter()
        for symbol in SYMBOLS:
            if symbol not in prepared:
                continue
            symbol_rows, symbol_excluded = fn(symbol, prepared[symbol])
            rows.extend(symbol_rows)
            episode_rows.extend(symbol_rows)
            excluded.update(symbol_excluded)
        summaries[name] = summarize(name, rows, excluded)
        print(name, summaries[name]["eligible_episodes"], summaries[name]["verdict"], flush=True)
    payload = {
        "source_sha": sha(),
        "definition": RULES,
        "dataset": {
            "provider": "alpaca_sip_5min_raw",
            "window": [START.isoformat(), END.isoformat()],
            "symbols_requested": len(SYMBOLS),
            "symbols_with_bars": len(bars),
            "symbols_missing": errors,
            "incomplete_regular_sessions": incomplete_sessions,
            "bar_counts": {symbol: len(rows) for symbol, rows in sorted(bars.items())},
        },
        "summaries": summaries,
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    episodes_path = OUT.with_name("episodes.jsonl")
    with episodes_path.open("w") as handle:
        for row in episode_rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(OUT)
    print(episodes_path)


if __name__ == "__main__":
    main()
