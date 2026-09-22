#!/usr/bin/env python3
"""Run the preregistered MNQ six-family combined portfolio audit.

Research only. This script reuses frozen family implementations, proves their
source controls first, then normalizes fillable events into one shared-account
timeline. A failed control aborts before any portfolio number is emitted.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import tempfile
import sys
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from config.settings import load_config
from context import asia_d_ema_paper_cohort as asia
from execution.cross_instrument_observation import observation_day as cme_observation_day
from replay.candle_loader import ReplayCandleLoader
from replay.replay_engine import ReplayEngine
from research.bars_12hr_miyagi_loader import load_5m_day
from research.mnq_combined_portfolio_audit import (
    PortfolioEvent,
    TIE_PRIORITY,
    leave_one_out,
    replay_portfolio,
    summarize_replay,
)
from research.mnq_sustained_trend_continuation_v1 import (
    ResearchTrade,
    SustainedTrendContinuationV1,
    resolve_trade_on_bar,
)
from research.replay_12hr_miyagi_honest_fill import replay_signal as replay_miyagi
from scripts import daily22_trigger_timing_ab_2026_09_18 as daily_audit
from scripts import mnq_sustained_trend_continuation_v1 as sustained_script
from scripts.edge_decomposition_audit import (
    LANES,
    _boundary,
    _completed_one_hour_stop,
    _window_rows,
    aggregate_et_bars,
    bracket_summary,
    extract_state_machine,
    load_bars,
    resolve_bracket,
    run_bracket_stage,
)
from strategy.shadow_setups import evaluate_shadow_setups


START = date(2025, 7, 24)
END = date(2026, 6, 26)
EXPECTED_COMMON_DAYS = 290
DEFAULT_DATA = REPO / "data"
DEFAULT_OUT = REPO / "logs/mnq_combined_portfolio_audit_2026-09-22.json"

# Canonical accepted full-window controls. They are provenance gates, not
# portfolio pass/fail thresholds.
EXPECTED = {
    "4HR_RETRIGGER": {"fills": 80, "eod_bar_missing": 1, "net": 1414.60, "pf": 1.299},
    "60M_322_FIRST_LIVE": {"fills": 33, "net": 2742.66},
    "DAILY_22_COMPLETED_CLOSE": {"fills": 34, "net": 13571.68, "pf": 1.9482},
    "12HR_MIYAGI": {"fills": 8, "net": 425.33, "pf": 2.322},
    "SUSTAINED_TREND_V1": {"terminal": 34, "net": 576.18, "pf": 1.762},
}

FAMILY_4HR = "4HR_RETRIGGER"
FAMILY_322 = "60M_322_FIRST_LIVE"
FAMILY_DAILY = "DAILY_22_COMPLETED_CLOSE"
FAMILY_MIYAGI = "12HR_MIYAGI"
FAMILY_ASIA = "ASIA_D_EMA"
FAMILY_ST = "SUSTAINED_TREND_V1"

# Standalone controls must preserve each family's frozen capacity contract.
# Sustained v1 alone had a max-3/day CapacityGate. Asia D+EMA did not; its
# archived cohort was one-position-only. The other families emit at most one
# same-day opportunity under their frozen state machines, so the large cap is
# behavior-neutral for them.
STANDALONE_DAILY_CAP = {
    FAMILY_ST: 3,
    FAMILY_ASIA: 1_000_000,
    FAMILY_4HR: 1_000_000,
    FAMILY_322: 1_000_000,
    FAMILY_DAILY: 1_000_000,
    FAMILY_MIYAGI: 1_000_000,
}


def _parse(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        out = value
    else:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if out.tzinfo is None:
        raise RuntimeError(f"naive timestamp: {value!r}")
    return out


def _bar_close_ts(bar_start: str | datetime, minutes: int) -> str:
    return (_parse(bar_start) + timedelta(minutes=minutes)).isoformat()


def _obs_day(ts: str | datetime) -> str:
    return cme_observation_day("MNQ", ts).isoformat()


def _date_in_window(value: str | date) -> bool:
    d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return START <= d <= END


def _pf(values: Iterable[float]) -> float | None:
    vals = list(values)
    wins = sum(v for v in vals if v > 0)
    losses = -sum(v for v in vals if v < 0)
    if losses == 0:
        return math.inf if wins > 0 else None
    return wins / losses


def _assert_close(label: str, got: float, expected: float, tol: float = 0.03) -> None:
    if abs(float(got) - float(expected)) > tol:
        raise RuntimeError(f"{label} control mismatch: {got} != {expected}")


def _assert_control(label: str, actual: dict, expected: dict) -> None:
    for key, exp in expected.items():
        got = actual.get(key)
        if got is None:
            raise RuntimeError(f"{label} control missing {key}")
        if isinstance(exp, int):
            if int(got) != exp:
                raise RuntimeError(f"{label} {key} mismatch: {got} != {exp}")
        else:
            _assert_close(f"{label} {key}", float(got), float(exp))


def _common_days(*roots: Path) -> list[str]:
    def days(root: Path) -> set[str]:
        return {
            p.stem.removeprefix("MNQ_")
            for p in (root / "MNQ").glob("MNQ_*.jsonl")
        }

    if not roots:
        raise RuntimeError("no corpus roots supplied")
    common_set = days(roots[0])
    for root in roots[1:]:
        common_set &= days(root)
    common = sorted(common_set)
    common = [d for d in common if START <= date.fromisoformat(d) <= END]
    if len(common) != EXPECTED_COMMON_DAYS:
        raise RuntimeError(
            f"common causal day count mismatch: {len(common)} != {EXPECTED_COMMON_DAYS}"
        )
    if not common or common[0] != START.isoformat() or common[-1] != END.isoformat():
        raise RuntimeError(
            f"common window mismatch: {common[0] if common else None} -> "
            f"{common[-1] if common else None}"
        )
    return common


def _event_from_bracket(
    family: str,
    source_id: str,
    cand,
    bars,
    resolution: dict,
    *,
    trigger_idx: int,
    source: str,
) -> PortfolioEvent | None:
    if resolution.get("status") not in {"RESOLVED", "OPEN"}:
        return None
    fill = resolution.get("fill_entry")
    if fill is None:
        return None

    fill_ts = _bar_close_ts(bars.rows[trigger_idx]["timestamp"], 5)
    exit_idx = resolution.get("exit_idx")
    exit_ts = (
        _bar_close_ts(bars.rows[int(exit_idx)]["timestamp"], 5)
        if exit_idx is not None and 0 <= int(exit_idx) < len(bars.rows)
        else None
    )
    status = resolution.get("status")
    result = resolution.get("result") if status == "RESOLVED" else status
    net = float(resolution.get("net") or 0.0)
    return PortfolioEvent(
        family=family,
        source_id=source_id,
        signal_ts=fill_ts,
        eligible_fill_ts=fill_ts,
        exit_ts=exit_ts,
        observation_day=_obs_day(fill_ts),
        direction=cand.direction,
        entry=float(fill),
        stop=float(cand.stop),
        target=float(cand.target),
        result=str(result),
        net_pnl=net,
        session=str(cand.session or ""),
        source=source,
    )


def _prepare_4hr_causal_candidates(bars):
    """Recreate the corrected pre-armed 4HR contract.

    The normal 5m state machine identifies the same 81 setup/trigger bars. For
    the pre-armed model, the stop must be the 1H candle completed BEFORE the
    trigger bar starts, not the one completed by its close. The resting stop
    order is represented by moving PaperBroker activation to the prior 5m bar.
    """
    originals = extract_state_machine(LANES["4hr_mnq"], bars)
    prepared = []
    for cand in originals:
        trigger_idx = cand.bar_idx
        if trigger_idx <= 0:
            raise RuntimeError(f"4HR {cand.date}: no pre-arm bar")
        trigger_open = bars.rows[trigger_idx]["_dt"]
        history = _window_rows(bars, trigger_idx, bars.bars_per_day * 5)
        stop, stop_ts = _completed_one_hour_stop(
            aggregate_et_bars(history, 60),
            trigger_open.astimezone(daily_audit.ET),
            cand.direction,
        )
        if stop is None or stop_ts is None:
            raise RuntimeError(f"4HR {cand.date}: causal stop unavailable")
        valid = (
            float(stop) < cand.entry < cand.target
            if cand.direction == "LONG"
            else cand.target < cand.entry < float(stop)
        )
        if not valid:
            raise RuntimeError(f"4HR {cand.date}: causal bracket invalid")
        extra = dict(cand.extra)
        extra.update(
            original_bar_idx=trigger_idx,
            original_bar_ts=cand.bar_ts,
            prearmed_from_idx=trigger_idx - 1,
            causal_stop_bar_ts=stop_ts.isoformat(),
        )
        prepared.append(
            dataclasses.replace(
                cand,
                bar_idx=trigger_idx - 1,
                bar_ts=bars.rows[trigger_idx - 1]["timestamp"],
                stop=float(stop),
                extra=extra,
            )
        )
    return originals, prepared


def _four_hr(root5: Path) -> tuple[list[PortfolioEvent], dict, list[dict]]:
    bars = load_bars(root5, "MNQ")
    originals, prearmed = _prepare_4hr_causal_candidates(bars)

    control_rows = run_bracket_stage(
        LANES["4hr_mnq"], bars, prearmed,
        fill_model="stop_market", slippage_ticks=1.0, tolerance_ticks=32.0,
    )
    control = bracket_summary(control_rows, _boundary(prearmed))
    control_check = {
        "fills": int(control["resolved"]),
        "eod_bar_missing": int(control["eod_bar_missing"]),
        "net": float(control["net"]),
        "pf": float(control["profit_factor"]),
    }
    _assert_control(FAMILY_4HR, control_check, EXPECTED[FAMILY_4HR])

    events: list[PortfolioEvent] = []
    attempts: list[dict] = []
    prepared_by_day = {c.date: c for c in prearmed}
    for original in originals:
        if not _date_in_window(original.date):
            continue
        cand = prepared_by_day[original.date]
        trigger_idx = int(cand.extra["original_bar_idx"])
        signal_ts = _bar_close_ts(bars.rows[trigger_idx]["timestamp"], 5)
        source_id = f"4hr:{original.date}"
        attempts.append({"family": FAMILY_4HR, "source_id": source_id, "ts": signal_ts})
        res = resolve_bracket(
            LANES["4hr_mnq"], bars, cand,
            fill_model="stop_market", slippage_ticks=1.0, tolerance_ticks=32.0,
        )
        event = _event_from_bracket(
            FAMILY_4HR, source_id, cand, bars, res,
            trigger_idx=trigger_idx,
            source="advance_4hr_retrigger + causal prearmed stop-touch",
        )
        if event is not None:
            events.append(event)

    return events, {
        "canonical_full_window": control_check,
        "raw_common_candidates": len(attempts),
        "raw_common_fillable": len(events),
    }, attempts


def _three_two_two(research_15m: Path, root5: Path) -> tuple[list[PortfolioEvent], dict, list[dict]]:
    # The frozen detector control was built on data/replay_polygon, not the
    # corrected 15m market-condition corpus used by other families. Using a
    # different 15m source changes the detector population and must fail closed.
    import importlib
    audit322 = importlib.import_module("scripts.322_trigger_timing_ab_2026_09_18")

    candidates, triggers, bars = audit322.crosscheck(research_15m, root5)
    prearmed = audit322.make_prearmed(candidates, triggers, bars)
    control_rows = run_bracket_stage(
        LANES["322_mnq"], bars, prearmed,
        fill_model="stop_market", slippage_ticks=1.0, tolerance_ticks=32.0,
    )
    control = bracket_summary(control_rows, _boundary(prearmed))
    control_check = {"fills": int(control["filled"]), "net": float(control["net"])}
    _assert_control(FAMILY_322, control_check, EXPECTED[FAMILY_322])

    pre_by_day = {c.date: c for c in prearmed}
    events: list[PortfolioEvent] = []
    attempts: list[dict] = []
    for original in candidates:
        if not _date_in_window(original.date):
            continue
        cand = pre_by_day[original.date]
        trigger_idx = int(cand.extra["original_bar_idx"])
        signal_ts = _bar_close_ts(bars.rows[trigger_idx]["timestamp"], 5)
        source_id = f"322:{original.date}"
        attempts.append({"family": FAMILY_322, "source_id": source_id, "ts": signal_ts})
        res = resolve_bracket(
            LANES["322_mnq"], bars, cand,
            fill_model="stop_market", slippage_ticks=1.0, tolerance_ticks=32.0,
        )
        event = _event_from_bracket(
            FAMILY_322, source_id, cand, bars, res,
            trigger_idx=trigger_idx,
            source="strat_322_first_live prearmed_touch 1tick",
        )
        if event is not None:
            events.append(event)

    return events, {
        "canonical_full_window": control_check,
        "raw_common_candidates": len(attempts),
        "raw_common_fillable": len(events),
    }, attempts


def _daily(root5: Path) -> tuple[list[PortfolioEvent], dict, list[dict]]:
    bars = load_bars(root5, "MNQ")
    structural = daily_audit.structural_events(bars, daily_audit.current_trading_day)
    canonical_rows = daily_audit.run_model(structural, bars, "A")
    canonical = daily_audit.summary(canonical_rows)
    control = {
        "fills": int(canonical["fills"]),
        "net": float(canonical["net"]),
        "pf": float(canonical["pf"]),
    }
    _assert_control(FAMILY_DAILY, control, EXPECTED[FAMILY_DAILY])

    events: list[PortfolioEvent] = []
    attempts: list[dict] = []
    for cand in structural:
        if cand.get("status") != "CONTINUATION" or not _date_in_window(cand["day"]):
            continue
        idx = int(cand["bar_idx"])
        signal_ts = _bar_close_ts(bars.rows[idx]["timestamp"], 5)
        source_id = f"daily22:{cand['day']}:{bars.rows[idx]['timestamp']}"
        attempts.append({"family": FAMILY_DAILY, "source_id": source_id, "ts": signal_ts})

        ok, _, _ = daily_audit.context_result(bars.rows[idx], cand["direction"])
        if not ok:
            continue
        expected_fill, _ = daily_audit.lane._expected_ioc_fill(
            cand, float(bars.rows[idx]["close"])
        )
        if expected_fill is None:
            continue
        if daily_audit.fill_rr(cand, expected_fill) < daily_audit.lane.MIN_ACTUAL_RR:
            continue
        if daily_audit.risk_dollars(cand, expected_fill) > daily_audit.lane.MAX_PLANNED_RISK_DOLLARS:
            continue
        res = daily_audit.resolve_model_a(cand, bars, daily_audit.lane.STARTING_BALANCE)
        if res.get("status") not in {"RESOLVED", "OPEN"}:
            continue
        exit_idx = int(res["exit_idx"])
        event = PortfolioEvent(
            family=FAMILY_DAILY,
            source_id=source_id,
            signal_ts=signal_ts,
            eligible_fill_ts=signal_ts,
            exit_ts=_bar_close_ts(bars.rows[exit_idx]["timestamp"], 5),
            observation_day=_obs_day(signal_ts),
            direction=str(cand["direction"]),
            entry=float(res["entry"]),
            stop=float(cand["stop"]),
            target=float(cand["target"]),
            result=str(res.get("result") or res["status"]),
            net_pnl=float(res.get("net") or 0.0),
            session=str(bars.rows[idx].get("session") or ""),
            source="daily_22 completed-close Model A",
        )
        events.append(event)

    return events, {
        "canonical_full_window": control,
        "raw_common_continuations": len(attempts),
        "raw_common_fillable": len(events),
    }, attempts


def _miyagi(root5: Path) -> tuple[list[PortfolioEvent], dict, list[dict]]:
    path = (
        REPO / "docs/strategy-rules/evidence_12hr_miyagi/"
        "mnq_results_trigger_bar_corrected_2026-09-19.json"
    )
    evidence = json.loads(path.read_text())
    candidates = evidence["candidates"]

    full_rows = []
    by_date_rows: dict[str, dict] = {}
    for raw in candidates:
        d = date.fromisoformat(raw["date"])
        signal = {
            "date": d,
            "instrument": "MNQ",
            "direction": raw["direction"],
            "entry_trigger": raw["entry_trigger"],
            "stop": raw["stop"],
            "target": raw["target"],
            "target_2": raw.get("target_2"),
        }
        bars = load_5m_day(root5, "MNQ", d)
        out = replay_miyagi(signal, bars, slippage_ticks=2.0)
        full_rows.append(out)
        by_date_rows[raw["date"]] = out

    resolved = [r for r in full_rows if r.get("filled") and r.get("net_pnl") is not None]
    vals = [float(r["net_pnl"]) for r in resolved]
    control = {
        "fills": len(resolved),
        "net": round(sum(vals), 2),
        "pf": round(float(_pf(vals) or 0.0), 3),
    }
    _assert_control(FAMILY_MIYAGI, control, EXPECTED[FAMILY_MIYAGI])

    events: list[PortfolioEvent] = []
    attempts: list[dict] = []
    for raw in candidates:
        if not _date_in_window(raw["date"]):
            continue
        out = by_date_rows[raw["date"]]
        attempt_ts = out.get("entry_bar_ts")
        source_id = f"miyagi:{raw['date']}"
        if attempt_ts is not None:
            attempts.append(
                {"family": FAMILY_MIYAGI, "source_id": source_id, "ts": _bar_close_ts(attempt_ts, 5)}
            )
        if not out.get("filled") or out.get("fill_entry_price") is None:
            continue
        fill_ts = _bar_close_ts(out["entry_bar_ts"], 5)
        exit_ts = (
            _bar_close_ts(out["exit_bar_ts"], 5)
            if out.get("exit_bar_ts") is not None else None
        )
        events.append(
            PortfolioEvent(
                family=FAMILY_MIYAGI,
                source_id=source_id,
                signal_ts=fill_ts,
                eligible_fill_ts=fill_ts,
                exit_ts=exit_ts,
                observation_day=_obs_day(fill_ts),
                direction=str(raw["direction"]),
                entry=float(out["fill_entry_price"]),
                stop=float(raw["stop"]),
                target=float(raw["target"]),
                result=str(out["result"]),
                net_pnl=float(out.get("net_pnl") or 0.0),
                session="new_york",
                source="corrected trigger-bar Miyagi replay, 2tick",
            )
        )

    return events, {
        "canonical_full_window": control,
        "raw_common_candidates_with_trigger": len(attempts),
        "raw_common_fillable": len(events),
    }, attempts


def _asia_fixture_control() -> dict:
    fixture = json.loads((REPO / "tests/fixtures/asia_d_ema_parity.json").read_text())
    selected_total = expected_total = outcomes_checked = 0
    for day, data in fixture["days"].items():
        seen: set[str] = set()
        selected = set()
        for row in data["journal"]:
            for cand in asia.d_ema_candidates(row["context"], row["shadow_candidates"], day, seen):
                selected.add((
                    row["ts"], cand["strategy"], cand["direction"],
                    float(cand["entry"]), float(cand["stop"]), float(cand["target"]),
                ))
        expected = {
            (
                row["ts"], row["strategy"], row["direction"],
                float(row["entry"]), float(row["stop"]), float(row["target"]),
            )
            for row in data["expected_d_ema_rows"]
        }
        if selected != expected:
            raise RuntimeError(f"Asia D+EMA fixture selection parity failed on {day}")
        selected_total += len(selected)
        expected_total += len(expected)

        bars = {b["ts"]: b for b in data["bars"]}
        order = [b["ts"] for b in data["bars"]]
        for row in data["expected_d_ema_rows"]:
            cand = {k: row[k] for k in ("strategy", "direction", "entry", "stop", "target")}
            idx = order.index(row["ts"])
            out = asia.resolve_offline(cand, bars[row["ts"]], [bars[t] for t in order[idx + 1:]])
            for field in ("result", "exit_reason", "exit_ts"):
                if out.get(field) != row.get(field):
                    raise RuntimeError(
                        f"Asia D+EMA fixture outcome parity failed {day} {row['ts']} {field}"
                    )
            outcomes_checked += 1
    return {
        "fixture_selection_rows": selected_total,
        "fixture_expected_rows": expected_total,
        "fixture_outcomes_checked": outcomes_checked,
        "pass": True,
    }


def _asia(root15: Path) -> tuple[list[PortfolioEvent], dict, list[dict]]:
    parity = _asia_fixture_control()
    bars = load_bars(root15, "MNQ")

    events: list[PortfolioEvent] = []
    attempts: list[dict] = []
    seen_by_day: dict[str, set[str]] = defaultdict(set)
    config = load_config()

    # Flat lookup supplies later bars across file boundaries while the state
    # builder follows each source file's own causal previous-candle sequence.
    with tempfile.TemporaryDirectory(prefix="mnq-portfolio-asia-") as tmp:
        engine = ReplayEngine(config=config, log_dir=tmp)
        loader = ReplayCandleLoader()
        for path in bars.files:
            file_day = path.stem.removeprefix("MNQ_")
            if not _date_in_window(file_day):
                continue
            candles = loader.load_jsonl(path)
            history: deque[dict] = deque(maxlen=8)
            prev = prev_prev = None
            for candle in candles:
                state = engine._market_state_from_candle(candle, prev, prev_prev)
                prev_prev, prev = prev, candle
                history.append({
                    "ts": candle.timestamp, "open": candle.open, "high": candle.high,
                    "low": candle.low, "close": candle.close, "volume": candle.volume,
                })
                shadow = [
                    cand.to_dict()
                    for cand in evaluate_shadow_setups(state, list(history), config)
                ]
                context = asia._bar_context(state)
                raw_source = getattr(candle, "source", None)
                if isinstance(raw_source, dict):
                    if not context.get("structural_market_condition"):
                        context["structural_market_condition"] = raw_source.get("structural_market_condition")
                    if not context.get("structural_direction"):
                        context["structural_direction"] = raw_source.get("structural_direction")
                day = _obs_day(_bar_close_ts(candle.timestamp, 15))
                picks = asia.d_ema_candidates(context, shadow, day, seen_by_day[day])
                for cand in sorted(picks, key=lambda c: (c["strategy"], c["direction"])):
                    if not asia.in_scope(
                        state.instrument, context.get("session"), cand["strategy"], state.ohlc.timeframe
                    ):
                        continue
                    idx = bars.by_ts.get(candle.timestamp)
                    if idx is None:
                        raise RuntimeError(f"Asia bar not found in flat corpus: {candle.timestamp}")
                    signal_ts = _bar_close_ts(candle.timestamp, 15)
                    source_id = f"asia:{signal_ts}:{cand['strategy']}:{cand['direction']}"
                    attempts.append({"family": FAMILY_ASIA, "source_id": source_id, "ts": signal_ts})

                    forward: list[dict] = []
                    for row in bars.rows[idx + 1:]:
                        ts = _bar_close_ts(row["timestamp"], 15)
                        if _obs_day(ts) != day:
                            break
                        forward.append({
                            "ts": row["timestamp"], "open": row["open"], "high": row["high"],
                            "low": row["low"], "close": row["close"],
                        })
                    decision_bar = {
                        "ts": candle.timestamp, "open": candle.open, "high": candle.high,
                        "low": candle.low, "close": candle.close,
                    }
                    out = asia.resolve_offline(cand, decision_bar, forward)
                    if out["result"] == "NO_FILL":
                        continue
                    exit_ts = (
                        _bar_close_ts(out["exit_ts"], 15)
                        if out.get("exit_ts") else signal_ts
                    )
                    events.append(
                        PortfolioEvent(
                            family=FAMILY_ASIA,
                            source_id=source_id,
                            signal_ts=signal_ts,
                            eligible_fill_ts=signal_ts,
                            exit_ts=exit_ts,
                            observation_day=day,
                            direction=cand["direction"],
                            entry=float(out["entry_price"]),
                            stop=float(cand["stop"]),
                            target=float(cand["target"]),
                            result=str(out["result"]),
                            net_pnl=float(out.get("pnl_dollars") or 0.0),
                            session="asian",
                            source="asia_d_ema frozen cohort helpers",
                        )
                    )

    return events, {
        "canonical_fixture_control": parity,
        "raw_common_candidates": len(attempts),
        "raw_common_fillable": len(events),
    }, attempts


def _sustained(root15: Path, root5late: Path) -> tuple[list[PortfolioEvent], dict, list[dict]]:
    canonical = sustained_script.run(root15, root5late)
    all_metrics = canonical["all"]
    control = {
        "terminal": int(all_metrics["terminal"]),
        "net": float(all_metrics["net_pnl"]),
        "pf": float(all_metrics["profit_factor"]),
    }
    _assert_control(FAMILY_ST, control, EXPECTED[FAMILY_ST])

    files15 = {
        sustained_script._day(p): p
        for p in sustained_script._corpus_files(root15)
        if _date_in_window(sustained_script._day(p))
    }
    files5 = {
        sustained_script._day(p): p
        for p in sustained_script._corpus_files(root5late)
        if _date_in_window(sustained_script._day(p))
    }
    if set(files15) - set(files5):
        raise RuntimeError("Sustained v1 common window missing 5m files")

    detector = SustainedTrendContinuationV1()
    triggers: list[tuple[Any, dict]] = []
    all_5m: list[tuple[datetime, dict]] = []

    for day in sorted(files15):
        b15 = sustained_script._load_jsonl(files15[day])
        b5 = sustained_script._load_jsonl(files5[day])
        all_5m.extend((sustained_script._close_time(row, 5), row) for row in b5)
        stream = (
            [(sustained_script._close_time(row, 15), 0, "15m", row) for row in b15]
            + [(sustained_script._close_time(row, 5), 1, "5m", row) for row in b5]
        )
        stream.sort(key=lambda item: (item[0], item[1]))
        for close_ts, _, tf, row in stream:
            produced = (
                detector.on_15m(row, close_time=close_ts)
                if tf == "15m"
                else detector.on_5m(row, close_time=close_ts)
            )
            for event in produced:
                if event.event in {"TRIGGERED", "STOP_CAP_REJECTED"}:
                    triggers.append((event, row))

    all_5m.sort(key=lambda item: item[0])
    events: list[PortfolioEvent] = []
    attempts: list[dict] = []
    for event, _ in triggers:
        source_id = f"sustained:{event.arm_bar_ts}:{event.event_ts}"
        attempts.append({"family": FAMILY_ST, "source_id": source_id, "ts": event.event_ts})
        if event.event != "TRIGGERED":
            continue
        trade = ResearchTrade(
            observation_day=_obs_day(event.event_ts),
            session=str(event.session or ""),
            arm_bar_ts=event.arm_bar_ts,
            trigger_ts=event.event_ts,
            entry=float(event.modeled_fill),
            stop=float(event.stop),
            target=float(event.target),
            stop_ticks=float(event.stop_ticks),
        )
        last_same_day = event.event_ts
        for close_ts, row in all_5m:
            if close_ts <= _parse(event.event_ts):
                continue
            if _obs_day(close_ts) != trade.observation_day:
                break
            last_same_day = close_ts.isoformat()
            if resolve_trade_on_bar(trade, row, close_time=close_ts) is not None:
                break
        if trade.result == "OPEN":
            trade.result = "OPEN_EOD"
            trade.exit_ts = last_same_day
            trade.net_pnl = 0.0
        events.append(
            PortfolioEvent(
                family=FAMILY_ST,
                source_id=source_id,
                signal_ts=event.event_ts,
                eligible_fill_ts=event.event_ts,
                exit_ts=trade.exit_ts,
                observation_day=trade.observation_day,
                direction="LONG",
                entry=trade.entry,
                stop=trade.stop,
                target=trade.target,
                result=trade.result,
                net_pnl=float(trade.net_pnl or 0.0),
                session=trade.session,
                source="frozen PR #912 v1 detector",
            )
        )

    return events, {
        "canonical_full_window": control,
        "canonical_coverage": canonical["data"]["coverage_check"],
        "raw_common_candidates": len(attempts),
        "raw_common_fillable": len(events),
    }, attempts


def _large_move_coverage(bars15, attempts: list[dict], replay) -> dict:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in bars15.rows:
        d = row["_dt"].date().isoformat()
        if _date_in_window(d):
            by_day[d].append(row)

    windows = []
    for day, rows in sorted(by_day.items()):
        for i in range(0, len(rows), 4):
            block = rows[i:i + 4]
            if len(block) < 2:
                continue
            rng = max(float(r["high"]) for r in block) - min(float(r["low"]) for r in block)
            if rng < 60.0:
                continue
            start = _parse(_bar_close_ts(block[0]["timestamp"], 15))
            end = _parse(_bar_close_ts(block[-1]["timestamp"], 15))
            windows.append((day, start, end, rng))

    decisions = {d.source_id: d for d in replay.decisions}
    fills = {e.source_id: e for e in replay.fills}
    counts = Counter()
    details = []
    for day, start, end, rng in windows:
        signals = [
            a for a in attempts
            if start <= _parse(a["ts"]) <= end
        ]
        fillable_decisions = [
            decisions[a["source_id"]]
            for a in signals
            if a["source_id"] in decisions
        ]
        selected = [
            fills[a["source_id"]]
            for a in signals
            if a["source_id"] in fills
        ]
        if selected:
            category = "FILLED"
        elif not signals:
            category = "NO_SIGNAL"
        elif not fillable_decisions:
            category = "SIGNAL_NO_FILL"
        elif any(d.disposition == "SKIPPED_BUSY_PORTFOLIO" for d in fillable_decisions):
            category = "BUSY_POSITION"
        elif any(d.disposition == "SKIPPED_MAX_TRADES_PORTFOLIO" for d in fillable_decisions):
            category = "DAILY_CAP"
        else:
            category = "SIGNAL_NO_FILL"
        counts[category] += 1
        details.append({
            "day": day,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "range_points": round(rng, 2),
            "category": category,
            "signal_families": sorted({a["family"] for a in signals}),
        })

    return {
        "definition": {
            "bars": 4,
            "threshold_points": 60.0,
            "non_overlapping": True,
            "descriptive_only": True,
        },
        "windows": len(windows),
        "represented_by_signal": len(windows) - int(counts.get("NO_SIGNAL", 0)),
        "actually_filled": int(counts.get("FILLED", 0)),
        "categories": dict(counts),
        "details": details,
    }


def run(data_root: Path) -> dict:
    root5 = data_root / "replay_corpus_v1_5m_4hr_audit"
    root15 = data_root / "replay_corpus_v1_market_condition_fixed"
    root5late = data_root / "replay_corpus_v1_5m"
    root322_15 = data_root / "replay_polygon"
    common_days = _common_days(root5, root15, root5late, root322_15)

    # Control order is deliberate. Any mismatch raises before combined numbers.
    adapters = [
        _four_hr(root5),
        _three_two_two(root322_15, root5),
        _daily(root5),
        _miyagi(root5),
        _asia(root15),
        _sustained(root15, root5late),
    ]
    all_events = [event for events, _, _ in adapters for event in events]
    all_attempts = [attempt for _, _, attempts in adapters for attempt in attempts]
    controls = {
        family: diag
        for family, (_, diag, _) in zip(TIE_PRIORITY, adapters)
    }

    missing = set(TIE_PRIORITY) - {event.family for event in all_events}
    # A family may legitimately have zero FILLS in the common window only if
    # it had at least one causal attempt. Zero attempts means the six-family
    # common replay is not demonstrated.
    attempted_families = {a["family"] for a in all_attempts}
    unavailable = set(TIE_PRIORITY) - attempted_families
    if unavailable:
        raise RuntimeError(
            "INSUFFICIENT_COMMON_DATA: no causal common-window attempts for "
            + ", ".join(sorted(unavailable))
        )

    standalone = {}
    attempts_by_family = Counter(a["family"] for a in all_attempts)
    for family in TIE_PRIORITY:
        family_events = [event for event in all_events if event.family == family]
        standalone[family] = summarize_replay(
            replay_portfolio(
                family_events,
                max_fills_per_day=STANDALONE_DAILY_CAP[family],
            )
        )
        standalone[family]["eligible_signals"] = int(attempts_by_family.get(family, 0))

    combined_replay = replay_portfolio(all_events)
    combined = summarize_replay(combined_replay)
    loo = leave_one_out(all_events)
    coverage = _large_move_coverage(load_bars(root15, "MNQ"), all_attempts, combined_replay)

    return {
        "status": "AUDIT_ONLY",
        "study": "mnq_combined_portfolio_audit_2026-09-22",
        "common_window": {
            "start": START.isoformat(),
            "end": END.isoformat(),
            "trading_days": len(common_days),
            "first_day": common_days[0],
            "last_day": common_days[-1],
        },
        "timestamp_normalization": {
            "armed_5m_touch_families": "trigger 5m bar close; exact intrabar touch time unavailable",
            "daily_22": "completed trigger 5m bar close",
            "asia_d_ema": "completed decision 15m bar close",
            "sustained_trend_v1": "completed trigger 5m close from frozen detector",
            "exit_ordering": "exit and new entry at same normalized timestamp => prior position remains busy",
        },
        "controls": controls,
        "common_raw": {
            "attempts_by_family": dict(attempts_by_family),
            "fillable_events_by_family": dict(Counter(e.family for e in all_events)),
            "families_with_no_fillable_events": sorted(missing),
        },
        "standalone_common_window": standalone,
        "combined": combined,
        "leave_one_family_out": loo,
        "large_move_coverage": coverage,
        "portfolio_fills": [e.to_dict() for e in combined_replay.fills],
        "portfolio_decisions": [d.to_dict() for d in combined_replay.decisions],
        "notes": [
            "No standalone P&Ls are added together; only chronological shared-account fills form the portfolio.",
            "Controls are checked before portfolio output; a mismatch raises and produces no result.",
            "Daily 2-2 can occupy the shared account across multiple days.",
            "This research output grants no paper, DEMO, live, broker, risk, or deployment authority.",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    report = run(args.data_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps({
        "status": report["status"],
        "common_window": report["common_window"],
        "controls": report["controls"],
        "standalone_common_window": report["standalone_common_window"],
        "combined": report["combined"],
        "leave_one_family_out": report["leave_one_family_out"],
        "large_move_coverage": {
            k: v for k, v in report["large_move_coverage"].items() if k != "details"
        },
    }, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
