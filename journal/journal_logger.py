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
            },
        }
        self._append(entry, for_date)

    def log_order_ids(
        self,
        instrument: str,
        session: str,
        order_ids: dict,
        for_date: Optional[date] = None,
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
        self._append(record, for_date)

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

    def claim_bar(
        self,
        *,
        instrument: str,
        bar_ts: str,
        for_date: Optional[date] = None,
    ) -> bool:
        """Atomically claim one instrument/bar timestamp before gate evaluation.

        Returns False if this journal already has a claim or decision for the
        same instrument + bar timestamp. This prevents duplicate webhook workers
        from both passing daily/open-position gates for the same TradingView bar.
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
                        return False
            entry = {
                "ts": bar_ts,
                "type": "BAR_CLAIM",
                "instrument": instrument,
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            }
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

    def _read_entries(self, path: Path) -> List[dict]:
        key = str(path)
        try:
            st = path.stat()
        except FileNotFoundError:
            JournalLogger._entries_cache.pop(key, None)
            return []
        sig = (st.st_mtime_ns, st.st_size)
        cached = JournalLogger._entries_cache.get(key)
        if cached is not None and cached[0] == sig:
            # Unchanged since last parse — reuse. Callers treat entries as
            # read-only (they filter/iterate, never mutate in place).
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
        JournalLogger._entries_cache[key] = (sig, entries)
        return entries

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
                    has_open_position = False
                    if trade_count > 0:
                        trade_count -= 1
                    if open_trade_session and session_trade_counts.get(open_trade_session, 0) > 0:
                        session_trade_counts[open_trade_session] -= 1
                    open_trade_session = None
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
            decision = entry.get("decision")
            risk_check = entry.get("risk_check") or {}
            if decision == "TRADE" and risk_check.get("result") == "APPROVED":
                self._apply_orb_break_state(entry, daily_state=daily_state)
        return daily_state

    @staticmethod
    def _apply_orb_break_state(entry: dict, daily_state: Optional[DailyState]) -> None:
        if daily_state is None:
            return
        setup = entry.get("setup") or {}
        context = entry.get("context") or {}
        orb = context.get("orb") or {}
        orb_status = orb.get("status")
        strategy = setup.get("strategy")

        if orb_status == "above":
            daily_state.orb_break_long_played = True
        elif orb_status == "below":
            daily_state.orb_break_short_played = True
        elif strategy in ("orb_reclaim", "strat_4hr_retrigger"):
            daily_state.orb_break_long_played = False
        elif strategy == "orb_rejection":
            daily_state.orb_break_short_played = False

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
                continue

            decision = entry.get("decision")
            risk_check = entry.get("risk_check") or {}
            if decision == "TRADE" and risk_check.get("result") == "APPROVED":
                outcome = entry.get("outcome") or {}
                if outcome.get("result") not in ("WIN", "LOSS", "BREAKEVEN"):
                    # Approved trade with no resolved outcome — position is open.
                    setup = entry.get("setup") or {}
                    last_open = {
                        "instrument": entry.get("instrument"),
                        "direction": setup.get("direction"),
                        "entry": setup.get("entry"),
                        "stop": setup.get("stop"),
                        "target": setup.get("target"),
                        "contracts": setup.get("contracts", 1),
                        "ts": entry.get("ts"),  # opened_at timestamp for stale detection
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
            for entry in self._read_entries(path):
                if entry.get("type") != "OUTCOME":
                    continue
                outcome = entry.get("outcome") or {}
                balance += float(outcome.get("pnl_dollars") or 0.0)
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
            for entry in self._read_entries(path):
                if entry.get("type") != "OUTCOME":
                    continue
                outcome = entry.get("outcome") or {}
                balance += float(outcome.get("pnl_dollars") or 0.0)
                peak = max(peak, balance)
        return round(peak, 2)

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

            for entry in self._read_entries(path):
                pnl: Optional[float] = None
                result: Optional[str] = None

                if entry.get("type") == "OUTCOME":
                    out = entry.get("outcome") or {}
                    result = out.get("result")
                    pnl = float(out.get("pnl_dollars") or 0.0)
                elif entry.get("decision") == "TRADE":
                    risk = entry.get("risk_check") or {}
                    if risk.get("result") == "APPROVED":
                        out = entry.get("outcome") or {}
                        result = out.get("result")
                        if result in ("WIN", "LOSS", "BREAKEVEN"):
                            pnl = float(out.get("pnl_dollars") or 0.0)

                if pnl is not None and result in ("WIN", "LOSS", "BREAKEVEN"):
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
