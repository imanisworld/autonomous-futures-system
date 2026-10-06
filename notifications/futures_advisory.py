"""Operator-facing futures advisory cards from already-recorded system state.

Presentation only. This module never imports broker, risk, or strategy-decision
code, never places or queues an order, and never invents a field the recorded
candidate/journal row does not already carry.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from notifications import plain_english as pe

logger = logging.getLogger(__name__)

ADVISORY_ONLY = "SHADOW / ADVISORY ONLY"
RANK_STATUS = "EXPERIMENTAL / UNVALIDATED SYSTEM RANK"
EXECUTION_CAPABLE = False
MAX_ADVISORY_CARDS_PER_ALERT = 3
_DUPLICATE_DECISIONS = frozenset({"BLOCKED_DUPLICATE_BAR", "DUPLICATE_IGNORED"})
_EVIDENCE_LABELS = (
    "PROMISING BUT UNPROVEN",
    "VALIDATED",
    "PAPER PROOF",
    "RESEARCH ONLY",
    "RETIRE",
    "WAIT",
    "BROKEN",
    "OVERFIT",
    "UNSAFE",
)
_INSTRUMENTS = ("MNQ", "MES", "NQ", "ES", "MGC", "MCL")
_INVENTORY_CACHE: Optional[list[dict[str, Any]]] = None
_TITLE_ACRONYMS = {"orb", "vwap", "pdh", "pdl", "4hr", "60m", "htf"}

_FOOTER = (
    "ADVISORY ONLY · cannot place an order · recorded system state only · "
    "rank is unvalidated when shown"
)


def advisory_can_place_order() -> bool:
    """Hard presentation invariant: advisory output cannot place an order."""
    return EXECUTION_CAPABLE


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _copy_if_present(dst: dict[str, Any], src: dict[str, Any], key: str, *, dest_key: Optional[str] = None) -> None:
    if key in src and _present(src.get(key)):
        dst[dest_key or key] = src[key]


def _geometry(src: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("entry", "stop", "target"):
        if key in src and src.get(key) is not None:
            try:
                out[key] = float(src[key])
            except (TypeError, ValueError):
                pass
    rr = src.get("rr_ratio")
    if rr is None:
        rr = src.get("rr")
    if rr is not None:
        try:
            out["rr_ratio"] = float(rr)
        except (TypeError, ValueError):
            pass
    return out


def _has_bracket(src: dict[str, Any]) -> bool:
    geo = _geometry(src)
    return "entry" in geo and "stop" in geo and "target" in geo


def _strategy_title(strategy: str) -> str:
    text = str(strategy or "").strip()
    if not text:
        return text
    try:
        from ops.project_check.daily import STRATEGY_NAME_ALIASES

        aliases = [name for name, concept in STRATEGY_NAME_ALIASES.items() if concept == text]
        raw = aliases[0] if len(aliases) == 1 else text.replace("_", " ")
    except Exception:  # noqa: BLE001 — display fallback only
        raw = text.replace("_", " ")
    parts = []
    for part in raw.split():
        parts.append(part.upper() if part.lower() in _TITLE_ACRONYMS else part.capitalize())
    return " ".join(parts)


def _evidence_label(verdict: str) -> Optional[str]:
    """Match the leading inventory verdict label, not an embedded substring.

    ``NOT VALIDATED`` must not classify as ``VALIDATED``. Embedded labels such
    as ``PAPER PROOF`` inside a later clause must not override the leading
    verdict. Unknown leading text is omitted rather than guessed.
    """
    upper = (verdict or "").strip().upper()
    if not upper:
        return None
    leading = [label for label in _EVIDENCE_LABELS if upper.startswith(label)]
    if not leading:
        return None
    return max(leading, key=len)


def _inventory_rows() -> list[dict[str, Any]]:
    global _INVENTORY_CACHE
    if _INVENTORY_CACHE is not None:
        return _INVENTORY_CACHE
    try:
        from ops.project_check.daily import _parse_strategy_inventory

        path = Path(__file__).resolve().parents[1] / "docs" / "strategy-rules" / "Strategy_Inventory.md"
        rows, error = _parse_strategy_inventory(path)
        _INVENTORY_CACHE = [] if error else list(rows)
        return _INVENTORY_CACHE
    except Exception:  # noqa: BLE001 — presentation must never affect trading
        logger.debug("advisory inventory lookup skipped", exc_info=True)
        _INVENTORY_CACHE = []
        return _INVENTORY_CACHE


def evidence_classification_for(strategy: str, instrument: Optional[str] = None) -> Optional[str]:
    """Return the inventory evidence verdict only for a unique confirmed match.

    Unmatched, ambiguous, or heuristic-only mappings are omitted rather than guessed.
    """
    from ops.project_check.daily import STRATEGY_NAME_ALIASES, _normalize

    key = str(strategy or "").strip()
    if not key:
        return None
    aliases = [name for name, concept in STRATEGY_NAME_ALIASES.items() if concept == key]
    if not aliases:
        return None
    rows = _inventory_rows()
    matches: list[dict[str, Any]] = []
    for row in rows:
        normalized = _normalize(str(row.get("name") or ""))
        if any(normalized == alias or normalized.startswith(alias + " ") for alias in aliases):
            matches.append(row)
    if not matches:
        return None
    inst = str(instrument or "").strip().upper()
    if inst:
        named = [row for row in matches if inst in str(row.get("name") or "").upper()]
        if len(named) == 1:
            return _evidence_label(str(row_verdict(named[0]) or ""))
        if len(named) > 1:
            return None
        other_inst = [
            row
            for row in matches
            if any(token in str(row.get("name") or "").upper() for token in _INSTRUMENTS if token != inst)
        ]
        leftover = [row for row in matches if row not in other_inst]
        if len(leftover) == 1:
            return _evidence_label(str(row_verdict(leftover[0]) or ""))
        return None
    if len(matches) == 1:
        return _evidence_label(str(row_verdict(matches[0]) or ""))
    return None


def row_verdict(row: dict[str, Any]) -> Optional[str]:
    return row.get("verdict")


def _rank_fields(src: dict[str, Any], *, ranked_count: Optional[int]) -> dict[str, Any]:
    """Surface existing ranked-mode metadata only. Never manufacture a rank."""
    if str(src.get("selection_mode") or "") != "ranked":
        return {}
    index = src.get("rank_priority_index")
    if index is None:
        return {}
    try:
        position = int(index) + 1
    except (TypeError, ValueError):
        return {}
    out: dict[str, Any] = {
        "system_rank": position,
        "ranking_status": RANK_STATUS,
    }
    if ranked_count is not None and ranked_count > 0:
        out["system_rank_of"] = int(ranked_count)
    if _present(src.get("rank_reason")):
        out["rank_reason"] = src["rank_reason"]
    return out


def _suppression_reason(src: dict[str, Any], result: dict[str, Any]) -> Optional[str]:
    for key in ("reject_reason", "reject_code", "blocking_gate", "skip_reason"):
        value = src.get(key)
        if _present(value):
            return str(value)
    failed = src.get("failed_gates")
    if isinstance(failed, list) and failed:
        return str(failed[-1])
    if src.get("selected") is True:
        if _present(result.get("gate_reason")):
            return str(result["gate_reason"])
        risk = result.get("risk") if isinstance(result.get("risk"), dict) else {}
        if _present(risk.get("failed_rule")):
            return str(risk["failed_rule"])
        if _present(risk.get("reason")):
            return str(risk["reason"])
        if _present(result.get("reason")):
            return str(result["reason"])
    return None


def _posture(src: dict[str, Any], result: dict[str, Any], *, source: str) -> str:
    if source in {"shadow_setups", "shadow_outcome"}:
        return ADVISORY_ONLY
    if src.get("observation_only") is True:
        return ADVISORY_ONLY
    decision = str(result.get("decision") or "")
    if decision in {"SHADOW_NO_ORDER", "ORDER_SUPPRESSED"}:
        return ADVISORY_ONLY
    if decision == "TRADE":
        return "PAPER" if src.get("selected") is True else ADVISORY_ONLY
    if _present(src.get("strategy_permission_status")):
        return str(src["strategy_permission_status"])
    return ADVISORY_ONLY


def _identity_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("candidate_key"),
        record.get("instrument"),
        record.get("strategy"),
        record.get("direction"),
        record.get("entry"),
        record.get("stop"),
        record.get("target"),
        record.get("detection_timestamp"),
    )


def _resolver_lane(src: dict[str, Any], source: str) -> Optional[str]:
    if _present(src.get("lane")):
        return str(src["lane"])
    if source == "shadow_setups":
        return "shadow_setups"
    if source in {"range_signal", "shadow_range_signal"}:
        return "range_signal"
    return None


def _resolver_bar_ts(src: dict[str, Any], result: dict[str, Any], source: str) -> Optional[str]:
    for key in ("candidate_bar_ts", "bar_ts", "detected_at"):
        if _present(src.get(key)):
            return str(src[key])
    # Mirror strategy.shadow_resolver._pending_from_row: ts before timestamp.
    for key in ("ts", "timestamp"):
        if _present(src.get(key)):
            return str(src[key])
    if source == "shadow_outcome":
        return None
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    for key in ("ts", "timestamp"):
        if _present(result.get(key)):
            return str(result[key])
        if _present(context.get(key)):
            return str(context[key])
    return None


def _canonical_candidate_key(
    src: dict[str, Any],
    result: dict[str, Any],
    *,
    source: str,
) -> Optional[str]:
    """Resolver identity: recorded candidate_key, else reconstruct via _candidate_key.

    Does not invent a looser join. Missing lane/timestamp/geometry means no key.
    """
    if _present(src.get("candidate_key")):
        return str(src["candidate_key"])
    lane = _resolver_lane(src, source)
    bar_ts = _resolver_bar_ts(src, result, source)
    instrument = src.get("instrument") or src.get("symbol") or result.get("instrument")
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    if not _present(instrument):
        instrument = context.get("instrument")
    strategy = src.get("strategy")
    direction = str(src.get("direction") or src.get("candidate_direction") or "").upper()
    if not lane or not bar_ts or not _present(instrument) or not _present(strategy):
        return None
    if direction not in {"LONG", "SHORT"}:
        return None
    try:
        entry = float(src["entry"])
    except (KeyError, TypeError, ValueError):
        return None
    from strategy.shadow_resolver import _candidate_key, _population_fields

    epoch, variant = _population_fields(src, result)
    return _candidate_key(
        lane,
        str(instrument).replace("1!", ""),
        bar_ts,
        str(strategy),
        direction,
        entry,
        epoch,
        variant,
    )


def _outcome_from_shadow_row(row: dict[str, Any]) -> dict[str, Any]:
    nested = row.get("shadow_outcome") if isinstance(row.get("shadow_outcome"), dict) else row
    out: dict[str, Any] = {}
    result = nested.get("result")
    if _present(result):
        out["outcome"] = str(result)
    if "pnl_ticks" in nested and nested.get("pnl_ticks") is not None:
        out["pnl_ticks"] = nested["pnl_ticks"]
    if "pnl_dollars" in nested and nested.get("pnl_dollars") is not None:
        out["pnl_dollars"] = nested["pnl_dollars"]
    if _present(nested.get("exit_reason")):
        out["outcome_exit_reason"] = nested["exit_reason"]
    return out


def _base_record(
    src: dict[str, Any],
    result: dict[str, Any],
    *,
    source: str,
    ranked_count: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    if not _has_bracket(src):
        return None
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    record: dict[str, Any] = {
        "source": source,
        "execution_capable": False,
        "advisory_only": True,
    }
    instrument = src.get("instrument") or src.get("symbol") or result.get("instrument") or context.get("instrument")
    if _present(instrument):
        record["instrument"] = str(instrument).replace("1!", "")
    _copy_if_present(record, src, "strategy")
    direction = src.get("direction") or src.get("candidate_direction")
    if _present(direction):
        record["direction"] = str(direction).upper()
    record.update(_geometry(src))
    _copy_if_present(record, src, "risk_tier")
    session = src.get("session") or result.get("session") or context.get("session")
    if _present(session):
        record["session"] = session
    ts = (
        src.get("detected_at")
        or src.get("candidate_bar_ts")
        or src.get("ts")
        or result.get("timestamp")
        or result.get("ts")
        or context.get("ts")
    )
    if _present(ts):
        record["detection_timestamp"] = ts
    if "selected" in src:
        record["selected"] = bool(src.get("selected"))
    if "attempted" in src:
        record["attempted"] = bool(src.get("attempted"))
    condition = src.get("market_condition") or result.get("market_condition") or context.get("market_condition")
    if _present(condition):
        record["market_condition"] = condition
    if _present(src.get("notes")):
        record["setup_notes"] = src["notes"]
    elif _present(src.get("direction_reason")):
        record["setup_notes"] = src["direction_reason"]
    candidate_key = _canonical_candidate_key(src, result, source=source)
    if candidate_key:
        record["candidate_key"] = candidate_key
    suppression = _suppression_reason(src, result)
    if suppression:
        record["suppression_reason"] = suppression
    record["posture"] = _posture(src, result, source=source)
    record.update(_rank_fields(src, ranked_count=ranked_count))
    evidence = evidence_classification_for(str(record.get("strategy") or ""), record.get("instrument"))
    if evidence:
        record["evidence_classification"] = evidence
    return record


def _outcome_candidate_key(outcome_row: dict[str, Any]) -> Optional[str]:
    return _canonical_candidate_key(outcome_row, outcome_row, source="shadow_outcome")


def _geometry_fields(src: dict[str, Any]) -> dict[str, float]:
    geo = _geometry(src)
    nested = src.get("shadow_outcome") if isinstance(src.get("shadow_outcome"), dict) else {}
    if nested:
        nested_geo = _geometry(nested)
        for key in ("stop", "target"):
            if key not in geo and key in nested_geo:
                geo[key] = nested_geo[key]
    return geo


def _bracket_conflict(record: dict[str, Any], outcome_row: dict[str, Any]) -> bool:
    """Defensive presentation check: same resolver key, different stop/target.

    Does not change strategy.shadow_resolver._candidate_key() identity. If both
    sides carry stop and/or target, those recorded values must match.
    """
    rec = _geometry_fields(record)
    out = _geometry_fields(outcome_row)
    for key in ("stop", "target"):
        if key in rec and key in out and rec[key] != out[key]:
            return True
    return False


def _unique_outcome_row(
    record: dict[str, Any],
    outcome_rows: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Attach an outcome only when exactly one canonical candidate_key matches."""
    rec_key = record.get("candidate_key")
    if not rec_key:
        return None
    matches: list[dict[str, Any]] = []
    for row in outcome_rows:
        out_key = _outcome_candidate_key(row)
        if out_key == rec_key:
            matches.append(row)
    if len(matches) != 1:
        return None
    if _bracket_conflict(record, matches[0]):
        return None
    return matches[0]


def _row_fingerprint(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def _orphan_dedup_key(row: dict[str, Any], out_key: Optional[str]) -> str:
    return f"key:{out_key}" if out_key else f"bytes:{_row_fingerprint(row)}"


def build_advisory_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Build advisory records from already-recorded result/journal fields only."""
    if not isinstance(result, dict):
        return []
    if str(result.get("decision") or "") in _DUPLICATE_DECISIONS:
        return []
    records: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def add(src: dict[str, Any], source: str, ranked_count: Optional[int] = None) -> None:
        record = _base_record(src, result, source=source, ranked_count=ranked_count)
        if record is None:
            return
        key = _identity_key(record)
        if key in seen:
            return
        seen.add(key)
        records.append(record)

    audit = result.get("candidate_audit")
    audit_rows = [row for row in audit if isinstance(row, dict)] if isinstance(audit, list) else []
    ranked_count = (
        sum(1 for row in audit_rows if str(row.get("selection_mode") or "") == "ranked")
        or None
    )
    for row in audit_rows:
        add(row, "candidate_audit", ranked_count)

    blocked = result.get("blocked_candidate_audit")
    if isinstance(blocked, dict):
        blocked_rows = blocked.get("candidates") if isinstance(blocked.get("candidates"), list) else []
        for row in blocked_rows:
            if isinstance(row, dict):
                add(row, "blocked_candidate_audit")

    shadows = result.get("shadow_candidates")
    if isinstance(shadows, list):
        for row in shadows:
            if isinstance(row, dict):
                add(row, "shadow_setups")

    candidate = result.get("candidate")
    if isinstance(candidate, dict):
        add(candidate, "candidate_snapshot")

    outcomes = result.get("shadow_outcomes")
    outcome_rows = [row for row in outcomes if isinstance(row, dict)] if isinstance(outcomes, list) else []
    matched_outcome_keys: set[str] = set()
    for record in records:
        row = _unique_outcome_row(record, outcome_rows)
        if row is None:
            continue
        record.update(_outcome_from_shadow_row(row))
        key = _outcome_candidate_key(row)
        if key:
            matched_outcome_keys.add(key)
    seen_orphans: set[str] = set()
    for row in outcome_rows:
        out_key = _outcome_candidate_key(row)
        if out_key and out_key in matched_outcome_keys:
            continue
        if out_key and any(item.get("candidate_key") == out_key for item in records):
            continue
        fingerprint = _orphan_dedup_key(row, out_key)
        if fingerprint in seen_orphans:
            continue
        seen_orphans.add(fingerprint)
        sourced = dict(row)
        record = _base_record(sourced, result, source="shadow_outcome")
        if record is None:
            continue
        identity = _identity_key(record)
        if identity in seen:
            continue
        seen.add(identity)
        record.update(_outcome_from_shadow_row(row))
        records.append(record)
    return records


def format_advisory_card(record: dict[str, Any]) -> str:
    """Operator card. Missing fields are omitted, never filled with placeholders."""
    instrument = str(record.get("instrument") or "").strip()
    strategy = _strategy_title(str(record.get("strategy") or "").strip())
    direction = str(record.get("direction") or "").strip()
    title_parts = [part for part in (instrument, strategy, direction) if part]
    lines = [" — ".join(title_parts) if title_parts else "Futures setup"]
    lines.append(f"Status: {record.get('posture') or ADVISORY_ONLY}")
    if _present(record.get("evidence_classification")):
        lines.append(f"Strategy evidence: {record['evidence_classification']}")
    if "entry" in record:
        lines.append(f"Entry: {pe.price(record['entry'])}")
    if "stop" in record:
        lines.append(f"Stop: {pe.price(record['stop'])}")
    if "target" in record:
        lines.append(f"Target: {pe.price(record['target'])}")
    if "rr_ratio" in record:
        rr = record["rr_ratio"]
        lines.append(f"R:R: {rr:g}" if isinstance(rr, (int, float)) else f"R:R: {rr}")
    if _present(record.get("risk_tier")):
        lines.append(f"Risk tier: {record['risk_tier']}")
    if "system_rank" in record:
        rank_line = f"System rank: #{record['system_rank']}"
        if record.get("system_rank_of"):
            rank_line += f" of {record['system_rank_of']}"
        lines.append(rank_line)
        lines.append(f"Ranking status: {record.get('ranking_status') or RANK_STATUS}")
    if _present(record.get("session")) or _present(record.get("market_condition")):
        bits = []
        if _present(record.get("session")):
            bits.append(str(record["session"]))
        if _present(record.get("market_condition")):
            bits.append(str(record["market_condition"]))
        lines.append(f"Market/session context: {' · '.join(bits)}")
    if _present(record.get("detection_timestamp")):
        lines.append(f"Detected: {pe.et_time(record['detection_timestamp'])}")
    if _present(record.get("setup_notes")):
        lines.append(f"Setup notes: {record['setup_notes']}")
    if "selected" in record:
        lines.append("Selected: yes" if record["selected"] else "Selected: no")
    if "attempted" in record:
        lines.append("Attempted: yes" if record["attempted"] else "Attempted: no")
    if _present(record.get("suppression_reason")):
        lines.append(f"Why execution was blocked/suppressed: {record['suppression_reason']}")
    if _present(record.get("outcome")):
        outcome_line = f"Later outcome: {record['outcome']}"
        if _present(record.get("outcome_exit_reason")):
            outcome_line += f" ({record['outcome_exit_reason']})"
        lines.append(outcome_line)
        if record.get("pnl_ticks") is not None:
            lines.append(f"Simulated ticks: {record['pnl_ticks']}")
        if record.get("pnl_dollars") is not None:
            lines.append(f"Simulated P&L: {pe.money(record['pnl_dollars'])}")
    lines.append(_FOOTER)
    return "\n".join(lines)


def attach_runtime_sources(
    result: dict[str, Any],
    *,
    decision: Any = None,
    shadow_outcomes: Optional[list] = None,
) -> None:
    """Copy already-recorded candidate/outcome sources onto the alert result."""
    if not isinstance(result, dict):
        return
    if decision is not None:
        audit = list(getattr(decision, "candidate_audit", []) or [])
        result["candidate_audit"] = audit
        blocked = getattr(decision, "blocked_candidate_audit", None)
        if blocked is not None:
            result["blocked_candidate_audit"] = blocked
    if shadow_outcomes:
        result["shadow_outcomes"] = list(shadow_outcomes)
        result["shadow_outcomes_resolved"] = len(shadow_outcomes)


def journal_advisory_records(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read-only advisory list from today's already-written journal rows."""
    records: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "SHADOW_OUTCOME":
            outcomes.append(entry)
            continue
        if entry.get("type") in {"OUTCOME", "ORDER_IDS", "BAR_CLAIM"}:
            continue
        pending.extend(build_advisory_records(entry))
    for record in pending:
        row = _unique_outcome_row(record, outcomes)
        if row is not None:
            record.update(_outcome_from_shadow_row(row))
        records.append(record)
    matched_keys = {
        str(record["candidate_key"])
        for record in records
        if record.get("candidate_key") and record.get("outcome")
    }
    seen_orphans: set[str] = set()
    for row in outcomes:
        out_key = _outcome_candidate_key(row)
        if out_key and out_key in matched_keys:
            continue
        fingerprint = _orphan_dedup_key(row, out_key)
        if fingerprint in seen_orphans:
            continue
        seen_orphans.add(fingerprint)
        extra = _base_record(row, row, source="shadow_outcome")
        if extra is None:
            extra = {
                "source": "shadow_outcome",
                "execution_capable": False,
                "advisory_only": True,
                "posture": ADVISORY_ONLY,
            }
            if _present(row.get("instrument")):
                extra["instrument"] = row["instrument"]
            if _present(row.get("strategy")):
                extra["strategy"] = row["strategy"]
            if _present(row.get("direction")):
                extra["direction"] = row["direction"]
            extra.update(_geometry(row))
            key = _outcome_candidate_key(row)
            if key:
                extra["candidate_key"] = key
        extra.update(_outcome_from_shadow_row(row))
        records.append(extra)
    return records[-20:]


def discord_advisory_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Discord-delivery subset. Does not cap journals, dashboard, or evidence."""
    return list(records[:MAX_ADVISORY_CARDS_PER_ALERT])


def _redact(exc: BaseException) -> str:
    from notifications.discord_router import redact_webhooks

    return redact_webhooks(exc)


# Background delivery matches notifications.observation_notifier: the alert path
# that holds _alert_lock only formats and enqueues. HTTP, retries, and 429
# sleeps run on one daemon thread and cannot delay later futures bars.
MAX_RETRY_WAIT = 30.0
MIN_SEND_INTERVAL = 1.0
MAX_QUEUE_MESSAGES = 20
MAX_MESSAGE_AGE = 600.0
DELIVERY_MODE = "background"


class _Dispatcher:
    """One daemon thread draining a bounded queue of advisory webhook bodies."""

    def __init__(self, *, sleep=time.sleep, clock=time.monotonic) -> None:
        self._queue: "queue.Queue[tuple[object, dict, int, float]]" = queue.Queue(
            maxsize=MAX_QUEUE_MESSAGES
        )
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.delivered_cards = 0
        self.dropped_cards = 0

    def submit(self, router, messages: list[tuple[dict, int]]) -> int:
        """Enqueue without blocking; returns the number of cards accepted."""
        self._ensure_thread()
        accepted = 0
        for body, cards in messages:
            try:
                self._queue.put_nowait((router, body, cards, self._clock()))
                accepted += cards
            except queue.Full:
                self.dropped_cards += cards
                logger.error(
                    "futures advisory Discord queue full (%d messages); %d card(s) dropped.",
                    MAX_QUEUE_MESSAGES,
                    cards,
                )
        return accepted

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="futures-advisory-discord", daemon=True
                )
                self._thread.start()

    def _run(self) -> None:
        while True:
            router, body, cards, queued_at = self._queue.get()
            try:
                age = self._clock() - queued_at
                if age > MAX_MESSAGE_AGE:
                    self.dropped_cards += cards
                    logger.error(
                        "futures advisory Discord message %.0fs old; %d card(s) dropped.",
                        age,
                        cards,
                    )
                    continue
                if router.send("signal", body, max_retry_wait=MAX_RETRY_WAIT):
                    self.delivered_cards += cards
                else:
                    self.dropped_cards += cards
                    logger.error(
                        "futures advisory Discord delivery failed; %d card(s) dropped.",
                        cards,
                    )
            except Exception as exc:  # noqa: BLE001 - the worker must survive anything
                self.dropped_cards += cards
                logger.warning(
                    "futures advisory Discord worker error; %d card(s) dropped: %s: %s",
                    cards,
                    type(exc).__name__,
                    _redact(exc),
                )
            finally:
                self._queue.task_done()
                self._sleep(MIN_SEND_INTERVAL)

    def join(self, timeout: float = 5.0) -> bool:
        """Test helper: wait until everything queued has been handled."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return not self._queue.unfinished_tasks


_DISPATCHER = _Dispatcher()


def notify_futures_advisory(
    result: dict[str, Any],
    *,
    config: Any = None,
    router: Any = None,
) -> int:
    """Enqueue advisory cards on the existing signal Discord route. Fail-soft.

    The caller that holds _alert_lock must not wait for Discord HTTP. A Discord
    failure cannot change trading state. The result snapshot is copied so
    notification work never mutates the trading decision dict.
    """
    del config  # reserved for callers that already hold SystemConfig
    if not isinstance(result, dict) or result.get("smoke_test"):
        return 0
    snapshot = dict(result)
    records = discord_advisory_records(build_advisory_records(snapshot))
    if not records:
        return 0
    try:
        from notifications.discord_card import card_payload
        from notifications.discord_router import DiscordRouter

        active_router = router or DiscordRouter()
        if not active_router.is_enabled("signal"):
            return 0
        messages = [
            (card_payload(format_advisory_card(record), source="futures advisory"), 1)
            for record in records
        ]
        inline = router is not None or DELIVERY_MODE == "inline"
        if not inline:
            return _DISPATCHER.submit(active_router, messages)
        sent = 0
        for body, cards in messages:
            if active_router.send("signal", body):
                sent += cards
        return sent
    except Exception:  # noqa: BLE001 — notification must never affect trading
        logger.warning("futures advisory Discord notification skipped", exc_info=True)
        return 0
