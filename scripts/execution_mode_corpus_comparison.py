#!/usr/bin/env python3
"""Execution-mode corpus comparison: the implemented entry modes, head-to-head.

Evidence orchestration ONLY (no strategy/replay/broker/risk/config/deployment/
Pine changes). Operator-scoped follow-up to PR #357: run the EXISTING
replay/evidence pipeline (the exact PR #346 posture — corrected corpus, frozen
rules, 20% breaker preserved, 1-tick adverse slippage, pessimistic same-bar,
$1.48 RT commission at the analysis layer) once per implemented entry
execution mode, on identical pinned code, and compare.

Arms (all in-memory config only; identical everything else):

- ioc_limit          — PR #346's canonical arm re-run on the merged code
                       (entry_fill_model="ioc_limit", MES 16t / MNQ 32t caps).
- market             — honest aggressive market entry: entry_fill_model=
                       "market" with the production force_market_entry fill
                       branch applied to every replay order (fill at the
                       decision bar's close ± adverse slippage — the live #259
                       semantics; NEVER the anchored plan price). Applied via
                       a scoped, documented wrapper around
                       PaperBroker.execute_bracket for the arm's replay only.
- marketable_limit   — the same bounded-aggressive-limit mechanism the live
                       broker's marketable_limit mode uses: an IOC limit at
                       plan ± N ticks. Modeled EXACTLY by entry_fill_model=
                       "ioc_limit" with the per-instrument cap set to PR
                       #357's marketable default (8 ticks both instruments).
- stop_market        — entry_fill_model="stop_market": PaperBroker's native
                       causal one-next-bar stop entry (gap through trigger →
                       next open ± slip; touch → level ± slip; else fails
                       closed), the replay analog of the live Stop parent.

- stop_limit         — NOT MODELED: PaperBroker has no StopLimit entry model.
                       Reported as an explicit gap; adding one is a simulator
                       change needing its own reviewed PR, not silently
                       approximated here.

Each arm replays the byte-identical PR #346 corpus start to finish through
the real ReplayEngine (fresh log dir per arm/instrument), then reuses the
PR #346 evidence module's own journal parsing and statistics, so numbers are
mode-to-mode comparable and #346-comparable by construction.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from execution.paper_broker import PaperBroker  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402
from scripts.corrected_ioc_corpus_evidence import (  # noqa: E402
    COMMISSION_ROUND_TRIP,
    FULL_RANGE,
    HALVES,
    INSTRUMENTS,
    QUARTERS,
    _fmt_money,
    _fmt_pf,
    _fmt_rate,
    _git,
    _group,
    _parse_logs,
    _period_label,
    _stats,
    _tree_sha256,
)

PR346_CORPUS_TREE_SHA256 = (
    "4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4"
)
PR346_REFERENCE = {
    "label": "PR #346 corrected IOC pass (main@e8f2fe23)",
    "attempts": 165,
    "fills": 97,
    "win_rate": 0.268,
    "net_after_commission": -802.28,
    "profit_factor_after_commission": 0.753,
}
MARKETABLE_TICKS = {"MES": 8.0, "MNQ": 8.0}  # PR #357 marketable_limit default


@contextmanager
def _force_market_entries():
    """Scoped wrapper: apply the production force_market_entry fill semantics
    to every order of ONE replay arm (fill at market_price ± adverse slip via
    paper_broker.py's proof-lane branch — the honest aggressive entry). The
    original method is always restored."""
    original = PaperBroker.execute_bracket

    def patched(self, order, market_price=None, *, paper_order_id=None):
        order.force_market_entry = True
        return original(
            self, order, market_price=market_price, paper_order_id=paper_order_id
        )

    PaperBroker.execute_bracket = patched
    try:
        yield
    finally:
        PaperBroker.execute_bracket = original


def _run_arm(arm: str, config, corpus: Path, logs_root: Path) -> None:
    for instrument in INSTRUMENTS:
        files = sorted((corpus / instrument).glob("*.jsonl"))
        if len(files) != 313:
            raise RuntimeError(
                f"{instrument}: expected 313 daily files, found {len(files)}"
            )
        engine = ReplayEngine(
            config=config, log_dir=str(logs_root / arm / instrument)
        )
        for index, candle_path in enumerate(files, 1):
            day = candle_path.stem.rsplit("_", 1)[-1]
            engine.run(candle_path, review_date=day)
            if index % 50 == 0 or index == len(files):
                print(f"[{arm}:{instrument}] {index}/{len(files)}", flush=True)


def _arm_configs(base) -> dict:
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


def _analyze_arm(arm: str, logs_root: Path) -> dict:
    trades, candidates, gates, risk_rejections, halt_audit = _parse_logs(
        logs_root / arm
    )
    if not trades:
        raise RuntimeError(f"{arm}: no approved order attempts found")
    for row in trades + candidates:
        row["half"] = _period_label(row["date"], HALVES)
        row["quarter"] = _period_label(row["date"], QUARTERS)
    return {
        "overall": _stats(trades, candidates),
        "breakdowns": {
            "instrument": _group(trades, candidates, "instrument", INSTRUMENTS),
            "strategy": _group(trades, candidates, "strategy"),
            "half": _group(trades, candidates, "half", HALVES),
        },
        "risk_rejections_by_rule": dict(risk_rejections.most_common()),
        "drawdown_breaker_audit": halt_audit,
        "raw_trades": trades,
    }


def _comparison_table(results: dict) -> list[str]:
    lines = [
        "| Arm | Attempts | Fills | Fill rate | WR | Net after $1.48 RT | Exp net | PF net | Max DD net | Breaker halts |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for arm, block in results.items():
        o = block["overall"]
        halts = block["drawdown_breaker_audit"]
        halt_desc = (
            ", ".join(
                f"{ins} {info.get('first_rejection_date')}"
                for ins, info in sorted(halts.items())
                if info.get("first_rejection_date")
            )
            or "none"
        )
        lines.append(
            f"| {arm} | {o['attempts']} | {o['fills']} | {_fmt_rate(o['fill_rate'])} | "
            f"{_fmt_rate(o['win_rate'])} | {_fmt_money(o['net_after_commission'])} | "
            f"{_fmt_money(o['expectancy_after_commission'])} | "
            f"{_fmt_pf(o['profit_factor_after_commission'])} | "
            f"{_fmt_money(o['max_drawdown_after_commission'])} | {halt_desc} |"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--logs", required=True, type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "scripts/execution_mode_corpus_comparison_results.json",
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=REPO / "scripts/execution_mode_corpus_comparison_raw.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO / "docs/execution-mode-corpus-comparison-2026-07-26.md",
    )
    parser.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args()

    base = load_config()
    if base.fill_slippage_ticks != 1.0:
        raise RuntimeError("canonical fill_slippage_ticks is not 1.0")
    if not base.fill_pessimistic_both_hit:
        raise RuntimeError("canonical pessimistic same-bar handling is disabled")
    if base.entry_tolerance_ticks_by_root.get("MES") != 16.0:
        raise RuntimeError("canonical MES IOC tolerance is not 16 ticks")
    if base.entry_tolerance_ticks_by_root.get("MNQ") != 32.0:
        raise RuntimeError("canonical MNQ IOC tolerance is not 32 ticks")

    corpus_files, corpus_hash = _tree_sha256(args.corpus)
    if corpus_files != 626:
        raise RuntimeError(f"expected 626 corpus files, found {corpus_files}")
    if corpus_hash != PR346_CORPUS_TREE_SHA256:
        raise RuntimeError("corpus tree hash differs from PR #346's documented corpus")

    configs = _arm_configs(base)
    if not args.analyze_only:
        for arm, config in configs.items():
            if arm == "market":
                with _force_market_entries():
                    _run_arm(arm, config, args.corpus, args.logs)
            else:
                _run_arm(arm, config, args.corpus, args.logs)

    results = {arm: _analyze_arm(arm, args.logs) for arm in configs}

    main_sha = _git("rev-parse", "HEAD")
    ranked = sorted(
        results.items(),
        key=lambda item: item[1]["overall"]["net_after_commission"],
        reverse=True,
    )
    best_arm, best = ranked[0]
    best_positive = (
        (best["overall"]["net_after_commission"] or 0) > 0
        and best["overall"]["profit_factor_after_commission"] is not None
        and not isinstance(best["overall"]["profit_factor_after_commission"], str)
        and best["overall"]["profit_factor_after_commission"] > 1
    )
    verdict = (
        f"BEST ARM {best_arm.upper()} IS POSITIVE — CANDIDATE FOR OPERATOR REVIEW"
        if best_positive
        else "NO IMPLEMENTED EXECUTION MODE RESCUES THE FROZEN SYSTEM ON THIS CORPUS"
    )

    payload = {
        "meta": {
            "main_sha": main_sha,
            "range": list(FULL_RANGE),
            "corpus": str(args.corpus),
            "corpus_files": corpus_files,
            "corpus_tree_sha256": corpus_hash,
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "posture": (
                "PR #346 frozen-system posture per arm: corrected corpus, frozen "
                "rules/permissions/sizing, 20% drawdown breaker preserved, 1-tick "
                "adverse slippage, pessimistic same-bar, commission analysis-layer"
            ),
            "arms": {
                "ioc_limit": "entry_fill_model=ioc_limit (canonical MES 16t / MNQ 32t)",
                "market": (
                    "entry_fill_model=market + production force_market_entry fill "
                    "semantics on every order (decision-bar close ± adverse slip)"
                ),
                "marketable_limit": (
                    "entry_fill_model=ioc_limit with PR #357 marketable default "
                    "caps (8t both) — the identical bounded-aggressive-IOC "
                    "mechanism the live marketable_limit mode submits"
                ),
                "stop_market": "entry_fill_model=stop_market (native causal one-next-bar)",
                "stop_limit": (
                    "NOT MODELED — PaperBroker has no StopLimit entry model; "
                    "adding one is a simulator change for a separate reviewed PR"
                ),
            },
        },
        "verdict": verdict,
        "pr346_reference": PR346_REFERENCE,
        "arms": {
            arm: {k: v for k, v in block.items() if k != "raw_trades"}
            for arm, block in results.items()
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    with args.raw.open("w", encoding="utf-8") as handle:
        for arm, block in results.items():
            for row in sorted(
                block["raw_trades"],
                key=lambda item: (item["date"], item["bar_ts"], item["instrument"]),
            ):
                handle.write(json.dumps({"arm": arm, **row}, sort_keys=True) + "\n")

    lines = [
        "# Execution-mode corpus comparison (implemented modes, PR #357)",
        "",
        f"**Verdict: {verdict}**",
        "",
        f"Pinned code: `{main_sha}` (PR #357 merged)",
        f"Corpus: `{corpus_hash}` ({corpus_files} files — byte-identical to PR #346's corpus)",
        f"Range: {FULL_RANGE[0]} → {FULL_RANGE[1]}",
        "",
        "## Posture",
        "",
        "- One full-corpus frozen-system replay per arm on identical pinned code —",
        "  the PR #346 pipeline and posture exactly (breaker preserved, 1-tick",
        "  adverse slippage, pessimistic same-bar, $1.48 RT at analysis layer).",
        "- Arms differ ONLY in the entry fill model (in-memory config; the",
        "  `market` arm additionally applies the production force_market_entry",
        "  fill branch via a scoped, documented wrapper).",
        "- `stop_limit` is NOT modeled in replay (no PaperBroker StopLimit entry",
        "  model) — explicit gap, not approximated.",
        "- System-path evidence: each arm's own losses can trip its own breaker;",
        "  halted arms are censored from their halt date (reported per arm).",
        "",
        "## Comparison (net after commission)",
        "",
    ]
    lines += _comparison_table(results)
    lines += [
        "",
        f"- PR #346 reference (same posture, pre-#357 code): 165 attempts, "
        f"PF 0.753, $-802.28.",
        "",
        "## Per-arm drawdown-breaker audit",
        "",
    ]
    for arm, block in results.items():
        lines.append(
            f"- **{arm}**: `{json.dumps(block['drawdown_breaker_audit'], sort_keys=True, default=str)}`"
        )
    lines += [
        "",
        "## Per-arm H1/H2",
        "",
        "| Arm | H1 net | H1 PF | H2 attempts | H2 net | H2 PF |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm, block in results.items():
        h1 = block["breakdowns"]["half"]["H1"]
        h2 = block["breakdowns"]["half"]["H2"]
        lines.append(
            f"| {arm} | {_fmt_money(h1['net_after_commission'])} | "
            f"{_fmt_pf(h1['profit_factor_after_commission'])} | {h2['attempts']} | "
            f"{_fmt_money(h2['net_after_commission'])} | "
            f"{_fmt_pf(h2['profit_factor_after_commission'])} |"
        )
    lines += [
        "",
        "## Limitations",
        "",
        "- Replay-scale dollars; historical evidence, not live-fill proof.",
        "- The marketable_limit arm uses PR #357's default 8-tick caps — note",
        "  these are TIGHTER than the canonical IOC caps (MES 16t / MNQ 32t);",
        "  it is the same IOC mechanism at the marketable default width.",
        "- Attempt sets differ across arms by construction (fills change",
        "  position blocking, session budgets, and breaker paths) — this is",
        "  the system-path comparison, complementing the attempt-matched",
        "  counterfactuals (#354/#355) and the causal trigger study (#356).",
        "",
        "## Reproduction",
        "",
        "```bash",
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES=16 ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ=32 \\",
        "python scripts/execution_mode_corpus_comparison.py \\",
        "  --corpus data/replay_corpus_v1_market_condition_fixed \\",
        "  --logs /private/tmp/execution_mode_comparison_logs \\",
        "  --out scripts/execution_mode_corpus_comparison_results.json \\",
        "  --raw scripts/execution_mode_corpus_comparison_raw.jsonl \\",
        "  --report docs/execution-mode-corpus-comparison-2026-07-26.md",
        "```",
        "",
    ]
    args.report.write_text("\n".join(lines).rstrip() + "\n")
    print(
        json.dumps(
            {
                "verdict": verdict,
                "arms": {
                    arm: {
                        "attempts": block["overall"]["attempts"],
                        "fills": block["overall"]["fills"],
                        "win_rate": block["overall"]["win_rate"],
                        "net_after_commission": block["overall"]["net_after_commission"],
                        "pf": block["overall"]["profit_factor_after_commission"],
                    }
                    for arm, block in results.items()
                },
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
