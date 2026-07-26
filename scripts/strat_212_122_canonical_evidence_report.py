#!/usr/bin/env python3
"""
scripts/strat_212_122_canonical_evidence_report.py

STATUS (2026-07-26, updated after PR #339 merged main@4684947): BOTH shared-
engine blockers are now fixed and incorporated. PR #338 (market-condition
parity, merged main@0057bc23) and PR #339 (cross-day position carry-forward,
merged main@4684947) both land in this run's corpus/engine. This is the
FINAL, non-provisional rerun of the strat_212/strat_122 canonical evidence --
`open_with_identity` counts below (if any) should now reflect only genuine
corpus-tail positions (no more day-file candles left to resolve against),
not day-boundary orphans -- verify this explicitly rather than assuming it.

Prior superseded runs preserved, not deleted, for provenance:
- Pre-#338 (market-condition-tainted):
  scripts/strat_212_122_canonical_evidence_results_pre_pr338_superseded.json
  / _raw_trades_pre_pr338_superseded.jsonl
- Post-#338, pre-#339 (cross-day carry-forward still broken), PARTIALLY_CORRECTED:
  scripts/strat_212_122_canonical_evidence_results_pre_pr339_partially_corrected.json
  / _raw_trades_pre_pr339_partially_corrected.jsonl

Aggregates the strat_212/strat_122 canonical evidence run produced by
scripts/strat_212_122_canonical_evidence_run.py (MNQ + MES,
2025-07-24 -> 2026-07-23, data/replay_corpus_v1_market_condition_fixed --
the post-#320-fix directional, post-#338 market-condition-parity-corrected
corpus, enabled_concepts isolated in-memory to [strat_212, strat_122] only,
risk_rules.yaml untouched).

Trade pairing reuses adaptive.journal_reader.JournalReader._trades_for_day --
the same exact-paper_order_id identity join (#327/#332), no FIFO fallback --
rather than a bespoke parser. Verified this run's journals carry a real
paper_order_id on every TRADE/OUTCOME pair (see run script's own smoke test).

Commission handling: PaperBroker/JournalLogger apply NO commission model
anywhere in this system (verified by direct grep -- zero references in
execution/paper_broker.py or journal/journal_logger.py). This script reports
BOTH raw (slippage-only) and commission-adjusted P&L side by side, applying
the commission-adjustment at the analysis layer only (never rewriting
journal rows), using the $1.48/round-trip constant this codebase already
treats as canonical elsewhere (execution/mnq_strat_evidence.py::
MNQ_COMMISSION_ROUND_TRIP, execution/mes_trend_consolidation_break_evidence.py::
MES_COMMISSION_ROUND_TRIP, scripts/four_hr_retrigger_stop_study.py, etc. --
all $1.48). NOTE: an earlier corpus_v1_report.py docstring claimed
"commissions ... included" for the Corpus v1 run; that claim does not hold
under this same grep and is corrected here, not repeated.

Usage:
    python3 scripts/strat_212_122_canonical_evidence_report.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adaptive.journal_reader import JournalReader  # noqa: E402

INSTRUMENTS = ("MNQ", "MES")
STRATEGIES = ("strat_212", "strat_122")
LOG_BASE = Path("logs/replay_strat212_122_canonical")

FULL_START, FULL_END = "2025-07-24", "2026-07-23"
H1 = ("2025-07-24", "2026-01-23")
H2 = ("2026-01-24", "2026-07-23")
QUARTERS = [
    ("Q1", "2025-07-24", "2025-10-23"),
    ("Q2", "2025-10-24", "2026-01-23"),
    ("Q3", "2026-01-24", "2026-04-23"),
    ("Q4", "2026-04-24", "2026-07-23"),
]

COMMISSION_RT = 1.48  # $/round-trip, same constant used everywhere else in this codebase


def _load_trades(log_dir: Path, instrument: str) -> list[dict]:
    reader = JournalReader(log_dir)
    trades: list[dict] = []
    for path in sorted(log_dir.glob("journal_*.jsonl")):
        day = _date.fromisoformat(path.stem.replace("journal_", ""))
        for record in reader._trades_for_day(day):
            trades.append({
                "date": record.date,
                "month": record.date[:7],
                "instrument": instrument,
                "strategy": record.strategy,
                "direction": record.direction,
                "session": record.session or "unknown",
                "result": record.result,
                "pnl": record.pnl_dollars,
                "unjoinable_legacy": record.unjoinable_legacy,
            })
    return trades


def _in_range(d: str, start: str, end: str) -> bool:
    return start <= d <= end


def _resolved(trades: list[dict]) -> list[dict]:
    return [t for t in trades if t["result"] in ("WIN", "LOSS")]


def _stats(trades: list[dict], commission_rt: float = 0.0) -> dict:
    resolved = _resolved(trades)
    wins = [t for t in resolved if t["result"] == "WIN"]
    losses = [t for t in resolved if t["result"] == "LOSS"]
    unjoinable = sum(1 for t in trades if t["unjoinable_legacy"])
    open_with_identity = sum(1 for t in trades if not t["unjoinable_legacy"] and t["result"] is None)

    def net(t: dict) -> float:
        return (t["pnl"] or 0.0) - commission_rt

    pnl = sum(net(t) for t in resolved)
    gross_win = sum(net(t) for t in wins)
    gross_loss = sum(net(t) for t in losses)  # negative
    if gross_loss < 0:
        pf = round(gross_win / abs(gross_loss), 3)
    elif gross_win > 0:
        pf = float("inf")
    else:
        pf = None

    win_nets = [net(t) for t in wins]
    loss_nets = [net(t) for t in losses]

    return {
        "attempts": len(trades),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "open_with_identity": open_with_identity,
        "unjoinable_legacy": unjoinable,
        "win_rate": round(len(wins) / len(resolved), 4) if resolved else None,
        "gross_win": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(pnl, 2),
        "profit_factor": pf,
        "expectancy": round(pnl / len(resolved), 2) if resolved else None,
        "avg_win": round(sum(win_nets) / len(win_nets), 2) if win_nets else None,
        "avg_loss": round(sum(loss_nets) / len(loss_nets), 2) if loss_nets else None,
        "largest_win": round(max(win_nets), 2) if win_nets else None,
        "largest_loss": round(min(loss_nets), 2) if loss_nets else None,
    }


def _drawdown_and_streaks(trades: list[dict], commission_rt: float = 0.0) -> dict:
    """Chronological equity curve over resolved trades only (unjoinable/open excluded).
    Max drawdown = largest peak-to-trough equity decline. Consecutive losses = longest
    run of LOSS results in chronological (date, then journal) order."""
    resolved = sorted(_resolved(trades), key=lambda t: t["date"])
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    cur_loss_streak = 0
    max_loss_streak = 0
    for t in resolved:
        equity += (t["pnl"] or 0.0) - commission_rt
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if t["result"] == "LOSS":
            cur_loss_streak += 1
            max_loss_streak = max(max_loss_streak, cur_loss_streak)
        else:
            cur_loss_streak = 0
    return {
        "max_drawdown": round(max_dd, 2),
        "max_consecutive_losses": max_loss_streak,
        "final_equity": round(equity, 2),
    }


def _concentration(trades: list[dict], commission_rt: float = 0.0) -> dict:
    resolved = _resolved(trades)
    nets = sorted(((t["pnl"] or 0.0) - commission_rt for t in resolved), reverse=True)
    total = sum(nets)
    ex_top1 = round(total - (nets[0] if nets else 0.0), 2)
    ex_top3 = round(total - sum(nets[:3]), 2)
    ex_top5 = round(total - sum(nets[:5]), 2)
    top1_share = round(nets[0] / total, 4) if nets and total else None
    top3_share = round(sum(nets[:3]) / total, 4) if nets and total else None
    top5_share = round(sum(nets[:5]) / total, 4) if nets and total else None
    return {
        "net_pnl": round(total, 2),
        "ex_top1_winner_net_pnl": ex_top1,
        "ex_top3_winners_net_pnl": ex_top3,
        "ex_top5_winners_net_pnl": ex_top5,
        "top1_share_of_net": top1_share,
        "top3_share_of_net": top3_share,
        "top5_share_of_net": top5_share,
    }


def _period_block(trades: list[dict], start: str, end: str, commission_rt: float) -> dict:
    t = [x for x in trades if _in_range(x["date"], start, end)]
    return {
        "range": [start, end],
        "raw": _stats(t, 0.0),
        "commission_adjusted": _stats(t, commission_rt),
    }


def _full_block(trades: list[dict], commission_rt: float) -> dict:
    return {
        "full_period": _period_block(trades, FULL_START, FULL_END, commission_rt),
        "h1": _period_block(trades, *H1, commission_rt=commission_rt),
        "h2": _period_block(trades, *H2, commission_rt=commission_rt),
        "quarterly": {
            label: _period_block(trades, qs, qe, commission_rt)
            for label, qs, qe in QUARTERS
        },
        "by_direction": {
            d: {"raw": _stats(v, 0.0), "commission_adjusted": _stats(v, commission_rt)}
            for d, v in _group(trades, "direction").items()
        },
        "by_session": {
            s: {"raw": _stats(v, 0.0), "commission_adjusted": _stats(v, commission_rt)}
            for s, v in _group(trades, "session").items()
        },
        "by_month_full": {
            m: {"raw": _stats(v, 0.0), "commission_adjusted": _stats(v, commission_rt)}
            for m, v in _group(trades, "month").items()
        },
        "drawdown_raw": _drawdown_and_streaks(trades, 0.0),
        "drawdown_commission_adjusted": _drawdown_and_streaks(trades, commission_rt),
        "concentration_raw": _concentration(trades, 0.0),
        "concentration_commission_adjusted": _concentration(trades, commission_rt),
    }


def _group(trades: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[t[key]].append(t)
    return dict(sorted(groups.items()))


def _fmt(v, money: bool = False) -> str:
    if v is None:
        return "—"
    if money:
        return f"${v:,.2f}"
    if isinstance(v, float):
        return f"{v * 100:.1f}%"
    return str(v)


def main() -> int:
    all_trades: list[dict] = []
    per_instrument: dict[str, list[dict]] = {}

    for instr in INSTRUMENTS:
        log_dir = LOG_BASE / instr
        if not log_dir.exists():
            print(f"[report] MISSING log dir: {log_dir}", file=sys.stderr)
            return 1
        trades = _load_trades(log_dir, instr)
        per_instrument[instr] = trades
        all_trades.extend(trades)

    results: dict = {
        "meta": {
            "status": "FINAL -- both shared-engine blockers fixed and incorporated: "
                       "market-condition parity (PR #338, main@0057bc23) and cross-day "
                       "position carry-forward (PR #339, main@4684947). Evidence-"
                       "completeness pass complete: see "
                       "strat_212_122_slippage_sensitivity_results.json (1/2/3-tick "
                       "adverse-slippage sweep) and "
                       "strat_212_122_evidence_completeness_classification.md (per-cell "
                       "VALIDATED/PROMISING BUT UNPROVEN/BROKEN/OVERFIT/WAIT verdicts).",
            "status_note": "This run reads data/replay_corpus_v1_market_condition_fixed "
                     "(post-#338, market-condition matches runtime/Pine per "
                     "scripts/rematerialize_market_condition_corpus.py) against the "
                     "post-#339 replay engine (self._carried_positions resolves any "
                     "position still open at a daily-candle-file boundary against the "
                     "next chronological day's candles, with correct cross-day risk-state "
                     "propagation and instrument isolation). Prior runs preserved: pre-#338 "
                     "as strat_212_122_canonical_evidence_results_pre_pr338_superseded.json, "
                     "post-#338/pre-#339 as "
                     "strat_212_122_canonical_evidence_results_pre_pr339_partially_corrected.json. "
                     "avg_win/avg_loss/largest_win/largest_loss and top-3 winner concentration "
                     "added to _stats()/_concentration() in this evidence-completeness pass "
                     "(additive only -- all pre-existing fields/values unchanged, verified by "
                     "diff against the prior committed results.json).",
            "corpus": "data/replay_corpus_v1_market_condition_fixed (post-#320-fix directional, "
                      "post-#338 market-condition-parity-corrected, 313 days/instrument)",
            "range": [FULL_START, FULL_END],
            "instruments": list(INSTRUMENTS),
            "strategies": list(STRATEGIES),
            "commission_round_trip_usd": COMMISSION_RT,
            "note": "PaperBroker/JournalLogger apply no commission model; "
                     "commission_adjusted blocks subtract COMMISSION_RT per "
                     "resolved trade at the analysis layer only.",
        },
        "combined": {},
        "per_instrument": {},
        "per_strategy_combined": {},
        "per_instrument_per_strategy": {},
    }

    results["combined"] = _full_block(all_trades, COMMISSION_RT)

    for instr in INSTRUMENTS:
        results["per_instrument"][instr] = _full_block(per_instrument[instr], COMMISSION_RT)
        for strat in STRATEGIES:
            sub = [t for t in per_instrument[instr] if t["strategy"] == strat]
            results["per_instrument_per_strategy"].setdefault(instr, {})[strat] = _full_block(sub, COMMISSION_RT)

    for strat in STRATEGIES:
        sub = [t for t in all_trades if t["strategy"] == strat]
        results["per_strategy_combined"][strat] = _full_block(sub, COMMISSION_RT)

    out_path = Path("scripts/strat_212_122_canonical_evidence_results.json")
    out_path.write_text(json.dumps(results, indent=2, default=str) + "\n")
    print(f"[report] wrote {out_path}")

    raw_path = Path("scripts/strat_212_122_canonical_evidence_raw_trades.jsonl")
    with raw_path.open("w") as f:
        for t in sorted(all_trades, key=lambda x: (x["instrument"], x["strategy"], x["date"])):
            f.write(json.dumps(t) + "\n")
    print(f"[report] wrote {raw_path}")

    # ── stdout summary ──────────────────────────────────────────────
    print("\n=== FULL PERIOD (2025-07-24 -> 2026-07-23), by instrument x strategy ===")
    print("| Instrument | Strategy | N | Resolved | WR | Net P&L (raw) | Net P&L (comm-adj) | PF (raw) | PF (comm-adj) |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for instr in INSTRUMENTS:
        for strat in STRATEGIES:
            b = results["per_instrument_per_strategy"][instr][strat]["full_period"]
            r, c = b["raw"], b["commission_adjusted"]
            print(f"| {instr} | {strat} | {r['attempts']} | {r['resolved']} | {_fmt(r['win_rate'])} "
                  f"| {_fmt(r['net_pnl'], True)} | {_fmt(c['net_pnl'], True)} "
                  f"| {_fmt(r['profit_factor'])} | {_fmt(c['profit_factor'])} |")

    print("\n=== Per-cell (instrument x strategy) trade magnitude, full period, raw ===")
    print("| Instrument | Strategy | Avg Win | Avg Loss | Largest Win | Largest Loss |")
    print("|---|---|---:|---:|---:|---:|")
    for instr in INSTRUMENTS:
        for strat in STRATEGIES:
            r = results["per_instrument_per_strategy"][instr][strat]["full_period"]["raw"]
            print(f"| {instr} | {strat} | {_fmt(r['avg_win'], True)} | {_fmt(r['avg_loss'], True)} "
                  f"| {_fmt(r['largest_win'], True)} | {_fmt(r['largest_loss'], True)} |")

    print("\n=== Per-cell (instrument x strategy) winner concentration, full period, raw ===")
    print("| Instrument | Strategy | Net P&L | Top-1 share | Top-3 share | Top-5 share | Ex-top-3 net |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for instr in INSTRUMENTS:
        for strat in STRATEGIES:
            sub = [t for t in per_instrument[instr] if t["strategy"] == strat]
            conc = _concentration(sub, 0.0)
            print(f"| {instr} | {strat} | {_fmt(conc['net_pnl'], True)} | {_fmt(conc['top1_share_of_net'])} "
                  f"| {_fmt(conc['top3_share_of_net'])} | {_fmt(conc['top5_share_of_net'])} "
                  f"| {_fmt(conc['ex_top3_winners_net_pnl'], True)} |")

    print("\n=== Per-strategy combined (both instruments), full period ===")
    for strat in STRATEGIES:
        b = results["per_strategy_combined"][strat]["full_period"]
        r, c = b["raw"], b["commission_adjusted"]
        print(f"\n-- {strat} --")
        print(f"  attempts={r['attempts']} resolved={r['resolved']} unjoinable={r['unjoinable_legacy']} "
              f"open_with_identity={r['open_with_identity']}")
        print(f"  raw:          WR={_fmt(r['win_rate'])} net={_fmt(r['net_pnl'], True)} "
              f"PF={_fmt(r['profit_factor'])} expectancy={_fmt(r['expectancy'], True)}")
        print(f"  comm-adjusted: WR={_fmt(c['win_rate'])} net={_fmt(c['net_pnl'], True)} "
              f"PF={_fmt(c['profit_factor'])} expectancy={_fmt(c['expectancy'], True)}")

    print("\n=== H1 vs H2 walk-forward, per strategy combined ===")
    print("| Strategy | Half | N resolved | Net P&L (raw) | Net P&L (comm-adj) |")
    print("|---|---|---:|---:|---:|")
    for strat in STRATEGIES:
        block = results["per_strategy_combined"][strat]
        for label, half in (("H1", block["h1"]), ("H2", block["h2"])):
            r, c = half["raw"], half["commission_adjusted"]
            print(f"| {strat} | {label} | {r['resolved']} | {_fmt(r['net_pnl'], True)} | {_fmt(c['net_pnl'], True)} |")

    print("\n=== Long/short split, per strategy combined ===")
    print("| Strategy | Direction | N resolved | Net P&L (raw) | WR |")
    print("|---|---|---:|---:|---:|")
    for strat in STRATEGIES:
        by_dir = results["per_strategy_combined"][strat]["by_direction"]
        for d, block in by_dir.items():
            r = block["raw"]
            print(f"| {strat} | {d} | {r['resolved']} | {_fmt(r['net_pnl'], True)} | {_fmt(r['win_rate'])} |")

    print("\n=== Session split, per strategy combined ===")
    print("| Strategy | Session | N resolved | Net P&L (raw) | WR |")
    print("|---|---|---:|---:|---:|")
    for strat in STRATEGIES:
        by_sess = results["per_strategy_combined"][strat]["by_session"]
        for s, block in by_sess.items():
            r = block["raw"]
            print(f"| {strat} | {s} | {r['resolved']} | {_fmt(r['net_pnl'], True)} | {_fmt(r['win_rate'])} |")

    print("\n=== Drawdown / streak / concentration, per strategy combined (raw, then comm-adjusted) ===")
    for strat in STRATEGIES:
        block = results["per_strategy_combined"][strat]
        print(f"\n-- {strat} --")
        print(f"  raw:           max_dd=${block['drawdown_raw']['max_drawdown']:,.2f} "
              f"max_consec_losses={block['drawdown_raw']['max_consecutive_losses']} "
              f"ex_top1=${block['concentration_raw']['ex_top1_winner_net_pnl']:,.2f} "
              f"ex_top3=${block['concentration_raw']['ex_top3_winners_net_pnl']:,.2f} "
              f"ex_top5=${block['concentration_raw']['ex_top5_winners_net_pnl']:,.2f} "
              f"top1_share={_fmt(block['concentration_raw']['top1_share_of_net'])} "
              f"top3_share={_fmt(block['concentration_raw']['top3_share_of_net'])}")
        print(f"  comm-adjusted: max_dd=${block['drawdown_commission_adjusted']['max_drawdown']:,.2f} "
              f"max_consec_losses={block['drawdown_commission_adjusted']['max_consecutive_losses']} "
              f"ex_top1=${block['concentration_commission_adjusted']['ex_top1_winner_net_pnl']:,.2f} "
              f"ex_top5=${block['concentration_commission_adjusted']['ex_top5_winners_net_pnl']:,.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
