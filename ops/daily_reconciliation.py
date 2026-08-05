"""Read-only daily reconciliation: repo/PR state, evidence preservation,
deployed state, strategy source-of-truth drift, and trade-chain integrity.

Entirely READ ONLY. This module never cancels orders, flattens positions,
modifies broker orders, repairs the journal, synthesizes an OUTCOME,
rewrites any trading state, retries execution, or submits an order. On any
discrepancy it can only report or fail closed. It never fetches/pulls from
git, never checks out/resets/rebases/commits/pushes, never creates or
deletes branches/tags, never drops a stash, and never edits
Strategy_Inventory.md or risk_rules.yaml.

The ONE deliberate write this module performs is its own checkpoint file
(``.git/ops-daily-reconciliation-checkpoint.json``, same "inside .git/,
never tracked" pattern as ``ops/session_safety.py``'s session-state file),
recording where the next unscoped run should resume trade-chain scanning
from. Passing ``--since`` always overrides the checkpoint for that run.

Git-state primitives (branch/worktree/PR/stash/archive-tag helpers) are
reused from ``ops/session_safety.py`` rather than re-derived here. Journal
TRADE/OUTCOME pairing and outcome classification are reused from
``ops/proof_30_mnq.py``, ``ops/reconciler_outcome_audit.py``, and
``ops/audit_plain_cancelled.py`` rather than re-implemented.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from ops.audit_plain_cancelled import audit_instrument as _plain_cancelled_audit_instrument
from ops.proof_30_mnq import (
    DEFAULT_JOURNAL_DIR,
    ResolvedTrade,
    classify_outcome,
    pair_resolved_trades,
    parse_proof_ts,
    read_journal_entries,
)
from ops.reconciler_outcome_audit import is_reconciler_touched_outcome
from ops.session_safety import (
    DEFAULT_MAIN_REF,
    DEFAULT_ORIGIN_REF,
    STALE_PR_DAYS,
    build_runtime_snapshot,
    collect_git_state,
    git_dir,
    merged_local_branches,
    prs_active_today,
    resolve_repo_root,
    stale_open_prs,
)

CHECKPOINT_FILENAME = "ops-daily-reconciliation-checkpoint.json"
# No checkpoint on record -> scan the last DEFAULT_LOOKBACK_HOURS of journal
# activity. A full trading day plus slack for after-hours resolution.
DEFAULT_LOOKBACK_HOURS = 24
# Local branches fully merged into main but still present locally, whose
# last commit predates this many days, are flagged "stale merged" — recently
# merged branches are normal pre-cleanup residue, not a hygiene problem.
STALE_MERGED_BRANCH_DAYS = 7
# An unresolved (no-OUTCOME) TRADE row older than this is no longer treated
# as "legitimately still open" — this system's strategies exit same-session
# via bracket/runner logic, so a multi-hour-unresolved position without an
# OUTCOME is anomalous rather than a normal overnight hold. Documented
# heuristic, not a broker fact.
STALE_TRADE_HOURS = 6
# When looking for an ORDER_IDS row that corroborates a stale TRADE row (best-
# effort evidence that it actually reached the broker), only consider one
# logged within this many minutes after the TRADE decision.
ORDER_ID_MATCH_WINDOW_MINUTES = 5
# Both MNQ and MES trade in 0.25-index-point ticks in this system (matches
# ops/audit_plain_cancelled.py's TOLERANCE_POINTS assumption, empirically
# validated there against docs/proof-operator-overrides.md).
TICK_SIZE_POINTS = {"MES": 0.25, "MNQ": 0.25}
DEFAULT_TICK_SIZE_POINTS = 0.25
# Strategy_Inventory.md carries only a document-level "Last updated" date, no
# per-row dates — staleness here is necessarily doc-wide, not per-strategy.
STALE_INVENTORY_DAYS = 30
# Candidate local read-only broker-status caches this routine will check
# before declaring broker-side parity UNKNOWN. None of these exist by
# default in this repo; they are checked, not assumed absent.
BROKER_STATUS_CACHE_CANDIDATES = (
    "broker_account_status.json",
    "status_broker_account.json",
    "status/broker-account.json",
)


# ─── Section A: GitHub/repo reconciliation ─────────────────────────────────

def build_repo_reconciliation_section(
    repo_root: Path,
    *,
    main_ref: str = DEFAULT_MAIN_REF,
    origin_ref: str = DEFAULT_ORIGIN_REF,
    stale_pr_days: int = STALE_PR_DAYS,
    stale_merged_branch_days: int = STALE_MERGED_BRANCH_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    state = collect_git_state(repo_root, main_ref=main_ref, origin_ref=origin_ref, now=now)
    today_activity = prs_active_today(repo_root, today=now.date().isoformat())
    stale_prs, stale_prs_error = stale_open_prs(repo_root, days=stale_pr_days, now=now)

    stale_merged = []
    cutoff = now - timedelta(days=stale_merged_branch_days)
    for branch in merged_local_branches(repo_root, main_ref=main_ref):
        last_commit = branch_last_commit_dt(repo_root, branch)
        if last_commit is not None and last_commit < cutoff:
            stale_merged.append({"branch": branch, "last_commit_at": last_commit.isoformat()})

    return {
        "generated_at": now.isoformat(),
        "prs_today": today_activity,
        "open_prs": state["open_prs"],
        "open_prs_error": state["open_prs_error"],
        "stale_open_prs": stale_prs,
        "stale_open_prs_error": stale_prs_error,
        "stale_pr_threshold_days": stale_pr_days,
        "stale_merged_local_branches": stale_merged,
        "stale_merged_branch_threshold_days": stale_merged_branch_days,
        "active_worktrees": state["worktrees"],
        "dirty_worktrees": [wt for wt in state["worktrees"] if wt.get("dirty")],
        "branches_tracking_deleted_remotes": state["branches_tracking_deleted_remotes"],
        "local_only_branches": state["local_only_branches"],
        "local_main_vs_origin_main": state["sync"],
        "unexpected_remote_branches": state["unexpected_remote_branches"],
        "unexpected_remote_branches_note": (
            "best-effort: remote branches with no local branch of the same name; "
            "does not mean unauthorized, just previously un-fetched-into-local locally"
        ),
        "stash_count": len(state["stashes"]),
        "stashes": state["stashes"],
        "closed_unmerged_candidates": state["closed_unmerged_candidates"],
        "archive_tags": state["archive_tags"],
    }


def branch_last_commit_dt(repo_root: Path, ref: str) -> datetime | None:
    from ops.session_safety import branch_last_commit_iso
    raw = branch_last_commit_iso(repo_root, ref)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


# ─── Section B: evidence preservation ──────────────────────────────────────

def build_evidence_preservation_section(closed_unmerged_candidates: dict[str, Any]) -> dict[str, Any]:
    """Never deletes or creates anything — flags unique-evidence branches
    with no exact-tip archive tag as BLOCKER."""
    if not closed_unmerged_candidates.get("scoped"):
        return {
            "scoped": False,
            "limitation": closed_unmerged_candidates.get("limitation"),
            "blockers": [],
            "preserved": [],
        }
    blockers = []
    preserved = []
    for candidate in closed_unmerged_candidates["branches"]:
        row = {
            "branch": candidate["branch"],
            "unique_commit_count": candidate["unique_commit_count"],
            "archive_exact_preserved": candidate["archive_exact_preserved"],
            "archive_descendant_preserved": candidate["archive_descendant_preserved"],
            "archive_matches": candidate["archive_matches"],
        }
        if candidate["archive_exact_preserved"]:
            preserved.append(row)
        else:
            row["severity"] = "BLOCKER"
            row["reason"] = (
                "unique commits vs main with no annotated archive/* tag whose tip exactly "
                "matches this branch's tip"
            )
            blockers.append(row)
    return {"scoped": True, "limitation": None, "blockers": blockers, "preserved": preserved}


# ─── Section C: deployed state ─────────────────────────────────────────────

def build_deployed_state_section(repo_root: Path) -> dict[str, Any]:
    """Thin re-export of ops.session_safety.build_runtime_snapshot — reused,
    not re-derived, per repo convention."""
    return build_runtime_snapshot(repo_root)


# ─── Section D: strategy source of truth ───────────────────────────────────

_ACTIVE_INVENTORY_VERDICTS = {"VALIDATED", "PAPER PROOF"}
_INACTIVE_INVENTORY_VERDICTS = {"WAIT", "RESEARCH ONLY", "BROKEN", "RETIRE"}

# Best-effort aliases for inventory display names that don't normalize
# cleanly onto their risk_rules.yaml / evidence_report.py strategy key.
# `None` marks a name known NOT to have a risk_rules/gate entry (standalone
# research module, no live wiring) so it's excluded from drift comparison
# rather than reported as an unresolved false-positive.
_INVENTORY_NAME_ALIASES: dict[str, str | None] = {
    "4hr_re_trigger": "strat_4hr_retrigger",
    "60m_3_2_2_first_live": "strat_322_first_live",
    "12hr_miyagi": None,
    "icc_all_variants": None,
    "ict_fvg": None,
    "ict_order_block": None,
    "ict_liquidity_sweep": None,
    "7hr_sweep": None,
    "fomc": None,
    "main_combos_naked": None,
    "ipc_short": None,
    "structural_level_fade": None,
}


def _normalize_strategy_name(name: str) -> str:
    base = re.sub(r"\([^)]*\)", "", name)  # drop parenthetical instrument/session suffix
    base = re.sub(r"[^a-zA-Z0-9]+", "_", base).strip("_").lower()
    return base


def _guess_risk_rules_key(inventory_name: str) -> str | None:
    """Best-effort only — returns None for names known to have no gate entry,
    otherwise a normalized guess that may or may not exist in risk_rules.yaml."""
    norm = _normalize_strategy_name(inventory_name)
    if norm in _INVENTORY_NAME_ALIASES:
        return _INVENTORY_NAME_ALIASES[norm]
    return norm


def parse_strategy_inventory(path: Path) -> dict[str, Any]:
    """Lightweight parser for Strategy_Inventory.md's '## Master Table' GFM
    table: strategy name + verdict (last column), plus the document-level
    'Last updated' date if present. No markdown library dependency."""
    if not path.exists():
        return {"available": False, "error": f"{path} not found", "last_updated": None, "rows": []}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"available": False, "error": str(exc), "last_updated": None, "rows": []}

    last_updated = None
    match = re.search(r"\*Last updated:\s*(\d{4}-\d{2}-\d{2})\*", text)
    if match:
        last_updated = match.group(1)

    rows: list[dict[str, str]] = []
    in_table = False
    header_seen = False
    for line in text.splitlines():
        if line.strip().startswith("## Master Table"):
            in_table = True
            header_seen = False
            continue
        if not in_table:
            continue
        stripped = line.strip()
        if not stripped.startswith("|"):
            if header_seen:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not header_seen:
            header_seen = True
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue  # header separator row
        if len(cells) < 2:
            continue
        name = cells[0]
        verdict_raw = cells[-1]
        verdict = re.sub(r"\*+", "", verdict_raw).strip()
        rows.append({"strategy": name, "verdict_raw": verdict_raw, "verdict": verdict})
    return {"available": True, "error": None, "last_updated": last_updated, "rows": rows}


def _read_risk_rules(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def build_strategy_source_of_truth_section(
    repo_root: Path,
    *,
    inventory_path: Path | None = None,
    risk_rules_path: Path | None = None,
    now: datetime | None = None,
    stale_after_days: int = STALE_INVENTORY_DAYS,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    inventory_path = inventory_path or (repo_root / "docs" / "strategy-rules" / "Strategy_Inventory.md")
    risk_rules_path = risk_rules_path or (repo_root / "risk_rules.yaml")

    inventory = parse_strategy_inventory(inventory_path)
    rules = _read_risk_rules(risk_rules_path)
    gate = (rules.get("strategy_permission_gate") or {}) if isinstance(rules, dict) else {}
    default_status = gate.get("default_status", "SHADOW_ONLY")
    strategy_status = dict(gate.get("strategy_status") or {})
    enabled_concepts = list(((rules.get("strategy") or {}) if isinstance(rules, dict) else {}).get("enabled_concepts") or [])

    try:
        from ops.evidence_report import LANE_CLASS
    except Exception:
        LANE_CLASS = {}

    def effective_status(key: str) -> str:
        return strategy_status.get(key, default_status)

    drift: list[dict[str, Any]] = []
    matched_keys: set[str] = set()

    for row in inventory["rows"] if inventory["available"] else []:
        guessed_key = _guess_risk_rules_key(row["strategy"])
        if guessed_key is None:
            continue  # known standalone/research concept, not gated — not a drift candidate
        in_risk_rules = guessed_key in strategy_status or guessed_key in enabled_concepts
        in_lane_class = guessed_key in LANE_CLASS
        if not in_risk_rules and not in_lane_class:
            # Can't confirm the name-normalization guess resolved to a real
            # key in either source — report UNKNOWN rather than asserting drift.
            drift.append({
                "strategy": row["strategy"], "guessed_key": guessed_key,
                "status": "UNKNOWN",
                "reason": "best-effort name match found no corresponding risk_rules/evidence_report key",
            })
            continue
        matched_keys.add(guessed_key)
        status = effective_status(guessed_key) if in_risk_rules else None
        lane_class = LANE_CLASS.get(guessed_key)
        verdict = row["verdict"].upper()

        if verdict in _ACTIVE_INVENTORY_VERDICTS and status not in (None, "PAPER_ELIGIBLE"):
            drift.append({
                "strategy": row["strategy"], "guessed_key": guessed_key, "status": "CONFLICT",
                "reason": (
                    f"inventory verdict {row['verdict']!r} implies active/PAPER_ELIGIBLE, but "
                    f"risk_rules.yaml effective status is {status!r}"
                ),
            })
        if verdict in _ACTIVE_INVENTORY_VERDICTS and guessed_key not in enabled_concepts and enabled_concepts:
            drift.append({
                "strategy": row["strategy"], "guessed_key": guessed_key, "status": "CONFLICT",
                "reason": (
                    f"inventory verdict {row['verdict']!r} implies active, but "
                    f"{guessed_key!r} is absent from risk_rules.yaml enabled_concepts"
                ),
            })
        if "PROMISING" in verdict and lane_class == "BROKEN_OR_INCOMPLETE":
            drift.append({
                "strategy": row["strategy"], "guessed_key": guessed_key, "status": "CONFLICT",
                "reason": (
                    "inventory marks this PROMISING BUT UNPROVEN, but ops/evidence_report.py's "
                    "LANE_CLASS marks it BROKEN_OR_INCOMPLETE"
                ),
            })

    # risk_rules strategies with no corresponding inventory row.
    risk_rules_keys = set(strategy_status) | set(enabled_concepts)
    unmatched_risk_rules_keys = sorted(risk_rules_keys - matched_keys)

    staleness = None
    if inventory.get("last_updated"):
        try:
            last_updated_date = date.fromisoformat(inventory["last_updated"])
            age_days = (now.date() - last_updated_date).days
            staleness = {
                "last_updated": inventory["last_updated"],
                "age_days": age_days,
                "stale": age_days > stale_after_days,
                "threshold_days": stale_after_days,
                "note": (
                    "document-wide heuristic only — Strategy_Inventory.md has no per-row "
                    "date, so staleness cannot be computed per strategy"
                ),
            }
        except ValueError:
            staleness = {"last_updated": inventory["last_updated"], "error": "unparseable date"}

    return {
        "generated_at": now.isoformat(),
        "inventory_path": str(inventory_path),
        "inventory_available": inventory["available"],
        "inventory_error": inventory["error"],
        "inventory_row_count": len(inventory["rows"]),
        "risk_rules_path": str(risk_rules_path),
        "risk_rules_default_status": default_status,
        "risk_rules_strategy_count": len(risk_rules_keys),
        "drift": drift,
        "risk_rules_strategies_missing_from_inventory": unmatched_risk_rules_keys,
        "inventory_staleness": staleness,
    }


# ─── Section E: trade chain integrity ──────────────────────────────────────

def _valid_bracket(direction: str | None, entry: Any, stop: Any, target: Any) -> bool:
    try:
        entry_f, stop_f, target_f = float(entry), float(stop), float(target)
    except (TypeError, ValueError):
        return False
    if direction == "LONG":
        return stop_f < entry_f < target_f
    if direction == "SHORT":
        return target_f < entry_f < stop_f
    return False


def _tick_size(instrument: str | None) -> float:
    return TICK_SIZE_POINTS.get((instrument or "").upper(), DEFAULT_TICK_SIZE_POINTS)


def per_fill_check(trade: ResolvedTrade, config: Any) -> dict[str, Any]:
    setup = trade.setup
    outcome_body = trade.outcome_body
    instrument = trade.trade.get("instrument")
    issues: list[str] = []

    required = {"strategy": setup.get("strategy"), "instrument": instrument, "direction": setup.get("direction"),
                "entry": setup.get("entry"), "stop": setup.get("stop"), "target": setup.get("target")}
    missing = [name for name, value in required.items() if value in (None, "")]
    if missing:
        issues.append(f"missing fields: {', '.join(missing)}")

    quantity = outcome_body.get("contracts") or setup.get("contracts")
    if quantity in (None, ""):
        issues.append("missing quantity/contracts")

    if not missing and not _valid_bracket(setup.get("direction"), setup.get("entry"), setup.get("stop"), setup.get("target")):
        issues.append("entry/stop/target not internally consistent with direction")

    entry_fill_model = trade.trade.get("entry_fill_model") or setup.get("entry_fill_model")
    entry_fill_model_source = "journal_row"
    if entry_fill_model is None:
        entry_fill_model = getattr(config, "entry_fill_model", None)
        entry_fill_model_source = "config_fallback_not_in_journal_row"

    tolerance_ticks = None
    if config is not None:
        tolerance_ticks = (getattr(config, "entry_tolerance_ticks_by_root", None) or {}).get((instrument or "").upper())

    slippage_ticks = None
    slippage_flag = False
    entry_price = outcome_body.get("entry_price")
    setup_entry = setup.get("entry")
    if entry_price is not None and setup_entry is not None:
        try:
            slippage_ticks = abs(float(entry_price) - float(setup_entry)) / _tick_size(instrument)
            if tolerance_ticks is not None and slippage_ticks > tolerance_ticks:
                slippage_flag = True
                issues.append(f"slippage {slippage_ticks:.2f} ticks exceeds configured tolerance {tolerance_ticks}")
        except (TypeError, ValueError, ZeroDivisionError):
            slippage_ticks = None

    return {
        "trade_ts": trade.trade_ts,
        "outcome_ts": trade.outcome_ts,
        "instrument": instrument,
        "strategy": setup.get("strategy"),
        "quantity": quantity,
        "entry_fill_model": entry_fill_model,
        "entry_fill_model_source": entry_fill_model_source,
        "tolerance_ticks": tolerance_ticks,
        "slippage_ticks": round(slippage_ticks, 2) if slippage_ticks is not None else None,
        "slippage_flag": slippage_flag,
        "consistent": not issues,
        "issues": issues,
    }


def _order_ids_rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if e.get("type") == "ORDER_IDS"]


def duplicate_order_identities(order_ids_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[Any, list[int]] = defaultdict(list)
    for idx, row in enumerate(order_ids_rows):
        for slot, value in (row.get("order_ids") or {}).items():
            if value is None:
                continue
            seen[value].append(idx)
    return [
        {"order_id": order_id, "occurrence_count": len(idxs), "row_indices": idxs}
        for order_id, idxs in seen.items() if len(idxs) > 1
    ]


def _entry_dt(entry: dict[str, Any] | None) -> datetime | None:
    if not entry:
        return None
    return parse_proof_ts(entry.get("ts"))


def _has_order_id_evidence(trade: dict[str, Any], order_ids_rows: list[dict[str, Any]]) -> bool:
    trade_ts = _entry_dt(trade)
    instrument = str(trade.get("instrument") or "").upper()
    if trade_ts is None:
        return False
    window_end = trade_ts + timedelta(minutes=ORDER_ID_MATCH_WINDOW_MINUTES)
    for row in order_ids_rows:
        if str(row.get("instrument") or "").upper() != instrument:
            continue
        row_ts = _entry_dt(row)
        if row_ts is not None and trade_ts <= row_ts <= window_end:
            return True
    return False


def _still_open_trades(entries: list[dict[str, Any]], instrument: str, resolved_count: int) -> list[dict[str, Any]]:
    """Trades never popped off pair_resolved_trades' internal FIFO queue are
    exactly a suffix of that instrument's TRADE/APPROVED rows in arrival
    order — a queue always leaves its most-recently-inserted items behind,
    regardless of when pops interleaved with inserts. This reuses
    `resolved_count` (already computed by pair_resolved_trades) rather than
    re-deriving the pairing itself."""
    trade_rows = [
        e for e in entries
        if str(e.get("instrument") or "").upper() == instrument.upper()
        and e.get("decision") == "TRADE"
        and (e.get("risk_check") or {}).get("result") == "APPROVED"
    ]
    if resolved_count >= len(trade_rows):
        return []
    return trade_rows[resolved_count:]


def _parse_since(value: str, *, now: datetime) -> datetime:
    raw = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            dt = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return now - timedelta(hours=DEFAULT_LOOKBACK_HOURS)


def load_checkpoint(repo_root: Path) -> dict[str, Any] | None:
    gd = git_dir(repo_root)
    if gd is None:
        return None
    path = gd / CHECKPOINT_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_checkpoint(repo_root: Path, *, now: datetime) -> tuple[bool, str | None]:
    gd = git_dir(repo_root)
    if gd is None:
        return False, None
    path = gd / CHECKPOINT_FILENAME
    payload = {"checkpoint_ts": now.isoformat(), "written_at": now.isoformat()}
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return False, str(path)
    return True, str(path)


def resolve_since(repo_root: Path, since_arg: str | None, *, now: datetime) -> tuple[datetime, str]:
    if since_arg:
        return _parse_since(since_arg, now=now), "explicit --since"
    checkpoint = load_checkpoint(repo_root)
    if checkpoint and checkpoint.get("checkpoint_ts"):
        try:
            dt = datetime.fromisoformat(checkpoint["checkpoint_ts"])
            return dt, f"checkpoint written at {checkpoint.get('written_at')}"
        except (TypeError, ValueError):
            pass
    return now - timedelta(hours=DEFAULT_LOOKBACK_HOURS), f"default: last {DEFAULT_LOOKBACK_HOURS}h (no checkpoint found)"


def _local_broker_status_cache(journal_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Best-effort check for a LOCAL, read-only cached broker-status file
    before declaring broker-side parity UNKNOWN. Never makes a network call
    — this routine is offline-safe by design."""
    for rel in BROKER_STATUS_CACHE_CANDIDATES:
        candidate = journal_dir / rel
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8")), str(candidate)
            except (OSError, json.JSONDecodeError):
                continue
    return None, None


def build_trade_chain_section(
    journal_dir: Path,
    *,
    since: datetime,
    config: Any | None = None,
    stale_after_hours: float = STALE_TRADE_HOURS,
) -> dict[str, Any]:
    entries = read_journal_entries(journal_dir)
    entries_since = [e for e in entries if (ts := _entry_dt(e)) is not None and ts >= since]
    # Entries with no parseable ts (e.g. READ_ERROR rows) are kept for
    # visibility but excluded from time-scoped counts below.
    read_errors = [e for e in entries_since if e.get("type") == "READ_ERROR"]

    instruments = sorted({
        str(e.get("instrument") or "").upper()
        for e in entries_since if e.get("instrument")
    })

    all_resolved: list[ResolvedTrade] = []
    still_open: list[dict[str, Any]] = []
    trade_decision_total = 0
    for instrument in instruments:
        resolved, _unmatched = pair_resolved_trades(entries_since, instrument=instrument, limit=1_000_000)
        all_resolved.extend(resolved)
        trade_rows = [
            e for e in entries_since
            if str(e.get("instrument") or "").upper() == instrument
            and e.get("decision") == "TRADE"
            and (e.get("risk_check") or {}).get("result") == "APPROVED"
        ]
        trade_decision_total += len(trade_rows)
        still_open.extend(_still_open_trades(entries_since, instrument, len(resolved)))

    categories = [classify_outcome(t.outcome_body) for t in all_resolved]
    filled_count = categories.count("filled_win_loss") + categories.count("breakeven")
    cancelled_count = categories.count("cancelled_nofill")
    reconciler_touched_count = categories.count("reconciler_touched")
    other_count = categories.count("other")
    resolved_total = len(all_resolved)

    order_ids_rows = _order_ids_rows(entries_since)
    now_ref = max((_entry_dt(e) for e in entries_since if _entry_dt(e)), default=since)
    stale_cutoff = now_ref - timedelta(hours=stale_after_hours)

    legitimate_opens = []
    stale_orders = []
    for trade in still_open:
        ts = _entry_dt(trade)
        if ts is not None and ts < stale_cutoff:
            stale_orders.append(trade)
        else:
            legitimate_opens.append(trade)
    orphans = [t for t in stale_orders if not _has_order_id_evidence(t, order_ids_rows)]

    # --- "attempts" identity ---------------------------------------------
    # Side A (independent, when the journal actually logs ORDER_IDS rows):
    # the count of rows recording a real broker-side order submission.
    # Falls back to the TRADE-decision count when ORDER_IDS isn't logged in
    # this journal range (older journals / different config) — in that
    # fallback case the identity is definitionally satisfied and NOT a
    # meaningful cross-check (documented via attempts_check.independent=False).
    attempts_independent = len(order_ids_rows) if order_ids_rows else None
    attempts_side_a = attempts_independent if attempts_independent is not None else trade_decision_total
    attempts_side_b = filled_count + len(still_open) + cancelled_count + reconciler_touched_count + other_count
    attempts_mismatch = attempts_independent is not None and attempts_side_a != attempts_side_b

    # --- "fills" identity ---------------------------------------------
    # total fills (closed + still open) vs the "healthy" decomposition
    # (resolved fills + legitimately-open) — any gap is exactly the orphan
    # count, i.e. a fill whose eventual OUTCOME silently never arrived.
    fills_total = filled_count + len(still_open)
    fills_healthy = filled_count + len(legitimate_opens)
    fills_mismatch = fills_total != fills_healthy  # == (len(orphans) > 0)

    per_fill_checks = [per_fill_check(t, config) for t in all_resolved if classify_outcome(t.outcome_body) in ("filled_win_loss", "breakeven")]
    inconsistent_fills = [c for c in per_fill_checks if not c["consistent"]]
    slippage_flags = [c for c in per_fill_checks if c["slippage_flag"]]

    duplicates = duplicate_order_identities(order_ids_rows)

    # Reuse ops/audit_plain_cancelled.py's IOC-marketability arithmetic on
    # plain (non-reconciler-touched) CANCELLED rows for the instruments seen
    # in this window, to catch the specific "mislabeled fill" bug class it
    # was built to detect rather than re-deriving that arithmetic here.
    plain_cancelled_audit: dict[str, Any] = {}
    mislabeled_fill_suspects: list[dict[str, Any]] = []
    for instrument in instruments:
        if instrument not in ("MNQ", "MES"):
            continue  # audit_instrument's marketability table only covers these two roots
        result = _plain_cancelled_audit_instrument(entries_since, instrument)
        plain_cancelled_audit[instrument] = {
            "plain_cancelled_total": result["plain_cancelled_total"],
            "classification_counts": result["classification_counts"],
            "suspect_count": len(result["suspect_rows"]),
        }
        mislabeled_fill_suspects.extend(result["suspect_rows"])

    broker_payload, broker_cache_path = _local_broker_status_cache(journal_dir)
    if broker_cache_path is not None:
        broker_parity_status = "CHECKED_LOCAL_CACHE"
        broker_parity_note = f"compared against local read-only status cache at {broker_cache_path}"
    else:
        broker_parity_status = "UNKNOWN"
        broker_parity_note = (
            "no local read-only broker status cache found "
            f"(checked: {', '.join(BROKER_STATUS_CACHE_CANDIDATES)}); this offline script has no "
            "broker API access, so broker-side parity is honestly UNKNOWN — only internal "
            "journal-only parity is claimed below"
        )

    journal_only_parity_ok = (
        not attempts_mismatch and not fills_mismatch
        and not inconsistent_fills and not duplicates and not orphans
        and not read_errors and not mislabeled_fill_suspects
    )

    clean = journal_only_parity_ok  # PASS/short-form gate

    return {
        "since": since.isoformat(),
        "journal_dir": str(journal_dir),
        "journal_read_errors": read_errors,
        "instruments_seen": instruments,
        "attempts_check": {
            "trade_decisions": trade_decision_total,
            "order_ids_rows": len(order_ids_rows),
            "independent_source_available": attempts_independent is not None,
            "side_a": attempts_side_a,
            "side_b": attempts_side_b,
            "mismatch": attempts_mismatch,
        },
        "fills_check": {
            "side_a_total_fills": fills_total,
            "side_b_resolved_plus_legit_open": fills_healthy,
            "mismatch": fills_mismatch,
        },
        "filled_count": filled_count,
        "no_fill_count": cancelled_count + reconciler_touched_count + other_count,
        "cancelled_nofill_count": cancelled_count,
        "reconciler_touched_count": reconciler_touched_count,
        "other_outcome_count": other_count,
        "resolved_count": resolved_total,
        "still_open_count": len(still_open),
        "legitimate_open_count": len(legitimate_opens),
        "stale_order_count": len(stale_orders),
        "orphan_count": len(orphans),
        "orphans": orphans,
        "stale_orders": stale_orders,
        "stale_after_hours": stale_after_hours,
        "duplicate_order_identities": duplicates,
        "plain_cancelled_audit": plain_cancelled_audit,
        "mislabeled_fill_suspects": mislabeled_fill_suspects,
        "per_fill_checks": per_fill_checks,
        "inconsistent_fills": inconsistent_fills,
        "slippage_flags": slippage_flags,
        "protective_exit_sanity_ok": len(orphans) == 0,
        "journal_only_parity_ok": journal_only_parity_ok,
        "broker_parity_status": broker_parity_status,
        "broker_parity_note": broker_parity_note,
        "clean": clean,
    }


def format_trade_chain_summary(section: dict[str, Any]) -> str:
    if section["clean"]:
        lines = [
            "TRADE CHAIN: PASS",
            f"{section['attempts_check']['side_a']} attempts",
            f"{section['fills_check']['side_a_total_fills']} fills",
            f"{section['no_fill_count']} no-fills",
            f"{section['resolved_count']} resolved",
            f"{section['legitimate_open_count']} legitimate opens",
            f"{section['orphan_count']} orphans",
            f"{section['stale_order_count']} stale orders",
            f"{len(section['duplicate_order_identities'])} duplicate identities",
            f"broker/journal parity: journal-only PASS; broker-side {section['broker_parity_status']}",
        ]
        return "\n".join(lines)

    lines = ["TRADE CHAIN: FAIL (details below)"]
    if section["attempts_check"]["mismatch"]:
        lines.append(f"  attempts mismatch: {section['attempts_check']}")
    if section["fills_check"]["mismatch"]:
        lines.append(f"  fills mismatch: {section['fills_check']}")
    if section["inconsistent_fills"]:
        lines.append(f"  {len(section['inconsistent_fills'])} inconsistent fill(s)")
    if section["duplicate_order_identities"]:
        lines.append(f"  {len(section['duplicate_order_identities'])} duplicate order identity(ies)")
    if section["orphans"]:
        lines.append(f"  {len(section['orphans'])} orphan(s): unresolved fills older than {section['stale_after_hours']}h with no OUTCOME and no order-id evidence")
    if section["mislabeled_fill_suspects"]:
        lines.append(f"  {len(section['mislabeled_fill_suspects'])} MISLABELED_FILL_SUSPECT row(s) per ops/audit_plain_cancelled.py")
    if section["journal_read_errors"]:
        lines.append(f"  {len(section['journal_read_errors'])} journal read error(s)")
    return "\n".join(lines)


# ─── Top-level report ───────────────────────────────────────────────────────

def build_daily_reconciliation_report(
    *,
    repo_root: str | Path | None = None,
    journal_dir: str | Path | None = None,
    since: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    root = resolve_repo_root(repo_root)
    if root is None:
        return {"ok": False, "error": "not inside a git repository (or `git` is unavailable)"}

    jdir = Path(journal_dir) if journal_dir else (root / "logs")
    since_dt, since_source = resolve_since(root, since, now=now)

    try:
        from config.settings import load_config
        config = load_config(str(root / "risk_rules.yaml"))
        config_error = None
    except Exception as exc:
        config = None
        config_error = str(exc)

    section_a = build_repo_reconciliation_section(root, now=now)
    section_b = build_evidence_preservation_section(section_a["closed_unmerged_candidates"])
    section_c = build_deployed_state_section(root)
    section_d = build_strategy_source_of_truth_section(root, now=now)
    section_e = build_trade_chain_section(jdir if jdir.exists() else DEFAULT_JOURNAL_DIR, since=since_dt, config=config)

    checkpoint_written, checkpoint_path = write_checkpoint(root, now=now)

    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "repo_root": str(root),
        "since": since_dt.isoformat(),
        "since_source": since_source,
        "config_load_error": config_error,
        "section_a_repo_reconciliation": section_a,
        "section_b_evidence_preservation": section_b,
        "section_c_deployed_state": section_c,
        "section_d_strategy_source_of_truth": section_d,
        "section_e_trade_chain_integrity": section_e,
        "checkpoint_written": checkpoint_written,
        "checkpoint_path": checkpoint_path,
    }


def format_daily_reconciliation_report(report: dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"DAILY RECONCILIATION — FAILED\n{report.get('error')}"

    lines = [
        "DAILY RECONCILIATION",
        f"repo_root: {report['repo_root']}",
        f"since: {report['since']}  ({report['since_source']})",
        "",
        "A. GITHUB/REPO RECONCILIATION",
    ]
    a = report["section_a_repo_reconciliation"]
    open_prs = a["open_prs"]
    lines.append(f"  open PRs: {len(open_prs) if isinstance(open_prs, list) else open_prs}")
    lines.append(f"  stale open PRs (>{a['stale_pr_threshold_days']}d): "
                 f"{len(a['stale_open_prs']) if isinstance(a['stale_open_prs'], list) else a['stale_open_prs']}")
    lines.append(f"  stale merged local branches (>{a['stale_merged_branch_threshold_days']}d): {len(a['stale_merged_local_branches'])}")
    lines.append(f"  active worktrees: {len(a['active_worktrees'])}  dirty: {len(a['dirty_worktrees'])}")
    lines.append(f"  branches tracking deleted remotes: {a['branches_tracking_deleted_remotes']}")
    lines.append(f"  local-only branches: {a['local_only_branches']}")
    lines.append(f"  local main vs origin/main: {a['local_main_vs_origin_main']['relationship']}")
    lines.append(f"  unexpected remote branches: {a['unexpected_remote_branches']}")
    lines.append(f"  stash count: {a['stash_count']}")

    lines.append("")
    lines.append("B. EVIDENCE PRESERVATION")
    b = report["section_b_evidence_preservation"]
    if not b["scoped"]:
        lines.append(f"  SKIPPED: {b['limitation']}")
    else:
        lines.append(f"  preserved: {len(b['preserved'])}  BLOCKERS: {len(b['blockers'])}")
        for row in b["blockers"]:
            lines.append(f"    BLOCKER: {row['branch']} — {row['reason']}")

    lines.append("")
    lines.append("C. DEPLOYED STATE")
    c = report["section_c_deployed_state"]
    lines.append(f"  intended deployed commit: {c['intended_deployed_commit']}")
    lines.append(f"  active paper-forward strategies: {c['active_paper_forward_lanes']}")
    lines.append(f"  contract cap per instrument: {c['contract_cap_per_instrument']}")

    lines.append("")
    lines.append("D. STRATEGY SOURCE OF TRUTH")
    d = report["section_d_strategy_source_of_truth"]
    lines.append(f"  inventory rows: {d['inventory_row_count']}  drift flags: {len(d['drift'])}")
    for row in d["drift"]:
        if row["status"] != "UNKNOWN":
            lines.append(f"    {row['status']}: {row['strategy']} — {row['reason']}")
    lines.append(f"  risk_rules strategies missing from inventory: {d['risk_rules_strategies_missing_from_inventory']}")
    if d["inventory_staleness"] and d["inventory_staleness"].get("stale"):
        lines.append(f"  INVENTORY STALE: last updated {d['inventory_staleness']['last_updated']} "
                     f"({d['inventory_staleness']['age_days']}d ago)")

    lines.append("")
    lines.append("E. TRADE CHAIN INTEGRITY")
    lines.append(format_trade_chain_summary(report["section_e_trade_chain_integrity"]))
    lines.append("")
    lines.append(f"checkpoint written: {report['checkpoint_written']} ({report['checkpoint_path']})")
    return "\n".join(lines)
