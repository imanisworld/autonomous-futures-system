#!/usr/bin/env python3
"""Canonical VWAP Reclaim evidence: isolated, honest-fill, walk-forward split.

Requested by the operator as the next strategy-cell evidence lane after PR
#346 (corrected Corpus v1 IOC pass, MERGED main@69ec77f) closed the
system-level question. `docs/strategy-rules/VWAP_FAMILY_SOURCE_OF_TRUTH_AUDIT_2026-07-26.md`
already scoped the exact gap: `vwap_reclaim` has the cleanest live/replay/Pine
formula parity of the three VWAP predicates, and two dated, reproducible
samples exist (n=29 NY-only 2026-07-09, n=50 all-session Corpus v1
2026-07-25) -- but NEITHER is walk-forward split per-strategy, and both were
generated under `entry_fill_model="market"` (the repo default), not the
`ioc_limit` honest-fill model #346 established as the corrected posture for
system-level evidence. Both historical figures are kept below as
provenance/context only, explicitly not walk-forward-valid comparators.

Why this is a NEW script rather than filtering #346's own output: #346's
corrected corpus run is COMBINED-BOOK -- every enabled strategy shares one
account, so the account-level 20% drawdown breaker (frozen risk_rules.yaml)
halted the whole book on 2025-09-08 (MNQ) / 2025-12-11 (MES), both inside
H1. Filtering #346's raw trades to strategy=="vwap_reclaim" yields only 10
attempts / 5 fills / 5 resolved, zero H2 data -- not because vwap_reclaim
itself is inactive that long, but because OTHER strategies' combined losses
tripped the shared breaker first. That is combined-book evidence, not
vwap_reclaim's own walk-forward evidence. Isolation (below) removes that
contamination: vwap_reclaim runs against its OWN fresh account, so its own
20% breaker (if it trips at all) reflects only its own P&L.

Isolation: `enabled_concepts` patched in-memory (`dataclasses.replace`) to
`["vwap_reclaim"]` ONLY, `disabled_concepts_per_instrument` cleared for this
run (so MES -- disabled in production via an unreproducible "40% WR" .yaml
comment the audit doc flagged as unsourced -- also gets evidence generated;
this is diagnostic only, NOT a recommendation to enable MES) -- never
written to risk_rules.yaml, verified unchanged on disk before/after, exactly
the pattern `scripts/strat_212_122_canonical_evidence_run.py` established.
Journals land in a NEW, isolated log directory
(`logs/replay_vwap_reclaim_canonical/`); no other lane's logs are read or
written.

Fill model: `entry_fill_model="ioc_limit"` in memory only (matches PR #346's
corrected posture, NOT the repo/prior-lane default of "market"), canonical
per-root IOC tolerance (MES=16 / MNQ=32 ticks) asserted, not overridden.
1-tick adverse PaperBroker slippage is the PRIMARY pass (config default);
2-tick and 3-tick are additional sensitivity passes, same isolation, same
corpus, only `fill_slippage_ticks` varies -- mirrors
`scripts/strat_212_122_slippage_sensitivity_run.py`.

Corpus: the SAME post-#338 corrected `market_condition` corpus #346 used
(`data/replay_corpus_v1_market_condition_fixed`, 626 files, tree SHA-256
`4ab58126...`), post-#339/#342 ReplayEngine cross-day + same-day-mixed-
instrument handling (both already on `main` this branches from).

No strategy/runtime/risk/broker/config/deployment/Pine files changed. No
enablement of any kind. Evidence and reporting only.

Usage:
    python3 scripts/vwap_reclaim_canonical_evidence.py \
        --logs logs/replay_vwap_reclaim_canonical \
        --out scripts/vwap_reclaim_canonical_evidence_results.json \
        --raw scripts/vwap_reclaim_canonical_evidence_raw_trades.jsonl \
        --report docs/vwap-reclaim-canonical-evidence-2026-07-26.md
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402

INSTRUMENTS = ("MNQ", "MES")
CORPUS = REPO / "data" / "replay_corpus_v1_market_condition_fixed"
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
PRIMARY_SLIPPAGE_TICKS = 1.0
SENSITIVITY_SLIPPAGE_TICKS = (2.0, 3.0)
SAMPLE_ADEQUATE_MIN = 30

HISTORICAL_N29 = {
    "label": "MNQ NY, ioc_limit_runner fill model, 2026-07-09 dated study",
    "source": "docs/mes-mnq-mechanical-research-2026-07-09.md:120",
    "instrument": "MNQ",
    "session": "new_york",
    "attempts": 29,
    "resolved": 29,
    "win_rate": 0.483,
    "expectancy_reported": 16.10,
    "net_pnl_reported": 466.96,
    "status": "provenance/context only -- not walk-forward split, predates #338/#339/#342 corpus corrections",
}
HISTORICAL_N50 = {
    "label": "MNQ all-session, Corpus v1 market-fill (entry_fill_model=market default), 2026-07-25",
    "source": "docs/corpus-v1-clean-baseline-report-2026-07-25.md:94",
    "instrument": "MNQ",
    "session": "all",
    "attempts": 50,
    "resolved": 50,
    "win_rate": 0.560,
    "profit_factor": 4.309,
    "net_pnl_reported": 3759.0,
    "expectancy_reported": 75.0,
    "status": (
        "provenance/context only -- market-fill (not ioc_limit), whole-corpus H1/H2 "
        "split reported (not vwap_reclaim's own halves), predates #339/#342 corpus "
        "corrections, superseded as combined-book evidence by PR #346's "
        "'HISTORICAL EDGE DOES NOT SURVIVE CORRECTED IOC TEST' finding"
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


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


def _run_isolated(config, log_dir: Path, fresh: bool) -> dict:
    ran_totals = {}
    for instrument in INSTRUMENTS:
        candle_dir = CORPUS / instrument
        files = sorted(candle_dir.glob(f"{instrument}_*.jsonl"))
        if len(files) != 313:
            raise RuntimeError(f"{instrument}: expected 313 daily files, found {len(files)}")
        inst_log_dir = log_dir / instrument
        inst_log_dir.mkdir(parents=True, exist_ok=True)
        engine = ReplayEngine(config=config, log_dir=str(inst_log_dir))
        ran = skipped = errors = 0
        for index, candle_path in enumerate(files, 1):
            day = candle_path.stem.rsplit("_", 1)[-1]
            if not fresh and (inst_log_dir / f"journal_{day}.jsonl").exists():
                skipped += 1
                continue
            try:
                engine.run(candle_path, review_date=day)
                ran += 1
            except Exception as exc:  # noqa: BLE001 - surface and keep going
                errors += 1
                print(f"[run] {instrument} {day} ERROR: {exc}", file=sys.stderr)
            if index % 100 == 0 or index == len(files):
                print(f"[run] {log_dir.name} {instrument} {index}/{len(files)} "
                      f"(ran={ran} skipped={skipped} errors={errors})", flush=True)
        ran_totals[instrument] = {"ran": ran, "skipped": skipped, "errors": errors, "files": len(files)}
        if errors:
            raise RuntimeError(f"{instrument}: {errors} day(s) errored during isolated run")
    return ran_totals


def _parse_logs(logs_root: Path) -> list[dict]:
    trades: list[dict] = []
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
                        raise RuntimeError(f"duplicate outcome identity {order_id} in {path}")
                    outcomes[order_id] = outcome

            for entry in entries:
                if entry.get("decision") != "TRADE":
                    continue
                risk = entry.get("risk_check") or {}
                if risk.get("result") != "APPROVED":
                    continue
                setup = entry.get("setup") or {}
                if setup.get("strategy") != "vwap_reclaim":
                    raise RuntimeError(
                        f"isolation leak: non-vwap_reclaim TRADE decision in {path}: "
                        f"{setup.get('strategy')!r}"
                    )
                order_id = entry.get("paper_order_id")
                if not order_id:
                    raise RuntimeError(f"approved TRADE has no identity in {path}")
                outcome = outcomes.get(order_id)
                result = (outcome or {}).get("result")
                cancelled = result == "CANCELLED"
                trades.append(
                    {
                        "date": day,
                        "bar_ts": entry.get("bar_ts") or entry.get("ts") or "",
                        "instrument": instrument,
                        "direction": setup.get("direction") or "UNKNOWN",
                        "paper_order_id": order_id,
                        "attempted": 1,
                        "filled": int(not cancelled),
                        "cancelled_no_fill": int(cancelled),
                        "result": None if cancelled else result,
                        "resolved": int(result in {"WIN", "LOSS", "BREAKEVEN"}),
                        "open": int(result is None),
                        "pnl_before_commission": float((outcome or {}).get("pnl_dollars") or 0.0),
                        "pnl_after_commission": (
                            float((outcome or {}).get("pnl_dollars") or 0.0) - COMMISSION_ROUND_TRIP
                            if result in {"WIN", "LOSS", "BREAKEVEN"}
                            else 0.0
                        ),
                        "exit_reason": (outcome or {}).get("exit_reason"),
                    }
                )
    return trades


def _profit_factor(values: list[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses:
        return round(wins / losses, 6)
    return math.inf if wins else None


def _max_drawdown(rows: list[dict], pnl_key: str) -> float:
    equity = peak = 0.0
    max_dd = 0.0
    for row in sorted(rows, key=lambda item: (item["date"], item["bar_ts"], item["instrument"])):
        if not row["resolved"]:
            continue
        equity += row[pnl_key]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def _stats(rows: list[dict]) -> dict:
    attempts = len(rows)
    fills = sum(row["filled"] for row in rows)
    resolved_rows = [row for row in rows if row["resolved"]]
    gross = [row["pnl_before_commission"] for row in resolved_rows]
    net = [row["pnl_after_commission"] for row in resolved_rows]
    wins = sum(row["result"] == "WIN" for row in resolved_rows)
    losses = sum(row["result"] == "LOSS" for row in resolved_rows)
    breakeven = sum(row["result"] == "BREAKEVEN" for row in resolved_rows)
    return {
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
        "expectancy_before_commission": round(statistics.fmean(gross), 4) if gross else None,
        "expectancy_after_commission": round(statistics.fmean(net), 4) if net else None,
        "profit_factor_before_commission": _profit_factor(gross),
        "profit_factor_after_commission": _profit_factor(net),
        "max_drawdown_before_commission": _max_drawdown(rows, "pnl_before_commission"),
        "max_drawdown_after_commission": _max_drawdown(rows, "pnl_after_commission"),
        "largest_win_after_commission": round(max(net), 2) if net else None,
        "largest_loss_after_commission": round(min(net), 2) if net else None,
        "sample_adequate": len(resolved_rows) >= SAMPLE_ADEQUATE_MIN,
    }


def _group(trades: list[dict], field: str, labels: Iterable[str]) -> dict:
    return {key: _stats([row for row in trades if str(row[field]) == key]) for key in labels}


def _walk_forward_both_halves_positive(halves: dict) -> bool | None:
    h1 = halves.get("H1", {})
    h2 = halves.get("H2", {})
    if h1.get("resolved", 0) == 0 or h2.get("resolved", 0) == 0:
        return None
    return (
        (h1.get("net_after_commission") or 0) > 0
        and (h2.get("net_after_commission") or 0) > 0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, default=REPO / "logs/replay_vwap_reclaim_canonical")
    parser.add_argument("--out", type=Path, default=REPO / "scripts/vwap_reclaim_canonical_evidence_results.json")
    parser.add_argument("--raw", type=Path, default=REPO / "scripts/vwap_reclaim_canonical_evidence_raw_trades.jsonl")
    parser.add_argument("--report", type=Path, default=REPO / "docs/vwap-reclaim-canonical-evidence-2026-07-26.md")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args()

    base_config = load_config()
    before_enabled = tuple(base_config.enabled_concepts)
    before_disabled = {k: tuple(v) for k, v in base_config.disabled_concepts_per_instrument.items()}
    risk_hash_before = _sha256(REPO / "risk_rules.yaml")

    if base_config.entry_tolerance_ticks_by_root.get("MES") != 16.0:
        raise RuntimeError("canonical MES IOC tolerance is not 16 ticks")
    if base_config.entry_tolerance_ticks_by_root.get("MNQ") != 32.0:
        raise RuntimeError("canonical MNQ IOC tolerance is not 32 ticks")
    if not base_config.fill_pessimistic_both_hit:
        raise RuntimeError("canonical pessimistic same-bar handling is disabled")

    slippage_results: dict[str, dict] = {}
    for slip in (PRIMARY_SLIPPAGE_TICKS,) + SENSITIVITY_SLIPPAGE_TICKS:
        tag = f"{slip:.0f}tick"
        iso_config = dataclasses.replace(
            base_config,
            enabled_concepts=["vwap_reclaim"],
            disabled_concepts_per_instrument={},
            entry_fill_model="ioc_limit",
            fill_slippage_ticks=float(slip),
        )
        log_dir = args.logs / tag
        if not args.analyze_only:
            print(f"[run] === slippage={slip} tick ===")
            _run_isolated(iso_config, log_dir, args.fresh)
        trades = _parse_logs(log_dir)
        for row in trades:
            row["half"] = _period_label(row["date"], HALVES)
            row["quarter"] = _period_label(row["date"], QUARTERS)
        slippage_results[tag] = {
            "slippage_ticks": slip,
            "overall": _stats(trades),
            "by_instrument": _group(trades, "instrument", INSTRUMENTS),
            "by_half": _group(trades, "half", HALVES.keys()),
            "by_quarter": _group(trades, "quarter", QUARTERS.keys()),
            "trades": trades,
        }

    after_config = load_config()
    after_enabled = tuple(after_config.enabled_concepts)
    after_disabled = {k: tuple(v) for k, v in after_config.disabled_concepts_per_instrument.items()}
    risk_hash_after = _sha256(REPO / "risk_rules.yaml")
    if (before_enabled, before_disabled, risk_hash_before) != (after_enabled, after_disabled, risk_hash_after):
        raise RuntimeError("risk_rules.yaml / enabled_concepts drifted on disk during this run")

    primary = slippage_results["1tick"]
    walk_forward = _walk_forward_both_halves_positive(primary["by_half"])
    slippage_survives = all(
        (slippage_results[f"{s:.0f}tick"]["overall"]["profit_factor_after_commission"] or 0) > 1
        and (slippage_results[f"{s:.0f}tick"]["overall"]["net_after_commission"] or 0) > 0
        for s in (PRIMARY_SLIPPAGE_TICKS,) + SENSITIVITY_SLIPPAGE_TICKS
    )
    mnq_primary = primary["by_instrument"]["MNQ"]
    if walk_forward is None:
        verdict = "WAIT — insufficient data for walk-forward (one half has zero resolved trades)"
    elif not mnq_primary["sample_adequate"]:
        verdict = f"WAIT — MNQ sample below {SAMPLE_ADEQUATE_MIN}-trade minimum (n={mnq_primary['resolved']})"
    elif not walk_forward:
        verdict = "WAIT — fails both-halves-positive walk-forward under honest fills"
    elif not slippage_survives:
        verdict = "WAIT — fails 1/2/3-tick slippage sensitivity"
    else:
        verdict = "PROMISING BUT UNPROVEN — clears walk-forward + slippage, sample still thin"

    main_sha = _git("rev-parse", "HEAD")
    results = {
        "meta": {
            "main_sha": main_sha,
            "range": list(FULL_RANGE),
            "corpus": str(CORPUS.relative_to(REPO)),
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "sample_adequate_min": SAMPLE_ADEQUATE_MIN,
            "isolation": {
                "enabled_concepts": ["vwap_reclaim"],
                "disabled_concepts_per_instrument": {},
                "entry_fill_model": "ioc_limit",
                "entry_tolerance_ticks_by_root": base_config.entry_tolerance_ticks_by_root,
            },
            "risk_rules_sha256_before": risk_hash_before,
            "risk_rules_sha256_after": risk_hash_after,
        },
        "verdict": verdict,
        "walk_forward_both_halves_positive_1tick": walk_forward,
        "slippage_1_2_3_tick_all_survive": slippage_survives,
        "slippage_tiers": {
            tag: {k: v for k, v in block.items() if k != "trades"}
            for tag, block in slippage_results.items()
        },
        "historical_comparators": {
            "n29_2026-07-09": HISTORICAL_N29,
            "n50_corpus_v1_market_fill_2026-07-25": HISTORICAL_N50,
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")

    with args.raw.open("w", encoding="utf-8") as handle:
        for tag, block in slippage_results.items():
            for row in sorted(block["trades"], key=lambda r: (r["date"], r["bar_ts"], r["instrument"])):
                out_row = dict(row)
                out_row["slippage_tag"] = tag
                handle.write(json.dumps(out_row, sort_keys=True) + "\n")

    args.report.write_text(_render_report(results).rstrip() + "\n")
    print(json.dumps({"verdict": verdict, "primary_overall": primary["overall"]}, indent=2))
    return 0


def _fmt_money(value: Any) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _fmt_rate(value: Any) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _fmt_pf(value: Any) -> str:
    if value is None:
        return "—"
    return "∞" if math.isinf(value) else f"{value:.3f}"


def _table_rows(blocks: dict[str, dict]) -> list[str]:
    lines = [
        "| Scope | Attempts | Fills | Fill rate | Resolved | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net | n≥30 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, row in blocks.items():
        lines.append(
            f"| {label} | {row['attempts']} | {row['fills']} | {_fmt_rate(row['fill_rate'])} | "
            f"{row['resolved']} | {_fmt_rate(row['win_rate'])} | {_fmt_money(row['net_before_commission'])} | "
            f"{_fmt_money(row['net_after_commission'])} | {_fmt_money(row['expectancy_after_commission'])} | "
            f"{_fmt_pf(row['profit_factor_after_commission'])} | {_fmt_money(row['max_drawdown_after_commission'])} | "
            f"{'✅' if row['sample_adequate'] else '❌'} |"
        )
    return lines


def _render_report(results: dict) -> str:
    primary = results["slippage_tiers"]["1tick"]
    lines = [
        "# VWAP Reclaim — canonical evidence, isolated + honest-fill walk-forward",
        "",
        f"**Verdict: {results['verdict']}**",
        "",
        f"Pinned code: `{results['meta']['main_sha']}`",
        f"Corpus: `{results['meta']['corpus']}` (post-#338 corrected market_condition, post-#339/#342 ReplayEngine)",
        f"Range: {FULL_RANGE[0]} → {FULL_RANGE[1]}",
        "",
        "## Method",
        "",
        "- **Isolated** single-strategy replay (`enabled_concepts=[\"vwap_reclaim\"]` only, "
        "`disabled_concepts_per_instrument` cleared) — own fresh account per run, so the frozen "
        "20% drawdown breaker (if it trips) reflects only `vwap_reclaim`'s own P&L, never "
        "contamination from other strategies sharing the combined book.",
        "- `entry_fill_model=\"ioc_limit\"` in memory only (PR #346's corrected posture) — canonical "
        f"per-root tolerance MES={results['meta']['isolation']['entry_tolerance_ticks_by_root'].get('MES'):.0f} / "
        f"MNQ={results['meta']['isolation']['entry_tolerance_ticks_by_root'].get('MNQ'):.0f} ticks, not overridden.",
        "- Primary pass: 1-tick adverse PaperBroker slippage (config default). Sensitivity passes: "
        "2-tick and 3-tick, same isolation/corpus, only `fill_slippage_ticks` varied.",
        f"- ${COMMISSION_ROUND_TRIP:.2f} round-trip commission at the analysis layer only.",
        "- Frozen strategy rules, sizing, and risk controls throughout. `risk_rules.yaml` hash "
        f"verified unchanged before/after (`{results['meta']['risk_rules_sha256_before'][:16]}…`).",
        "- MES included (evidence-only — `disabled_concepts_per_instrument` cleared for THIS "
        "isolated run only, never on disk) because the audit doc flagged the production MES "
        "disable rationale (\"40% WR\" `risk_rules.yaml` comment) as unsourced/unreproducible. "
        "**This is diagnostic, not a recommendation to enable MES.**",
        "",
        "## Primary pass (1-tick) — overall",
        "",
        *_table_rows({"COMBINED": primary["overall"]}),
        "",
        "## By instrument (1-tick)",
        "",
        *_table_rows(primary["by_instrument"]),
        "",
        "## Walk-forward H1/H2 (1-tick)",
        "",
        *_table_rows(primary["by_half"]),
        "",
        f"Both halves positive: **{results['walk_forward_both_halves_positive_1tick']}**",
        "",
        "## Quarter (1-tick)",
        "",
        *_table_rows(primary["by_quarter"]),
        "",
        "## Slippage sensitivity (1/2/3-tick, overall)",
        "",
        *_table_rows({
            f"{tag}": results["slippage_tiers"][tag]["overall"]
            for tag in ("1tick", "2tick", "3tick")
        }),
        "",
        f"Survives 1/2/3-tick (PF>1 and net>0 at every tier): "
        f"**{results['slippage_1_2_3_tick_all_survive']}**",
        "",
        "## Historical comparators (context only — NOT walk-forward-valid)",
        "",
        f"- n=29 (2026-07-09, MNQ NY, `ioc_limit_runner`): {results['historical_comparators']['n29_2026-07-09']['win_rate']*100:.1f}% WR, "
        f"${results['historical_comparators']['n29_2026-07-09']['net_pnl_reported']:,.2f} net. "
        f"{results['historical_comparators']['n29_2026-07-09']['status']}",
        f"- n=50 (2026-07-25, MNQ all-session, Corpus v1 market-fill): "
        f"{results['historical_comparators']['n50_corpus_v1_market_fill_2026-07-25']['win_rate']*100:.1f}% WR, "
        f"PF {results['historical_comparators']['n50_corpus_v1_market_fill_2026-07-25']['profit_factor']:.3f}, "
        f"${results['historical_comparators']['n50_corpus_v1_market_fill_2026-07-25']['net_pnl_reported']:,.2f} net. "
        f"{results['historical_comparators']['n50_corpus_v1_market_fill_2026-07-25']['status']}",
        "- Neither historical figure is comparable to this pass's numbers: different fill model "
        "(market vs ioc_limit), not walk-forward split, and (n=50) combined-book/pre-#339/#342 corpus.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "python scripts/vwap_reclaim_canonical_evidence.py \\",
        "  --logs logs/replay_vwap_reclaim_canonical \\",
        "  --out scripts/vwap_reclaim_canonical_evidence_results.json \\",
        "  --raw scripts/vwap_reclaim_canonical_evidence_raw_trades.jsonl \\",
        "  --report docs/vwap-reclaim-canonical-evidence-2026-07-26.md",
        "```",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
