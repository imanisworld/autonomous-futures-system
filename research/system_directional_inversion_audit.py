#!/usr/bin/env python3
"""Frozen #346/#358 directional-inversion audit.

Run this module from a checkout of commit 74b1407.  It does not mutate runtime
configuration: all execution changes are process-scoped PaperBroker wrappers
and every output path is supplied explicitly.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime
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
    QUARTERS,
    _group,
    _json_lines,
    _parse_logs,
    _period_label,
    _stats,
    _tree_sha256,
)
from scripts.execution_mode_corpus_comparison import MARKETABLE_TICKS  # noqa: E402

CORPUS_SHA256 = "4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4"
FROZEN_SHA = "74b1407"
MODES = ("ioc_limit", "market", "marketable_limit", "stop_market")


def _mirror(order: BracketOrder) -> BracketOrder:
    stop_distance = abs(float(order.entry) - float(order.stop))
    target_distance = abs(float(order.target) - float(order.entry))
    direction = "SHORT" if order.direction == "LONG" else "LONG"
    stop = order.entry + stop_distance if direction == "SHORT" else order.entry - stop_distance
    target = order.entry - target_distance if direction == "SHORT" else order.entry + target_distance
    return replace(order, direction=direction, stop=stop, target=target)


def _configs(base) -> dict[str, Any]:
    return {
        "ioc_limit": replace(base, entry_fill_model="ioc_limit"),
        "market": replace(base, entry_fill_model="market"),
        "marketable_limit": replace(
            base,
            entry_fill_model="ioc_limit",
            entry_tolerance_ticks_by_root=dict(MARKETABLE_TICKS),
        ),
        "stop_market": replace(base, entry_fill_model="stop_market"),
    }


@contextmanager
def _execution_patch(
    mode: str,
    *,
    invert: bool,
    captures: dict[str, dict] | None = None,
):
    original = PaperBroker.execute_bracket

    def patched(self, order, market_price=None, *, paper_order_id=None):
        submitted = _mirror(order) if invert else replace(order)
        if mode == "market":
            submitted.force_market_entry = True
        if captures is not None:
            captures[str(paper_order_id)] = {
                "paper_order_id": paper_order_id,
                "market_price": market_price,
                "order": asdict(submitted),
            }
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


def _run_mode(mode: str, config, corpus: Path, logs: Path, *, invert: bool, captures=None):
    with _execution_patch(mode, invert=invert, captures=captures):
        for instrument in INSTRUMENTS:
            files = sorted((corpus / instrument).glob("*.jsonl"))
            if len(files) != 313:
                raise RuntimeError(f"{instrument}: expected 313 files, got {len(files)}")
            engine = ReplayEngine(config=config, log_dir=str(logs / mode / instrument))
            for index, path in enumerate(files, 1):
                engine.run(path, review_date=path.stem.rsplit("_", 1)[-1])
                if index % 100 == 0 or index == len(files):
                    print(f"[{mode}:{instrument}] {index}/{len(files)}", flush=True)


def _session_map(logs: Path) -> dict[str, str]:
    found: dict[str, str] = {}
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


def _analyze(logs: Path, *, inverted_directions: bool) -> tuple[dict, list[dict]]:
    trades, candidates, gates, risks, halt = _parse_logs(logs)
    sessions = _session_map(logs)
    for row in trades:
        row["session"] = sessions.get(row["paper_order_id"], "UNKNOWN")
        if inverted_directions:
            row["direction"] = "SHORT" if row["direction"] == "LONG" else "LONG"
        row["half"] = _period_label(row["date"], HALVES)
        row["quarter"] = _period_label(row["date"], QUARTERS)
    for row in candidates:
        row["half"] = _period_label(row["date"], HALVES)
        row["quarter"] = _period_label(row["date"], QUARTERS)
    block = {
        "overall": _stats(trades, candidates),
        "breakdowns": {
            "instrument": _group(trades, candidates, "instrument", INSTRUMENTS),
            "strategy": _group(trades, candidates, "strategy"),
            "half": _group(trades, candidates, "half", HALVES),
            "quarter": _group(trades, candidates, "quarter", QUARTERS),
            "direction": _group(trades, candidates, "direction", ("LONG", "SHORT")),
            "session": {
                key: _stats(
                    [row for row in trades if row["session"] == key],
                    [],
                )
                for key in sorted({row["session"] for row in trades})
            },
        },
        "risk_rejections_by_rule": dict(risks.most_common()),
        "drawdown_breaker_audit": halt,
        "strategy_detail": _strategy_detail(trades),
    }
    return block, trades


def _load_bars(corpus: Path) -> tuple[dict[str, list], dict[str, dict[str, int]]]:
    all_bars: dict[str, list] = {}
    indexes: dict[str, dict[str, int]] = {}
    loader = ReplayCandleLoader()
    for instrument in INSTRUMENTS:
        bars = []
        for path in sorted((corpus / instrument).glob("*.jsonl")):
            bars.extend(loader.load_jsonl(path))
        bars.sort(key=lambda row: row.timestamp)
        seen: dict[str, Any] = {}
        for bar in bars:
            seen[str(bar.timestamp)] = bar
        unique = sorted(seen.values(), key=lambda row: row.timestamp)
        all_bars[instrument] = unique
        indexes[instrument] = {str(bar.timestamp): i for i, bar in enumerate(unique)}
    return all_bars, indexes


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


def _independent_inverse(
    mode: str,
    config,
    captures: dict[str, dict],
    original_rows: list[dict],
    bars: dict[str, list],
    indexes: dict[str, dict[str, int]],
) -> list[dict]:
    by_id = {row["paper_order_id"]: row for row in original_rows}
    if set(captures) != set(by_id):
        missing = sorted(set(by_id) - set(captures))
        extra = sorted(set(captures) - set(by_id))
        raise RuntimeError(f"{mode}: capture mismatch missing={missing[:3]} extra={extra[:3]}")
    output: list[dict] = []
    for order_id, captured in captures.items():
        source = by_id[order_id]
        order = BracketOrder(**captured["order"])
        inverse = _mirror(order)
        if mode == "market":
            inverse.force_market_entry = True
        broker = _new_broker(config)
        fill = broker.execute_bracket(
            inverse,
            market_price=captured["market_price"],
            paper_order_id=order_id,
        )
        ambiguity = 0
        if fill.result not in {"CANCELLED"}:
            instrument_bars = bars[inverse.instrument]
            try:
                start = indexes[inverse.instrument][source["bar_ts"]]
            except KeyError as exc:
                raise RuntimeError(f"decision bar not found: {source['bar_ts']}") from exc
            for bar in instrument_bars[start + 1 :]:
                position = broker.get_position()
                if position is not None:
                    both = (
                        bar.low <= position.stop and bar.high >= position.target
                        if position.direction == "LONG"
                        else bar.high >= position.stop and bar.low <= position.target
                    )
                    ambiguity += int(both)
                resolved = broker.resolve_position(
                    NextBarOHLC(open=bar.open, high=bar.high, low=bar.low)
                )
                if resolved is not None:
                    fill = resolved
                    break
            else:
                if broker.has_pending_entry():
                    fill = broker.cancel_pending_entry("ENTRY_NO_NEXT_BAR")
        resolved = fill.result in {"WIN", "LOSS", "BREAKEVEN"}
        gross = float(fill.pnl_dollars or 0.0) if resolved else 0.0
        output.append(
            {
                **{k: source[k] for k in (
                    "date", "bar_ts", "instrument", "strategy", "half", "quarter", "session"
                )},
                "arm": mode,
                "paper_order_id": order_id,
                "direction": inverse.direction,
                "attempted": 1,
                "filled": int(fill.result != "CANCELLED"),
                "cancelled_no_fill": int(fill.result == "CANCELLED"),
                "resolved": int(resolved),
                "open": int(fill.result in {"OPEN", "PENDING"}),
                "result": fill.result if resolved else None,
                "exit_reason": fill.exit_reason,
                "pnl_before_commission": gross,
                "pnl_after_commission": gross - COMMISSION_ROUND_TRIP if resolved else 0.0,
                "same_bar_ambiguities": ambiguity,
                "original_direction": order.direction,
                "planned_entry": order.entry,
                "inverse_stop": inverse.stop,
                "inverse_target": inverse.target,
            }
        )
    return sorted(output, key=lambda x: (x["date"], x["bar_ts"], x["instrument"]))


def _stats_for_rows(rows: list[dict]) -> dict:
    # Candidate counts are intentionally empty: this is an approved-attempt
    # population, not a detector-funnel rerun.
    return _stats(rows, [])


def _strategy_detail(rows: list[dict]) -> dict:
    output = {}
    for strategy in sorted({row["strategy"] for row in rows}):
        lane = [row for row in rows if row["strategy"] == strategy]
        output[strategy] = {"overall": _stats_for_rows(lane)}
        for field in ("instrument", "half", "quarter", "direction", "session"):
            output[strategy][field] = {
                key: _stats_for_rows(
                    [row for row in lane if str(row[field]) == key]
                )
                for key in sorted({str(row[field]) for row in lane})
            }
    return output


def _breakdowns(rows: list[dict]) -> dict:
    return {
        "overall": _stats_for_rows(rows),
        "breakdowns": {
            field: {
                key: _stats_for_rows([row for row in rows if str(row[field]) == key])
                for key in sorted({str(row[field]) for row in rows})
            }
            for field in ("instrument", "strategy", "half", "quarter", "direction", "session")
        },
        "same_bar_ambiguities": sum(row["same_bar_ambiguities"] for row in rows),
        "strategy_detail": _strategy_detail(rows),
    }


def _lane_metrics(rows: list[dict]) -> dict:
    pnls = [row["net_pnl"] for row in rows]
    gross = [row["gross_pnl"] for row in rows]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]
    equity = peak = max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(rows),
        "gross": round(sum(gross), 2),
        "net": round(sum(pnls), 2),
        "expectancy": round(statistics.fmean(pnls), 4) if pnls else None,
        "profit_factor": round(sum(winners) / abs(sum(losers)), 6) if losers else math.inf,
        "win_rate": round(len(winners) / len(rows), 6) if rows else None,
        "max_drawdown": round(max_dd, 2),
    }


def _lane_b(path: Path) -> dict:
    original = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    result: dict[str, Any] = {"original": {}, "inverse": {}, "cost_sensitivity": {}}
    for ticks in (1, 2, 3, 4):
        inverse = []
        for row in original:
            signed = -1.0 if row["direction"] == "LONG" else 1.0
            gross = signed * (row["raw_exit"] - row["raw_entry"]) * 2.0
            cost = ticks * 0.25 * 2.0 * 2.0
            inverse.append(
                {
                    **row,
                    "direction": "SHORT" if row["direction"] == "LONG" else "LONG",
                    "gross_pnl": gross,
                    "net_pnl": gross - cost - 1.48,
                }
            )
        result["cost_sensitivity"][f"{ticks}_ticks"] = _lane_metrics(inverse)
        if ticks == 1:
            inv = inverse
    midpoint = len(original) // 2
    split = int(len(original) * .75)
    quarters = lambda rs: {
        f"P{i+1}": _lane_metrics(rs[i * len(rs) // 4 : (i + 1) * len(rs) // 4])
        for i in range(4)
    }
    for label, rows in (("original", original), ("inverse", inv)):
        result[label] = {
            "overall": _lane_metrics(rows),
            "half": {"H1": _lane_metrics(rows[:midpoint]), "H2": _lane_metrics(rows[midpoint:])},
            "direction": {
                d: _lane_metrics([r for r in rows if r["direction"] == d])
                for d in ("LONG", "SHORT")
            },
            "periods": quarters(rows),
            "holdout": _lane_metrics(rows[split:]),
        }
        winners = sorted((r["net_pnl"] for r in rows if r["net_pnl"] > 0), reverse=True)
        total = sum(r["net_pnl"] for r in rows)
        result[label]["concentration"] = {
            "top_1": round(winners[0], 2),
            "top_5": round(sum(winners[:5]), 2),
            "net_without_top_5": round(total - sum(winners[:5]), 2),
        }
    result["gross_reconciliation_error"] = round(
        result["inverse"]["overall"]["gross"] + result["original"]["overall"]["gross"], 10
    )
    return result


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--logs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--lane-trades", required=True, type=Path)
    args = parser.parse_args()
    files, digest = _tree_sha256(args.corpus)
    if (files, digest) != (626, CORPUS_SHA256):
        raise RuntimeError(f"corpus mismatch: {files}, {digest}")
    base = load_config()
    configs = _configs(base)
    bars, indexes = _load_bars(args.corpus)
    original: dict[str, Any] = {}
    trade_inverse: dict[str, Any] = {}
    system_inverse: dict[str, Any] = {}
    all_raw: list[dict] = []
    for mode in MODES:
        captures: dict[str, dict] = {}
        original_logs = args.logs / "original"
        _run_mode(mode, configs[mode], args.corpus, original_logs, invert=False, captures=captures)
        original[mode], original_rows = _analyze(original_logs / mode, inverted_directions=False)
        inverse_rows = _independent_inverse(
            mode, configs[mode], captures, original_rows, bars, indexes
        )
        trade_inverse[mode] = _breakdowns(inverse_rows)
        all_raw.extend(inverse_rows)

        inverse_logs = args.logs / "system_inverse"
        _run_mode(mode, configs[mode], args.corpus, inverse_logs, invert=True)
        system_inverse[mode], _ = _analyze(
            inverse_logs / mode, inverted_directions=True
        )

    published = json.loads(
        (REPO / "scripts/execution_mode_corpus_comparison_results.json").read_text()
    )["arms"]
    reconciliation = {}
    for mode in MODES:
        got = original[mode]["overall"]
        expected = published[mode]["overall"]
        reconciliation[mode] = {
            key: [got[key], expected[key]]
            for key in ("attempts", "fills", "resolved", "net_after_commission")
        }
        if any(pair[0] != pair[1] for pair in reconciliation[mode].values()):
            raise RuntimeError(f"{mode}: original rerun does not reconcile: {reconciliation[mode]}")

    results = {
        "meta": {
            "frozen_sha": FROZEN_SHA,
            "corpus_files": files,
            "corpus_sha256": digest,
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "generated_at": datetime.now().astimezone().isoformat(),
            "stop_limit": "excluded: absent from frozen PaperBroker",
        },
        "original_reconciliation": reconciliation,
        "original": original,
        "trade_level_inverse": trade_inverse,
        "system_path_inverse": system_inverse,
        "lane_b": _lane_b(args.lane_trades),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    _write_jsonl(args.raw, all_raw)
    print(json.dumps({
        mode: {
            "original": original[mode]["overall"]["net_after_commission"],
            "trade_inverse": trade_inverse[mode]["overall"]["net_after_commission"],
            "system_inverse": system_inverse[mode]["overall"]["net_after_commission"],
        }
        for mode in MODES
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
