#!/usr/bin/env python3
"""Run and report the corrected Corpus v1 IOC evidence pass.

This is evidence orchestration only.  It changes no strategy, replay, broker,
risk, config, deployment, or Pine behavior.  The sole in-memory config change
is ``entry_fill_model="ioc_limit"``; all other values come from the pinned
``risk_rules.yaml`` and canonical ``SystemConfig`` loader.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402

INSTRUMENTS = ("MNQ", "MES")
FULL_RANGE = ("2025-07-24", "2026-07-23")
HALVES = {
    "H1": ("2025-07-24", "2026-01-23"),
    "H2": ("2026-01-24", "2026-07-23"),
}
QUARTERS = {
    "Q1": ("2025-07-24", "2025-10-23"),
    "Q2": ("2025-10-24", "2026-01-23"),
    "Q3": ("2026-01-24", "2026-04-23"),
    "Q4": ("2026-04-24", "2026-07-23"),
}
COMMISSION_ROUND_TRIP = 1.48
OLD_CORPUS = {
    "label": "Superseded Corpus v1 market-fill result",
    "main_sha": "a5434794e471137af83f6e5886b535fb9e3cfcd5",
    "attempts": 747,
    "resolved": 747,
    "fill_rate": 1.0,
    "win_rate": 0.4525,
    "profit_factor": 1.959,
    "net_pnl_reported": 54124.93,
    "expectancy_reported": 72.46,
    "status": "SUPERSEDED / parity-invalid market_condition and market-fill evidence",
    "source": "docs/corpus-v1-clean-baseline-report-2026-07-25.md",
}
PRIOR_IOC = {
    "label": "Prior 622-day IOC-limit static study, breaker disabled",
    "main_sha": "c2188c7",
    "range": ["2024-07-01", "2026-06-26"],
    "MES": {
        "attempts": 1654,
        "fill_rate": 0.372,
        "resolved": 570,
        "win_rate": 0.323,
        "net_pnl_reported": -1550.0,
        "h1_pnl": -1219.0,
        "h2_pnl": -330.0,
    },
    "MNQ": {
        "attempts": 823,
        "fill_rate": 0.363,
        "resolved": 296,
        "win_rate": 0.341,
        "net_pnl_reported": -1523.0,
        "h1_pnl": -467.0,
        "h2_pnl": -1056.0,
    },
    "status": "historical comparator; different corpus/code and breaker-off diagnostic",
    "source": "docs/ioc-faithful-baseline-622d-2026-07-06.md",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_sha256(root: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*.jsonl"))
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return len(paths), digest.hexdigest()


def _coverage_audit(corpus: Path, logs: Path) -> dict:
    missing_reports: list[str] = []
    zero_decision_files: list[str] = []
    journal_count = 0
    report_count = 0
    for instrument in INSTRUMENTS:
        for candle_path in sorted((corpus / instrument).glob("*.jsonl")):
            day = candle_path.stem.rsplit("_", 1)[-1]
            report = logs / instrument / f"replay_report_{day}.md"
            journal = logs / instrument / f"journal_{day}.jsonl"
            if report.exists():
                report_count += 1
            else:
                missing_reports.append(f"{instrument}/{day}")
            if journal.exists():
                journal_count += 1
            else:
                zero_decision_files.append(f"{instrument}/{day}")
    return {
        "input_files": sum(
            1
            for instrument in INSTRUMENTS
            for _ in (corpus / instrument).glob("*.jsonl")
        ),
        "replay_reports": report_count,
        "journal_files": journal_count,
        "missing_replay_reports": missing_reports,
        "zero_decision_files_without_journal": zero_decision_files,
    }


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO, text=True
    ).strip()


def _json_lines(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _period_label(value: str, periods: dict[str, tuple[str, str]]) -> str:
    for label, (start, end) in periods.items():
        if start <= value <= end:
            return label
    return "OUT_OF_RANGE"


def _parse_logs(
    logs_root: Path,
) -> tuple[list[dict], list[dict], Counter, Counter, dict[str, dict]]:
    trades: list[dict] = []
    candidate_bars: list[dict] = []
    decision_gates: Counter = Counter()
    risk_rejections: Counter = Counter()
    halt_audit: dict[str, dict] = {}

    for instrument in INSTRUMENTS:
        for path in sorted((logs_root / instrument).glob("journal_*.jsonl")):
            day = path.stem.removeprefix("journal_")
            entries = list(_json_lines(path))
            outcomes: dict[str, dict] = {}
            for entry in entries:
                if entry.get("type") != "OUTCOME":
                    continue
                outcome = entry.get("outcome") or {}
                order_id = outcome.get("paper_order_id")
                if order_id:
                    if order_id in outcomes:
                        raise RuntimeError(f"duplicate outcome identity {order_id}")
                    outcomes[order_id] = outcome

            for entry in entries:
                decision = entry.get("decision")
                if decision in {"NO_TRADE", "RISK_REJECTED"}:
                    decision_gates.update(
                        entry.get("failed_gates") or ["UNSPECIFIED"]
                    )
                if decision == "RISK_REJECTED":
                    risk = entry.get("risk_check") or {}
                    failed_rule = risk.get("failed_rule") or "UNKNOWN"
                    risk_rejections[failed_rule] += 1
                    if (
                        failed_rule == "max_drawdown"
                        and instrument not in halt_audit
                    ):
                        halt_audit[instrument] = {
                            "first_rejection_date": day,
                            "first_rejection_bar_ts": entry.get("bar_ts"),
                            "reason": risk.get("reason"),
                        }

                admitted = entry.get("candidate_audit") or []
                blocked = (
                    (entry.get("blocked_candidate_audit") or {}).get("candidates")
                    or []
                )
                if admitted or blocked:
                    candidates = admitted + blocked
                    candidate_bars.append(
                        {
                            "date": day,
                            "instrument": instrument,
                            "direction": (
                                candidates[0].get("direction") or "UNKNOWN"
                            ),
                            "strategy": (
                                candidates[0].get("strategy") or "unknown"
                            ),
                            "raw_candidate": 1,
                            "regime_admitted": int(bool(admitted)),
                            "blocked_by_market_condition": int(bool(blocked)),
                        }
                    )

                if decision != "TRADE":
                    continue
                risk = entry.get("risk_check") or {}
                if risk.get("result") != "APPROVED":
                    continue
                order_id = entry.get("paper_order_id")
                if not order_id:
                    raise RuntimeError(f"approved TRADE has no identity in {path}")
                outcome = outcomes.get(order_id)
                result = (outcome or {}).get("result")
                cancelled = result == "CANCELLED"
                setup = entry.get("setup") or {}
                trades.append(
                    {
                        "date": day,
                        "bar_ts": entry.get("bar_ts") or entry.get("ts") or "",
                        "instrument": instrument,
                        "strategy": setup.get("strategy") or "unknown",
                        "direction": setup.get("direction") or "UNKNOWN",
                        "paper_order_id": order_id,
                        "attempted": 1,
                        "filled": int(not cancelled),
                        "cancelled_no_fill": int(cancelled),
                        "result": None if cancelled else result,
                        "resolved": int(result in {"WIN", "LOSS", "BREAKEVEN"}),
                        "open": int(result is None),
                        "pnl_before_commission": float(
                            (outcome or {}).get("pnl_dollars") or 0.0
                        ),
                        "pnl_after_commission": (
                            float((outcome or {}).get("pnl_dollars") or 0.0)
                            - COMMISSION_ROUND_TRIP
                            if result in {"WIN", "LOSS", "BREAKEVEN"}
                            else 0.0
                        ),
                        "exit_reason": (outcome or {}).get("exit_reason"),
                    }
                )
    for instrument in INSTRUMENTS:
        lane = [row for row in trades if row["instrument"] == instrument]
        if lane:
            halt_audit.setdefault(instrument, {})["last_order_attempt_date"] = max(
                row["date"] for row in lane
            )
    return trades, candidate_bars, decision_gates, risk_rejections, halt_audit


def _winner_concentration(values: list[float], top_n: int) -> float | None:
    winners = sorted((value for value in values if value > 0), reverse=True)
    total = sum(winners)
    return round(sum(winners[:top_n]) / total, 6) if total else None


def _profit_factor(values: list[float]) -> float | None:
    wins = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    if losses:
        return round(wins / losses, 6)
    return math.inf if wins else None


def _max_drawdown(rows: list[dict], pnl_key: str) -> float:
    equity = peak = 0.0
    max_dd = 0.0
    for row in sorted(
        rows, key=lambda item: (item["date"], item["bar_ts"], item["instrument"])
    ):
        if not row["resolved"]:
            continue
        equity += row[pnl_key]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def _stats(rows: list[dict], candidate_rows: list[dict]) -> dict:
    attempts = len(rows)
    fills = sum(row["filled"] for row in rows)
    resolved_rows = [row for row in rows if row["resolved"]]
    gross = [row["pnl_before_commission"] for row in resolved_rows]
    net = [row["pnl_after_commission"] for row in resolved_rows]
    wins = sum(row["result"] == "WIN" for row in resolved_rows)
    losses = sum(row["result"] == "LOSS" for row in resolved_rows)
    breakeven = sum(row["result"] == "BREAKEVEN" for row in resolved_rows)
    return {
        "funnel": {
            "raw_candidate_bars": len(candidate_rows),
            "regime_admitted_candidate_bars": sum(
                row["regime_admitted"] for row in candidate_rows
            ),
            "market_condition_blocked_candidate_bars": sum(
                row["blocked_by_market_condition"] for row in candidate_rows
            ),
            "order_attempts": attempts,
            "ioc_fills": fills,
            "ioc_cancelled_no_fill": attempts - fills,
            "resolved_trades": len(resolved_rows),
            "open_trades": sum(row["open"] for row in rows),
        },
        "attempts": attempts,
        "fills": fills,
        "fill_rate": round(fills / attempts, 6) if attempts else None,
        "cancellations_no_fills": attempts - fills,
        "resolved": len(resolved_rows),
        "open": sum(row["open"] for row in rows),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": round(wins / len(resolved_rows), 6) if resolved_rows else None,
        "net_before_commission": round(sum(gross), 2),
        "commission": round(COMMISSION_ROUND_TRIP * len(resolved_rows), 2),
        "net_after_commission": round(sum(net), 2),
        "expectancy_before_commission": (
            round(statistics.fmean(gross), 4) if gross else None
        ),
        "expectancy_after_commission": (
            round(statistics.fmean(net), 4) if net else None
        ),
        "profit_factor_before_commission": _profit_factor(gross),
        "profit_factor_after_commission": _profit_factor(net),
        "max_drawdown_before_commission": _max_drawdown(rows, "pnl_before_commission"),
        "max_drawdown_after_commission": _max_drawdown(rows, "pnl_after_commission"),
        "largest_win_before_commission": round(max(gross), 2) if gross else None,
        "largest_loss_before_commission": round(min(gross), 2) if gross else None,
        "largest_win_after_commission": round(max(net), 2) if net else None,
        "largest_loss_after_commission": round(min(net), 2) if net else None,
        "winner_concentration_after_commission": {
            "top_1": _winner_concentration(net, 1),
            "top_3": _winner_concentration(net, 3),
            "top_5": _winner_concentration(net, 5),
        },
    }


def _group(
    trades: list[dict],
    candidates: list[dict],
    field: str,
    labels: Iterable[str] | None = None,
) -> dict:
    keys = list(labels or sorted({str(row[field]) for row in trades + candidates}))
    return {
        key: _stats(
            [row for row in trades if str(row[field]) == key],
            [row for row in candidates if str(row[field]) == key],
        )
        for key in keys
    }


def _fmt_money(value: Any) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _fmt_rate(value: Any) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _fmt_pf(value: Any) -> str:
    if value is None:
        return "—"
    return "∞" if math.isinf(value) else f"{value:.3f}"


def _table(title: str, blocks: dict[str, dict]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| Scope | Attempts | Fills | Fill rate | No-fill | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, row in blocks.items():
        lines.append(
            f"| {label} | {row['attempts']} | {row['fills']} | "
            f"{_fmt_rate(row['fill_rate'])} | {row['cancellations_no_fills']} | "
            f"{row['resolved']} | {row['open']} | {_fmt_rate(row['win_rate'])} | "
            f"{_fmt_money(row['net_before_commission'])} | "
            f"{_fmt_money(row['net_after_commission'])} | "
            f"{_fmt_money(row['expectancy_after_commission'])} | "
            f"{_fmt_pf(row['profit_factor_after_commission'])} | "
            f"{_fmt_money(row['max_drawdown_after_commission'])} |"
        )
    lines.append("")
    return lines


def _render_report(results: dict) -> str:
    overall = results["overall"]
    funnel = overall["funnel"]
    concentration = overall["winner_concentration_after_commission"]
    comparison = results["comparison"]
    lines = [
        "# Corrected Corpus v1 + IOC evidence pass",
        "",
        f"**Verdict: {results['verdict']}**",
        "",
        f"Pinned code: `{results['meta']['main_sha']}` (PR #342 ancestor: "
        f"`{results['meta']['pr_342_ancestor']}`)",
        f"Corpus: `{results['meta']['corpus_tree_sha256']}` "
        f"({results['meta']['corpus_files']} files)",
        f"Range: {FULL_RANGE[0]} → {FULL_RANGE[1]}",
        "",
        "## Canonical posture",
        "",
        "- Corrected post-#338 `market_condition` corpus, verified with zero parity mismatches.",
        "- Post-#339/#342 ReplayEngine position identity and cross-day resolution behavior.",
        "- `entry_fill_model=ioc_limit` applied in memory only.",
        f"- IOC tolerance: MES={results['meta']['config']['entry_tolerance_ticks_by_root']['MES']:.0f} ticks; "
        f"MNQ={results['meta']['config']['entry_tolerance_ticks_by_root']['MNQ']:.0f} ticks.",
        "- Static exits; 1-tick adverse PaperBroker slippage; pessimistic stop-first same-bar resolution.",
        f"- ${COMMISSION_ROUND_TRIP:.2f} round-trip commission deducted only at the analysis layer.",
        "- Frozen strategy rules, permissions, selection order, sizing, and risk controls.",
        "",
        "## Full funnel",
        "",
        "| Stage | Count |",
        "|---|---:|",
        f"| Raw candidate bars (journal-visible) | {funnel['raw_candidate_bars']} |",
        f"| Regime-admitted candidate bars | {funnel['regime_admitted_candidate_bars']} |",
        f"| Market-condition-blocked candidate bars | {funnel['market_condition_blocked_candidate_bars']} |",
        f"| Orders attempted | {funnel['order_attempts']} |",
        f"| IOC filled | {funnel['ioc_fills']} |",
        f"| IOC cancelled / no-fill | {funnel['ioc_cancelled_no_fill']} |",
        f"| Resolved trades | {funnel['resolved_trades']} |",
        f"| Open trades | {funnel['open_trades']} |",
        "",
        "“Raw candidate” is one decision bar with at least one candidate exposed by "
        "`candidate_audit` or the observation-only `blocked_candidate_audit`. "
        "CHOPPY/DEAD return before candidate collection and therefore cannot be "
        "invented into this count; the report separately preserves those gate counts.",
        "",
    ]
    lines += _table("Overall", {"COMBINED": overall})
    lines += _table("By instrument", results["breakdowns"]["instrument"])
    lines += _table("By strategy", results["breakdowns"]["strategy"])
    lines += _table("By direction", results["breakdowns"]["direction"])
    lines += _table("H1 / H2", results["breakdowns"]["half"])
    lines += _table("Quarter", results["breakdowns"]["quarter"])
    lines += [
        "## Tail and concentration",
        "",
        f"- Largest win after commission: {_fmt_money(overall['largest_win_after_commission'])}",
        f"- Largest loss after commission: {_fmt_money(overall['largest_loss_after_commission'])}",
        f"- Top-1 winner concentration: {_fmt_rate(concentration['top_1'])}",
        f"- Top-3 winner concentration: {_fmt_rate(concentration['top_3'])}",
        f"- Top-5 winner concentration: {_fmt_rate(concentration['top_5'])}",
        "",
        "## Comparisons",
        "",
        f"- Superseded market-fill Corpus v1: {OLD_CORPUS['attempts']} attempts, "
        f"{_fmt_rate(OLD_CORPUS['win_rate'])} WR, PF {OLD_CORPUS['profit_factor']:.3f}, "
        f"${OLD_CORPUS['net_pnl_reported']:,.2f} reported net. It remains "
        "**SUPERSEDED / parity-invalid** and is not rehabilitated by this rerun.",
        f"- Corrected IOC pass: {overall['attempts']} attempts, "
        f"{_fmt_rate(overall['fill_rate'])} fill rate, {_fmt_rate(overall['win_rate'])} WR, "
        f"PF {_fmt_pf(overall['profit_factor_after_commission'])}, "
        f"{_fmt_money(overall['net_after_commission'])} after commission.",
        f"- Delta versus the old headline P&L: "
        f"{_fmt_money(comparison['old_corpus_to_corrected_ioc_pnl_delta'])}.",
        "- Prior 622-day IOC static study (different period/code, breaker-off) was "
        "negative on both instruments: MES -$1,550 / MNQ -$1,523 with 36–37% fills. "
        "It is context, not a matched rerun.",
        "",
        "## Audit and limitations",
        "",
        f"- Risk rejections: `{json.dumps(results['risk_rejections_by_rule'], sort_keys=True)}`.",
        f"- Drawdown-breaker audit: `{json.dumps(results['drawdown_breaker_audit'], sort_keys=True)}`.",
        "- MNQ stopped admitting orders on 2025-09-08 and MES on 2025-12-11; "
        "therefore H2/Q3/Q4 have zero attempts by design, not missing replay files.",
        "- The primary run preserves the configured 20% drawdown breaker. No breaker-off "
        "or other post-result diagnostic was run because that would no longer be the frozen system.",
        "- Commission is analysis-layer only, so it does not accelerate the ReplayEngine's "
        "drawdown halt; it only makes reported expectancy and P&L more conservative.",
        f"- Coverage audit: `{json.dumps(results['meta']['coverage'], sort_keys=True)}`.",
        "- Combined drawdown sequences the two independently replayed instrument lanes by "
        "historical decision time; per-instrument drawdowns are the account-specific values.",
        "- Dollar magnitudes are replay-scale. This is historical evidence, not live-fill proof.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "python scripts/corrected_ioc_corpus_evidence.py \\",
        "  --corpus data/replay_corpus_v1_market_condition_fixed \\",
        "  --logs /private/tmp/corrected_ioc_corpus_logs \\",
        "  --out scripts/corrected_ioc_corpus_results.json \\",
        "  --raw scripts/corrected_ioc_corpus_raw_trades.jsonl \\",
        "  --report docs/corrected-ioc-corpus-evidence-2026-07-26.md",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--logs", required=True, type=Path)
    parser.add_argument(
        "--out", type=Path, default=REPO / "scripts/corrected_ioc_corpus_results.json"
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=REPO / "scripts/corrected_ioc_corpus_raw_trades.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO / "docs/corrected-ioc-corpus-evidence-2026-07-26.md",
    )
    parser.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args()

    base_config = load_config()
    config = replace(base_config, entry_fill_model="ioc_limit")
    if config.fill_slippage_ticks != 1.0:
        raise RuntimeError("canonical fill_slippage_ticks is not 1.0")
    if not config.fill_pessimistic_both_hit:
        raise RuntimeError("canonical pessimistic same-bar handling is disabled")
    if config.entry_tolerance_ticks_by_root.get("MES") != 16.0:
        raise RuntimeError("canonical MES IOC tolerance is not 16 ticks")
    if config.entry_tolerance_ticks_by_root.get("MNQ") != 32.0:
        raise RuntimeError("canonical MNQ IOC tolerance is not 32 ticks")

    risk_hash_before = _sha256(REPO / "risk_rules.yaml")
    corpus_files, corpus_hash = _tree_sha256(args.corpus)
    if corpus_files != 626:
        raise RuntimeError(f"expected 626 corpus files, found {corpus_files}")

    if not args.analyze_only:
        for instrument in INSTRUMENTS:
            files = sorted((args.corpus / instrument).glob("*.jsonl"))
            if len(files) != 313:
                raise RuntimeError(
                    f"{instrument}: expected 313 daily files, found {len(files)}"
                )
            engine = ReplayEngine(
                config=config, log_dir=str(args.logs / instrument)
            )
            for index, candle_path in enumerate(files, 1):
                day = candle_path.stem.rsplit("_", 1)[-1]
                engine.run(candle_path, review_date=day)
                if index % 50 == 0 or index == len(files):
                    print(f"[{instrument}] {index}/{len(files)}", flush=True)

    coverage = _coverage_audit(args.corpus, args.logs)
    if coverage["replay_reports"] != corpus_files:
        raise RuntimeError(
            f"replay coverage incomplete: {coverage['replay_reports']}/{corpus_files}"
        )
    (
        trades,
        candidates,
        decision_gates,
        risk_rejections,
        halt_audit,
    ) = _parse_logs(args.logs)
    if not trades:
        raise RuntimeError("no approved order attempts found")
    if len({row["paper_order_id"] for row in trades}) != len(trades):
        raise RuntimeError("paper_order_id is not unique across order attempts")

    for row in trades + candidates:
        row["half"] = _period_label(row["date"], HALVES)
        row["quarter"] = _period_label(row["date"], QUARTERS)

    overall = _stats(trades, candidates)
    verdict = (
        "EDGE SURVIVES CORRECTED IOC TEST"
        if (
            overall["net_after_commission"] > 0
            and overall["profit_factor_after_commission"] is not None
            and overall["profit_factor_after_commission"] > 1
        )
        else "HISTORICAL EDGE DOES NOT SURVIVE CORRECTED IOC TEST"
    )
    main_sha = _git("rev-parse", "HEAD")
    pr_342_sha = "e393def1a9fcef5f14a17b32c9dd64c2a9f3ac29"
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", pr_342_sha, main_sha],
        cwd=REPO,
        check=False,
    ).returncode == 0
    if not ancestor:
        raise RuntimeError("PR #342 merge commit is not an ancestor of HEAD")

    results = {
        "meta": {
            "main_sha": main_sha,
            "pr_342_merge_sha": pr_342_sha,
            "pr_342_ancestor": ancestor,
            "range": list(FULL_RANGE),
            "corpus": str(args.corpus),
            "corpus_files": corpus_files,
            "corpus_tree_sha256": corpus_hash,
            "coverage": coverage,
            "risk_rules_sha256_before": risk_hash_before,
            "risk_rules_sha256_after": _sha256(REPO / "risk_rules.yaml"),
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "config": {
                "entry_fill_model": config.entry_fill_model,
                "entry_tolerance_ticks_by_root": config.entry_tolerance_ticks_by_root,
                "fill_slippage_ticks": config.fill_slippage_ticks,
                "fill_pessimistic_both_hit": config.fill_pessimistic_both_hit,
                "breakeven_at_1r": config.breakeven_at_1r,
                "exit_mode": config.exit_mode,
                "runner_mode": config.runner_mode,
                "require_trending_condition": config.require_trending_condition,
                "strategy_selection_mode": config.strategy_selection_mode,
                "strategy_fallback_enabled": config.strategy_fallback_enabled,
                "strategy_permission_gate_enabled": config.strategy_permission_gate_enabled,
                "max_daily_loss": config.max_daily_loss,
                "max_drawdown_percent": config.max_drawdown_percent,
                "starting_balance": config.position_sizing.starting_balance,
                "enabled_concepts": config.enabled_concepts,
                "disabled_concepts_per_instrument": (
                    config.disabled_concepts_per_instrument
                ),
            },
        },
        "verdict": verdict,
        "overall": overall,
        "breakdowns": {
            "instrument": _group(trades, candidates, "instrument", INSTRUMENTS),
            "strategy": _group(trades, candidates, "strategy"),
            "direction": _group(
                trades, candidates, "direction", ("LONG", "SHORT")
            ),
            "half": _group(trades, candidates, "half", HALVES),
            "quarter": _group(trades, candidates, "quarter", QUARTERS),
        },
        "risk_rejections_by_rule": dict(risk_rejections.most_common()),
        "decision_blocks_by_gate": dict(decision_gates.most_common()),
        "drawdown_breaker_audit": halt_audit,
        "comparison": {
            "old_corpus_market_fill": OLD_CORPUS,
            "prior_ioc_622d_static": PRIOR_IOC,
            "old_corpus_to_corrected_ioc_pnl_delta": round(
                overall["net_after_commission"] - OLD_CORPUS["net_pnl_reported"],
                2,
            ),
        },
    }
    if results["meta"]["risk_rules_sha256_before"] != results["meta"]["risk_rules_sha256_after"]:
        raise RuntimeError("risk_rules.yaml changed during evidence run")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    with args.raw.open("w", encoding="utf-8") as handle:
        for row in sorted(
            trades, key=lambda item: (item["date"], item["bar_ts"], item["instrument"])
        ):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    args.report.write_text(_render_report(results).rstrip() + "\n")
    print(json.dumps({"verdict": verdict, "overall": overall}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
