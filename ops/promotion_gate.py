"""Strategy Promotion Proof Gate.

Prevents standalone research/backtest results from being mistaken for
executable strategy evidence. Composes existing machinery instead of
re-running a strategy-specific replay:

  - docs/strategy-rules/Strategy_Inventory.md   -- documented 8-gate status
    and verdict (IDENTITY / PARITY section; read, never edited)
  - ops.evidence_lane_health.build_snapshot()   -- live paper-forward lane
    mode/health, the real-runtime execution context (RiskEngine/PaperBroker
    already sit behind these lanes; see execution/mnq_strat_evidence.py and
    execution/mes_trend_consolidation_break_evidence.py)
  - ops.live_box_guard proof-critical overrides -- actual entry fill model,
    effective tolerance, and contract cap currently in force
  - ops.trade_chain.trade_chain_report()        -- paper-forward accounting
    (attempts/fills/cancellations/resolved/open) and PASS/FAIL integrity

A caller may optionally pass a path to a standalone research evidence JSON
(e.g. one of the scripts/*_canonical_evidence_results.json files). Its
content is surfaced verbatim under RESEARCH RESULT, explicitly labeled as
unverified by this gate — it is never used to compute PAPER FORWARD EVIDENCE
or the classification's pass/fail thresholds.

This gate never changes runtime, risk, or strategy config; never merges;
never deploys.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from execution.mes_trend_consolidation_break_evidence import lane_mode as mes_lane_mode
from execution.mnq_strat_evidence import LANES as MNQ_LANES, lane_mode as mnq_lane_mode
from ops.evidence_lane_health import build_snapshot as evidence_lane_snapshot
from ops.evidence_readiness import MIN_PROFIT_FACTOR, STRATEGY_MIN_EXAMPLES
from ops.live_box_guard import live_box_drift_report
from ops.proof_30_mnq import read_journal_entries
from ops.trade_chain import FILLED_CATEGORIES, classify_outcome, pair_trades, trade_chain_report

VERDICTS = (
    "VALIDATED",
    "PROMISING BUT UNPROVEN",
    "BROKEN",
    "OVERFIT",
    "UNSAFE",
    "WAIT",
)

FILL_MODEL_OVERRIDE_NAMES = (
    "ENTRY_FILL_MODEL",
    "ENTRY_SLIPPAGE_TOLERANCE_TICKS",
    "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES",
    "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ",
    "TRADOVATE_ENTRY_EXECUTION_MODE",
    "MARKETABLE_LIMIT_TICKS",
    "MARKETABLE_LIMIT_TICKS_MES",
    "MARKETABLE_LIMIT_TICKS_MNQ",
    "STOP_LIMIT_ALLOWANCE_TICKS",
    "MAX_CONTRACTS_HARD_CAP",
    "FILL_SLIPPAGE_TICKS",
    "FILL_PESSIMISTIC_BOTH_HIT",
)

DEFAULT_INVENTORY_PATH = Path("docs/strategy-rules/Strategy_Inventory.md")

_TABLE_ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|(.+)\|\s*$")


def _strategy_lane(strategy: str) -> tuple[str, str] | None:
    """Map a strategy identifier to (instrument, evidence_lane_health lane name)."""
    if strategy in MNQ_LANES:
        return "MNQ", strategy
    if strategy in ("trend_consolidation_break", "strat_trend_consolidation_break"):
        return "MES", "trend_consolidation_break"
    return None


def parse_master_table(inventory_text: str) -> list[dict[str, str]]:
    """Parse Strategy_Inventory.md's '## Master Table' into row dicts.

    Stops at the first non-table block after the header row so later
    per-strategy tables (which reuse similar-looking pipe rows) are not
    swept in. Best-effort markdown parsing, not a schema contract.
    """
    header_seen = False
    columns: list[str] = []
    rows: list[dict[str, str]] = []
    for line in inventory_text.splitlines():
        if not line.strip().startswith("|"):
            if header_seen and rows:
                break
            header_seen = False
            continue
        match = _TABLE_ROW_RE.match(line.strip())
        if not match:
            continue
        first_cell = match.group(1).strip()
        rest = [cell.strip() for cell in match.group(2).split("|")]
        if not header_seen:
            if first_cell.lower() == "strategy":
                columns = [first_cell] + rest
                header_seen = True
            continue
        if set(first_cell) <= {"-", ":"}:
            continue
        rows.append(dict(zip(columns, [first_cell] + rest)))
    return rows


def find_inventory_row(inventory_text: str, strategy_query: str) -> dict[str, Any]:
    """Best-effort lookup of a strategy row in Strategy_Inventory.md's master table.

    Returns UNKNOWN with candidates instead of guessing when the match is
    ambiguous or absent -- this document uses display names, not the
    strategy identifiers used in risk_rules.yaml / journal setup.strategy.
    """
    query = strategy_query.strip().lower()
    query_tokens = set(re.split(r"[^a-z0-9]+", query))
    matches: list[dict[str, Any]] = []
    for row in parse_master_table(inventory_text):
        row_name = re.sub(r"\*\*|`", "", row.get("Strategy", ""))
        row_tokens = set(re.split(r"[^a-z0-9]+", row_name.lower()))
        if query in row_name.lower() or (query_tokens and query_tokens <= row_tokens):
            matches.append(row)

    if len(matches) == 1:
        row = matches[0]
        return {
            "status": "FOUND",
            "row_name": row.get("Strategy"),
            "documented_verdict": re.sub(r"\*\*", "", row.get("Verdict", "")).strip() or None,
            "raw_row": row,
        }
    if not matches:
        return {"status": "NOT_FOUND", "row_name": None, "documented_verdict": None, "raw_row": None}
    return {
        "status": "AMBIGUOUS",
        "row_name": None,
        "documented_verdict": None,
        "raw_row": None,
        "candidates": [row.get("Strategy") for row in matches],
    }


def _identity_parity(repo_root: Path, strategy: str, inventory_path: Path) -> dict[str, Any]:
    full_path = inventory_path if inventory_path.is_absolute() else repo_root / inventory_path
    if not full_path.exists():
        return {"status": "UNKNOWN", "reason": f"{full_path} not found", "source": str(full_path)}
    text = full_path.read_text(encoding="utf-8")
    row = find_inventory_row(text, strategy)
    row["source"] = str(full_path)
    row["source_caveat"] = (
        "As last edited in Strategy_Inventory.md; this gate does not "
        "re-verify rules/detector/replay-parity/honest-fills/walk-forward/"
        "slippage/sample dimensions itself."
    )
    return row


def _research_result(research_evidence_path: str | Path | None) -> dict[str, Any]:
    if research_evidence_path is None:
        return {"status": "NOT_PROVIDED"}
    path = Path(research_evidence_path)
    if not path.exists():
        return {"status": "NOT_FOUND", "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "UNREADABLE", "path": str(path), "error": str(exc)}
    summary_keys = (
        "net_pnl", "net_dollars", "profit_factor", "pf", "expectancy",
        "win_rate", "total_trades", "trade_count", "max_drawdown",
    )
    summary = {key: data[key] for key in summary_keys if isinstance(data, dict) and key in data}
    return {
        "status": "PROVIDED_UNVERIFIED",
        "path": str(path),
        "summary": summary,
        "caveat": (
            "Standalone research result, not independently verified by this gate. "
            "Never used to compute paper-forward evidence or classification thresholds."
        ),
    }


def _runtime_execution_context(repo_root: Path, strategy: str, log_dir: str | Path) -> dict[str, Any]:
    drift = live_box_drift_report(repo_root=repo_root, log_dir=log_dir)
    overrides = {
        item["name"]: {"observed": item["observed"], "active": item["active"]}
        for item in drift.get("proof_critical_runtime_overrides", [])
        if item["name"] in FILL_MODEL_OVERRIDE_NAMES
    }
    lane_ref = _strategy_lane(strategy)
    lane_mode_value = "UNKNOWN"
    if lane_ref:
        instrument, lane = lane_ref
        lane_mode_value = mnq_lane_mode(lane) if instrument == "MNQ" else mes_lane_mode()
    return {
        "fill_model_and_tolerance_overrides": overrides,
        "lane_execution_mode": lane_mode_value,
        "lane_mapping": {"instrument": lane_ref[0], "lane": lane_ref[1]} if lane_ref else "UNKNOWN — strategy is not one of the live paper-forward lanes",
    }


def _lane_snapshot_for(strategy: str, log_dir: str | Path) -> dict[str, Any]:
    lane_ref = _strategy_lane(strategy)
    if lane_ref is None:
        return {"status": "UNKNOWN", "reason": "strategy is not a registered MES/MNQ evidence lane"}
    _, lane_name = lane_ref
    snapshot = evidence_lane_snapshot(log_dir)
    for lane in snapshot["lanes"]:
        if lane["lane"] == lane_name:
            return lane
    return {"status": "UNKNOWN", "reason": f"lane {lane_name!r} not present in today's snapshot"}


def _performance_stats(resolved_pairs: list[tuple[dict, dict]]) -> dict[str, Any]:
    filled = [
        (trade, outcome) for trade, outcome in resolved_pairs
        if classify_outcome(outcome.get("outcome") or {}) in FILLED_CATEGORIES
    ]
    pnls = [float((outcome.get("outcome") or {}).get("pnl_dollars") or 0.0) for _t, outcome in filled]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    return {
        "filled_trade_count": len(filled),
        "net_pnl_dollars": round(sum(pnls), 2),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(filled), 4) if filled else None,
        "profit_factor": (round(profit_factor, 3) if profit_factor not in (float("inf"),) else "inf") if filled else None,
        "expectancy_dollars": round(sum(pnls) / len(filled), 2) if filled else None,
    }


def _classify(
    *,
    documented_verdict: str | None,
    accounting: dict[str, Any],
    performance: dict[str, Any],
    chain_ok: bool,
    chain_problems: list[str],
) -> dict[str, Any]:
    attempts = accounting["attempts"]
    fills = accounting["fills"]

    if documented_verdict and documented_verdict.upper() in ("BROKEN", "RETIRE"):
        return {
            "verdict": "BROKEN",
            "why": (
                f"Strategy_Inventory.md already documents this strategy as "
                f"{documented_verdict!r}. This gate does not re-litigate a "
                "documented failure without new evidence; no rescue/tuning "
                "variant is evaluated in this pass."
            ),
        }
    if attempts == 0:
        return {
            "verdict": "WAIT",
            "why": (
                "Zero order attempts observed in paper-forward evidence for this "
                "strategy/lane. Standalone research or replay results alone do not "
                "authorize promotion — see 'What Does Not Authorize Execution' in "
                "Strategy_Inventory.md."
            ),
        }
    if fills == 0:
        return {
            "verdict": "BROKEN",
            "why": (
                f"Zero executable fills across {attempts} order attempt(s). The real "
                "executable path (RiskEngine/PaperBroker/entry fill model) rejects or "
                "cancels the entire observed population, matching the Miyagi and 60M "
                "3-2-2 precedent where standalone evidence looked positive but the real "
                "system rejected most/all of the population."
            ),
        }
    if not chain_ok:
        return {
            "verdict": "UNSAFE",
            "why": (
                "Trade-chain integrity problems were found in the paper-forward journal "
                f"evidence for this strategy: {'; '.join(chain_problems)}. Evidence integrity "
                "cannot be certified until these are resolved."
            ),
        }
    if performance["filled_trade_count"] < STRATEGY_MIN_EXAMPLES:
        return {
            "verdict": "PROMISING BUT UNPROVEN",
            "why": (
                f"Only {performance['filled_trade_count']} filled paper-forward trade(s), below "
                f"the {STRATEGY_MIN_EXAMPLES}-trade minimum sample used by Strategy_Inventory.md's "
                "pipeline gates."
            ),
        }
    pf = performance["profit_factor"]
    pf_value = float("inf") if pf == "inf" else (pf or 0.0)
    if pf_value < MIN_PROFIT_FACTOR:
        return {
            "verdict": "OVERFIT" if documented_verdict in ("PROMISING BUT UNPROVEN", "VALIDATED", "PAPER PROOF") else "BROKEN",
            "why": (
                f"Paper-forward profit factor {pf} is below the {MIN_PROFIT_FACTOR} minimum despite "
                f"{performance['filled_trade_count']} filled trades. The documented research verdict "
                f"({documented_verdict or 'none on file'}) is not reproduced by the real executable path."
            ),
        }
    if documented_verdict and documented_verdict.upper() in ("VALIDATED", "PAPER PROOF"):
        return {
            "verdict": "VALIDATED",
            "why": (
                f"Paper-forward evidence ({performance['filled_trade_count']} filled trades, PF {pf}) "
                f"is consistent with the documented verdict ({documented_verdict})."
            ),
        }
    return {
        "verdict": "PROMISING BUT UNPROVEN",
        "why": (
            f"Paper-forward evidence ({performance['filled_trade_count']} filled trades, PF {pf}) clears "
            "the sample and PF thresholds, but Strategy_Inventory.md does not document a VALIDATED/PAPER "
            f"PROOF verdict for this strategy (documented: {documented_verdict or 'not found'}). Promotion "
            "requires that document to be updated by a human reviewer, not this gate."
        ),
    }


def build_promotion_report(
    strategy: str,
    *,
    repo_root: str | Path,
    log_dir: str | Path = "logs",
    journal_dir: str | Path | None = None,
    research_evidence_path: str | Path | None = None,
    inventory_path: str | Path = DEFAULT_INVENTORY_PATH,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    journal_root = Path(journal_dir) if journal_dir else root / log_dir

    identity_parity = _identity_parity(root, strategy, Path(inventory_path))
    research_result = _research_result(research_evidence_path)
    runtime_context = _runtime_execution_context(root, strategy, log_dir)
    lane_snapshot = _lane_snapshot_for(strategy, log_dir)

    chain = trade_chain_report(journal_root, strategy=strategy)
    entries = read_journal_entries(journal_root)
    pairing = pair_trades(entries, strategy=strategy)
    performance = _performance_stats(pairing["resolved"])

    classification = _classify(
        documented_verdict=identity_parity.get("documented_verdict"),
        accounting=chain["accounting"],
        performance=performance,
        chain_ok=chain["ok"],
        chain_problems=chain["problems"],
    )

    return {
        "strategy": strategy,
        "read_only": True,
        "identity_parity": identity_parity,
        "research_result": research_result,
        "runtime_execution_context": runtime_context,
        "lane_snapshot": lane_snapshot,
        "paper_forward_evidence": {
            "journal_dir": str(journal_root),
            "accounting": chain["accounting"],
            "performance": performance,
            "zero_executable_fills": chain["accounting"]["fills"] == 0,
            "trade_chain_ok": chain["ok"],
            "trade_chain_problems": chain["problems"],
        },
        "classification": classification,
        "no_rescue_tuning_this_pass": True,
        "no_automatic_runtime_change": True,
        "no_automatic_merge": True,
        "no_deployment": True,
    }


def format_promotion_report(report: dict[str, Any]) -> str:
    lines = [
        f"STRATEGY PROMOTION PROOF GATE: {report['strategy']}",
        f"Classification: {report['classification']['verdict']}",
        f"  {report['classification']['why']}",
        "",
        "Identity/parity (Strategy_Inventory.md):",
        f"  status={report['identity_parity'].get('status')} "
        f"documented_verdict={report['identity_parity'].get('documented_verdict')}",
        "",
        "Runtime execution context:",
        f"  lane_mode={report['runtime_execution_context'].get('lane_execution_mode')} "
        f"lane_mapping={report['runtime_execution_context'].get('lane_mapping')}",
        "",
        "Paper-forward evidence (real journal, not standalone research):",
    ]
    pfe = report["paper_forward_evidence"]
    acc = pfe["accounting"]
    lines.append(
        f"  attempts={acc['attempts']} fills={acc['fills']} "
        f"cancellations={acc['cancellations_or_no_fill']} resolved={acc['resolved']} "
        f"open={acc['legitimately_open']}"
    )
    if pfe["zero_executable_fills"]:
        lines.append("  ZERO EXECUTABLE FILLS")
    perf = pfe["performance"]
    lines.append(
        f"  filled_trades={perf['filled_trade_count']} net_pnl=${perf['net_pnl_dollars']} "
        f"win_rate={perf['win_rate']} pf={perf['profit_factor']} expectancy=${perf['expectancy_dollars']}"
    )
    if not pfe["trade_chain_ok"]:
        lines.append("  trade-chain problems:")
        lines.extend(f"    - {p}" for p in pfe["trade_chain_problems"])
    research = report["research_result"]
    lines.append("")
    lines.append(f"Research result (standalone, unverified): {research['status']}")
    if research.get("summary"):
        lines.append(f"  {research['summary']}")
    return "\n".join(lines)
