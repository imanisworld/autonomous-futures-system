"""
journal/journal_logger.py

Append-only JSONL journal of all decisions and trade outcomes.
Logs are the authoritative record of system behavior.

File format: logs/journal_YYYY-MM-DD.jsonl
One JSON object per line, never edited after write.

Also provides daily state reconstruction by reading today's journal.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Iterator, Optional, List

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from risk.risk_engine import DailyState


logger = logging.getLogger(__name__)


# Full journal rows are large Python object graphs (several times larger than
# their JSONL representation).  Keep only the small recent working set needed
# by daily/status readers.  All-history account/performance scans use the
# compact outcome cache below instead of pinning every parsed journal in RAM.
_MAX_PARSED_JOURNAL_CACHE_FILES = 8

# Ceilings for the compact outcome cache below. That cache is what keeps
# all-history balance/peak/epoch scans cheap without pinning parsed rows, and
# those scans touch EVERY retained journal, so a ceiling at or below the
# journal count would re-parse the whole set on every scan -- LRU is the worst
# policy for a full-sweep access pattern, because it evicts exactly what the
# next sweep reads first. These are therefore set far above observed volume:
# they bound a pathological day, they are not a working-set limit.
# Measured on the live box 2026-09-03: 82 journal files / 86.3 MB of JSONL held
# 96 OUTCOME rows and 32 performance rows in total -- about 20 KB of tuples,
# growing about 1-2 rows/day. Both ceilings are unreachable at that rate.
_MAX_OUTCOME_CACHE_FILES = 512      # ~1.4 years of daily journals
_MAX_OUTCOME_CACHE_ROWS = 50_000    # ~8 MB worst case, ~390x the current total


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class JournalLogger:
    """
    Append-only JSONL decision and trade journal.

    Responsibilities:
    - Write every decision (TRADE, NO_TRADE, DONE_FOR_DAY, WAIT) to disk
    - Update trade outcomes when paper broker resolves a position
    - Reconstruct daily state (trade count, loss streak) from today's journal
    """

    # Process-wide parsed-journal cache, keyed by path → ((mtime_ns, size), entries).
    # Parsing the day's JSONL was the dominant /status cost (~8 reads per request,
    # every 30s per open tab). A webhook append changes mtime/size and invalidates
    # the entry automatically; stat() is ~microseconds vs a full re-parse. Shared
    # across instances because each request builds a fresh JournalLogger.
    _entries_cache: dict = {}
    # path -> ((mtime_ns, size), {account: [(ts, pnl)], performance: [(result, pnl)]})
    # This preserves fast all-history scans without retaining every decision row.
    _outcome_cache: dict = {}

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._error_log = self.log_dir / "errors.log"

    def _journal_path(self, for_date: Optional[date] = None) -> Path:
        d = for_date or date.today()
        return self.log_dir / f"journal_{d.isoformat()}.jsonl"

    # ── Write ──────────────────────────────────────────────────────────────────

    def log_decision(
        self,
        decision_dict: dict,
        risk_result: Optional[dict] = None,
        for_date: Optional[date] = None,
    ) -> None:
        """
        Append a decision entry to today's journal.

        Args:
            decision_dict: Output from DecisionOutput.to_dict()
            risk_result: Optional dict with {result, failed_rule, reason}
        """
        entry = dict(decision_dict)
        if risk_result:
            entry["risk_check"] = risk_result
        entry.setdefault("outcome", None)
        self._append(entry, for_date)

    def log_outcome(
        self,
        instrument: str,
        session: str,
        result: str,
        entry_price: float,
        exit_price: Optional[float],
        exit_reason: Optional[str],
        pnl_ticks: Optional[float],
        pnl_dollars: Optional[float],
        contracts: int = 1,
        for_date: Optional[date] = None,
        *,
        # Diagnostic-only fields (no-fill cause taxonomy). All optional,
        # default None so every existing caller is unaffected. See
        # execution/no_fill_taxonomy.py for no_fill_reason bucket meanings.
        no_fill_reason: Optional[str] = None,
        order_type: Optional[str] = None,
        broker_status_raw: Optional[str] = None,
        strategy: Optional[str] = None,
        signal_timestamp: Optional[str] = None,
        submit_timestamp: Optional[str] = None,
        cancel_timestamp: Optional[str] = None,
        seconds_until_cancel: Optional[float] = None,
        requested_entry: Optional[float] = None,
        last_price_at_submit: Optional[float] = None,
        last_price_at_cancel: Optional[float] = None,
        best_bid_at_submit: Optional[float] = None,
        best_ask_at_submit: Optional[float] = None,
        ticks_moved_from_entry: Optional[float] = None,
        paper_order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        execution_audit: Optional[dict] = None,
    ) -> None:
        """
        Append a trade outcome entry to today's journal.
        Called after paper broker resolves a position.
        """
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "OUTCOME",
            "instrument": instrument,
            "session": session,
            "outcome": {
                "result": result,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "pnl_ticks": pnl_ticks,
                "pnl_dollars": pnl_dollars,
                "contracts": contracts,
                "no_fill_reason": no_fill_reason,
                "order_type": order_type,
                "broker_status_raw": broker_status_raw,
                "strategy": strategy,
                "signal_timestamp": signal_timestamp,
                "submit_timestamp": submit_timestamp,
                "cancel_timestamp": cancel_timestamp,
                "seconds_until_cancel": seconds_until_cancel,
                "requested_entry": requested_entry,
                "last_price_at_submit": last_price_at_submit,
                "last_price_at_cancel": last_price_at_cancel,
                "best_bid_at_submit": best_bid_at_submit,
                "best_ask_at_submit": best_ask_at_submit,
                "ticks_moved_from_entry": ticks_moved_from_entry,
                "paper_order_id": paper_order_id,
                "client_order_id": client_order_id,
                "execution_audit": execution_audit,
            },
        }
        self._append(entry, for_date)

    def log_day_only_exit_issue(
        self,
        *,
        instrument: str,
        strategy: str,
        reason: str,
        for_date: Optional[date] = None,
    ) -> None:
        """Record unresolved day-only evidence without fabricating an OUTCOME."""
        self._append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": "DAY_ONLY_EXIT_ISSUE",
                "instrument": instrument,
                "strategy": strategy,
                "reason": reason,
                "outcome": None,
            },
            for_date,
        )

    def log_order_ids(
        self,
        instrument: str,
        session: str,
        order_ids: dict,
        for_date: Optional[date] = None,
        *,
        stop: Optional[float] = None,
        exit_mode: Optional[str] = None,
        client_order_id: Optional[str] = None,
    ) -> None:
        """Append the broker's OSO order ids for the currently-open position.

        Lets a fresh broker instance (e.g. after a restart) restore order-id based
        exit attribution in resolve_position() instead of degrading to price-
        matching (which can misbook a slipped-stop loss as BREAKEVEN). Additive,
        append-only record; read only by get_open_position().
        """
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "ORDER_IDS",
            "instrument": instrument,
            "session": session,
            "order_ids": dict(order_ids or {}),
        }
        if client_order_id:
            record["client_order_id"] = str(client_order_id)
        if stop is not None:
            record["stop"] = float(stop)
        if exit_mode:
            record["exit_mode"] = str(exit_mode)
        self._append(record, for_date)

    def log_block_visibility(self, record: dict, for_date: Optional[date] = None) -> None:
        """Append a BLOCK_VISIBILITY record for a single-position block.

        Makes a BLOCKED_OPEN_POSITION early-return visible in the journal (the
        gate itself is unchanged and still runs upstream). The record is INERT
        to daily state: it carries no `decision`, and its `type` is neither
        OUTCOME, TRADE, nor ORDER_IDS, so `_compute_daily_state` and
        `get_open_position` both skip it — repeated blocked bars can never create
        a duplicate trade outcome or alter the position slot. Fails closed
        (errors swallowed, never raised) so visibility can never break the block.
        """
        try:
            entry = dict(record)
            entry.setdefault("type", "BLOCK_VISIBILITY")
            entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
            self._append(entry, for_date)
        except Exception as exc:  # noqa: BLE001 — visibility must never raise
            logger.debug("log_block_visibility failed: %s", exc)

    def log_order_suppression(
        self,
        *,
        instrument: str,
        session: str,
        final_decision: str,
        gate_reason: str,
        strategy: Optional[str] = None,
        signal_timestamp: Optional[str] = None,
        client_order_id: Optional[str] = None,
        for_date: Optional[date] = None,
    ) -> None:
        """Append the final reason an approved intent did not reach the broker.

        Audit-only and inert to daily state: the record carries no `decision`
        field and is not an OUTCOME/TRADE/ORDER_IDS row. It therefore cannot
        create, close, count, or otherwise mutate a position; it only makes the
        terminal suppression durable after the earlier TRADE_INTENT row.
        """
        try:
            self._append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "type": "ORDER_SUPPRESSION",
                    "instrument": instrument,
                    "session": session,
                    "final_decision": final_decision,
                    "gate_reason": gate_reason,
                    "strategy": strategy,
                    "signal_timestamp": signal_timestamp,
                    "client_order_id": client_order_id,
                },
                for_date,
            )
        except Exception as exc:  # noqa: BLE001 — visibility must never raise
            logger.debug("log_order_suppression failed: %s", exc)

    def last_reconcile_ts(self, for_date: Optional[date] = None) -> Optional[str]:
        """Best-effort timestamp of the most recent reconcile-sourced OUTCOME on
        `for_date` (session=='reconcile'), else None. Read-only; used only to
        populate the visibility record's `last_reconcile_ts` field."""
        try:
            path = self._journal_path(for_date)
            if not path.exists():
                return None
            latest = None
            for entry in self._read_entries(path):
                if entry.get("type") == "OUTCOME" and entry.get("session") == "reconcile":
                    ts = entry.get("ts")
                    if ts and (latest is None or ts > latest):
                        latest = ts
            return latest
        except Exception:
            return None

    def log_error(self, message: str, exc: Optional[Exception] = None) -> None:
        """Append to the error log."""
        ts = datetime.now(timezone.utc).isoformat()
        line = f"[{ts}] ERROR: {message}"
        if exc:
            line += f" | {type(exc).__name__}: {exc}"
        with self._locked():
            with open(self._error_log, "a") as f:
                f.write(line + "\n")
        logger.error(line)

    def log_scout(self, entry: dict, for_date: Optional[date] = None) -> None:
        """Append a Scout classification entry (read-only context).

        Tags the entry as an external Scout signal and writes it append-only.
        This is context/audit output ONLY — it never places or authorizes a
        trade. Fails closed (errors are swallowed, never raised to the caller).
        """
        record = dict(entry)
        record["type"] = "SCOUT_SIGNAL"
        record.setdefault("source", "scout")
        record.setdefault("scout_trade_authorized", False)
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        self._append(record, for_date)

    def log_shadow_outcome(self, entry: dict, for_date: Optional[date] = None) -> None:
        """Append a resolved observe-only candidate outcome (evidence lane).

        Audit output ONLY — never places, sizes, or authorizes a trade. The
        record is written under type="SHADOW_OUTCOME" with its payload in
        `shadow_outcome` (deliberately NOT `outcome`/`decision`, so daily-state
        reconstruction, claim_bar, and trade-pair matching all ignore it).
        """
        record = dict(entry)
        record["type"] = "SHADOW_OUTCOME"
        record.pop("outcome", None)
        record.pop("decision", None)
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        self._append(record, for_date)

    def read_day(self, for_date: Optional[date] = None) -> List[dict]:
        """Return all parsed journal rows for one day (cached, read-only)."""
        path = self._journal_path(for_date)
        if not path.exists():
            return []
        return self._read_entries(path)

    def claim_bar(
        self,
        *,
        instrument: str,
        bar_ts: str,
        for_date: Optional[date] = None,
        timeframe_minutes: Optional[int] = None,
    ) -> bool:
        """Atomically claim one instrument/bar timestamp before gate evaluation.

        Returns False if this journal already has a claim or decision for the
        same instrument + bar timestamp. This prevents duplicate webhook workers
        from both passing daily/open-position gates for the same TradingView bar.

        timeframe_minutes: when provided, bar identity is
        (instrument, bar_ts, timeframe_minutes) instead of just
        (instrument, bar_ts). A 5-minute bar and a 15-minute bar can share the
        same wall-clock timestamp (e.g. every 15-minute boundary) without being
        the same bar -- a 5-minute-native strategy's bar must never suppress
        the authoritative 15-minute decision bar, or vice versa. An existing
        entry with no recorded timeframe (older rows, or a caller that omitted
        this parameter) is still treated as a match for backward compatibility
        -- this only carves out a DIFFERENT, explicitly-recorded timeframe as
        non-colliding, it never makes matching looser than before.
        """
        path = self._journal_path(for_date)
        with self._locked():
            if path.exists():
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if entry.get("instrument") != instrument:
                            continue
                        if entry.get("ts") != bar_ts:
                            continue
                        if entry.get("type") == "OUTCOME":
                            continue
                        if timeframe_minutes is not None:
                            existing_tf = entry.get("timeframe_minutes")
                            if existing_tf is not None and existing_tf != timeframe_minutes:
                                continue
                        return False
            entry = {
                "ts": bar_ts,
                "type": "BAR_CLAIM",
                "instrument": instrument,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }
            if timeframe_minutes is not None:
                entry["timeframe_minutes"] = timeframe_minutes
            with open(path, "a") as f:
                f.write(json.dumps(entry) + "\n")
            return True

    def _append(self, entry: dict, for_date: Optional[date] = None) -> None:
        """Append a single JSON entry to today's journal file."""
        path = self._journal_path(for_date)
        try:
            with self._locked():
                with open(path, "a") as f:
                    f.write(json.dumps(entry) + "\n")
        except Exception as e:
            self.log_error(f"Failed to write journal entry: {entry}", exc=e)

    # ── Read / Reconstruct ────────────────────────────────────────────────────

    def get_daily_state(self, for_date: Optional[date] = None) -> DailyState:
        """
        Reconstruct DailyState from today's journal.

        Counts:
        - trade_count: number of entries where decision==TRADE and risk_check.result==APPROVED
        - consecutive_losses: trailing count of LOSS outcomes
        - has_open_position: True if last approved trade has no outcome yet
        """
        path = self._journal_path(for_date)
        if not path.exists():
            return DailyState(
                trade_count=0,
                consecutive_losses=0,
                has_open_position=False,
                date=(for_date or date.today()).isoformat(),
            )

        entries = self._read_entries(path)
        return self._compute_daily_state(entries, for_date)

    def get_daily_state_since(
        self,
        since: datetime,
        for_date: Optional[date] = None,
    ) -> DailyState:
        """Reconstruct daily risk counters from an explicit epoch boundary.

        Entries without a parseable processing timestamp are excluded. This is
        used only by preregistered isolated paper epochs; the ordinary shared
        account reconstruction remains unchanged.
        """
        path = self._journal_path(for_date)
        if not path.exists():
            return DailyState(date=(for_date or date.today()).isoformat())
        boundary = _aware_utc(since)
        entries = [
            entry
            for entry in self._read_entries(path)
            if (entry_ts := _parse_ts(entry.get("ts"))) is not None
            and _aware_utc(entry_ts) >= boundary
        ]
        return self._compute_daily_state(entries, for_date)

    def _read_entries(self, path: Path) -> List[dict]:
        key = str(path)
        try:
            st = path.stat()
        except FileNotFoundError:
            JournalLogger._entries_cache.pop(key, None)
            JournalLogger._outcome_cache.pop(key, None)
            return []
        sig = (st.st_mtime_ns, st.st_size)
        cached = JournalLogger._entries_cache.get(key)
        if cached is not None and cached[0] == sig:
            # Unchanged since last parse — reuse. Callers treat entries as
            # read-only (they filter/iterate, never mutate in place).
            JournalLogger._entries_cache.pop(key)
            JournalLogger._entries_cache[key] = cached
            return cached[1]
        entries: List[dict] = []
        with self._locked():
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        JournalLogger._entries_cache.pop(key, None)
        JournalLogger._entries_cache[key] = (sig, entries)
        while len(JournalLogger._entries_cache) > _MAX_PARSED_JOURNAL_CACHE_FILES:
            oldest = next(iter(JournalLogger._entries_cache))
            JournalLogger._entries_cache.pop(oldest, None)
        return entries

    def _read_outcome_summary(self, path: Path) -> dict:
        """Return compact realized-outcome facts for one journal file.

        Account reconstruction historically counts standalone OUTCOME rows,
        while performance stats also accept the legacy inline TRADE outcome
        format.  Keep those two views separate so this memory repair does not
        change accounting semantics.
        """
        key = str(path)
        try:
            st = path.stat()
        except FileNotFoundError:
            JournalLogger._entries_cache.pop(key, None)
            JournalLogger._outcome_cache.pop(key, None)
            return {"account": [], "performance": []}
        sig = (st.st_mtime_ns, st.st_size)
        cached = JournalLogger._outcome_cache.get(key)
        if cached is not None and cached[0] == sig:
            # Refresh recency so a hot file is not the next one evicted.
            JournalLogger._outcome_cache.pop(key)
            JournalLogger._outcome_cache[key] = cached
            return cached[1]

        account: list[tuple[object, float]] = []
        performance: list[tuple[str, float]] = []
        with self._locked():
            with open(path) as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if entry.get("type") == "OUTCOME":
                        outcome = entry.get("outcome") or {}
                        result = outcome.get("result")
                        pnl = float(outcome.get("pnl_dollars") or 0.0)
                        account.append((entry.get("ts"), pnl))
                        if result in ("WIN", "LOSS", "BREAKEVEN"):
                            performance.append((str(result), pnl))
                        continue

                    if entry.get("decision") != "TRADE":
                        continue
                    if (entry.get("risk_check") or {}).get("result") != "APPROVED":
                        continue
                    outcome = entry.get("outcome") or {}
                    result = outcome.get("result")
                    if result in ("WIN", "LOSS", "BREAKEVEN"):
                        performance.append(
                            (str(result), float(outcome.get("pnl_dollars") or 0.0))
                        )

        summary = {"account": account, "performance": performance}
        JournalLogger._outcome_cache.pop(key, None)
        JournalLogger._outcome_cache[key] = (sig, summary)
        JournalLogger._evict_outcome_cache()
        return summary

    @staticmethod
    def _evict_outcome_cache() -> None:
        """Drop least-recently-used summaries until both ceilings hold.

        Eviction only costs a re-parse of that file on its next read; it never
        changes an accounting result. The newest entry is always retained, so a
        single journal larger than the row ceiling degrades to no caching for
        that file rather than to an empty cache.
        """
        cache = JournalLogger._outcome_cache
        while len(cache) > _MAX_OUTCOME_CACHE_FILES:
            cache.pop(next(iter(cache)), None)
        rows = sum(
            len(summary["account"]) + len(summary["performance"])
            for _sig, summary in cache.values()
        )
        while rows > _MAX_OUTCOME_CACHE_ROWS and len(cache) > 1:
            _key, (_sig, summary) = next(iter(cache.items()))
            rows -= len(summary["account"]) + len(summary["performance"])
            cache.pop(_key, None)

    def _compute_daily_state(
        self, entries: List[dict], for_date: Optional[date]
    ) -> DailyState:
        trade_count = 0
        session_trade_counts: dict = {}
        last_outcomes: List[str] = []  # WIN, LOSS, or BREAKEVEN in order
        has_open_position = False
        realized_pnl = 0.0
        last_loss_at = None
        open_trade_session = None  # session of the currently-open trade, for CANCELLED reversal

        for entry in entries:
            entry_type = entry.get("type")

            if entry_type == "OUTCOME":
                outcome_data = entry.get("outcome", {})
                result = outcome_data.get("result")
                if result in ("WIN", "LOSS", "BREAKEVEN"):
                    last_outcomes.append(result)
                    realized_pnl += float(outcome_data.get("pnl_dollars") or 0.0)
                    if result == "LOSS":
                        last_loss_at = _parse_ts(entry.get("ts"))
                    has_open_position = False
                elif result == "CANCELLED":
                    # Naked-flatten / phantom-reconcile / manual cancel: the trade
                    # never held a position, so REVERSE its trade-count increment.
                    # A failed attempt must NOT consume the daily/per-session trade
                    # limit (that locks the session into "doing nothing"). P&L,
                    # win/loss streak, and win rate are untouched.
                    #
                    # Reverse ONLY when there is an open counted position this cancel
                    # actually closes (has_open_position). Under the confirmed-
                    # execution model (2026-07-10) a trade intent that never became a
                    # confirmed TRADE is logged as decision="TRADE_INTENT" and is never
                    # counted; its later CANCELLED must therefore NOT decrement a PRIOR
                    # filled trade's count. Without this guard, TRADE→WIN→(intent)→
                    # CANCELLED would wrongly report trade_count=0 and re-open the daily
                    # budget. Legacy TRADE→CANCELLED sequences and reconciler clears of
                    # legacy phantom-open TRADE rows still reverse correctly, because the
                    # position is open when its CANCELLED is read.
                    if has_open_position:
                        if trade_count > 0:
                            trade_count -= 1
                        if open_trade_session and session_trade_counts.get(open_trade_session, 0) > 0:
                            session_trade_counts[open_trade_session] -= 1
                        open_trade_session = None
                    has_open_position = False
                continue

            decision = entry.get("decision")
            risk_check = entry.get("risk_check") or {}
            risk_approved = risk_check.get("result") == "APPROVED"
            outcome = entry.get("outcome") or {}
            outcome_result = outcome.get("result")

            if decision == "TRADE" and risk_approved:
                session = entry.get("session") or ""
                if outcome_result == "CANCELLED":
                    # Inline-cancelled attempt: never held a position -> do NOT
                    # count it toward the daily/per-session trade limit.
                    has_open_position = False
                    open_trade_session = None
                else:
                    trade_count += 1
                    if session:
                        session_trade_counts[session] = session_trade_counts.get(session, 0) + 1
                    if outcome_result in ("WIN", "LOSS", "BREAKEVEN"):
                        last_outcomes.append(outcome_result)
                        if outcome_result == "LOSS":
                            last_loss_at = _parse_ts(entry.get("ts"))
                        has_open_position = False
                        open_trade_session = None
                    else:
                        has_open_position = True
                        open_trade_session = session

        # Count trailing consecutive losses and wins from outcome history
        consecutive_losses = 0
        for r in reversed(last_outcomes):
            if r == "LOSS":
                consecutive_losses += 1
            else:
                break

        consecutive_wins = 0
        for r in reversed(last_outcomes):
            if r == "WIN":  # BREAKEVEN resets a loss streak but does not count toward win streak
                consecutive_wins += 1
            else:
                break

        # Derive session-start P&L and time from the first approved trade per session.
        # Used by the early-session loss floor check in RiskEngine.
        session_start_pnl: dict = {}
        session_start_time: dict = {}
        running_pnl = 0.0
        for entry in entries:
            entry_type = entry.get("type")
            rc = entry.get("risk_check") or {}
            # Record session-start P&L snapshot on first approved TRADE of that session
            if entry.get("decision") == "TRADE" and rc.get("result") == "APPROVED":
                session = entry.get("session") or ""
                if session and session not in session_start_pnl:
                    session_start_pnl[session] = running_pnl
                    ts = _parse_ts(entry.get("ts"))
                    if ts:
                        session_start_time[session] = ts
            # Accumulate P&L — OUTCOME entries are standalone records (type="OUTCOME");
            # old-format TRADE entries may embed outcome inline (legacy).
            if entry_type == "OUTCOME":
                pnl = (entry.get("outcome") or {}).get("pnl_dollars")
                if isinstance(pnl, (int, float)):
                    running_pnl += float(pnl)
            elif entry_type != "OUTCOME":
                # Legacy embedded outcome in TRADE entry (old journal format)
                pnl = (entry.get("outcome") or {}).get("pnl_dollars")
                if isinstance(pnl, (int, float)):
                    running_pnl += float(pnl)

        daily_state = DailyState(
            trade_count=trade_count,
            consecutive_losses=consecutive_losses,
            has_open_position=has_open_position,
            date=(for_date or date.today()).isoformat(),
            session_trade_counts=session_trade_counts,
            account_balance=None,
            realized_pnl_dollars=round(realized_pnl, 2),
            last_loss_at=last_loss_at,
            consecutive_wins=consecutive_wins,
            session_start_pnl=session_start_pnl,
            session_start_time=session_start_time,
        )
        for entry in entries:
            strategy_state = entry.get("strategy_state") or {}
            four_hr_state = strategy_state.get("strat_4hr_retrigger")
            if isinstance(four_hr_state, dict):
                daily_state.four_hr_retrigger_state = dict(four_hr_state)
            strat_212_122_state = strategy_state.get("strat_212_122")
            if isinstance(strat_212_122_state, dict):
                daily_state.strat_212_122_state = dict(strat_212_122_state)
        for entry in entries:
            decision = entry.get("decision")
            risk_check = entry.get("risk_check") or {}
            if decision == "TRADE" and risk_check.get("result") == "APPROVED":
                self._apply_orb_break_state(entry, daily_state=daily_state)
        return daily_state

    @staticmethod
    def _apply_orb_break_state(entry: dict, daily_state: Optional[DailyState]) -> None:
        if daily_state is None:
            return
        instrument = entry.get("instrument")
        if not instrument:
            return
        setup = entry.get("setup") or {}
        context = entry.get("context") or {}
        orb = context.get("orb") or {}
        orb_status = orb.get("status")
        strategy = setup.get("strategy")

        if orb_status == "above":
            daily_state.orb_break_long_played[instrument] = True
        elif orb_status == "below":
            daily_state.orb_break_short_played[instrument] = True
        elif strategy == "orb_reclaim":
            daily_state.orb_break_long_played[instrument] = False
        elif strategy == "orb_rejection":
            daily_state.orb_break_short_played[instrument] = False

    def get_open_position(self, for_date: Optional[date] = None) -> Optional[dict]:
        """
        Return the setup dict of the last TRADE/APPROVED journal entry that has
        no following OUTCOME entry (i.e., the position is still open).

        Returns None if no open position exists for the day.
        The returned dict has keys: instrument, direction, entry, stop, target.
        """
        path = self._journal_path(for_date)
        if not path.exists():
            return None

        entries = self._read_entries(path)
        last_open: Optional[dict] = None

        for entry in entries:
            entry_type = entry.get("type")
            if entry_type == "OUTCOME":
                # Any OUTCOME (WIN/LOSS/BREAKEVEN/CANCELLED) closes the tracked position.
                last_open = None
                continue

            if entry_type == "ORDER_IDS":
                # Attach the broker OSO order ids to the currently-open position so
                # a restarted broker can restore order-id exit attribution. Only a
                # dict is attached — a corrupt/typed payload (e.g. a string) is
                # dropped so resolve_position degrades to price-matching, never
                # stalls on a non-dict ids.get().
                order_ids = entry.get("order_ids")
                if (last_open is not None and isinstance(order_ids, dict)
                        and entry.get("instrument") == last_open.get("instrument")):
                    last_open["order_ids"] = order_ids
                    if entry.get("stop") is not None:
                        last_open["stop"] = entry.get("stop")
                    if entry.get("exit_mode"):
                        last_open["exit_mode"] = entry.get("exit_mode")
                        if entry.get("exit_mode") == "runner_live":
                            last_open["target"] = None
                continue

            decision = entry.get("decision")
            risk_check = entry.get("risk_check") or {}
            if decision == "TRADE" and risk_check.get("result") == "APPROVED":
                outcome = entry.get("outcome") or {}
                if outcome.get("result") not in ("WIN", "LOSS", "BREAKEVEN"):
                    # Approved trade with no resolved outcome — position is open.
                    setup = entry.get("setup") or {}
                    context = entry.get("context") or {}
                    last_open = {
                        "instrument": entry.get("instrument"),
                        "session": entry.get("session"),
                        "direction": setup.get("direction"),
                        "entry": setup.get("entry"),
                        "stop": setup.get("stop"),
                        "target": setup.get("target"),
                        "contracts": setup.get("contracts", 1),
                        "strategy": setup.get("strategy"),
                        "paper_order_id": entry.get("paper_order_id"),
                        "client_order_id": entry.get("client_order_id"),
                        "mnq_orb_reclaim_proof_audit": entry.get(
                            "mnq_orb_reclaim_proof_audit"
                        ),
                        "mnq_orb_breakout_proof_audit": entry.get(
                            "mnq_orb_breakout_proof_audit"
                        ),
                        "mnq_orb_breakout_inverse_audit": entry.get(
                            "mnq_orb_breakout_inverse_audit"
                        ),
                        "mnq_vwap_hold_proof_audit": entry.get(
                            "mnq_vwap_hold_proof_audit"
                        ),
                        "direction_role": setup.get("direction_role"),
                        "ts": entry.get("ts"),  # processing-time age/stale reference
                        # Decision time is after the bar close, so runner history
                        # needs the originating bar timestamp as a separate bound.
                        "bar_ts": context.get("timestamp") or entry.get("ts"),
                    }
                else:
                    last_open = None

        return last_open

    def get_account_balance(
        self,
        starting_balance: float,
        through_date: Optional[date] = None,
    ) -> float:
        """Reconstruct account balance by summing all journaled realized P&L."""
        balance = float(starting_balance)
        paths = sorted(self.log_dir.glob("journal_*.jsonl"))
        if through_date is not None:
            cutoff_name = f"journal_{through_date.isoformat()}.jsonl"
            paths = [path for path in paths if path.name <= cutoff_name]

        for path in paths:
            for _, pnl in self._read_outcome_summary(path)["account"]:
                balance += pnl
        return round(balance, 2)


    def get_account_peak_balance(
        self,
        starting_balance: float,
        through_date: Optional[date] = None,
    ) -> float:
        """Reconstruct the highest account balance reached after journaled outcomes."""
        balance = float(starting_balance)
        peak = balance
        paths = sorted(self.log_dir.glob("journal_*.jsonl"))
        if through_date is not None:
            cutoff_name = f"journal_{through_date.isoformat()}.jsonl"
            paths = [path for path in paths if path.name <= cutoff_name]

        for path in paths:
            for _, pnl in self._read_outcome_summary(path)["account"]:
                balance += pnl
                peak = max(peak, balance)
        return round(peak, 2)

    def get_account_state_since(
        self,
        starting_balance: float,
        since: datetime,
        through_date: Optional[date] = None,
    ) -> tuple[float, float]:
        """Return balance and peak using only resolved outcomes in an epoch."""
        balance = peak = float(starting_balance)
        boundary = _aware_utc(since)
        paths = sorted(self.log_dir.glob("journal_*.jsonl"))
        if through_date is not None:
            cutoff_name = f"journal_{through_date.isoformat()}.jsonl"
            paths = [path for path in paths if path.name <= cutoff_name]

        for path in paths:
            for raw_ts, pnl in self._read_outcome_summary(path)["account"]:
                entry_ts = _parse_ts(raw_ts)
                if entry_ts is None or _aware_utc(entry_ts) < boundary:
                    continue
                balance += pnl
                peak = max(peak, balance)
        return round(balance, 2), round(peak, 2)

    def get_performance_stats(self, starting_balance: float) -> dict:
        """
        All-time performance statistics derived from resolved OUTCOME entries
        across every journal file.  Handles both the standalone OUTCOME entry
        format (current) and the older inline outcome-on-TRADE format.
        """
        paths = sorted(self.log_dir.glob("journal_*.jsonl"))

        wins: list[float] = []      # pnl_dollars for each WIN
        losses: list[float] = []    # abs(pnl_dollars) for each LOSS
        daily_pnl: dict[str, float] = {}

        balance = float(starting_balance)
        peak = balance
        max_drawdown = 0.0

        for path in paths:
            day_str = path.stem[len("journal_"):]
            day_pnl = 0.0

            for result, pnl in self._read_outcome_summary(path)["performance"]:
                day_pnl += pnl
                balance += pnl
                if balance > peak:
                    peak = balance
                dd = peak - balance
                if dd > max_drawdown:
                    max_drawdown = dd
                if result == "WIN" and pnl > 0:
                    wins.append(pnl)
                elif result == "LOSS":
                    losses.append(abs(pnl))

            if day_pnl:
                daily_pnl[day_str] = round(day_pnl, 2)

        gross_win = sum(wins)
        gross_loss = sum(losses)
        return {
            "total_trades": len(wins) + len(losses),
            "wins": len(wins),
            "losses": len(losses),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "avg_win": round(gross_win / len(wins), 2) if wins else None,
            "avg_loss": round(gross_loss / len(losses), 2) if losses else None,
            "largest_win": round(max(wins), 2) if wins else None,
            "largest_loss": round(max(losses), 2) if losses else None,
            "best_day": round(max(daily_pnl.values()), 2) if daily_pnl else None,
            "worst_day": round(min(daily_pnl.values()), 2) if daily_pnl else None,
            "max_drawdown": round(max_drawdown, 2),
        }

    def get_summary(self, for_date: Optional[date] = None) -> dict:
        """Return a human-readable summary of today's trading activity."""
        path = self._journal_path(for_date)
        if not path.exists():
            return {"message": "No journal for this date.", "trades": 0, "no_trades": 0}

        entries = self._read_entries(path)
        trades = sum(
            1 for e in entries
            if e.get("decision") == "TRADE"
            and (e.get("risk_check") or {}).get("result") == "APPROVED"
        )
        no_trades = sum(1 for e in entries if e.get("decision") == "NO_TRADE")
        wins = sum(
            1 for e in entries
            if (e.get("outcome") or {}).get("result") == "WIN"
        )
        losses = sum(
            1 for e in entries
            if (e.get("outcome") or {}).get("result") == "LOSS"
        )

        return {
            "date": (for_date or date.today()).isoformat(),
            "trades": trades,
            "no_trades": no_trades,
            "wins": wins,
            "losses": losses,
            "journal_path": str(path),
        }

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize journal file access across concurrent webhook requests."""
        lock_path = self.log_dir / ".journal.lock"
        with open(lock_path, "a") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)


def _parse_ts(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None
