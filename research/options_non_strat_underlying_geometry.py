"""Options non-Strat underlying geometry study (ong-v0.1).

Research-only. Uses merged ns-v0.1 event definitions and historical SIP 5m
underlying bars. It does not select option contracts, write scanner state,
consume risk, alert, or call a broker.

Prereg:
docs/prereg-options-nonstrat-underlying-geometry-2026-09-21.md
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.bar_provider import AlpacaBarProvider, CONSOLIDATED_FEED  # noqa: E402
from alert_ranker.causal_bars import MINUTE_5, Bar, missing_bar_starts, session_bars  # noqa: E402
from alert_ranker.config import resolve_alpaca_credentials  # noqa: E402
from alert_ranker.non_strat_coverage import NonStratEvent, observe_session  # noqa: E402
from alert_ranker.session_calendar import Session, nyse_session_for  # noqa: E402
from scripts.options_coverage_observer import DEFAULT_UNIVERSE, fetch_all, load_universe  # noqa: E402

STUDY_ID = "OPTIONS_NON_STRAT_UNDERLYING_GEOMETRY"
STUDY_VERSION = "ong-v0.1"
START_DATE = date(2026, 4, 1)
END_DATE = date(2026, 9, 18)
HALF_SPLIT = date(2026, 7, 1)
LOOKBACK_CALENDAR_DAYS = 7
FETCH_WINDOW_DAYS = 14
INDEX_SYMBOLS = ("SPY", "QQQ")
GEOMETRIES = ("O1_EVENT_LEVEL", "O2_TRIGGER_BAR")
SLIPPAGE = {"base": 0.01, "stress": 0.03}
PRICE_TICK = 0.01
FROZEN_UNIVERSE_BLOB_SHA = "5a2eaf442c910012faddfc69bcd1c2a696f46d08"


@dataclass(frozen=True)
class Candidate:
    symbol: str
    session_date: str
    episode_id: str
    family: str
    direction: str
    level_name: str
    level_value: float
    trigger_bar_start: str
    trigger_idx: int
    trigger_open: float
    trigger_high: float
    trigger_low: float
    trigger_close: float
    market_aligned: bool | None


@dataclass(frozen=True)
class GeometryRow:
    symbol: str
    session_date: str
    half: str
    episode_id: str
    family: str
    direction: str
    geometry: str
    slippage_label: str
    slippage_dollars: float
    level_name: str
    level_value: float
    trigger_bar_start: str
    trigger_close: float
    decision_open: float | None
    fill_entry: float | None
    stop: float | None
    target: float | None
    planned_risk: float | None
    level_distance: float | None
    status: str
    result: str | None
    exit_reason: str | None
    exit_price: float | None
    realized_r: float | None
    bars_held: int | None
    market_aligned: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _git_blob_sha(path: Path) -> str:
    """Return the Git blob SHA-1 for the exact on-disk universe bytes."""
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def sessions_between(start: date, end: date) -> list[Session]:
    out: list[Session] = []
    cursor = start
    while cursor <= end:
        session = nyse_session_for(cursor)
        if session is not None:
            out.append(session)
        cursor += timedelta(days=1)
    return out


def _slice(bars: Sequence[Bar], session: Session) -> list[Bar]:
    return session_bars(list(bars), MINUTE_5, session.open, session.close)


def _complete(bars: Sequence[Bar], session: Session) -> bool:
    return not missing_bar_starts(
        list(bars),
        MINUTE_5,
        session.open,
        session.close,
        through=session.close,
    )


def _fetch_windows(start: date, end: date) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor <= end:
        last = min(end, cursor + timedelta(days=FETCH_WINDOW_DAYS - 1))
        start_dt = datetime.combine(cursor, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(last + timedelta(days=1), time.min, tzinfo=timezone.utc)
        windows.append((start_dt, end_dt))
        cursor = last + timedelta(days=1)
    return windows


async def fetch_history(
    provider: AlpacaBarProvider,
    symbols: Sequence[str],
    *,
    start: date,
    end: date,
) -> tuple[dict[str, list[Bar]], dict[str, list[str]]]:
    merged: dict[str, dict[datetime, Bar]] = {symbol: {} for symbol in symbols}
    errors: dict[str, list[str]] = defaultdict(list)

    for window_start, window_end in _fetch_windows(start, end):
        bars, window_errors = await fetch_all(
            provider,
            symbols,
            MINUTE_5,
            window_start,
            window_end,
        )
        for symbol, rows in bars.items():
            bucket = merged.setdefault(symbol, {})
            for bar in rows:
                bucket[bar.start_utc] = bar
        for symbol, reason in window_errors.items():
            errors[symbol].append(
                f"{window_start.date().isoformat()}:{reason}"
            )

    return (
        {
            symbol: sorted(rows.values(), key=lambda bar: bar.start_utc)
            for symbol, rows in merged.items()
            if rows
        },
        dict(errors),
    )


def collect_candidates(
    symbol: str,
    bars: Sequence[Bar],
    sessions: Sequence[Session],
    *,
    study_start: date,
    study_end: date,
    spy_bars: Sequence[Bar] | None,
    qqq_bars: Sequence[Bar] | None,
) -> tuple[list[Candidate], dict[str, int]]:
    complete_by_date: dict[date, list[Bar]] = {}
    skipped = {"missing": 0, "incomplete": 0, "no_prior": 0}

    for session in sessions:
        sliced = _slice(bars, session)
        if not sliced:
            skipped["missing"] += 1
            continue
        if not _complete(sliced, session):
            skipped["incomplete"] += 1
            continue
        complete_by_date[session.date] = sliced

    spy_by_date: dict[date, list[Bar]] = {}
    qqq_by_date: dict[date, list[Bar]] = {}
    if spy_bars is not None:
        for session in sessions:
            sliced = _slice(spy_bars, session)
            if sliced and _complete(sliced, session):
                spy_by_date[session.date] = sliced
    if qqq_bars is not None:
        for session in sessions:
            sliced = _slice(qqq_bars, session)
            if sliced and _complete(sliced, session):
                qqq_by_date[session.date] = sliced

    session_days = [session.date for session in sessions]
    candidates: list[Candidate] = []

    for idx, day in enumerate(session_days):
        if not (study_start <= day <= study_end):
            continue
        current = complete_by_date.get(day)
        if current is None:
            continue
        # Fail closed on the immediate prior market session. Never leap over an
        # incomplete/missing day and silently turn a stale high/low into PDH/PDL.
        if idx == 0:
            skipped["no_prior"] += 1
            continue
        prior_day = session_days[idx - 1]
        prior = complete_by_date.get(prior_day)
        if prior is None:
            skipped["no_prior"] += 1
            continue

        history_days = session_days[max(0, idx - 4):idx]
        history = [
            bar
            for hist_day in history_days
            for bar in complete_by_date.get(hist_day, ())
        ]

        spy_session = spy_by_date.get(day)
        qqq_session = qqq_by_date.get(day)
        spy_history = [
            bar
            for hist_day in history_days
            for bar in spy_by_date.get(hist_day, ())
        ] if spy_session is not None else None
        qqq_history = [
            bar
            for hist_day in history_days
            for bar in qqq_by_date.get(hist_day, ())
        ] if qqq_session is not None else None

        events = observe_session(
            symbol=symbol,
            session_date=day.isoformat(),
            prior_session_bars=prior,
            session_bars=current,
            history_bars=history,
            spy_history=spy_history,
            spy_session=spy_session,
            qqq_history=qqq_history,
            qqq_session=qqq_session,
        )

        first: dict[str, NonStratEvent] = {}
        for event in sorted(events, key=lambda item: item.bar_start):
            first.setdefault(event.episode_id, event)

        by_start = {bar.start_utc.isoformat(): i for i, bar in enumerate(current)}
        for episode_id, event in first.items():
            start = datetime.fromisoformat(
                event.bar_start.replace("Z", "+00:00")
            ).isoformat()
            trigger_idx = by_start.get(start)
            if trigger_idx is None:
                continue
            trigger = current[trigger_idx]
            candidates.append(
                Candidate(
                    symbol=symbol,
                    session_date=day.isoformat(),
                    episode_id=episode_id,
                    family=event.family,
                    direction=event.direction,
                    level_name=event.level_name,
                    level_value=float(event.level_value),
                    trigger_bar_start=start,
                    trigger_idx=trigger_idx,
                    trigger_open=float(trigger.open),
                    trigger_high=float(trigger.high),
                    trigger_low=float(trigger.low),
                    trigger_close=float(trigger.close),
                    market_aligned=event.market_aligned,
                )
            )

    return candidates, skipped


def geometry_prices(
    candidate: Candidate,
    decision_open: float,
    geometry: str,
) -> tuple[float, float, float]:
    sign = 1.0 if candidate.direction == "LONG" else -1.0
    if geometry == "O1_EVENT_LEVEL":
        stop = candidate.level_value - sign * PRICE_TICK
    elif geometry == "O2_TRIGGER_BAR":
        stop = (
            candidate.trigger_low - PRICE_TICK
            if sign > 0
            else candidate.trigger_high + PRICE_TICK
        )
    else:
        raise ValueError(f"unknown geometry {geometry!r}")

    risk = sign * (decision_open - stop)
    if risk <= 0:
        return stop, float("nan"), risk
    target = decision_open + sign * (2.0 * risk)
    return stop, target, risk


def simulate(
    candidate: Candidate,
    session_bars_: Sequence[Bar],
    *,
    geometry: str,
    slippage_label: str,
    slippage_dollars: float,
) -> GeometryRow:
    half = "H1" if date.fromisoformat(candidate.session_date) < HALF_SPLIT else "H2"
    sign = 1.0 if candidate.direction == "LONG" else -1.0
    next_idx = candidate.trigger_idx + 1
    if next_idx >= len(session_bars_):
        return GeometryRow(
            candidate.symbol, candidate.session_date, half, candidate.episode_id,
            candidate.family, candidate.direction, geometry, slippage_label,
            slippage_dollars, candidate.level_name, candidate.level_value,
            candidate.trigger_bar_start, candidate.trigger_close, None, None,
            None, None, None, None, "NO_NEXT_BAR", None, None, None, None, None,
            candidate.market_aligned,
        )

    decision_open = float(session_bars_[next_idx].open)
    stop, target, planned_risk = geometry_prices(candidate, decision_open, geometry)
    level_distance = abs(decision_open - candidate.level_value)
    if not math.isfinite(target) or planned_risk <= 0:
        return GeometryRow(
            candidate.symbol, candidate.session_date, half, candidate.episode_id,
            candidate.family, candidate.direction, geometry, slippage_label,
            slippage_dollars, candidate.level_name, candidate.level_value,
            candidate.trigger_bar_start, candidate.trigger_close, decision_open,
            None, stop, None, planned_risk, level_distance, "BRACKET_INVALID",
            None, None, None, None, None, candidate.market_aligned,
        )

    fill_entry = decision_open + sign * slippage_dollars
    bracket_valid = (
        stop < fill_entry < target
        if sign > 0
        else target < fill_entry < stop
    )
    if not bracket_valid:
        return GeometryRow(
            candidate.symbol, candidate.session_date, half, candidate.episode_id,
            candidate.family, candidate.direction, geometry, slippage_label,
            slippage_dollars, candidate.level_name, candidate.level_value,
            candidate.trigger_bar_start, candidate.trigger_close, decision_open,
            fill_entry, stop, target, planned_risk, level_distance,
            "BRACKET_INVALID_AT_FILL", None, None, None, None, None,
            candidate.market_aligned,
        )

    for j in range(next_idx, len(session_bars_)):
        bar = session_bars_[j]
        if sign > 0:
            stop_hit = float(bar.low) <= stop
            target_hit = float(bar.high) >= target
        else:
            stop_hit = float(bar.high) >= stop
            target_hit = float(bar.low) <= target

        # Unknown intrabar order -> stop first.
        if stop_hit:
            exit_price = stop - sign * slippage_dollars
            realized_r = sign * (exit_price - fill_entry) / planned_risk
            return GeometryRow(
                candidate.symbol, candidate.session_date, half, candidate.episode_id,
                candidate.family, candidate.direction, geometry, slippage_label,
                slippage_dollars, candidate.level_name, candidate.level_value,
                candidate.trigger_bar_start, candidate.trigger_close, decision_open,
                fill_entry, stop, target, planned_risk, level_distance, "RESOLVED",
                "LOSS", "STOP", exit_price, realized_r, j - next_idx,
                candidate.market_aligned,
            )
        if target_hit:
            exit_price = target
            realized_r = sign * (exit_price - fill_entry) / planned_risk
            return GeometryRow(
                candidate.symbol, candidate.session_date, half, candidate.episode_id,
                candidate.family, candidate.direction, geometry, slippage_label,
                slippage_dollars, candidate.level_name, candidate.level_value,
                candidate.trigger_bar_start, candidate.trigger_close, decision_open,
                fill_entry, stop, target, planned_risk, level_distance, "RESOLVED",
                "WIN", "TARGET", exit_price, realized_r, j - next_idx,
                candidate.market_aligned,
            )

    final_close = float(session_bars_[-1].close)
    exit_price = final_close - sign * slippage_dollars
    realized_r = sign * (exit_price - fill_entry) / planned_risk
    result = "WIN" if realized_r > 0 else "LOSS" if realized_r < 0 else "BREAKEVEN"
    return GeometryRow(
        candidate.symbol, candidate.session_date, half, candidate.episode_id,
        candidate.family, candidate.direction, geometry, slippage_label,
        slippage_dollars, candidate.level_name, candidate.level_value,
        candidate.trigger_bar_start, candidate.trigger_close, decision_open,
        fill_entry, stop, target, planned_risk, level_distance, "RESOLVED",
        result, "RTH_EOD", exit_price, realized_r,
        len(session_bars_) - 1 - next_idx, candidate.market_aligned,
    )


def _pf(values: Sequence[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses == 0:
        return None
    return wins / losses


def _max_drawdown(values: Sequence[float]) -> float:
    equity = peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _ticker_concentration(rows: Sequence[GeometryRow]) -> float | None:
    by_symbol: dict[str, float] = defaultdict(float)
    for row in rows:
        if row.realized_r is not None:
            by_symbol[row.symbol] += row.realized_r
    positives = [value for value in by_symbol.values() if value > 0]
    if not positives:
        return None
    return max(positives) / sum(positives)


def summarize_cell(rows: Sequence[GeometryRow]) -> dict[str, Any]:
    resolved = [r for r in rows if r.status == "RESOLVED" and r.realized_r is not None]
    values = [float(r.realized_r) for r in resolved]
    distances = [float(r.level_distance) for r in rows if r.level_distance is not None]

    halves: dict[str, Any] = {}
    for half in ("H1", "H2"):
        half_values = [
            float(r.realized_r)
            for r in resolved
            if r.half == half and r.realized_r is not None
        ]
        halves[half] = {
            "n": len(half_values),
            "mean_r": round(statistics.fmean(half_values), 5) if half_values else None,
            "median_r": round(statistics.median(half_values), 5) if half_values else None,
            "pf": round(_pf(half_values), 5) if _pf(half_values) is not None else None,
            "net_r": round(sum(half_values), 5),
        }

    aligned = {
        "aligned": sum(r.market_aligned is True for r in rows),
        "unaligned": sum(r.market_aligned is False for r in rows),
        "unknown": sum(r.market_aligned is None for r in rows),
    }
    return {
        "episodes": len(rows),
        "resolved": len(resolved),
        "symbols": len({r.symbol for r in rows}),
        "sessions": len({r.session_date for r in rows}),
        "no_next_bar": sum(r.status == "NO_NEXT_BAR" for r in rows),
        "bracket_invalid": sum(r.status.startswith("BRACKET_INVALID") for r in rows),
        "mean_r": round(statistics.fmean(values), 5) if values else None,
        "median_r": round(statistics.median(values), 5) if values else None,
        "pf": round(_pf(values), 5) if _pf(values) is not None else None,
        "net_r": round(sum(values), 5),
        "max_drawdown_r": round(_max_drawdown(values), 5),
        "target_exits": sum(r.exit_reason == "TARGET" for r in resolved),
        "stop_exits": sum(r.exit_reason == "STOP" for r in resolved),
        "eod_exits": sum(r.exit_reason == "RTH_EOD" for r in resolved),
        "top_positive_ticker_share": (
            round(_ticker_concentration(resolved), 5)
            if _ticker_concentration(resolved) is not None
            else None
        ),
        "level_distance_median": (
            round(statistics.median(distances), 4) if distances else None
        ),
        "level_distance_p90": (
            round(_quantile(distances, 0.90), 4) if distances else None
        ),
        "alignment": aligned,
        "halves": halves,
    }


def gate_family(report: dict[str, Any], family: str, geometry: str) -> dict[str, Any]:
    reasons: list[str] = []
    base = report["families"][family]["cells"][f"{geometry}:base"]
    stress = report["families"][family]["cells"][f"{geometry}:stress"]

    for half in ("H1", "H2"):
        s = stress["halves"][half]
        if s["n"] < 100:
            reasons.append(f"{half}:n<100")
        if not (
            s["mean_r"] is not None
            and s["mean_r"] > 0
            and s["pf"] is not None
            and s["pf"] > 1.10
        ):
            reasons.append(f"{half}:stress_mean_or_pf")
    if base["net_r"] <= 0:
        reasons.append("base_total_not_positive")
    if stress["net_r"] <= 0:
        reasons.append("stress_total_not_positive")
    share = stress["top_positive_ticker_share"]
    if share is None or share >= 0.30:
        reasons.append("ticker_concentration")

    return {
        "family": family,
        "geometry": geometry,
        "passes": not reasons,
        "classification": "PROMISING_BUT_UNPROVEN" if not reasons else "WAIT",
        "reasons": reasons,
    }


async def run(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    split = date.fromisoformat(args.split)
    if split != HALF_SPLIT:
        raise SystemExit(
            f"ong-v0.1 split is frozen at {HALF_SPLIT.isoformat()}, got {split}"
        )
    if start != START_DATE or end != END_DATE:
        raise SystemExit(
            f"ong-v0.1 range frozen at {START_DATE}..{END_DATE}; "
            f"got {start}..{end}"
        )

    universe_path = Path(args.universe) if args.universe else DEFAULT_UNIVERSE
    debug_population = bool(args.universe or args.symbols)
    if not debug_population:
        observed_sha = _git_blob_sha(universe_path)
        if observed_sha != FROZEN_UNIVERSE_BLOB_SHA:
            raise SystemExit(
                "ong-v0.1 frozen universe changed: "
                f"expected {FROZEN_UNIVERSE_BLOB_SHA}, got {observed_sha}"
            )

    universe = load_universe(universe_path)
    if args.symbols:
        requested = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        requested = universe
    requested = list(dict.fromkeys(requested))
    all_symbols = list(dict.fromkeys([*requested, *INDEX_SYMBOLS]))

    key, secret = resolve_alpaca_credentials()
    if not key or not secret:
        raise SystemExit("Alpaca credentials not configured")
    provider = AlpacaBarProvider(
        base_url=os.environ.get(
            "ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"
        ).rstrip("/"),
        api_key=key,
        secret_key=secret,
        feed=CONSOLIDATED_FEED,
    )

    fetch_start = start - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    bars_by_symbol, provider_errors = await fetch_history(
        provider,
        all_symbols,
        start=fetch_start,
        end=end,
    )
    sessions = sessions_between(fetch_start, end)
    spy = bars_by_symbol.get("SPY")
    qqq = bars_by_symbol.get("QQQ")

    candidates_by_symbol: dict[str, list[Candidate]] = {}
    skipped_by_symbol: dict[str, Any] = {}
    session_bars_by_symbol_date: dict[tuple[str, str], list[Bar]] = {}

    for symbol in requested:
        bars = bars_by_symbol.get(symbol)
        if not bars:
            skipped_by_symbol[symbol] = {"missing_all": 1}
            continue
        candidates, skipped = collect_candidates(
            symbol,
            bars,
            sessions,
            study_start=start,
            study_end=end,
            spy_bars=spy,
            qqq_bars=qqq,
        )
        candidates_by_symbol[symbol] = candidates
        skipped_by_symbol[symbol] = skipped
        for session in sessions:
            if start <= session.date <= end:
                sliced = _slice(bars, session)
                if sliced and _complete(sliced, session):
                    session_bars_by_symbol_date[(symbol, session.date.isoformat())] = sliced

    rows: list[GeometryRow] = []
    for symbol, candidates in candidates_by_symbol.items():
        for candidate in candidates:
            bars = session_bars_by_symbol_date.get((symbol, candidate.session_date))
            if not bars:
                continue
            for geometry in GEOMETRIES:
                for label, slip in SLIPPAGE.items():
                    rows.append(
                        simulate(
                            candidate,
                            bars,
                            geometry=geometry,
                            slippage_label=label,
                            slippage_dollars=slip,
                        )
                    )

    families = sorted({row.family for row in rows})
    report: dict[str, Any] = {
        "study_id": STUDY_ID,
        "study_version": STUDY_VERSION,
        "range": [start.isoformat(), end.isoformat()],
        "split": split.isoformat(),
        "universe_source": str(universe_path),
        "universe_blob_sha": _git_blob_sha(universe_path),
        "population_mode": "DEBUG_OVERRIDE" if debug_population else "FROZEN_150",
        "symbols_requested": len(requested),
        "symbols_with_candidates": sum(bool(rows) for rows in candidates_by_symbol.values()),
        "provider_error_windows": provider_errors,
        "skipped_by_symbol": skipped_by_symbol,
        "families": {},
    }
    for family in families:
        block: dict[str, Any] = {"cells": {}}
        for geometry in GEOMETRIES:
            for label in SLIPPAGE:
                selected = [
                    row
                    for row in rows
                    if row.family == family
                    and row.geometry == geometry
                    and row.slippage_label == label
                ]
                block["cells"][f"{geometry}:{label}"] = summarize_cell(selected)
        report["families"][family] = block

    report["gate"] = [
        gate_family(report, family, geometry)
        for family in families
        for geometry in GEOMETRIES
    ]
    if debug_population:
        report["gate"] = [
            {
                **gate,
                "passes": False,
                "classification": "DEBUG_ONLY",
                "reasons": ["debug_population_override", *gate["reasons"]],
            }
            for gate in report["gate"]
        ]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "ong_v01_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    with (out / "ong_v01_rows.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row.to_dict(), sort_keys=True) + "\n")

    print(
        f"{STUDY_ID} {STUDY_VERSION}: "
        f"{len(rows)} geometry rows across {len(families)} families"
    )
    for gate in report["gate"]:
        print(
            gate["family"],
            gate["geometry"],
            gate["classification"],
            ",".join(gate["reasons"]) or "PASS",
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ong-v0.1 non-Strat underlying geometry study"
    )
    parser.add_argument("--start", default=START_DATE.isoformat())
    parser.add_argument("--end", default=END_DATE.isoformat())
    parser.add_argument("--split", default=HALF_SPLIT.isoformat())
    parser.add_argument("--universe")
    parser.add_argument("--symbols", help="optional comma-separated debug subset")
    parser.add_argument("--out", default=str(ROOT / "logs" / "ong_v01"))
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())