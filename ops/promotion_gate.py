"""Read-only Strategy Promotion Proof Gate.

WHY THIS EXISTS: this repo has repeatedly seen a strategy look profitable in
standalone research/backtest/detector output, then fail once it is actually
run through the real executable path (ReplayEngine -> DecisionEngine ->
RiskEngine -> PaperBroker with the real configured entry-fill model and risk
controls). The 12HR "Miyagi" and "60M 3-2-2" strategies are the named
precedents in docs/strategy-rules/Strategy_Inventory.md: standalone evidence
looked positive, but the real system rejected most/all of the population. A
strategy may NOT be called paper-ready merely because detector output or a
standalone replay was profitable — promotion must be proven through the real
executable path.

WHAT THIS MODULE IS: an aggregator/classifier over evidence artifacts that
ALREADY exist on disk (results.json + raw_trades.jsonl produced by scripts
like scripts/orb_breakout_canonical_evidence.py,
scripts/vwap_reclaim_canonical_evidence.py, and
scripts/strat_212_122_canonical_evidence_run.py /
scripts/strat_212_122_canonical_evidence_report.py), plus the journal (via
ops.proof_30_mnq) and risk_rules.yaml / config.settings. It does NOT invoke
ReplayEngine or any strategy-replay machinery itself — that would duplicate
the exact machinery that already exists and risks subtly diverging from the
real pipeline. Building a second replay driver is explicitly out of scope.

WHAT THIS MODULE NEVER DOES: bypass or exempt a real account risk control to
reproduce research numbers; generate or suggest a "rescue"/tuning variant in
the same pass a strategy fails; change strategy/runtime/risk/broker config;
deploy, merge, or auto-tune anything. If a strategy fails, this module's job
is to say why and stop.

UNKNOWN-NOT-INVENTED RULE: every field in the required report is either a
real, traceable value read from an artifact/config/journal, or the literal
string "UNKNOWN -- <reason>" explaining what data would be needed and why it
is not available. Nothing here is guessed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.proof_30_mnq import (  # noqa: E402
    classify_outcome,
    pair_resolved_trades,
    parse_proof_ts,
    read_journal_entries,
)

try:
    from ops.evidence_report import LANE_CLASS, LANE_NOTES  # noqa: E402
except Exception:  # pragma: no cover - defensive only, evidence_report is owned here too
    LANE_CLASS, LANE_NOTES = {}, {}

VERDICTS = (
    "VALIDATED",
    "PROMISING BUT UNPROVEN",
    "BROKEN",
    "OVERFIT",
    "UNSAFE",
    "WAIT",
)

DEFAULT_JOURNAL_DIR = REPO_ROOT / "logs"
DEFAULT_RISK_RULES_PATH = REPO_ROOT / "risk_rules.yaml"
DEFAULT_SAMPLE_ADEQUATE_MIN = 30

# The shared H1/H2 walk-forward convention used identically by
# scripts/orb_breakout_canonical_evidence.py, scripts/vwap_reclaim_canonical_evidence.py,
# and scripts/strat_212_122_canonical_evidence_run.py. Only used as a fallback
# split (clearly labelled as derived, never as an artifact-native field) when
# an artifact's own meta.range matches this exact range and its raw_trades
# rows carry no "half" field of their own.
KNOWN_FULL_RANGE = ["2025-07-24", "2026-07-23"]
KNOWN_HALVES = {
    "H1": ("2025-07-24", "2026-01-23"),
    "H2": ("2026-01-24", "2026-07-23"),
}

RESOLVED_RESULTS = {"WIN", "LOSS", "BREAKEVEN"}

# ── Strategy identity -> default canonical-evidence artifact locations ─────
# Learned by reading scripts/orb_breakout_canonical_evidence.py (--out/--raw
# defaults), scripts/vwap_reclaim_canonical_evidence.py (--out/--raw
# defaults), and scripts/strat_212_122_canonical_evidence_run.py +
# scripts/strat_212_122_canonical_evidence_report.py (hardcoded output paths,
# shared MNQ+MES/strat_212+strat_122 combined-book artifact, filtered here by
# the raw_trades.jsonl "strategy" field).
KNOWN_STRATEGIES: dict[str, dict[str, Any]] = {
    "orb_breakout": {
        "results": "scripts/orb_breakout_canonical_evidence_results.json",
        "raw_trades": "scripts/orb_breakout_canonical_evidence_raw_trades.jsonl",
        "canonical_script": "scripts/orb_breakout_canonical_evidence.py",
        "raw_trades_strategy_field": None,
        "note": "isolated single-strategy artifact (enabled_concepts=['orb_breakout'] only) -- "
        "every row belongs to this strategy, raw_trades.jsonl carries no 'strategy' field",
    },
    "vwap_reclaim": {
        "results": "scripts/vwap_reclaim_canonical_evidence_results.json",
        "raw_trades": "scripts/vwap_reclaim_canonical_evidence_raw_trades.jsonl",
        "canonical_script": "scripts/vwap_reclaim_canonical_evidence.py",
        "raw_trades_strategy_field": None,
        "note": "isolated single-strategy artifact (enabled_concepts=['vwap_reclaim'] only) -- "
        "every row belongs to this strategy, raw_trades.jsonl carries no 'strategy' field",
    },
    "strat_212": {
        "results": "scripts/strat_212_122_canonical_evidence_results.json",
        "raw_trades": "scripts/strat_212_122_canonical_evidence_raw_trades.jsonl",
        "canonical_script": "scripts/strat_212_122_canonical_evidence_run.py",
        "raw_trades_strategy_field": "strat_212",
        "note": "combined-book artifact (enabled_concepts=['strat_212','strat_122'], MNQ+MES) -- "
        "filtered to rows where raw_trades['strategy'] == 'strat_212'",
    },
    "strat_122": {
        "results": "scripts/strat_212_122_canonical_evidence_results.json",
        "raw_trades": "scripts/strat_212_122_canonical_evidence_raw_trades.jsonl",
        "canonical_script": "scripts/strat_212_122_canonical_evidence_run.py",
        "raw_trades_strategy_field": "strat_122",
        "note": "combined-book artifact (enabled_concepts=['strat_212','strat_122'], MNQ+MES) -- "
        "filtered to rows where raw_trades['strategy'] == 'strat_122'",
    },
    "strat_212_122": {
        "results": "scripts/strat_212_122_canonical_evidence_results.json",
        "raw_trades": "scripts/strat_212_122_canonical_evidence_raw_trades.jsonl",
        "canonical_script": "scripts/strat_212_122_canonical_evidence_run.py",
        "raw_trades_strategy_field": None,
        "note": "combined-book artifact covering BOTH strat_212 and strat_122 together, unfiltered -- "
        "pass --strategy strat_212 or --strategy strat_122 for a single-strategy verdict",
    },
}


# ── small generic helpers ───────────────────────────────────────────────────


def _unk(reason: str) -> str:
    return f"UNKNOWN — {reason}"


def is_unknown(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("UNKNOWN")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _sha256_text(path: Path) -> Optional[str]:
    import hashlib

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# ── artifact resolution / loading ───────────────────────────────────────────


def resolve_default_artifact_paths(
    strategy: str,
) -> tuple[Optional[Path], Optional[Path], Optional[Path], Optional[str]]:
    """Return (results_path, raw_trades_path, canonical_script_path, note) by convention.

    None for all four if the strategy is not a known convention -- caller must
    require --results/--raw-trades explicitly in that case.
    """
    entry = KNOWN_STRATEGIES.get(strategy)
    if not entry:
        return None, None, None, None
    return (
        REPO_ROOT / entry["results"],
        REPO_ROOT / entry["raw_trades"],
        REPO_ROOT / entry["canonical_script"],
        entry.get("note"),
    )


def load_results_json(path: Optional[Path]) -> tuple[Optional[dict], Optional[str]]:
    if path is None:
        return None, "no --results path given and no known default for this strategy"
    if not path.exists():
        return None, f"results.json not found at {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"failed to parse {path}: {exc}"


def load_raw_trades(path: Optional[Path]) -> tuple[Optional[list[dict]], list[str], Optional[str]]:
    """Returns (rows, per_line_read_errors, fatal_error)."""
    if path is None:
        return None, [], "no --raw-trades path given and no known default for this strategy"
    if not path.exists():
        return None, [], f"raw_trades.jsonl not found at {path}"
    rows: list[dict] = []
    read_errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return None, [], f"failed to read {path}: {exc}"
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            read_errors.append(f"{path}:{line_no}: invalid JSON ({exc})")
    return rows, read_errors, None


def filter_rows_for_strategy(
    rows: list[dict], strategy: str, raw_trades_strategy_field: Optional[str]
) -> tuple[list[dict], str]:
    field_name = raw_trades_strategy_field or strategy
    if any("strategy" in r for r in rows):
        filtered = [r for r in rows if str(r.get("strategy")) == field_name]
        note = f"filtered raw_trades rows to strategy == '{field_name}' ({len(filtered)}/{len(rows)} rows kept)"
        return filtered, note
    note = (
        "raw_trades rows carry no 'strategy' field -- assumed this is an isolated single-strategy "
        f"artifact (all {len(rows)} rows treated as belonging to '{strategy}'); this assumption is "
        "correct for the orb_breakout/vwap_reclaim canonical-evidence shape (enabled_concepts pinned "
        "to one strategy) but would be WRONG if a raw_trades.jsonl from a multi-strategy combined-book "
        "run were passed without a 'strategy' column"
    )
    return rows, note


def select_primary_scenario(rows: list[dict]) -> tuple[Optional[str], list[dict], list[str]]:
    """Pick one tagged scenario (run_tag/slippage_tag) as primary when an artifact
    concatenates multiple slippage/exit-mode sweeps in one raw_trades.jsonl (as
    orb_breakout and vwap_reclaim's canonical-evidence scripts do). Never silently
    mixes scenarios into one aggregate -- that would double-count/blend distinct runs.
    """
    tags = sorted({r.get("run_tag") or r.get("slippage_tag") for r in rows if (r.get("run_tag") or r.get("slippage_tag"))})
    if not tags:
        return None, rows, []
    preferred = [t for t in tags if "static" in t and "1tick" in t]
    if not preferred:
        preferred = [t for t in tags if "1tick" in t]
    if not preferred:
        preferred = [tags[0]]
    primary = preferred[0]
    primary_rows = [r for r in rows if (r.get("run_tag") or r.get("slippage_tag")) == primary]
    return primary, primary_rows, tags


def detect_pnl_field(rows: list[dict]) -> Optional[str]:
    for candidate in ("pnl_after_commission", "pnl", "pnl_dollars", "pnl_before_commission"):
        if any(candidate in r for r in rows):
            return candidate
    return None


# ── EXECUTION section ───────────────────────────────────────────────────────


def compute_execution_identity(rows: list[dict]) -> dict[str, Any]:
    if not rows:
        reason = "no rows for this strategy/scenario in the raw_trades artifact"
        return {
            "raw_candidate_count": 0,
            "candidates_reaching_risk_engine": _unk(reason),
            "candidates_approved": 0,
            "entry_attempts": 0,
            "fills": 0,
            "cancellations_no_fill": 0,
            "rejects_known_no_fills": _unk(reason),
            "resolved_outcomes": 0,
            "legitimately_open_positions": 0,
            "identity_attempts_eq_fills_plus_cancellations": None,
            "identity_fills_eq_resolved_plus_open": None,
            "identity_mismatches": [],
            "identity_checkable": False,
        }

    n = len(rows)
    has_filled_shape = any("filled" in r for r in rows)
    has_result_shape = any("result" in r for r in rows)

    if has_filled_shape:
        attempts = sum(int(_num(r.get("attempted", 1))) for r in rows)
        fills = sum(int(_num(r.get("filled", 0))) for r in rows)
        cancellations = sum(int(_num(r.get("cancelled_no_fill", 0))) for r in rows)
        resolved = sum(int(_num(r.get("resolved", 0))) for r in rows)
        open_ = sum(int(_num(r.get("open", 0))) for r in rows)
        rejects = 0  # not separately tracked by this artifact shape; folded into cancellations below

        mismatches: list[str] = []
        if attempts != fills + cancellations + rejects:
            mismatches.append(
                f"entry_attempts({attempts}) != fills({fills}) + cancellations_no_fill({cancellations}) "
                f"+ rejects(0, not separately tracked)"
            )
        if fills != resolved + open_:
            mismatches.append(f"fills({fills}) != resolved_outcomes({resolved}) + legitimately_open({open_})")

        return {
            "raw_candidate_count": n,
            "candidates_reaching_risk_engine": _unk(
                "this artifact's raw_trades.jsonl only records risk-APPROVED entries (the underlying "
                "canonical-evidence script's journal parser skips any decision != TRADE or risk_check "
                "!= APPROVED); pre-approval candidate/reject counts at the RiskEngine boundary are not "
                "captured in raw_trades.jsonl or results.json for this strategy"
            ),
            "candidates_approved": attempts,
            "entry_attempts": attempts,
            "fills": fills,
            "cancellations_no_fill": cancellations,
            "rejects_known_no_fills": _unk(
                f"this artifact does not separately distinguish a broker/engine reject from an IOC "
                f"no-fill cancellation; both are folded into cancelled_no_fill={cancellations}"
            ),
            "resolved_outcomes": resolved,
            "legitimately_open_positions": open_,
            "identity_attempts_eq_fills_plus_cancellations": attempts == fills + cancellations + rejects,
            "identity_fills_eq_resolved_plus_open": fills == resolved + open_,
            "identity_mismatches": mismatches,
            "identity_checkable": True,
        }

    if has_result_shape:
        resolved = sum(1 for r in rows if str(r.get("result") or "").upper() in RESOLVED_RESULTS)
        has_unjoinable = any("unjoinable_legacy" in r for r in rows)
        if has_unjoinable:
            unjoinable = sum(1 for r in rows if r.get("unjoinable_legacy"))
            open_ = sum(1 for r in rows if r.get("result") is None and not r.get("unjoinable_legacy"))
        else:
            unjoinable = _unk("this artifact shape has no 'unjoinable_legacy' field")
            open_ = sum(1 for r in rows if r.get("result") is None)
        entry_note = _unk(
            "this artifact shape (one row per journaled trade: date/instrument/strategy/direction/"
            "session/result/pnl[/unjoinable_legacy], produced via adaptive.journal_reader.JournalReader) "
            "does not carry attempted/filled/cancelled_no_fill fields, so entry attempts, fills, "
            "cancellations, and rejects/known-no-fills cannot be computed or verified against each "
            "other -- only resolved_outcomes, legitimately_open_positions, and unjoinable_legacy rows "
            "are trackable from this artifact"
        )
        return {
            "raw_candidate_count": n,
            "candidates_reaching_risk_engine": _unk("not tracked by this artifact shape (see entry_attempts note)"),
            "candidates_approved": n,
            "entry_attempts": entry_note,
            "fills": entry_note,
            "cancellations_no_fill": entry_note,
            "rejects_known_no_fills": entry_note,
            "resolved_outcomes": resolved,
            "legitimately_open_positions": open_,
            "unjoinable_legacy_rows": unjoinable,
            "identity_attempts_eq_fills_plus_cancellations": None,
            "identity_fills_eq_resolved_plus_open": None,
            "identity_mismatches": [],
            "identity_checkable": False,
        }

    reason = "raw_trades.jsonl rows carry neither a 'filled' field nor a 'result' field recognized by this tool"
    return {
        "raw_candidate_count": n,
        "candidates_reaching_risk_engine": _unk(reason),
        "candidates_approved": _unk(reason),
        "entry_attempts": _unk(reason),
        "fills": _unk(reason),
        "cancellations_no_fill": _unk(reason),
        "rejects_known_no_fills": _unk(reason),
        "resolved_outcomes": _unk(reason),
        "legitimately_open_positions": _unk(reason),
        "identity_attempts_eq_fills_plus_cancellations": None,
        "identity_fills_eq_resolved_plus_open": None,
        "identity_mismatches": [],
        "identity_checkable": False,
    }


# ── PERFORMANCE section ─────────────────────────────────────────────────────


def _mini_stats(rows: list[dict], pnl_field: str) -> dict[str, Any]:
    resolved = [r for r in rows if str(r.get("result") or "").upper() in RESOLVED_RESULTS]
    wins = [r for r in resolved if str(r.get("result")).upper() == "WIN"]
    losses = [r for r in resolved if str(r.get("result")).upper() == "LOSS"]
    net = sum(_num(r.get(pnl_field)) for r in resolved)
    gross_win = sum(_num(r.get(pnl_field)) for r in wins)
    gross_loss = sum(_num(r.get(pnl_field)) for r in losses)
    if gross_loss < 0:
        pf: Any = round(gross_win / abs(gross_loss), 4)
    elif gross_win > 0:
        pf = math.inf
    else:
        pf = None
    return {
        "n": len(rows),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(resolved), 4) if resolved else None,
        "net_pnl": round(net, 2),
        "profit_factor": pf,
    }


def _derive_half_split(rows: list[dict], pnl_field: str) -> dict[str, Any]:
    out = {}
    for label, (start, end) in KNOWN_HALVES.items():
        subset = [r for r in rows if r.get("date") and start <= str(r["date"]) <= end]
        out[label] = _mini_stats(subset, pnl_field)
    return out


def _group_stats(rows: list[dict], field_name: str, pnl_field: str) -> Any:
    if not any(field_name in r for r in rows):
        return _unk(f"no '{field_name}' field present in raw_trades rows")
    groups: dict[str, list[dict]] = {}
    for r in rows:
        key = str(r.get(field_name))
        groups.setdefault(key, []).append(r)
    return {k: _mini_stats(v, pnl_field) for k, v in sorted(groups.items())}


def compute_performance(
    rows: list[dict],
    pnl_field: Optional[str],
    all_scenario_rows: list[dict],
    results_meta: Optional[dict],
) -> dict[str, Any]:
    if not rows:
        reason = "no rows for this strategy/scenario in the raw_trades artifact"
        return {k: _unk(reason) for k in (
            "resolved_count", "wins", "losses", "breakeven", "win_rate", "net_pnl", "profit_factor",
            "expectancy_per_resolved", "max_drawdown", "max_consecutive_losses",
            "final_equity_ended_at_or_above_prior_peak", "winner_concentration", "by_direction",
            "by_instrument", "by_session", "by_half_walk_forward", "by_year", "recent_period_last_90d",
            "slippage_sensitivity_by_tag",
        )}
    if pnl_field is None:
        reason = (
            "raw_trades rows carry no recognized P&L field (checked pnl_after_commission, pnl, "
            "pnl_dollars, pnl_before_commission, in that priority order)"
        )
        return {k: _unk(reason) for k in (
            "resolved_count", "wins", "losses", "breakeven", "win_rate", "net_pnl", "profit_factor",
            "expectancy_per_resolved", "max_drawdown", "max_consecutive_losses",
            "final_equity_ended_at_or_above_prior_peak", "winner_concentration", "by_direction",
            "by_instrument", "by_session", "by_half_walk_forward", "by_year", "recent_period_last_90d",
            "slippage_sensitivity_by_tag",
        )}

    def is_resolved(r: dict) -> bool:
        return str(r.get("result") or "").upper() in RESOLVED_RESULTS

    resolved_rows = [r for r in rows if is_resolved(r)]
    wins = [r for r in resolved_rows if str(r.get("result")).upper() == "WIN"]
    losses = [r for r in resolved_rows if str(r.get("result")).upper() == "LOSS"]
    breakeven = [r for r in resolved_rows if str(r.get("result")).upper() == "BREAKEVEN"]

    def pnl(r: dict) -> float:
        return _num(r.get(pnl_field))

    net = sum(pnl(r) for r in resolved_rows)
    gross_win = sum(pnl(r) for r in wins)
    gross_loss = sum(pnl(r) for r in losses)
    if gross_loss < 0:
        pf: Any = round(gross_win / abs(gross_loss), 4)
    elif gross_win > 0:
        pf = math.inf
    else:
        pf = None
    win_rate = round(len(wins) / len(resolved_rows), 4) if resolved_rows else None
    expectancy = round(net / len(resolved_rows), 4) if resolved_rows else None

    ordered = sorted(resolved_rows, key=lambda r: (str(r.get("date") or ""), str(r.get("bar_ts") or "")))
    equity = peak = max_dd = 0.0
    streak = worst_streak = 0
    for r in ordered:
        equity += pnl(r)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if str(r.get("result")).upper() == "LOSS":
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0
    ended_at_or_above_peak = (equity >= peak - 1e-9) if ordered else None

    win_nets_sorted = sorted((pnl(r) for r in wins), reverse=True)
    total_win = sum(win_nets_sorted)

    def top_share(top_n: int) -> Optional[float]:
        return round(sum(win_nets_sorted[:top_n]) / total_win, 4) if total_win else None

    concentration = {"top1": top_share(1), "top3": top_share(3), "top5": top_share(5)}

    by_direction = _group_stats(rows, "direction", pnl_field)
    by_instrument = _group_stats(rows, "instrument", pnl_field)
    by_session = _group_stats(rows, "session", pnl_field)

    if any("half" in r for r in rows):
        by_half = _group_stats(rows, "half", pnl_field)
        walk_forward_source = "artifact's own 'half' field"
    elif results_meta and results_meta.get("range") == KNOWN_FULL_RANGE:
        by_half = _derive_half_split(rows, pnl_field)
        walk_forward_source = (
            "derived using this repo's shared H1/H2 convention (H1 2025-07-24->2026-01-23, "
            "H2 2026-01-24->2026-07-23) because raw_trades has no 'half' field but "
            "results.json meta.range matches this exact known full-period convention"
        )
    else:
        by_half = _unk(
            "raw_trades has no 'half' field and results.json meta.range does not match this repo's "
            "known full-period convention (2025-07-24 -> 2026-07-23), so H1/H2 cannot be safely derived"
        )
        walk_forward_source = None

    dated = [r for r in resolved_rows if r.get("date")]
    if dated:
        by_year: Any = {}
        for r in dated:
            y = str(r["date"])[:4]
            by_year.setdefault(y, []).append(r)
        by_year = {y: _mini_stats(v, pnl_field) for y, v in sorted(by_year.items())}
        max_date = max(str(r["date"]) for r in dated)
        try:
            cutoff = (date.fromisoformat(max_date) - timedelta(days=90)).isoformat()
            recent = _mini_stats([r for r in dated if str(r["date"]) >= cutoff], pnl_field)
            recent["window"] = f"{cutoff} .. {max_date} (last 90 days of resolved-trade dates present)"
        except ValueError:
            recent = _unk("could not parse the 'date' field on resolved rows as ISO date")
    else:
        by_year = _unk("no 'date' field present on resolved rows")
        recent = _unk("no 'date' field present on resolved rows")

    tag_field = "run_tag" if any("run_tag" in r for r in all_scenario_rows) else (
        "slippage_tag" if any("slippage_tag" in r for r in all_scenario_rows) else None
    )
    if tag_field:
        tagged: dict[str, list[dict]] = {}
        for r in all_scenario_rows:
            tag = r.get(tag_field)
            if tag is None:
                continue
            tagged.setdefault(tag, []).append(r)
        slippage_sensitivity: Any = {tag: _mini_stats(v, pnl_field) for tag, v in sorted(tagged.items())}
    else:
        slippage_sensitivity = _unk(
            "raw_trades rows carry no run_tag/slippage_tag field distinguishing multiple adverse-slippage "
            "scenarios -- only a single scenario is present in this artifact, so slippage sensitivity "
            "cannot be assessed from raw_trades alone"
        )

    return {
        "resolved_count": len(resolved_rows),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": win_rate,
        "net_pnl": round(net, 2),
        "profit_factor": pf,
        "expectancy_per_resolved": expectancy,
        "max_drawdown": round(max_dd, 2),
        "max_consecutive_losses": worst_streak,
        "final_equity_ended_at_or_above_prior_peak": ended_at_or_above_peak,
        "winner_concentration": concentration,
        "by_direction": by_direction,
        "by_instrument": by_instrument,
        "by_session": by_session,
        "by_half_walk_forward": by_half,
        "walk_forward_source": walk_forward_source or _unk("see by_half_walk_forward"),
        "by_year": by_year,
        "recent_period_last_90d": recent,
        "slippage_sensitivity_by_tag": slippage_sensitivity,
    }


# ── IDENTITY/PARITY + GATE ATTRITION sections ───────────────────────────────


def _scan_docstring_claims(script_path: Optional[Path]) -> Any:
    if script_path is None or not script_path.exists():
        return _unk("no canonical-evidence script path available to scan for documented claims")
    try:
        import ast

        tree = ast.parse(script_path.read_text(encoding="utf-8"))
        doc = ast.get_docstring(tree) or ""
    except (OSError, SyntaxError, ValueError):
        return _unk(f"could not parse {script_path} to extract its module docstring")
    if not doc:
        return _unk(f"{script_path} has no module docstring")
    keywords = ("lookahead", "look-ahead", "partial-bar", "partial bar", "causal", "reproducib")
    matches = [
        line.strip()
        for line in doc.splitlines()
        if any(kw in line.lower() for kw in keywords)
    ]
    if not matches:
        return _unk(
            f"{script_path}'s module docstring makes no explicit lookahead/partial-bar/causal-data claim"
        )
    return matches


def build_identity_parity(
    results: Optional[dict], raw_rows_all: list[dict], canonical_script_path: Optional[Path]
) -> dict[str, Any]:
    out: dict[str, Any] = {"raw_candidate_count": len(raw_rows_all)}

    directions = sorted({str(r.get("direction")) for r in raw_rows_all if r.get("direction") is not None})
    out["direction_parity_observed_values"] = directions or _unk("no 'direction' field present in raw_trades rows")

    if any("paper_order_id" in r for r in raw_rows_all):
        ids = [r.get("paper_order_id") for r in raw_rows_all if r.get("paper_order_id")]
        dup = len(ids) - len(set(ids))
        out["candidate_identity_parity"] = {
            "identity_field": "paper_order_id",
            "rows_with_identity": len(ids),
            "duplicate_identity_count": dup,
            "ok": dup == 0,
        }
    else:
        out["candidate_identity_parity"] = _unk(
            "raw_trades rows carry no paper_order_id (or equivalent) identity field in this artifact shape"
        )

    has_price_fields = any(any(k in r for k in ("entry", "stop", "target")) for r in raw_rows_all)
    out["entry_stop_target_parity"] = (
        "artifact raw_trades rows carry entry/stop/target price fields"
        if has_price_fields
        else _unk(
            "raw_trades.jsonl rows in this artifact carry only outcome/pnl fields (no entry/stop/target "
            "price fields), so entry/stop/target price-level parity cannot be checked from this artifact "
            "without cross-referencing the underlying journal TRADE rows directly"
        )
    )

    out["timeframe_parity"] = _unk(
        "not recorded as a structured field in results.json/raw_trades.jsonl for this strategy; timeframe "
        "is documented only in the canonical-evidence script's docstring/corpus path, not machine-checked "
        "by this tool"
    )

    out["lookahead_partial_bar_causal_claims_from_script_docstring"] = _scan_docstring_claims(canonical_script_path)

    if isinstance(results, dict) and "parity_findings" in results:
        out["artifact_recorded_parity_findings"] = results["parity_findings"]
    else:
        out["artifact_recorded_parity_findings"] = _unk("this results.json has no 'parity_findings' block")

    return out


GATE_ATTRITION_FIELDS = (
    "market_condition",
    "trend_strength",
    "ema_alignment",
    "r_r_ratio",
    "confluence",
    "max_stop",
    "min_target",
    "entry_sanity",
    "volume_filter",
    "session_filter",
    "news_filter",
)


def build_gate_attrition(results: Optional[dict]) -> dict[str, Any]:
    if isinstance(results, dict):
        for key in ("gate_attrition", "candidate_gate_counts", "filter_counts"):
            if key in results:
                return {"source_field": key, "data": results[key]}
    reason = (
        "not tracked by the underlying evidence artifact -- this level of detail is strategy-detector-"
        "internal and this repo's canonical-evidence scripts (orb_breakout/vwap_reclaim/strat_212_122) "
        "do not record per-gate candidate-attrition counts in results.json or raw_trades.jsonl"
    )
    return {field: _unk(reason) for field in GATE_ATTRITION_FIELDS}


# ── RUNTIME PARITY section ──────────────────────────────────────────────────


def compute_runtime_parity(
    results: Optional[dict], raw_rows_primary: list[dict], config: Any
) -> dict[str, Any]:
    instruments = sorted({r.get("instrument") for r in raw_rows_primary if r.get("instrument")})
    isolation = (results or {}).get("meta", {}).get("isolation", {}) if isinstance(results, dict) else {}

    artifact_fill_model = isolation.get("entry_fill_model")
    tolerance_by_root: dict[str, Any] = dict(isolation.get("entry_tolerance_ticks_by_root") or {})
    import re

    for key, value in isolation.items():
        m = re.match(r"^entry_tolerance_ticks_([a-z]+)$", key)
        if m:
            tolerance_by_root.setdefault(m.group(1).upper(), value)

    parity_defects: list[str] = []

    if config is None:
        runtime_fill_model = _unk("config.settings.load_config() was not available when this report was built")
        runtime_tolerance_by_root: Any = _unk("config.settings.load_config() was not available when this report was built")
        fill_model_checked = False
        tolerance_checks: Any = _unk("runtime config unavailable")
    else:
        runtime_fill_model = config.entry_fill_model
        runtime_tolerance_by_root = dict(config.entry_tolerance_ticks_by_root or {})
        fill_model_checked = artifact_fill_model is not None
        if fill_model_checked and artifact_fill_model != runtime_fill_model:
            parity_defects.append(
                f"entry_fill_model mismatch: artifact recorded '{artifact_fill_model}', current runtime "
                f"config.entry_fill_model='{runtime_fill_model}'"
            )
        tolerance_checks = {}
        for inst in instruments:
            artifact_val = tolerance_by_root.get(inst)
            runtime_val = runtime_tolerance_by_root.get(inst)
            if artifact_val is None:
                tolerance_checks[inst] = _unk(
                    f"artifact meta.isolation does not record an entry_tolerance value for {inst}"
                )
            elif runtime_val is None:
                tolerance_checks[inst] = _unk(
                    f"current runtime config has no entry_tolerance_ticks_by_root entry for {inst} "
                    f"(ENTRY_SLIPPAGE_TOLERANCE_TICKS_{inst} env unset)"
                )
            else:
                match = float(artifact_val) == float(runtime_val)
                tolerance_checks[inst] = {"artifact": artifact_val, "runtime": runtime_val, "match": match}
                if not match:
                    parity_defects.append(
                        f"entry_tolerance_ticks mismatch for {inst}: artifact={artifact_val}, runtime={runtime_val}"
                    )

    return {
        "instruments_covered": instruments or _unk("no 'instrument' field present in raw_trades rows"),
        "artifact_entry_fill_model": artifact_fill_model
        if artifact_fill_model is not None
        else _unk("artifact meta.isolation has no entry_fill_model recorded"),
        "runtime_entry_fill_model": runtime_fill_model,
        "entry_fill_model_parity_checked": fill_model_checked,
        "entry_tolerance_by_instrument": tolerance_checks,
        "parity_defects": parity_defects,
    }


# ── risk_rules.yaml strategy_permission_gate ────────────────────────────────


def load_risk_permission_gate(risk_rules_path: Path) -> dict[str, Any]:
    if not risk_rules_path.exists():
        return {"error": f"{risk_rules_path} not found"}
    try:
        data = yaml.safe_load(risk_rules_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {"error": f"failed to parse {risk_rules_path}: {exc}"}
    gate = data.get("strategy_permission_gate") or {}
    return {
        "enabled": gate.get("enabled"),
        "default_status": gate.get("default_status", "SHADOW_ONLY"),
        "strategy_status": gate.get("strategy_status") or {},
        "risk_rules_sha256": _sha256_text(risk_rules_path),
    }


def strategy_permission_status(gate: dict[str, Any], strategy: str) -> str:
    statuses = gate.get("strategy_status") or {}
    return statuses.get(strategy, gate.get("default_status", "SHADOW_ONLY"))


# ── EXECUTION CONTEXT section ───────────────────────────────────────────────


def build_execution_context(
    results: Optional[dict], config: Any, permission_status: str, raw_rows_primary: list[dict]
) -> dict[str, Any]:
    meta = (results or {}).get("meta", {}) if isinstance(results, dict) else {}
    isolation = meta.get("isolation", {}) if isinstance(meta, dict) else {}
    commission = meta.get("commission_round_trip")

    tag_field = "run_tag" if any("run_tag" in r for r in raw_rows_primary) else (
        "slippage_tag" if any("slippage_tag" in r for r in raw_rows_primary) else None
    )
    slippage_tags = sorted({r.get(tag_field) for r in raw_rows_primary if r.get(tag_field)}) if tag_field else []

    tolerance_fields = {k: v for k, v in isolation.items() if str(k).startswith("entry_tolerance")}

    if config is None:
        account_caps: Any = _unk("config.settings.load_config() was not available when this report was built")
    else:
        account_caps = {
            "max_daily_loss": config.max_daily_loss,
            "max_drawdown_percent": config.max_drawdown_percent,
            "max_trades_per_day": config.max_trades_per_day,
            "max_open_positions": config.max_open_positions,
            "circuit_breaker_losses": config.circuit_breaker_losses,
        }

    return {
        "entry_fill_model_used_in_evidence": isolation.get(
            "entry_fill_model", _unk("not recorded in artifact meta.isolation")
        ),
        "entry_tolerance_used_in_evidence": tolerance_fields or _unk(
            "not recorded in artifact meta.isolation"
        ),
        "commission_assumption_usd_round_trip": commission if commission is not None else _unk(
            "not recorded in artifact meta"
        ),
        "slippage_scenarios_present_in_artifact": slippage_tags or _unk(
            "no run_tag/slippage_tag field on raw_trades rows -- single, untagged scenario"
        ),
        "contract_quantity": _unk(
            "raw_trades.jsonl rows in this artifact do not carry a contracts/position-size field"
        ),
        "runtime_account_risk_caps": account_caps,
        "strategy_permission_gate_status": permission_status,
    }


# ── PAPER FORWARD EVIDENCE section (real journal, via ops.proof_30_mnq) ────


def compute_paper_forward_evidence(
    journal_dir: Path, strategy: str, instruments: list[str], since: Optional[str]
) -> dict[str, Any]:
    if not journal_dir.exists():
        return {
            "journal_dir": str(journal_dir),
            "status": "UNKNOWN",
            "reason": f"journal_dir {journal_dir} does not exist",
        }

    entries = read_journal_entries(journal_dir)
    if not entries:
        return {
            "journal_dir": str(journal_dir),
            "total_resolved_pairs_this_strategy": 0,
            "has_real_paper_forward_evidence": False,
            "note": "no journal_*.jsonl entries found under this journal_dir",
        }

    freeze_ts = None
    if since:
        freeze_ts = parse_proof_ts(since)
        if freeze_ts is None:
            return {
                "journal_dir": str(journal_dir),
                "status": "UNKNOWN",
                "reason": f"--since value '{since}' could not be parsed as an ISO8601 timestamp",
            }

    insts = instruments or ["MNQ"]
    per_instrument: dict[str, Any] = {}
    strategy_matches: list[Any] = []
    for inst in insts:
        resolved, unmatched = pair_resolved_trades(
            entries, instrument=inst, freeze_ts=freeze_ts, limit=100_000
        )
        matches = [t for t in resolved if t.setup.get("strategy") == strategy]
        per_instrument[inst] = {
            "resolved_pairs_all_strategies": len(resolved),
            "resolved_pairs_this_strategy": len(matches),
            "unmatched_outcomes_all_strategies": len(unmatched),
        }
        strategy_matches.extend(matches)

    categories = [classify_outcome(t.outcome_body) for t in strategy_matches]
    filled_wl = categories.count("filled_win_loss")
    filled_pnl = sum(
        _num(t.outcome_body.get("pnl_dollars"))
        for t, cat in zip(strategy_matches, categories)
        if cat == "filled_win_loss"
    )

    return {
        "journal_dir": str(journal_dir),
        "instruments_checked": insts,
        "since": since,
        "per_instrument": per_instrument,
        "total_resolved_pairs_this_strategy": len(strategy_matches),
        "filled_win_loss_count": filled_wl,
        "breakeven_count": categories.count("breakeven"),
        "cancelled_nofill_count": categories.count("cancelled_nofill"),
        "reconciler_touched_count": categories.count("reconciler_touched"),
        "other_count": categories.count("other"),
        "filled_win_loss_pnl_dollars": round(filled_pnl, 2),
        "has_real_paper_forward_evidence": filled_wl > 0,
        "caveat": (
            "pairing reuses ops.proof_30_mnq.pair_resolved_trades, which is FIFO-by-instrument (not "
            "order-id keyed) and is then filtered to setup.strategy == this strategy; if multiple "
            "strategies traded the same instrument concurrently in this journal, FIFO pairing could "
            "theoretically mis-attribute an OUTCOME before the strategy filter is applied"
        ),
    }


# ── drawdown-breaker halt detection (generic, artifact-agnostic) ───────────


def find_drawdown_breaker_halts(results: Optional[dict]) -> list[dict[str, Any]]:
    halts: list[dict[str, Any]] = []
    if not isinstance(results, dict):
        return halts

    def walk(obj: Any, path: str) -> None:
        if not isinstance(obj, dict):
            return
        for key, value in obj.items():
            key_path = f"{path}.{key}"
            if "drawdown_breaker" in key.lower() or "breaker_halt" in key.lower():
                if isinstance(value, dict):
                    for sub_key, sub_val in value.items():
                        if isinstance(sub_val, dict) and sub_val.get("first_halt_date"):
                            halts.append({"path": f"{key_path}.{sub_key}", "detail": sub_val})
                elif isinstance(value, dict) and value.get("first_halt_date"):
                    halts.append({"path": key_path, "detail": value})
            else:
                walk(value, key_path)

    walk(results, "results")
    return halts


# ── CLASSIFICATION / verdict ────────────────────────────────────────────────


def classify(
    *,
    execution: dict[str, Any],
    performance: dict[str, Any],
    runtime_parity: dict[str, Any],
    permission_status: str,
    drawdown_breaker_halts: list[dict[str, Any]],
    sample_adequate_min: int = DEFAULT_SAMPLE_ADEQUATE_MIN,
) -> tuple[str, list[str]]:
    mismatches = execution.get("identity_mismatches") or []
    if mismatches:
        return "BROKEN", [f"accounting-identity mismatch: {m}" for m in mismatches]

    if drawdown_breaker_halts and permission_status == "PAPER_ELIGIBLE":
        detail = "; ".join(f"{h['path']}: {h['detail']}" for h in drawdown_breaker_halts)
        return "UNSAFE", [
            "this strategy's own isolated evidence account tripped its own configured max-drawdown "
            f"breaker during replay ({detail}), and risk_rules.yaml strategy_permission_gate currently "
            "marks it PAPER_ELIGIBLE (able to place real orders) -- the real risk control that would "
            "protect a live/paper account already tripped on this strategy's own honest-fill P&L"
        ]

    fills = execution.get("fills")
    if isinstance(fills, int) and fills == 0:
        return "WAIT", [
            "zero executable fills — the strategy has not been proven to fill through the real "
            "entry-fill model at all; cannot be VALIDATED"
        ]

    resolved = execution.get("resolved_outcomes")
    if isinstance(resolved, int) and resolved == 0:
        return "WAIT", ["zero resolved outcomes"]

    if isinstance(resolved, int) and resolved < sample_adequate_min:
        return "WAIT", [f"sample below the {sample_adequate_min}-trade minimum (resolved={resolved})"]

    net = performance.get("net_pnl")
    pf = performance.get("profit_factor")
    if not isinstance(net, (int, float)) or net <= 0:
        return "WAIT", [f"net P&L is not positive under this evidence (net={net})"]
    if not (pf is math.inf or (isinstance(pf, (int, float)) and pf > 1)):
        return "WAIT", [f"profit factor is not > 1 under this evidence (PF={pf})"]

    reasons: list[str] = []

    by_half = performance.get("by_half_walk_forward")
    walk_forward_ok = None
    if isinstance(by_half, dict) and isinstance(by_half.get("H1"), dict) and isinstance(by_half.get("H2"), dict):
        h1_net = by_half["H1"].get("net_pnl")
        h2_net = by_half["H2"].get("net_pnl")
        if isinstance(h1_net, (int, float)) and isinstance(h2_net, (int, float)):
            walk_forward_ok = h1_net > 0 and h2_net > 0

    if walk_forward_ok is False:
        return "OVERFIT", [
            "aggregate net P&L is positive but the strategy fails a both-halves-positive walk-forward "
            f"split (H1 net={by_half['H1'].get('net_pnl')}, H2 net={by_half['H2'].get('net_pnl')}) — "
            "the aggregate edge is not stable across the sample, a classic overfit signature"
        ]

    concentration = performance.get("winner_concentration")
    top5 = concentration.get("top5") if isinstance(concentration, dict) else None
    if isinstance(top5, (int, float)) and top5 > 0.75:
        return "OVERFIT", [
            f"top-5 winning trades account for {top5:.1%} of gross winning P&L — the positive aggregate "
            "result is concentration-driven rather than broad-based, a classic overfit signature"
        ]

    slippage = performance.get("slippage_sensitivity_by_tag")
    slippage_ok = None
    if isinstance(slippage, dict):
        tiers = [v for v in slippage.values() if isinstance(v, dict)]
        if tiers:
            slippage_ok = all(
                (v.get("profit_factor") not in (None,) and (v["profit_factor"] is math.inf or v["profit_factor"] > 1))
                and isinstance(v.get("net_pnl"), (int, float)) and v["net_pnl"] > 0
                for v in tiers
            )

    if walk_forward_ok is None:
        reasons.append("walk-forward H1/H2 split not computable from this artifact")
    if slippage_ok is None:
        reasons.append("slippage sensitivity not computable from this artifact (no tagged multi-tick sweep present)")
    elif slippage_ok is False:
        reasons.append("fails slippage sensitivity (net P&L or PF <= 1 at some tested adverse-slippage tier)")

    if reasons:
        return "PROMISING BUT UNPROVEN", reasons

    parity_defects = runtime_parity.get("parity_defects") or []
    if parity_defects:
        return "PROMISING BUT UNPROVEN", [
            "runtime fill-model/tolerance parity defect(s), so this replay evidence cannot be confirmed "
            "to have used the currently-configured real execution parameters: " + "; ".join(parity_defects)
        ]

    return "VALIDATED", [
        "accounting identities hold, sample adequate, net positive with PF>1, both-halves-positive "
        "walk-forward, survives every tested slippage tier, winner concentration not extreme, no "
        "runtime fill-model/tolerance parity defect detected"
    ]


# ── orchestrator ─────────────────────────────────────────────────────────


def _load_config_safely() -> Any:
    try:
        from config.settings import load_config

        return load_config()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, this is a read-only report tool
        return None, str(exc)


def build_promotion_report(
    *,
    strategy: str,
    results_path: Optional[Path],
    raw_trades_path: Optional[Path],
    journal_dir: Path,
    since: Optional[str],
    risk_rules_path: Path = DEFAULT_RISK_RULES_PATH,
    sample_adequate_min: int = DEFAULT_SAMPLE_ADEQUATE_MIN,
    config: Any = "AUTO",
) -> dict[str, Any]:
    canonical_note = None
    canonical_script_path = None
    if strategy in KNOWN_STRATEGIES:
        canonical_script_path = REPO_ROOT / KNOWN_STRATEGIES[strategy]["canonical_script"]
        canonical_note = KNOWN_STRATEGIES[strategy].get("note")

    raw_rows, read_errors, raw_fatal = load_raw_trades(raw_trades_path)
    results, results_error = load_results_json(results_path)

    if raw_fatal:
        instructions = (
            f"Run the relevant canonical-evidence script for '{strategy}' first, then pass "
            "--results/--raw-trades explicitly."
            if strategy not in KNOWN_STRATEGIES
            else f"Run: python3 {KNOWN_STRATEGIES[strategy]['canonical_script']}  (see its --out/--raw flags), "
            "then re-run this gate, or pass --results/--raw-trades explicitly."
        )
        return {
            "ok": False,
            "strategy": strategy,
            "error": raw_fatal,
            "results_json_error": results_error,
            "instructions": instructions,
        }

    raw_trades_strategy_field = (
        KNOWN_STRATEGIES.get(strategy, {}).get("raw_trades_strategy_field")
    )
    strategy_rows, filter_note = filter_rows_for_strategy(raw_rows, strategy, raw_trades_strategy_field)
    primary_tag, primary_rows, all_tags = select_primary_scenario(strategy_rows)
    pnl_field = detect_pnl_field(primary_rows)

    if config == "AUTO":
        loaded = _load_config_safely()
        if isinstance(loaded, tuple):
            config = None
            config_error = loaded[1]
        else:
            config = loaded
            config_error = None
    else:
        config_error = None

    execution = compute_execution_identity(primary_rows)
    performance = compute_performance(primary_rows, pnl_field, strategy_rows, (results or {}).get("meta"))
    identity_parity = build_identity_parity(results, strategy_rows, canonical_script_path)
    gate_attrition = build_gate_attrition(results)
    runtime_parity = compute_runtime_parity(results, primary_rows, config)
    permission_gate = load_risk_permission_gate(risk_rules_path)
    permission_status = strategy_permission_status(permission_gate, strategy)
    execution_context = build_execution_context(results, config, permission_status, primary_rows)
    instruments = sorted({r.get("instrument") for r in strategy_rows if r.get("instrument")}) or ["MNQ"]
    paper_forward = compute_paper_forward_evidence(journal_dir, strategy, instruments, since)
    drawdown_breaker_halts = find_drawdown_breaker_halts(results)

    verdict, verdict_reasons = classify(
        execution=execution,
        performance=performance,
        runtime_parity=runtime_parity,
        permission_status=permission_status,
        drawdown_breaker_halts=drawdown_breaker_halts,
        sample_adequate_min=sample_adequate_min,
    )

    lane_class = LANE_CLASS.get(strategy, _unk(f"'{strategy}' not present in ops.evidence_report.LANE_CLASS"))
    lane_note = LANE_NOTES.get(strategy)

    permission_note = None
    if permission_status != "PAPER_ELIGIBLE":
        permission_note = (
            f"risk_rules.yaml strategy_permission_gate marks this strategy '{permission_status}' — real "
            "risk controls, not a defect in this evidence, are why it cannot currently reach paper/live "
            "execution regardless of the verdict above. This is intended-and-expected, not BROKEN."
        )

    return {
        "ok": True,
        "strategy": strategy,
        "artifact_sources": {
            "results_path": str(results_path) if results_path else None,
            "results_json_error": results_error,
            "raw_trades_path": str(raw_trades_path) if raw_trades_path else None,
            "raw_trades_read_errors": read_errors,
            "canonical_script": str(canonical_script_path) if canonical_script_path else _unk(
                "strategy not in this tool's known-strategy registry; canonical script unknown"
            ),
            "canonical_script_note": canonical_note,
            "strategy_filter_note": filter_note,
            "scenario_selection": {
                "all_tags_present": all_tags,
                "primary_tag_selected": primary_tag,
                "primary_selection_rule": (
                    "prefer a tag containing both 'static' and '1tick'; else prefer any '1tick' tag; else "
                    "the first tag alphabetically. PERFORMANCE/EXECUTION sections below are computed on "
                    "the primary scenario only, to avoid blending distinct slippage/exit-mode runs into "
                    "one misleading aggregate; slippage_sensitivity_by_tag under PERFORMANCE covers every "
                    "tag present."
                ) if all_tags else "raw_trades has no run_tag/slippage_tag field -- single untagged scenario",
            },
        },
        "config_load_error": config_error,
        "identity_parity": identity_parity,
        "gate_attrition": gate_attrition,
        "execution": execution,
        "performance": performance,
        "execution_context": execution_context,
        "classification": {
            "research_result": {
                "description": (
                    "isolated, honest-fill REPLAY through the real ReplayEngine -> DecisionEngine -> "
                    "RiskEngine -> PaperBroker pipeline (per the canonical-evidence script's own method), "
                    "NOT a standalone backtest/detector-only study"
                ),
                "resolved_count": execution.get("resolved_outcomes"),
                "net_pnl": performance.get("net_pnl"),
                "profit_factor": performance.get("profit_factor"),
            },
            "runtime_parity": runtime_parity,
            "paper_forward_evidence": paper_forward,
            "lane_class_cross_check": {
                "lane_class": lane_class,
                "lane_note": lane_note,
                "source": "ops.evidence_report.LANE_CLASS (static, hand-maintained code-wiring classification"
                " -- a DIFFERENT axis from risk_rules.yaml's strategy_permission_gate and from this tool's own"
                " verdict; surfaced as a cross-check, not silently reconciled if it disagrees)",
            },
            "strategy_permission_gate_status": permission_status,
            "strategy_permission_gate_note": permission_note,
            "verdict": verdict,
            "verdict_reasons": verdict_reasons,
            "verdict_taxonomy": list(VERDICTS),
        },
    }


# ── CLI ──────────────────────────────────────────────────────────────────


def _fmt_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.4f}"
    return str(value)


def print_human(report: dict[str, Any]) -> None:
    if not report.get("ok"):
        print(f"Strategy Promotion Proof Gate: {report.get('strategy')}")
        print(f"ERROR: {report.get('error')}")
        if report.get("results_json_error"):
            print(f"  (results.json: {report['results_json_error']})")
        print(report.get("instructions", ""))
        return

    print(f"Strategy Promotion Proof Gate: {report['strategy']}")
    print(f"  results.json:   {report['artifact_sources']['results_path']}")
    print(f"  raw_trades:     {report['artifact_sources']['raw_trades_path']}")
    if report["artifact_sources"]["raw_trades_read_errors"]:
        print(f"  ⚠ {len(report['artifact_sources']['raw_trades_read_errors'])} raw_trades read error(s)")
    print(f"  filter:         {report['artifact_sources']['strategy_filter_note']}")
    print(f"  scenario:       {report['artifact_sources']['scenario_selection']}")
    print()
    print("== EXECUTION ==")
    for k, v in report["execution"].items():
        print(f"  {k}: {_fmt_value(v)}")
    print()
    print("== PERFORMANCE ==")
    for k in ("resolved_count", "wins", "losses", "win_rate", "net_pnl", "profit_factor",
              "expectancy_per_resolved", "max_drawdown", "max_consecutive_losses"):
        print(f"  {k}: {_fmt_value(report['performance'].get(k))}")
    print()
    cls = report["classification"]
    print("== CLASSIFICATION ==")
    print(f"  RESEARCH RESULT: resolved={cls['research_result']['resolved_count']} "
          f"net={cls['research_result']['net_pnl']} pf={cls['research_result']['profit_factor']}")
    print(f"  RUNTIME PARITY: defects={cls['runtime_parity'].get('parity_defects')}")
    print(f"  PAPER FORWARD EVIDENCE: filled_win_loss="
          f"{cls['paper_forward_evidence'].get('filled_win_loss_count')} "
          f"has_evidence={cls['paper_forward_evidence'].get('has_real_paper_forward_evidence')}")
    print(f"  strategy_permission_gate: {cls['strategy_permission_gate_status']}")
    if cls.get("strategy_permission_gate_note"):
        print(f"    {cls['strategy_permission_gate_note']}")
    print(f"  lane_class (cross-check): {cls['lane_class_cross_check']['lane_class']}")
    print()
    print(f"VERDICT: {cls['verdict']}")
    for reason in cls["verdict_reasons"]:
        print(f"  - {reason}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Strategy Promotion Proof Gate — classifies whether a strategy's "
        "REAL executable-path evidence (not a standalone backtest) supports paper-readiness."
    )
    parser.add_argument("--strategy", required=True, help="Strategy identifier, e.g. orb_breakout, vwap_reclaim, strat_212, strat_122.")
    parser.add_argument("--results", type=Path, help="Path to the canonical-evidence results.json. Defaults by convention for known strategies.")
    parser.add_argument("--raw-trades", type=Path, help="Path to the canonical-evidence raw_trades.jsonl. Defaults by convention for known strategies.")
    parser.add_argument("--journal-dir", type=Path, default=DEFAULT_JOURNAL_DIR, help="Directory containing journal_*.jsonl for PAPER FORWARD EVIDENCE.")
    parser.add_argument("--since", help="Only count journal trades/outcomes at or after this ISO8601 timestamp.")
    parser.add_argument("--risk-rules", type=Path, default=DEFAULT_RISK_RULES_PATH, help="Path to risk_rules.yaml.")
    parser.add_argument("--sample-adequate-min", type=int, default=DEFAULT_SAMPLE_ADEQUATE_MIN, help="Minimum resolved-trade sample size (default 30, matches this repo's canonical-evidence scripts).")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    default_results, default_raw, default_script, default_note = resolve_default_artifact_paths(args.strategy)
    results_path = args.results or default_results
    raw_trades_path = args.raw_trades or default_raw

    if results_path is None and raw_trades_path is None:
        print(f"Unknown strategy '{args.strategy}' -- no default canonical-evidence artifact convention.", file=sys.stderr)
        print("Run the relevant canonical-evidence script for this strategy first (see scripts/*_canonical_evidence*.py),", file=sys.stderr)
        print("then re-run this gate with --results and --raw-trades pointing at its output.", file=sys.stderr)
        print(f"Known strategies: {', '.join(sorted(KNOWN_STRATEGIES))}", file=sys.stderr)
        return 2

    report = build_promotion_report(
        strategy=args.strategy,
        results_path=results_path,
        raw_trades_path=raw_trades_path,
        journal_dir=args.journal_dir,
        since=args.since,
        risk_rules_path=args.risk_rules,
        sample_adequate_min=args.sample_adequate_min,
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print_human(report)

    if not report.get("ok"):
        return 2
    if report["classification"]["verdict"] == "BROKEN":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
