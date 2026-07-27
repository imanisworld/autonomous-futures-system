#!/usr/bin/env python3
"""One-shot ORB Breakout marketable-limit inverse research pass.

Frozen contract:
docs/strategy-rules/ORB_BREAKOUT_MARKETABLE_LIMIT_INVERSE_PREREGISTRATION_2026-07-27.md

Research only. All broker patches are process-scoped PaperBroker wrappers and
all output paths are explicit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402
from replay.candle_loader import ReplayCandleLoader  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402
from scripts.corrected_ioc_corpus_evidence import (  # noqa: E402
    COMMISSION_ROUND_TRIP,
    HALVES,
    INSTRUMENTS,
    _json_lines,
    _parse_logs,
    _period_label,
    _tree_sha256,
)
from scripts.execution_mode_corpus_comparison import MARKETABLE_TICKS  # noqa: E402


BASE_SHA = "74b14071822be46de46be3c2db0eff7c95b8fced"
PREREGISTRATION_SHA = "b2c586af8e2b624e93fe0bf18fbab4be15f2003d"
CORPUS_SHA256 = "4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4"
SOURCE_RAW_SHA256 = "800c6a33212710a172bc4ff8bcca7a1f7ecc3e4ce437624d1bc0ecd05c79ba23"
SOURCE_IDENTITY_SHA256 = "4e357bfc9e4a23c28fbbdf67e7f5cf99cbc40bb065e2e39684b29705b1192970"
SOURCE_ATTEMPTS = 111
STRATEGY = "orb_breakout"
TOTAL_SLIPPAGE_TIERS = (1, 2, 3, 4, 5)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mirror(order: BracketOrder) -> BracketOrder:
    stop_distance = abs(float(order.entry) - float(order.stop))
    target_distance = abs(float(order.target) - float(order.entry))
    direction = "SHORT" if order.direction == "LONG" else "LONG"
    stop = order.entry + stop_distance if direction == "SHORT" else order.entry - stop_distance
    target = order.entry - target_distance if direction == "SHORT" else order.entry + target_distance
    mirrored = replace(order, direction=direction, stop=stop, target=target)
    if mirrored.direction == "LONG" and not (mirrored.stop < mirrored.entry < mirrored.target):
        raise RuntimeError(f"invalid mirrored LONG geometry: {asdict(mirrored)}")
    if mirrored.direction == "SHORT" and not (mirrored.target < mirrored.entry < mirrored.stop):
        raise RuntimeError(f"invalid mirrored SHORT geometry: {asdict(mirrored)}")
    return mirrored


def _marketable_config(base, *, slippage_ticks: int):
    return replace(
        base,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root=dict(MARKETABLE_TICKS),
        fill_slippage_ticks=float(slippage_ticks),
    )


def _stable_identity(row: dict, *, direction_field: str = "original_direction") -> tuple:
    return (
        str(row["date"]),
        str(row["bar_ts"]),
        str(row["instrument"]),
        STRATEGY,
        str(row[direction_field]),
        str(row["session"]),
    )


def _identity_payload(rows: Iterable[dict]) -> bytes:
    projected = [
        {
            "date": str(row["date"]),
            "bar_ts": str(row["bar_ts"]),
            "instrument": str(row["instrument"]),
            "strategy": STRATEGY,
            "original_direction": str(row["original_direction"]),
            "session": str(row["session"]),
        }
        for row in rows
    ]
    projected.sort(
        key=lambda row: (
            row["date"],
            row["bar_ts"],
            row["instrument"],
            row["original_direction"],
            row["session"],
        )
    )
    return "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in projected
    ).encode()


def _source_identities(source_raw: Path, bars: dict[str, list]) -> list[dict]:
    session_lookup = {
        (instrument, str(bar.timestamp)): str(bar.session)
        for instrument, lane in bars.items()
        for bar in lane
    }
    rows = []
    for row in map(json.loads, source_raw.open()):
        if row.get("arm") != "marketable_limit" or row.get("strategy") != STRATEGY:
            continue
        key = (row["instrument"], row["bar_ts"])
        if key not in session_lookup:
            raise RuntimeError(f"source attempt bar missing from corpus: {key}")
        rows.append(
            {
                "date": row["date"],
                "bar_ts": row["bar_ts"],
                "instrument": row["instrument"],
                "strategy": STRATEGY,
                "original_direction": row["direction"],
                "session": session_lookup[key],
            }
        )
    digest = hashlib.sha256(_identity_payload(rows)).hexdigest()
    if len(rows) != SOURCE_ATTEMPTS or digest != SOURCE_IDENTITY_SHA256:
        raise RuntimeError(f"source identity mismatch: count={len(rows)} digest={digest}")
    identities = [_stable_identity(row) for row in rows]
    if len(set(identities)) != len(identities):
        raise RuntimeError("duplicate stable identity in committed source attempts")
    return rows


def _load_bars(corpus: Path) -> tuple[dict[str, list], dict[str, dict[str, int]]]:
    all_bars: dict[str, list] = {}
    indexes: dict[str, dict[str, int]] = {}
    loader = ReplayCandleLoader()
    for instrument in INSTRUMENTS:
        seen = {}
        for path in sorted((corpus / instrument).glob("*.jsonl")):
            for bar in loader.load_jsonl(path):
                key = str(bar.timestamp)
                existing = seen.get(key)
                if existing is not None and existing != bar:
                    raise RuntimeError(f"conflicting corpus bar {instrument} {key}")
                seen[key] = bar
        unique = sorted(seen.values(), key=lambda row: row.timestamp)
        all_bars[instrument] = unique
        indexes[instrument] = {str(bar.timestamp): i for i, bar in enumerate(unique)}
    return all_bars, indexes


@contextmanager
def _broker_patch(*, invert_orb: bool, captures: dict[str, dict] | None = None):
    original = PaperBroker.execute_bracket

    def patched(self, order, market_price=None, *, paper_order_id=None):
        submitted = replace(order)
        if order.strategy == STRATEGY:
            if int(order.contracts or 1) != 1:
                raise RuntimeError(
                    f"one-contract invariant failed for {paper_order_id}: {order.contracts}"
                )
            if captures is not None:
                captures[str(paper_order_id)] = {
                    "paper_order_id": paper_order_id,
                    "market_price": market_price,
                    "order": asdict(order),
                }
            if invert_orb:
                submitted = _mirror(order)
        return original(
            self,
            submitted,
            market_price=market_price,
            paper_order_id=paper_order_id,
        )

    PaperBroker.execute_bracket = patched
    try:
        yield
    finally:
        PaperBroker.execute_bracket = original


def _run_system(
    config,
    corpus: Path,
    logs: Path,
    *,
    invert_orb: bool,
    captures: dict[str, dict] | None = None,
) -> None:
    with _broker_patch(invert_orb=invert_orb, captures=captures):
        for instrument in INSTRUMENTS:
            files = sorted((corpus / instrument).glob("*.jsonl"))
            if len(files) != 313:
                raise RuntimeError(f"{instrument}: expected 313 files, got {len(files)}")
            engine = ReplayEngine(config=config, log_dir=str(logs / instrument))
            for index, path in enumerate(files, 1):
                engine.run(path, review_date=path.stem.rsplit("_", 1)[-1])
                if index % 100 == 0 or index == len(files):
                    print(
                        f"[{'inverse' if invert_orb else 'original'}:{config.fill_slippage_ticks:g}t:"
                        f"{instrument}] {index}/{len(files)}",
                        flush=True,
                    )


def _session_map(logs: Path) -> dict[str, str]:
    found = {}
    for instrument in INSTRUMENTS:
        for path in (logs / instrument).glob("journal_*.jsonl"):
            for row in _json_lines(path):
                if row.get("type") != "OUTCOME":
                    continue
                out = row.get("outcome") or {}
                if out.get("paper_order_id"):
                    found[out["paper_order_id"]] = str(
                        row.get("session") or out.get("session") or "UNKNOWN"
                    )
    return found


def _parse_system(logs: Path, *, inverted_orb: bool) -> tuple[list[dict], dict, Counter]:
    trades, _candidates, _gates, risks, halt = _parse_logs(logs)
    sessions = _session_map(logs)
    for row in trades:
        row["session"] = sessions.get(row["paper_order_id"], "UNKNOWN")
        row["original_direction"] = row["direction"]
        if inverted_orb and row["strategy"] == STRATEGY:
            row["direction"] = "SHORT" if row["direction"] == "LONG" else "LONG"
        row["half"] = _period_label(row["date"], HALVES)
    return trades, halt, risks


def _new_broker(config) -> PaperBroker:
    return PaperBroker(
        starting_balance=config.position_sizing.starting_balance,
        slippage_ticks=config.fill_slippage_ticks,
        pessimistic_both_hit=config.fill_pessimistic_both_hit,
        breakeven_at_1r=config.breakeven_at_1r,
        runner_mode=config.runner_mode,
        runner_activation_r=config.runner_activation_r,
        runner_trail_r=config.runner_trail_r,
        entry_fill_model=config.entry_fill_model,
        entry_tolerance_ticks_by_root=config.entry_tolerance_ticks_by_root,
    )


def _fixed_inverse(
    config,
    captures: dict[str, dict],
    original_rows: list[dict],
    bars: dict[str, list],
    indexes: dict[str, dict[str, int]],
) -> list[dict]:
    by_id = {row["paper_order_id"]: row for row in original_rows}
    if set(captures) != set(by_id):
        raise RuntimeError(
            f"ORB capture mismatch: captures={len(captures)} rows={len(by_id)}"
        )
    output = []
    for order_id, captured in captures.items():
        source = by_id[order_id]
        original_order = BracketOrder(**captured["order"])
        inverse = _mirror(original_order)
        broker = _new_broker(config)
        fill = broker.execute_bracket(
            inverse,
            market_price=captured["market_price"],
            paper_order_id=order_id,
        )
        same_bar_ambiguities = 0
        if fill.result != "CANCELLED":
            start = indexes[inverse.instrument].get(source["bar_ts"])
            if start is None:
                raise RuntimeError(f"decision bar absent: {source['bar_ts']}")
            for bar in bars[inverse.instrument][start + 1 :]:
                position = broker.get_position()
                if position is not None:
                    both = (
                        bar.low <= position.stop and bar.high >= position.target
                        if position.direction == "LONG"
                        else bar.high >= position.stop and bar.low <= position.target
                    )
                    same_bar_ambiguities += int(both)
                resolved = broker.resolve_position(
                    NextBarOHLC(open=bar.open, high=bar.high, low=bar.low)
                )
                if resolved is not None:
                    fill = resolved
                    break

        resolved = fill.result in {"WIN", "LOSS", "BREAKEVEN"}
        gross = float(fill.pnl_dollars or 0.0) if resolved else 0.0
        tick = 0.25
        limit_px = (
            inverse.entry + MARKETABLE_TICKS[inverse.instrument] * tick
            if inverse.direction == "LONG"
            else inverse.entry - MARKETABLE_TICKS[inverse.instrument] * tick
        )
        if fill.result != "CANCELLED" and fill.entry_price is not None:
            if inverse.direction == "LONG" and fill.entry_price > limit_px + 1e-9:
                raise RuntimeError("LONG fill exceeded marketable-limit cap")
            if inverse.direction == "SHORT" and fill.entry_price < limit_px - 1e-9:
                raise RuntimeError("SHORT fill exceeded marketable-limit cap")
        output.append(
            {
                "date": source["date"],
                "bar_ts": source["bar_ts"],
                "instrument": source["instrument"],
                "strategy": STRATEGY,
                "session": source["session"],
                "half": source["half"],
                "paper_order_id": order_id,
                "original_direction": original_order.direction,
                "direction": inverse.direction,
                "attempted": 1,
                "filled": int(fill.result != "CANCELLED"),
                "cancelled_no_fill": int(fill.result == "CANCELLED"),
                "resolved": int(resolved),
                "open": int(fill.result in {"OPEN", "PENDING"}),
                "result": fill.result if resolved else None,
                "exit_reason": fill.exit_reason,
                "entry_price": fill.entry_price,
                "planned_entry": inverse.entry,
                "limit_price": limit_px,
                "stop": inverse.stop,
                "target": inverse.target,
                "pnl_before_commission": gross,
                "pnl_after_commission": (
                    gross - COMMISSION_ROUND_TRIP if resolved else 0.0
                ),
                "same_bar_ambiguities": same_bar_ambiguities,
                "slippage_ticks": config.fill_slippage_ticks,
            }
        )
    return sorted(output, key=lambda row: (row["date"], row["bar_ts"], row["instrument"]))


def _equity_path(rows: list[dict]) -> dict:
    resolved = [row for row in rows if row["resolved"]]
    equity = peak = 0.0
    peak_index = 0
    peak_day = resolved[0]["date"] if resolved else None
    max_dd = 0.0
    dd_peak = dd_trough = None
    streak = longest = 0
    max_recovery_obs = max_recovery_days = 0
    underwater_start_index = 0
    underwater_start_day = peak_day
    underwater = False
    completed = 0
    for index, row in enumerate(resolved):
        pnl = float(row["pnl_after_commission"])
        if pnl < 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
        equity += pnl
        if equity >= peak:
            if underwater:
                completed += 1
                max_recovery_obs = max(max_recovery_obs, index - underwater_start_index)
                max_recovery_days = max(
                    max_recovery_days,
                    (date.fromisoformat(row["date"]) - date.fromisoformat(underwater_start_day)).days,
                )
            peak = equity
            peak_index = index
            peak_day = row["date"]
            underwater_start_index = index
            underwater_start_day = row["date"]
            underwater = False
        else:
            underwater = True
            drawdown = peak - equity
            if drawdown > max_dd:
                max_dd = drawdown
                dd_peak = resolved[peak_index]["date"]
                dd_trough = row["date"]
    terminal_obs = terminal_days = 0
    if underwater and resolved:
        terminal_obs = len(resolved) - 1 - underwater_start_index
        terminal_days = (
            date.fromisoformat(resolved[-1]["date"])
            - date.fromisoformat(underwater_start_day)
        ).days
        max_recovery_obs = max(max_recovery_obs, terminal_obs)
        max_recovery_days = max(max_recovery_days, terminal_days)
    return {
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_peak_date": dd_peak,
        "max_drawdown_trough_date": dd_trough,
        "longest_losing_streak": longest,
        "max_recovery_observations": max_recovery_obs,
        "max_recovery_calendar_days": max_recovery_days,
        "terminal_drawdown_unrecovered": underwater,
        "terminal_underwater_observations": terminal_obs,
        "terminal_underwater_calendar_days": terminal_days,
        "completed_recovery_episodes": completed,
    }


def _metrics(rows: list[dict]) -> dict:
    resolved = [row for row in rows if row["resolved"]]
    pnls = [float(row["pnl_after_commission"]) for row in resolved]
    gross = [float(row["pnl_before_commission"]) for row in resolved]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    return {
        "attempts": len(rows),
        "fills": sum(int(row["filled"]) for row in rows),
        "resolved": len(resolved),
        "open": sum(int(row["open"]) for row in rows),
        "cancelled_no_fill": sum(int(row["cancelled_no_fill"]) for row in rows),
        "gross": round(sum(gross), 2),
        "net": round(sum(pnls), 2),
        "expectancy": round(statistics.fmean(pnls), 4) if pnls else None,
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 6)
            if losses
            else math.inf if wins else None
        ),
        "win_rate": round(len(wins) / len(resolved), 6) if resolved else None,
        "wins": len(wins),
        "losses": len(losses),
        **_equity_path(rows),
    }


def _group(rows: list[dict], field: str) -> dict:
    values = sorted({str(row[field]) for row in rows})
    return {
        value: _metrics([row for row in rows if str(row[field]) == value])
        for value in values
    }


def _chronological_quarters(rows: list[dict]) -> dict:
    resolved = [row for row in rows if row["resolved"]]
    output = {}
    for index in range(4):
        lane = resolved[index * len(resolved) // 4 : (index + 1) * len(resolved) // 4]
        output[f"P{index + 1}"] = {
            **_metrics(lane),
            "first_date": lane[0]["date"] if lane else None,
            "last_date": lane[-1]["date"] if lane else None,
        }
    return output


def _rolling_months(rows: list[dict], months: int) -> dict:
    resolved = [row for row in rows if row["resolved"]]
    if not resolved:
        return {}
    first = date.fromisoformat(resolved[0]["date"])
    last = date.fromisoformat(resolved[-1]["date"])
    first_index = first.year * 12 + first.month - 1
    last_index = last.year * 12 + last.month - 1
    output = {}
    for end_index in range(first_index + months - 1, last_index + 1):
        start_index = end_index - months + 1
        lane = [
            row
            for row in resolved
            if start_index
            <= (
                date.fromisoformat(row["date"]).year * 12
                + date.fromisoformat(row["date"]).month
                - 1
            )
            <= end_index
        ]
        year, month_zero = divmod(end_index, 12)
        output[f"{year:04d}-{month_zero + 1:02d}"] = _metrics(lane)
    return output


def _concentration(rows: list[dict]) -> dict:
    pnls = [float(row["pnl_after_commission"]) for row in rows if row["resolved"]]
    winners = sorted((value for value in pnls if value > 0), reverse=True)
    total = sum(pnls)
    output = {}
    for count in (1, 5, 10):
        contribution = sum(winners[:count])
        output[f"top_{count}_winner_contribution"] = round(contribution, 2)
        output[f"net_without_top_{count}"] = round(total - contribution, 2)
        output[f"top_{count}_pct_of_winner_dollars"] = (
            round(contribution / sum(winners), 6) if winners else None
        )
    return output


def _robustness(rows: list[dict]) -> dict:
    resolved = [row for row in rows if row["resolved"]]
    latest = resolved[-max(1, math.ceil(len(resolved) * 0.25)) :] if resolved else []
    years = defaultdict(list)
    for row in rows:
        years[row["date"][:4]].append(row)
    return {
        "overall": _metrics(rows),
        "half": _group(rows, "half"),
        "instrument": _group(rows, "instrument"),
        "session": _group(rows, "session"),
        "direction": _group(rows, "direction"),
        "year": {key: _metrics(lane) for key, lane in sorted(years.items())},
        "chronological_quarters": _chronological_quarters(rows),
        "latest_25pct": {
            **_metrics(latest),
            "first_date": latest[0]["date"] if latest else None,
            "last_date": latest[-1]["date"] if latest else None,
        },
        "rolling_3_month": _rolling_months(rows, 3),
        "rolling_6_month": _rolling_months(rows, 6),
        "concentration": _concentration(rows),
    }


def _identity_set(rows: list[dict]) -> set[tuple]:
    identities = [_stable_identity(row) for row in rows]
    if len(set(identities)) != len(identities):
        duplicates = [item for item, count in Counter(identities).items() if count > 1]
        raise RuntimeError(f"duplicate stable identities: {duplicates[:3]}")
    return set(identities)


def _attribution(original: list[dict], inverse: list[dict]) -> dict:
    original_by = {_stable_identity(row): row for row in original}
    inverse_by = {_stable_identity(row): row for row in inverse}
    if set(original_by) != set(inverse_by):
        raise RuntimeError("fixed-population attribution identity mismatch")
    common = []
    original_only = []
    inverse_only = []
    for identity in sorted(original_by):
        left, right = original_by[identity], inverse_by[identity]
        if left["resolved"] and right["resolved"]:
            common.append((left, right))
        elif left["resolved"] and not right["resolved"]:
            original_only.append(left)
        elif right["resolved"] and not left["resolved"]:
            inverse_only.append(right)
    directional_delta = sum(
        right["pnl_after_commission"] - left["pnl_after_commission"]
        for left, right in common
    )
    fill_selection_delta = (
        sum(row["pnl_after_commission"] for row in inverse_only)
        - sum(row["pnl_after_commission"] for row in original_only)
    )
    return {
        "common_resolved_attempts": len(common),
        "original_only_resolved_attempts": len(original_only),
        "inverse_only_resolved_attempts": len(inverse_only),
        "directional_effect_common_resolved_net_delta": round(directional_delta, 2),
        "fill_selection_net_delta": round(fill_selection_delta, 2),
        "reconciled_total_fixed_net_delta": round(
            directional_delta + fill_selection_delta, 2
        ),
        "actual_total_fixed_net_delta": round(
            _metrics(inverse)["net"] - _metrics(original)["net"], 2
        ),
    }


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def run(args) -> dict:
    files, digest = _tree_sha256(args.corpus)
    if (files, digest) != (626, CORPUS_SHA256):
        raise RuntimeError(f"corpus mismatch: files={files} digest={digest}")
    if _sha256(args.source_raw) != SOURCE_RAW_SHA256:
        raise RuntimeError("source #358 raw artifact hash mismatch")

    bars, indexes = _load_bars(args.corpus)
    source_manifest = _source_identities(args.source_raw, bars)
    source_identity_set = {_stable_identity(row) for row in source_manifest}
    base = load_config()
    if not base.fill_pessimistic_both_hit:
        raise RuntimeError("pessimistic same-bar handling is not enabled")
    if base.fill_slippage_ticks != 1.0:
        raise RuntimeError("frozen baseline slippage is not one tick")
    if MARKETABLE_TICKS != {"MES": 8.0, "MNQ": 8.0}:
        raise RuntimeError(f"marketable ticks changed: {MARKETABLE_TICKS}")

    baseline_config = _marketable_config(base, slippage_ticks=1)
    captures: dict[str, dict] = {}
    original_logs = args.logs / "original_baseline"
    _run_system(
        baseline_config,
        args.corpus,
        original_logs,
        invert_orb=False,
        captures=captures,
    )
    original_all, original_halts, original_risks = _parse_system(
        original_logs, inverted_orb=False
    )
    original_orb = [row for row in original_all if row["strategy"] == STRATEGY]
    original_identity_set = _identity_set(original_orb)
    if original_identity_set != source_identity_set:
        raise RuntimeError(
            f"source attempt mismatch: missing={len(source_identity_set-original_identity_set)} "
            f"extra={len(original_identity_set-source_identity_set)}"
        )
    published = json.loads(
        (REPO / "scripts/execution_mode_corpus_comparison_results.json").read_text()
    )["arms"]["marketable_limit"]["overall"]
    original_system_metrics = _metrics(original_all)
    reconciliation = {
        "attempts": [original_system_metrics["attempts"], published["attempts"]],
        "fills": [original_system_metrics["fills"], published["fills"]],
        "resolved": [original_system_metrics["resolved"], published["resolved"]],
        "net": [original_system_metrics["net"], published["net_after_commission"]],
    }
    if any(actual != expected for actual, expected in reconciliation.values()):
        raise RuntimeError(f"#358 baseline reconciliation failed: {reconciliation}")

    captured_orb = {
        order_id: captured
        for order_id, captured in captures.items()
        if captured["order"]["strategy"] == STRATEGY
    }
    if len(captured_orb) != SOURCE_ATTEMPTS:
        raise RuntimeError(f"expected 111 ORB captures, got {len(captured_orb)}")

    fixed_by_tier = {}
    fixed_rows_by_tier = {}
    system_by_tier = {}
    system_rows_by_tier = {}
    system_halts_by_tier = {}
    for ticks in TOTAL_SLIPPAGE_TIERS:
        config = _marketable_config(base, slippage_ticks=ticks)
        fixed_rows = _fixed_inverse(
            config, captured_orb, original_orb, bars, indexes
        )
        fixed_rows_by_tier[ticks] = fixed_rows
        fixed_by_tier[ticks] = _robustness(fixed_rows)

        inverse_logs = args.logs / f"system_inverse_{ticks}t"
        _run_system(
            config,
            args.corpus,
            inverse_logs,
            invert_orb=True,
        )
        system_all, halts, _risks = _parse_system(
            inverse_logs, inverted_orb=True
        )
        system_orb = [row for row in system_all if row["strategy"] == STRATEGY]
        system_rows_by_tier[ticks] = system_orb
        system_by_tier[ticks] = _robustness(system_orb)
        system_halts_by_tier[ticks] = halts

    fixed_baseline = fixed_rows_by_tier[1]
    system_baseline = system_rows_by_tier[1]
    system_identity_set = _identity_set(system_baseline)
    retained = source_identity_set & system_identity_set
    removed = source_identity_set - system_identity_set
    added = system_identity_set - source_identity_set

    attribution = _attribution(original_orb, fixed_baseline)
    if attribution["reconciled_total_fixed_net_delta"] != attribution["actual_total_fixed_net_delta"]:
        raise RuntimeError(f"attribution failed to reconcile: {attribution}")

    causality = {
        "base_sha_is_exact_358": BASE_SHA
        == "74b14071822be46de46be3c2db0eff7c95b8fced",
        "corpus_hash_matches": digest == CORPUS_SHA256,
        "source_identity_matches": original_identity_set == source_identity_set,
        "signal_precedes_resolution": True,
        "resolution_begins_next_bar": True,
        "marketable_caps_enforced": True,
        "pessimistic_same_bar_enabled": base.fill_pessimistic_both_hit,
        "one_contract_invariant": all(
            int(captured["order"].get("contracts") or 1) == 1
            for captured in captured_orb.values()
        ),
        "commission_included": COMMISSION_ROUND_TRIP == 1.48,
        "adverse_slippage_included": base.fill_slippage_ticks == 1.0,
        "roll_corpus_frozen": True,
    }
    if not all(causality.values()):
        raise RuntimeError(f"causality invariant failed: {causality}")

    results = {
        "study": "ORB Breakout marketable-limit inverse",
        "generated_at": datetime.now().astimezone().isoformat(),
        "preregistration_sha": PREREGISTRATION_SHA,
        "base_sha": BASE_SHA,
        "source": {
            "corpus_files": files,
            "corpus_tree_sha256": digest,
            "source_raw_sha256": SOURCE_RAW_SHA256,
            "attempt_identity_sha256": SOURCE_IDENTITY_SHA256,
            "attempts": len(source_manifest),
            "manifest": source_manifest,
        },
        "original_baseline_reconciliation": reconciliation,
        "original_orb_breakout": _robustness(original_orb),
        "fixed_population_inverse": fixed_by_tier[1],
        "system_path_inverse": system_by_tier[1],
        "cost_sensitivity": {
            "fixed_population": {
                "baseline_1t": fixed_by_tier[1]["overall"],
                "+1_tick_total_2t": fixed_by_tier[2]["overall"],
                "+2_ticks_total_3t": fixed_by_tier[3]["overall"],
                "+3_ticks_total_4t": fixed_by_tier[4]["overall"],
                "+4_ticks_total_5t": fixed_by_tier[5]["overall"],
            },
            "system_path": {
                "baseline_1t": system_by_tier[1]["overall"],
                "+1_tick_total_2t": system_by_tier[2]["overall"],
                "+2_ticks_total_3t": system_by_tier[3]["overall"],
                "+3_ticks_total_4t": system_by_tier[4]["overall"],
                "+4_ticks_total_5t": system_by_tier[5]["overall"],
            },
        },
        "original_vs_inverse_attribution": attribution,
        "breaker_path_effects": {
            "original_system_breakers": original_halts,
            "inverse_system_breakers_by_tier": system_halts_by_tier,
            "baseline_retained_attempts": len(retained),
            "baseline_removed_attempts": len(removed),
            "baseline_added_attempts": len(added),
            "retained_identities": sorted(retained),
            "removed_identities": sorted(removed),
            "added_identities": sorted(added),
            "fixed_inverse_net": _metrics(fixed_baseline)["net"],
            "system_inverse_net": _metrics(system_baseline)["net"],
            "non_additive_system_minus_fixed_net": round(
                _metrics(system_baseline)["net"] - _metrics(fixed_baseline)["net"], 2
            ),
            "original_risk_rejections": dict(original_risks.most_common()),
        },
        "causality_fill_realism": causality,
        "same_bar_ambiguities_fixed_baseline": sum(
            row["same_bar_ambiguities"] for row in fixed_baseline
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True, default=str) + "\n")
    _write_jsonl(args.fixed_raw, fixed_baseline)
    _write_jsonl(args.system_raw, system_baseline)
    print(
        json.dumps(
            {
                "preregistration_sha": PREREGISTRATION_SHA,
                "original": _metrics(original_orb),
                "fixed_inverse": _metrics(fixed_baseline),
                "system_inverse": _metrics(system_baseline),
                "path": {
                    "retained": len(retained),
                    "removed": len(removed),
                    "added": len(added),
                },
                "causality": causality,
            },
            indent=2,
            default=str,
        )
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--source-raw", required=True, type=Path)
    parser.add_argument("--logs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--fixed-raw", required=True, type=Path)
    parser.add_argument("--system-raw", required=True, type=Path)
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
