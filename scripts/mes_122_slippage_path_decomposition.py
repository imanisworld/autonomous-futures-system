#!/usr/bin/env python3
"""Decompose the MES strat_122 33->21 slippage trade-count collapse.

Evidence only. No runtime/config/deployment changes.

This study answers two questions:
1. In the existing combined [strat_212, strat_122] replay, what exact
   journal/risk state causes later 1-2-2 trades to disappear at 2/3 ticks?
2. Does the same disappearance occur when strat_122 is replayed alone under
   identical fixed-one-contract, realistic-fill assumptions?
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.mes_122_controlled_one_variable_tests import (  # noqa: E402
    STRATEGY,
    _fixed_one_contract_config,
    _json_lines,
    _metric_block,
    _run_pass,
    _variant_advance,
)

SLIPS = (1.0, 2.0, 3.0)


def _resolved_rows_for_strategy(run: dict[str, Any], strategy: str) -> list[dict]:
    rows: list[dict] = []
    for bar_ts, entry in sorted(run["decisions"].items()):
        if entry.get("decision") != "TRADE":
            continue
        setup = entry.get("setup") or {}
        if setup.get("strategy") != strategy:
            continue
        order_id = entry.get("paper_order_id")
        outcome = run["outcomes"].get(str(order_id)) if order_id else None
        if not outcome or outcome.get("result") not in {"WIN", "LOSS", "BREAKEVEN"}:
            continue
        rows.append(
            {
                "bar_ts": bar_ts,
                "date": bar_ts[:10],
                "pnl_dollars": float(outcome.get("pnl_dollars") or 0.0),
                "result": outcome.get("result"),
                "paper_order_id": order_id,
            }
        )
    return rows


def _trade_timestamps(run: dict[str, Any], strategy: str) -> list[str]:
    out = []
    for bar_ts, entry in sorted(run["decisions"].items()):
        if entry.get("decision") != "TRADE":
            continue
        if (entry.get("setup") or {}).get("strategy") == strategy:
            out.append(bar_ts)
    return out


def _journal_decisions(log_dir: Path) -> dict[str, list[dict]]:
    by_ts: dict[str, list[dict]] = {}
    for path in sorted(log_dir.glob("journal_*.jsonl")):
        for row in _json_lines(path):
            bar_ts = row.get("bar_ts")
            if not bar_ts:
                continue
            by_ts.setdefault(str(bar_ts), []).append(row)
    return by_ts


def _risk_projection(rows: list[dict]) -> list[dict]:
    projected = []
    for row in rows:
        risk = row.get("risk") or {}
        setup = row.get("setup") or {}
        projected.append(
            {
                "decision": row.get("decision"),
                "strategy": setup.get("strategy"),
                "direction": setup.get("direction"),
                "failed_rule": risk.get("failed_rule"),
                "reason": risk.get("reason"),
                "failed_gates": row.get("failed_gates"),
                "paper_order_id": row.get("paper_order_id"),
            }
        )
    return projected


def _risk_rejection_counts(journal_rows: dict[str, list[dict]], *, after_ts: str | None = None) -> dict:
    failed = Counter()
    reasons = Counter()
    for bar_ts, rows in journal_rows.items():
        if after_ts and bar_ts < after_ts:
            continue
        for row in rows:
            if row.get("decision") != "RISK_REJECTED":
                continue
            risk = row.get("risk") or {}
            failed[str(risk.get("failed_rule"))] += 1
            reasons[str(risk.get("reason"))] += 1
    return {
        "failed_rule_counts": dict(failed.most_common()),
        "top_reasons": dict(reasons.most_common(10)),
    }


def _carry_summary(run: dict[str, Any]) -> list[dict]:
    out = []
    for item in run.get("carry_history", []):
        positions = item.get("positions") or {}
        simple = {}
        for instrument, pos in positions.items():
            simple[instrument] = {
                "strategy": pos.get("strategy"),
                "direction": pos.get("direction"),
                "entry": pos.get("entry"),
                "stop": pos.get("stop"),
                "target": pos.get("target"),
                "paper_order_id": pos.get("paper_order_id"),
                "journal_date": str(pos.get("journal_date")),
            }
        out.append({"after_date": item.get("after_date"), "positions": simple})
    return out


def _run_family(root: Path, *, pure_122: bool) -> tuple[dict, dict]:
    runs: dict[str, dict] = {}
    journals: dict[str, dict] = {}
    base = _fixed_one_contract_config(isolated=True)
    if pure_122:
        base = dataclasses.replace(base, enabled_concepts=[STRATEGY])

    for slip in SLIPS:
        key = f"slip_{int(slip)}t"
        cfg = dataclasses.replace(base, fill_slippage_ticks=slip)
        log_dir = root / key
        print(f"[{root.name}/{key}] {base.enabled_concepts}", flush=True)
        runs[key] = _run_pass(
            cfg,
            log_dir,
            advance_fn=_variant_advance("baseline"),
        )
        journals[key] = _journal_decisions(log_dir)
    return runs, journals


def _family_report(runs: dict[str, dict], journals: dict[str, dict]) -> dict:
    report: dict[str, Any] = {"slippage": {}, "missing_vs_1t": {}}
    one_ts = _trade_timestamps(runs["slip_1t"], STRATEGY)
    for key in ("slip_1t", "slip_2t", "slip_3t"):
        rows_122 = _resolved_rows_for_strategy(runs[key], STRATEGY)
        rows_212 = _resolved_rows_for_strategy(runs[key], "strat_212")
        report["slippage"][key] = {
            "strat_122": _metric_block(rows_122),
            "strat_212": _metric_block(rows_212),
            "strat_122_trade_count": len(_trade_timestamps(runs[key], STRATEGY)),
            "latest_strat_122_trade_ts": (
                _trade_timestamps(runs[key], STRATEGY)[-1]
                if _trade_timestamps(runs[key], STRATEGY)
                else None
            ),
            "carry_history": _carry_summary(runs[key]),
        }

    for key in ("slip_2t", "slip_3t"):
        current = set(_trade_timestamps(runs[key], STRATEGY))
        missing = [ts for ts in one_ts if ts not in current]
        first_missing = missing[0] if missing else None
        report["missing_vs_1t"][key] = {
            "count": len(missing),
            "timestamps": missing,
            "rows_at_missing_timestamps": {
                ts: _risk_projection(journals[key].get(ts, [])) for ts in missing
            },
            "risk_rejections_from_first_missing_forward": _risk_rejection_counts(
                journals[key], after_ts=first_missing
            ),
        }
    return report


def run(out_path: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="mes122_slippage_decomp_") as tmp:
        root = Path(tmp)
        combined_runs, combined_journals = _run_family(root / "combined_212_122", pure_122=False)
        pure_runs, pure_journals = _run_family(root / "pure_122", pure_122=True)
        result = {
            "design": {
                "contracts": 1,
                "slippage_ticks": list(SLIPS),
                "combined_enabled_concepts": ["strat_212", "strat_122"],
                "pure_enabled_concepts": ["strat_122"],
                "strategy_parameters_changed": False,
                "purpose": "separate cross-strategy risk-path contamination from strat_122 fill sensitivity",
            },
            "combined": _family_report(combined_runs, combined_journals),
            "pure_122": _family_report(pure_runs, pure_journals),
        }

    pure_screen = {}
    for key in ("slip_1t", "slip_2t", "slip_3t"):
        m = result["pure_122"]["slippage"][key]["strat_122"]
        pure_screen[key] = {
            "net_positive": m["commission_adjusted_net"] > 0,
            "pf_above_1": bool(m["commission_adjusted_pf"] and m["commission_adjusted_pf"] > 1),
            "h1_positive": m["h1_commission_adjusted"] > 0,
            "h2_positive": m["h2_commission_adjusted"] > 0,
        }
    result["pure_122"]["screen"] = pure_screen
    result["pure_122"]["robust_through_3t"] = all(all(v.values()) for v in pure_screen.values())

    combined_missing = result["combined"]["missing_vs_1t"]["slip_2t"]["count"]
    pure_missing = result["pure_122"]["missing_vs_1t"]["slip_2t"]["count"]
    result["diagnosis"] = {
        "combined_2t_missing": combined_missing,
        "pure_122_2t_missing": pure_missing,
        "cross_strategy_path_contamination_supported": combined_missing > pure_missing,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")

    console = {
        "combined": {
            key: {
                "122": result["combined"]["slippage"][key]["strat_122"],
                "212": result["combined"]["slippage"][key]["strat_212"],
                "latest_122": result["combined"]["slippage"][key]["latest_strat_122_trade_ts"],
            }
            for key in ("slip_1t", "slip_2t", "slip_3t")
        },
        "combined_missing": result["combined"]["missing_vs_1t"],
        "pure_122": {
            key: result["pure_122"]["slippage"][key]["strat_122"]
            for key in ("slip_1t", "slip_2t", "slip_3t")
        },
        "pure_screen": pure_screen,
        "pure_robust_through_3t": result["pure_122"]["robust_through_3t"],
        "diagnosis": result["diagnosis"],
    }
    print(json.dumps(console, indent=2))
    print(f"wrote {out_path}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "scripts" / "mes_122_slippage_path_decomposition_2026-09-08.json",
    )
    args = parser.parse_args()
    run(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
