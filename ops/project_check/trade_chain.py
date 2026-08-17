"""Trade chain integrity: signal -> decision -> risk -> order -> fill/no-fill
-> protective bracket -> exit -> outcome -> flat position, for every new
paper/demo trade attempt since the prior checkpoint.

Strictly read-only against journals. Never cancels an order, flattens a
position, modifies a broker order, repairs a journal, synthesizes an
OUTCOME, rewrites state, retries an execution, or submits an order. It also
never calls a broker API -- broker/journal PARITY is reported UNKNOWN unless
the caller explicitly injects already-fetched, read-only broker snapshots
(see `broker_positions`/`broker_orders` params), which this package's CLI
does not do by default.

Reuses ops.proof_30_mnq (read_journal_entries, classify_outcome, parse_proof_ts)
and mirrors the TRADE<->OUTCOME/ORDER_IDS pairing style already used by
ops/reconciler_outcome_audit.py and scripts/session_audit.py, generalized to
all instruments in one pass instead of one instrument at a time.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from execution.paper_broker import TICK_SIZE
from ops.proof_30_mnq import classify_outcome, parse_proof_ts, read_journal_entries
from ops.project_check.runtime import runtime_snapshot

CHECKPOINT_SUBDIR = "afs-project-check"
CHECKPOINT_FILENAME = "trade_chain_checkpoint.json"
UNKNOWN = "UNKNOWN"


def _instrument_root(instrument: str | None) -> str | None:
    """MNQU5 -> MNQ, MESZ5 -> MES, etc: match down to the known alphabetic
    root TICK_SIZE and the runtime tolerance map are keyed on. Returns the
    raw instrument string unchanged if it doesn't match a known root
    (TICK_SIZE/tolerance lookups on it will then correctly report UNKNOWN
    rather than silently guessing), or None if instrument is empty/None.
    """
    if not instrument:
        return None
    for known in TICK_SIZE:
        if instrument == known or instrument.startswith(known):
            return known
    return instrument


def _execution_context_for_fill(
    row: dict[str, Any], outcome_body: dict[str, Any], live_snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Fold the journaled fill's ACTUAL entry model + the CURRENTLY configured
    tolerance into every resolved fill row -- the same "entry model and
    effective tolerance must be checked, not just assumed" lesson the
    promotion gate (ops/project_check/promotion.py) already encodes for
    research evidence, applied here to real paper/demo fills instead.

    entry_model_recorded comes straight from the journaled OUTCOME's
    order_type (set by PaperBroker/TradovateBroker at fill time -- see
    execution/paper_broker.py's `order_type=self._entry_fill_model`); it is
    UNKNOWN, never guessed, when the journal row predates that field or the
    broker path didn't set it.

    current_effective_tolerance_ticks is the runtime's tolerance RIGHT NOW,
    not necessarily what was configured when this historical fill happened --
    that caveat is stated explicitly rather than implied, so this is never
    mistaken for a claim about point-in-time configuration.
    """
    entry_model_recorded = outcome_body.get("order_type") or UNKNOWN
    requested_entry = outcome_body.get("requested_entry")
    entry_price = outcome_body.get("entry_price")
    root = _instrument_root(row.get("instrument"))
    tick_size = TICK_SIZE.get(root) if root else None
    live_tol = (live_snapshot.get("entry_tolerance_ticks") or {}).get(root) if root else None
    current_tolerance_ticks = (
        live_tol.get("effective_replay_paper") if isinstance(live_tol, dict) else None
    )

    actual_slippage_ticks: float | None = None
    direction = row.get("direction")
    if (
        requested_entry is not None
        and entry_price is not None
        and tick_size
        and direction in ("LONG", "SHORT")
    ):
        try:
            requested_f = float(requested_entry)
            entry_f = float(entry_price)
            adverse_price_delta = (
                (entry_f - requested_f) if direction == "LONG" else (requested_f - entry_f)
            )
            actual_slippage_ticks = round(adverse_price_delta / tick_size, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            actual_slippage_ticks = None

    exceeds_tolerance = None
    if actual_slippage_ticks is not None and current_tolerance_ticks is not None:
        try:
            exceeds_tolerance = actual_slippage_ticks > float(current_tolerance_ticks)
        except (TypeError, ValueError):
            exceeds_tolerance = None

    return {
        "entry_model_recorded": entry_model_recorded,
        "requested_entry": requested_entry,
        "entry_price": entry_price,
        "actual_slippage_ticks": actual_slippage_ticks if actual_slippage_ticks is not None else UNKNOWN,
        "current_effective_tolerance_ticks": (
            current_tolerance_ticks if current_tolerance_ticks is not None else UNKNOWN
        ),
        "current_tolerance_note": (
            "reflects the runtime's tolerance configuration NOW, at report time -- not "
            "necessarily what was configured when this historical fill executed"
        ),
        "exceeds_current_tolerance": exceeds_tolerance if exceeds_tolerance is not None else UNKNOWN,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _checkpoint_path(repo_root: Path) -> Path:
    return repo_root / ".git" / CHECKPOINT_SUBDIR / CHECKPOINT_FILENAME


def load_checkpoint_full(repo_root: Path) -> dict[str, Any] | None:
    path = _checkpoint_path(repo_root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_checkpoint(repo_root: Path) -> str | None:
    data = load_checkpoint_full(repo_root)
    return data.get("last_processed_ts") if data else None


def save_checkpoint(
    repo_root: Path,
    last_processed_ts: str | None,
    *,
    entries_at_or_before_count: int | None = None,
    content_fingerprint: str | None = None,
) -> None:
    """Persist the checkpoint ts plus a fingerprint of everything at/before it
    used to detect a journal that was rotated/truncated/rewritten, or
    backdated-appended/mutated, behind the checkpoint boundary between runs
    (see `_journal_integrity_check`). Never called on a FAIL result -- see
    build_trade_chain_report.
    """
    if last_processed_ts is None:
        return
    path = _checkpoint_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"last_processed_ts": last_processed_ts, "saved_at": _now_iso()}
    if entries_at_or_before_count is not None:
        payload["entries_at_or_before_count"] = entries_at_or_before_count
    if content_fingerprint is not None:
        payload["content_fingerprint"] = content_fingerprint
    fd, tmp_name = tempfile.mkstemp(prefix=f".{CHECKPOINT_FILENAME}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _entries_at_or_before(entries: list[dict[str, Any]], ts_cutoff: str) -> list[dict[str, Any]]:
    at_or_before = [e for e in entries if e.get("ts") and str(e["ts"]) <= ts_cutoff]
    # Sort explicitly rather than trusting read_journal_entries' file/line
    # order to stay stable -- the fingerprint must be reproducible across
    # runs regardless of read order, and must change if a row's CONTENT
    # changes even when its ts/count/position do not.
    return sorted(at_or_before, key=lambda e: (str(e.get("ts") or ""), str(e.get("_path") or ""), e.get("_line") or 0))


def _count_at_or_before(entries: list[dict[str, Any]], ts_cutoff: str) -> int:
    return len(_entries_at_or_before(entries, ts_cutoff))


def _content_fingerprint(entries: list[dict[str, Any]], ts_cutoff: str) -> str:
    """SHA-256 over every field of every entry at/before ts_cutoff, in a
    stable order, EXCLUDING the reader-injected _path/_line (file layout is
    not semantic content -- a row moving between files without its content
    changing should not itself trip this). This is the check that catches a
    same-count, same-timestamp REWRITE (e.g. an OUTCOME's result silently
    changed from WIN to LOSS) that `_count_at_or_before` alone cannot see.
    """
    rows = _entries_at_or_before(entries, ts_cutoff)
    canonical = [
        json.dumps({k: v for k, v in e.items() if k not in ("_path", "_line")}, sort_keys=True, default=str)
        for e in rows
    ]
    digest = hashlib.sha256("\n".join(canonical).encode("utf-8"))
    return digest.hexdigest()


def _journal_integrity_check(
    entries_no_errors: list[dict[str, Any]], checkpoint_data: dict[str, Any] | None, checkpoint_ts: str | None
) -> dict[str, Any]:
    """Detect a journal that changed behind the checkpoint boundary since it
    was saved -- rotation, truncation, a same-count content rewrite, or an
    append with a backdated timestamp into an already-processed day file.
    Pure ts-based windowing (`entry.ts > checkpoint_ts`) cannot see any of
    these on its own: a shrink silently drops evidence that was already
    counted, a rewrite silently swaps evidence without changing the count,
    and a backdated append silently never counts as "new". All three are
    exactly the kind of thing "no proof, no run" requires surfacing, not
    assuming away.
    """
    if checkpoint_ts is None:
        return {"checked": False, "status": "NOT_APPLICABLE", "reason": "no checkpoint in use this run"}
    current_count = _count_at_or_before(entries_no_errors, checkpoint_ts)
    current_fingerprint = _content_fingerprint(entries_no_errors, checkpoint_ts)
    recorded_count = (checkpoint_data or {}).get("entries_at_or_before_count")
    recorded_fingerprint = (checkpoint_data or {}).get("content_fingerprint")
    base = {
        "current_at_or_before_count": current_count,
        "recorded_at_or_before_count": recorded_count,
        "current_content_fingerprint": current_fingerprint,
        "recorded_content_fingerprint": recorded_fingerprint,
    }
    if recorded_count is None and recorded_fingerprint is None:
        return {
            "checked": True,
            "status": "UNKNOWN",
            "reason": "saved checkpoint predates integrity-fingerprint tracking; cannot verify",
            **base,
        }
    if recorded_count is not None and current_count < recorded_count:
        return {
            "checked": True,
            "status": "SHRUNK",
            "reason": (
                f"journal history at/before the checkpoint boundary shrank from {recorded_count} to "
                f"{current_count} entries -- rotation, truncation, or a rewrite likely destroyed "
                f"evidence behind the checkpoint; the checkpoint window is not trustworthy this run "
                f"and was NOT used (falling back to a full scan)"
            ),
            **base,
        }
    if recorded_count is not None and current_count > recorded_count:
        return {
            "checked": True,
            "status": "GREW_BEHIND_CHECKPOINT",
            "reason": (
                f"{current_count - recorded_count} entries now sit at/before the checkpoint boundary "
                f"that were not there when the checkpoint was saved -- likely a late/backdated append "
                f"into an already-processed journal day. Pure timestamp windowing will NEVER pick these "
                f"up as 'new'; re-run with --no-checkpoint (use_checkpoint=False) to force a full rescan "
                f"and confirm what they are."
            ),
            **base,
        }
    if recorded_fingerprint is not None and current_fingerprint != recorded_fingerprint:
        return {
            "checked": True,
            "status": "MUTATED",
            "reason": (
                "journal history at/before the checkpoint boundary has the SAME row count as when the "
                "checkpoint was saved, but its content fingerprint changed -- one or more rows behind "
                "the checkpoint were rewritten in place (e.g. a result/strategy/order-id/P&L field "
                "changed without changing the row count or timestamp). This is evidence tampering or "
                "corruption, not new activity; the checkpoint window is not trustworthy this run and "
                "was NOT used (falling back to a full scan)"
            ),
            **base,
        }
    if recorded_fingerprint is None:
        return {
            "checked": True,
            "status": "OK_COUNT_ONLY",
            "reason": (
                "count matches the saved checkpoint, but it predates content-fingerprint tracking, so "
                "a same-count in-place rewrite behind the checkpoint cannot be ruled out this run"
            ),
            **base,
        }
    return {"checked": True, "status": "OK", "reason": None, **base}


def _entry_ts(entry: dict[str, Any]) -> datetime | None:
    return parse_proof_ts(entry.get("ts"))


def _pair_fifo_by_instrument(
    anchors: list[dict[str, Any]], events: list[dict[str, Any]], all_in_order: list[dict[str, Any]]
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Pair each anchor with the next same-instrument event in a strict FIFO
    queue, walking all entries in journal (chronological) order. Returns
    (paired, unmatched_events) -- an event that arrives with no anchor
    currently queued for its instrument is NOT silently dropped, it comes
    back in unmatched_events for the caller to report.

    Same approach as ops.proof_30_mnq.pair_resolved_trades (queue.pop(0) on
    the oldest open anchor for that instrument when a matching event arrives),
    generalized to all instruments in one pass instead of one at a time. A
    nearest-single-global-match approach was tried first and rejected: it let
    an unresolved earlier trade "steal" a later trade's outcome across a
    journal-day boundary, which is exactly the kind of misattribution this
    routine exists to catch, not commit.
    """
    anchor_ids = {id(a) for a in anchors}
    event_ids = {id(e) for e in events}
    queues: dict[str, list[dict[str, Any]]] = defaultdict(list)
    paired: dict[int, dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []
    for entry in all_in_order:
        instrument = entry.get("instrument")
        if id(entry) in anchor_ids:
            queues[instrument].append(entry)
        elif id(entry) in event_ids:
            if queues[instrument]:
                paired[id(queues[instrument].pop(0))] = entry
            else:
                unmatched.append(entry)
    return paired, unmatched


def build_trade_chain_report(
    *,
    journal_dir: Path,
    repo_root: Path,
    since_ts: str | None = None,
    use_checkpoint: bool = True,
    advance_checkpoint: bool = False,
    broker_positions: Callable[[], list[dict[str, Any]]] | None = None,
    broker_orders: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    entries = read_journal_entries(journal_dir)
    read_errors = [e for e in entries if e.get("type") == "READ_ERROR"]
    entries_no_errors = [e for e in entries if e.get("type") != "READ_ERROR"]

    # Read once, reused for every resolved fill's execution-context check below
    # (see _execution_context_for_fill) -- this is the same "check the CURRENT
    # live runtime, don't just assert it" lesson ops/project_check/promotion.py
    # already applies to research evidence, applied here to real fills instead.
    live_snapshot = runtime_snapshot(repo_root=repo_root)

    checkpoint_data: dict[str, Any] | None = None
    checkpoint_ts = since_ts
    if checkpoint_ts is None and use_checkpoint:
        checkpoint_data = load_checkpoint_full(repo_root)
        checkpoint_ts = checkpoint_data.get("last_processed_ts") if checkpoint_data else None

    journal_integrity = _journal_integrity_check(entries_no_errors, checkpoint_data, checkpoint_ts)
    # A SHRUNK or MUTATED journal (rotation/truncation, or a same-count
    # in-place rewrite, behind the checkpoint) means the checkpoint boundary
    # can no longer be trusted -- fall back to a full scan rather than
    # silently windowing off of a boundary that may no longer correspond to
    # what's actually on disk.
    effective_checkpoint_ts = None if journal_integrity["status"] in ("SHRUNK", "MUTATED") else checkpoint_ts

    def is_new(entry: dict[str, Any]) -> bool:
        if effective_checkpoint_ts is None:
            return True
        ts = entry.get("ts")
        return ts is not None and str(ts) > effective_checkpoint_ts

    all_ts = sorted(str(e.get("ts")) for e in entries_no_errors if e.get("ts"))
    latest_journal_ts = all_ts[-1] if all_ts else None
    latest_journal_day = latest_journal_ts[:10] if latest_journal_ts else None

    # Pairing MUST run over the full journal history, not just the entries
    # newer than the checkpoint: a TRADE attempt logged before the checkpoint
    # can still be waiting on an OUTCOME that only arrives after it. Windowing
    # before pairing would make that OUTCOME look unmatched even though it
    # correctly resolves an older, still-open attempt -- exactly the false
    # permanent-FAIL failure mode this function exists to avoid.
    attempts_all = [
        e for e in entries_no_errors
        if e.get("decision") == "TRADE" and (e.get("risk_check") or {}).get("result") == "APPROVED"
    ]
    outcomes_all = [e for e in entries_no_errors if e.get("type") == "OUTCOME"]
    order_id_events_all = [e for e in entries_no_errors if e.get("type") == "ORDER_IDS"]
    risk_rejected = [e for e in entries_no_errors if e.get("decision") == "RISK_REJECTED" and is_new(e)]
    config_blocked = [e for e in entries_no_errors if e.get("decision") == "CONFIG_BLOCKED" and is_new(e)]

    outcome_by_attempt, unmatched_outcomes_all = _pair_fifo_by_instrument(
        attempts_all, outcomes_all, entries_no_errors
    )
    orderids_by_attempt, unmatched_order_ids_all = _pair_fifo_by_instrument(
        attempts_all, order_id_events_all, entries_no_errors
    )
    # Only report NEW unmatched events: one at/before the checkpoint was
    # already visible (and would have blocked the prior PASS) in an earlier
    # run, so re-flagging it forever would defeat the point of a checkpoint.
    unmatched_outcomes = [e for e in unmatched_outcomes_all if is_new(e)]
    unmatched_order_ids = [e for e in unmatched_order_ids_all if is_new(e)]

    attempts = [a for a in attempts_all if is_new(a)]

    resolved_fills: list[dict[str, Any]] = []
    resolved_cancellations: list[dict[str, Any]] = []
    needs_broker_verification: list[dict[str, Any]] = []
    unverified_open_attempts: list[dict[str, Any]] = []
    unresolved_orphan: list[dict[str, Any]] = []
    naked_position_risk: list[dict[str, Any]] = []
    slippage_flags: list[dict[str, Any]] = []
    # A carryover resolution: the ATTEMPT is old (at/before the checkpoint,
    # already reported by a prior run), but its OUTCOME just arrived. This is
    # new information -- a previously open/unresolved attempt got resolved --
    # surfaced separately rather than either re-counted as a "new attempt" or
    # dropped.
    carryover_resolutions: list[dict[str, Any]] = []

    def _classify_row(attempt: dict[str, Any], outcome: dict[str, Any] | None) -> dict[str, Any]:
        setup = attempt.get("setup") or {}
        row = {
            "trade_ts": attempt.get("ts"),
            "instrument": attempt.get("instrument"),
            "strategy": setup.get("strategy"),
            "direction": setup.get("direction"),
            "path": attempt.get("_path"),
            "line": attempt.get("_line"),
        }
        if outcome is None:
            return {**row, "_bucket": "unresolved", "_setup": setup}
        category = classify_outcome(outcome.get("outcome") or {})
        row["category"] = category
        row["exit_reason"] = (outcome.get("outcome") or {}).get("exit_reason")
        row["pnl_dollars"] = (outcome.get("outcome") or {}).get("pnl_dollars")
        return {**row, "_bucket": "resolved", "_category": category, "_setup": setup}

    for attempt in attempts:
        outcome = outcome_by_attempt.get(id(attempt))
        classified = _classify_row(attempt, outcome)
        if classified["_bucket"] == "unresolved":
            # No OUTCOME row means this attempt has NOT been proven to fill --
            # a journaled TRADE decision is an order attempt, not confirmed
            # broker evidence of a fill (see ops/forward_campaign_report.py's
            # fillable_state convention for the same distinction elsewhere in
            # the repo). Neither bucket below is ever counted as a "fill".
            attempt_day = (str(attempt.get("ts")) or "")[:10]
            row = {k: v for k, v in classified.items() if not k.startswith("_")}
            if latest_journal_day is not None and attempt_day == latest_journal_day:
                unverified_open_attempts.append({**row, "category": "UNVERIFIED_OPEN_ATTEMPT"})
            else:
                unresolved_orphan.append(
                    {
                        **row,
                        "category": "UNVERIFIED_STALE_ATTEMPT",
                        "reason": "no OUTCOME found and not from the most recent journal day",
                    }
                )
            continue
        setup = classified.pop("_setup")
        category = classified.pop("_category")
        classified.pop("_bucket")
        row = classified
        if category in ("filled_win_loss", "breakeven"):
            exec_ctx = _execution_context_for_fill(row, outcome.get("outcome") or {}, live_snapshot)
            row = {**row, "execution_context": exec_ctx}
            resolved_fills.append(row)
            if exec_ctx.get("exceeds_current_tolerance") is True:
                slippage_flags.append(
                    {
                        **{k: row[k] for k in ("trade_ts", "instrument", "strategy", "direction", "path", "line")},
                        "actual_slippage_ticks": exec_ctx["actual_slippage_ticks"],
                        "current_effective_tolerance_ticks": exec_ctx["current_effective_tolerance_ticks"],
                    }
                )
            if setup.get("stop") in (None, "") or setup.get("target") in (None, ""):
                naked_position_risk.append({**row, "issue": "filled trade missing stop or target in journaled setup"})
        elif category == "cancelled_nofill":
            resolved_cancellations.append(row)
        else:
            needs_broker_verification.append(row)

    for attempt in attempts_all:
        if is_new(attempt):
            continue  # already handled above as a "new" attempt
        outcome = outcome_by_attempt.get(id(attempt))
        if outcome is None or not is_new(outcome):
            continue  # still unresolved, or was already resolved before the checkpoint
        classified = _classify_row(attempt, outcome)
        setup = classified.pop("_setup")
        category = classified.pop("_category")
        classified.pop("_bucket")
        classified["fills_this_run"] = category in ("filled_win_loss", "breakeven")
        if classified["fills_this_run"]:
            classified["execution_context"] = _execution_context_for_fill(
                classified, outcome.get("outcome") or {}, live_snapshot
            )
        carryover_resolutions.append(classified)

    # Duplicate order-identity check across all order_ids events in the window.
    seen_order_ids: dict[str, list[str]] = defaultdict(list)
    for attempt in attempts:
        oi = orderids_by_attempt.get(id(attempt))
        if not oi:
            continue
        ids = (oi.get("order_ids") or {})
        label = f"{attempt.get('_path')}:{attempt.get('_line')}"
        for key, value in ids.items():
            if key == "instrument" or value in (None, ""):
                continue
            seen_order_ids[str(value)].append(label)
    duplicate_order_ids = {oid: locs for oid, locs in seen_order_ids.items() if len(locs) > 1}

    # "fills" counts ONLY attempts with independent fill evidence -- a resolved
    # OUTCOME classified WIN/LOSS/BREAKEVEN by ops.proof_30_mnq.classify_outcome.
    # A TRADE row alone is an order attempt, not proof of a fill: an
    # unresolved attempt (no OUTCOME yet) is reported separately as
    # UNVERIFIED_OPEN_ATTEMPT / UNVERIFIED_STALE_ATTEMPT and is NEVER folded
    # into fills, per the same "TRADE row != proven fill" distinction
    # ops/forward_campaign_report.py's fillable_state draws elsewhere in this
    # repo. Counting it as a fill without broker/order confirmation would let
    # this routine report a clean identity without actually proving anything
    # filled.
    fills_count = len(resolved_fills)
    cancellations_count = len(resolved_cancellations)
    rejects_or_no_fill_count = len(needs_broker_verification)
    unverified_open_count = len(unverified_open_attempts)
    orphans_count = len(unresolved_orphan)
    attempts_identity_holds = len(attempts) == (
        fills_count + cancellations_count + rejects_or_no_fill_count + unverified_open_count + orphans_count
    )

    risk_rejected_missing_reason = [
        {"ts": e.get("ts"), "instrument": e.get("instrument"), "path": e.get("_path"), "line": e.get("_line")}
        for e in risk_rejected
        if not e.get("reason")
    ]

    broker_parity: dict[str, Any]
    if broker_positions is None and broker_orders is None:
        broker_parity = {
            "checked": False,
            "reason": (
                "no read-only broker snapshot was supplied to this routine; broker/journal "
                "parity requires a live broker read this routine does not perform by default "
                "(see build_trade_chain_report's broker_positions/broker_orders params)"
            ),
        }
    else:
        broker_parity = {"checked": True, "note": "broker snapshot comparison not yet implemented for the supplied data"}

    problems = (
        orphans_count > 0
        or bool(duplicate_order_ids)
        or bool(naked_position_risk)
        or not attempts_identity_holds
        or bool(risk_rejected_missing_reason)
        or bool(unmatched_outcomes)
        or bool(unmatched_order_ids)
        or bool(read_errors)
        or journal_integrity["status"] in ("SHRUNK", "GREW_BEHIND_CHECKPOINT", "MUTATED")
    )
    status = "PASS" if not problems else "FAIL"

    # Never advance the checkpoint on FAIL: doing so would let today's
    # orphans/duplicate-identities/unmatched-outcomes/naked-fill findings
    # silently drop out of tomorrow's window, which is exactly the "checkpoint
    # hides a failure" defect this build was HELD for. Checkpoint advancement
    # is also opt-in (advance_checkpoint defaults to False, both here and in
    # the CLI) rather than a default side effect of running the report.
    checkpoint_advanced = False
    checkpoint_skip_reason = None
    if advance_checkpoint and latest_journal_ts:
        if status == "PASS":
            save_checkpoint(
                repo_root,
                latest_journal_ts,
                entries_at_or_before_count=_count_at_or_before(entries_no_errors, latest_journal_ts),
                content_fingerprint=_content_fingerprint(entries_no_errors, latest_journal_ts),
            )
            checkpoint_advanced = True
        else:
            checkpoint_skip_reason = (
                f"status=FAIL: checkpoint NOT advanced so the next run re-scans and re-reports "
                f"these findings instead of silently skipping past them"
            )
    elif advance_checkpoint and not latest_journal_ts:
        checkpoint_skip_reason = "no journal entries found in range; nothing to advance the checkpoint to"

    return {
        "ok": not read_errors,
        "routine": "trade-chain-integrity",
        "generated_at": _now_iso(),
        "journal_dir": str(journal_dir),
        "window": {
            "since_ts_exclusive": checkpoint_ts,
            "effective_since_ts_exclusive": effective_checkpoint_ts,
            "latest_journal_ts": latest_journal_ts,
            "used_checkpoint": use_checkpoint and since_ts is None,
            "checkpoint_advance_requested": advance_checkpoint,
            "checkpoint_advanced": checkpoint_advanced,
            "checkpoint_skip_reason": checkpoint_skip_reason,
        },
        "journal_integrity": journal_integrity,
        "journal_read_errors": read_errors,
        "status": status,
        "summary": {
            "attempts": len(attempts),
            "fills": fills_count,
            "resolved_fills": len(resolved_fills),
            "unverified_open_attempts": unverified_open_count,
            "cancellations": cancellations_count,
            "needs_broker_verification": rejects_or_no_fill_count,
            "orphans": orphans_count,
            "duplicate_order_identities": len(duplicate_order_ids),
            "naked_position_risk": len(naked_position_risk),
            "risk_rejected": len(risk_rejected),
            "risk_rejected_missing_reason": len(risk_rejected_missing_reason),
            "config_blocked": len(config_blocked),
            "unmatched_outcomes": len(unmatched_outcomes),
            "unmatched_order_ids": len(unmatched_order_ids),
            "carryover_resolutions": len(carryover_resolutions),
            "slippage_exceeds_current_tolerance": len(slippage_flags),
        },
        "accounting": {
            "attempts_identity": (
                "attempts = fills + cancellations + needs_broker_verification + "
                "unverified_open_attempts + orphans"
            ),
            "attempts_identity_holds": attempts_identity_holds,
            "fills_definition": (
                "fills counts ONLY attempts with a resolved WIN/LOSS/BREAKEVEN OUTCOME row "
                "(independent fill evidence via ops.proof_30_mnq.classify_outcome). An "
                "unresolved TRADE attempt is NEVER counted as a fill -- it is reported as "
                "unverified_open_attempts (current journal day) or orphans (stale) until an "
                "OUTCOME or broker fill confirmation proves it either way."
            ),
        },
        "detail": {
            "resolved_fills": resolved_fills,
            "unverified_open_attempts": unverified_open_attempts,
            "cancellations": resolved_cancellations,
            "needs_broker_verification": needs_broker_verification,
            "orphans": unresolved_orphan,
            "duplicate_order_identities": duplicate_order_ids,
            "naked_position_risk": naked_position_risk,
            "slippage_flags": slippage_flags,
            "risk_rejected_missing_reason": risk_rejected_missing_reason,
            "carryover_resolutions": carryover_resolutions,
            "unmatched_outcomes": [
                {
                    "ts": e.get("ts"),
                    "instrument": e.get("instrument"),
                    "path": e.get("_path"),
                    "line": e.get("_line"),
                    "result": (e.get("outcome") or {}).get("result"),
                    "exit_reason": (e.get("outcome") or {}).get("exit_reason"),
                }
                for e in unmatched_outcomes
            ],
            "unmatched_order_ids": [
                {"ts": e.get("ts"), "instrument": e.get("instrument"), "path": e.get("_path"), "line": e.get("_line")}
                for e in unmatched_order_ids
            ],
        },
        "broker_journal_parity": broker_parity,
        "execution_context_note": (
            "Every resolved fill's detail.resolved_fills row carries an execution_context "
            "block: the actual entry model recorded on that fill's journaled OUTCOME "
            "(order_type, UNKNOWN if the row predates it), the runtime's CURRENTLY "
            "configured tolerance for that instrument (not necessarily what was configured "
            "at execution time), and the actual adverse slippage in ticks computed from "
            "requested_entry vs entry_price. A fill exceeding the current tolerance is "
            "flagged in detail.slippage_flags and summary.slippage_exceeds_current_tolerance "
            "-- this is visibility only and does not by itself fail the trade-chain status; "
            "see the promotion gate for hard execution-context blockers on new evidence."
        ),
        "eod_day_only_boundary_check": {
            "checked": False,
            "reason": "no machine-readable per-strategy day-only-exit registry found in the repo to verify against",
        },
        "not_checked_reminder": (
            "This routine is read-only and never cancels an order, flattens a position, "
            "modifies a broker order, repairs a journal, synthesizes an OUTCOME, rewrites "
            "state, retries an execution, or submits an order."
        ),
    }
