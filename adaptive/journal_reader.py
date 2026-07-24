"""
adaptive/journal_reader.py

Multi-day journal reader that converts raw JSONL entries into structured
TradeRecord objects for use by the committee agents.

Handles both outcome formats:
  - Separate OUTCOME-type entries  (normal live flow)
  - Inline outcome field in DECISION entry  (replay / legacy)
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .models import DecisionRecord, TradeRecord


class JournalReader:
    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir)

    # ── Public API ─────────────────────────────────────────────────────────────

    def read_trades(self, days: int = 30) -> list[TradeRecord]:
        """
        Return approved TRADE records from the last `days` calendar days,
        with outcomes resolved where available.
        """
        records: list[TradeRecord] = []
        today = date.today()
        for offset in range(days):
            day = today - timedelta(days=offset)
            records.extend(self._trades_for_day(day))
        return records


    def read_decisions(self, days: int = 30) -> list[DecisionRecord]:
        """Return every journaled decision from the last `days` calendar days."""
        records: list[DecisionRecord] = []
        today = date.today()
        for offset in range(days):
            day = today - timedelta(days=offset)
            records.extend(self._decisions_for_day(day))
        return records

    def latest_entry_age_seconds(self) -> Optional[float]:
        """Seconds since the most recent journal entry across all files."""
        from datetime import datetime, timezone

        latest_ts: Optional[datetime] = None
        for path in sorted(self.log_dir.glob("journal_*.jsonl"), reverse=True)[:3]:
            for raw in self._read_raw(path):
                ts_str = raw.get("ts")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if latest_ts is None or ts > latest_ts:
                        latest_ts = ts
                except ValueError:
                    pass

        if latest_ts is None:
            return None
        now = datetime.now(timezone.utc)
        return (now - latest_ts).total_seconds()

    def all_journal_paths(self) -> list[Path]:
        return sorted(self.log_dir.glob("journal_*.jsonl"))

    # ── Internal ───────────────────────────────────────────────────────────────


    def _decisions_for_day(self, day: date) -> list[DecisionRecord]:
        path = self.log_dir / f"journal_{day.isoformat()}.jsonl"
        if not path.exists():
            return []

        records: list[DecisionRecord] = []
        for entry in self._read_raw(path):
            decision = entry.get("decision")
            if not decision:
                continue

            setup = entry.get("setup") or {}
            context = entry.get("context") or {}
            trend = context.get("trend") or {}
            vwap_ctx = context.get("vwap") or {}
            vol = context.get("volume") or {}
            risk = entry.get("risk_check") or {}
            notes = setup.get("notes") or ""
            failed_gates = entry.get("failed_gates") or []
            if isinstance(failed_gates, str):
                failed_gates = [failed_gates]

            records.append(DecisionRecord(
                date=day.isoformat(),
                ts=entry.get("ts", ""),
                instrument=entry.get("instrument", ""),
                session=entry.get("session", ""),
                decision=decision,
                reason=entry.get("reason"),
                failed_gates=[str(gate) for gate in failed_gates],
                risk_failed_rule=risk.get("failed_rule"),
                strategy=setup.get("strategy", "unknown"),
                direction=setup.get("direction", ""),
                entry=_opt_float(setup.get("entry")),
                stop=_opt_float(setup.get("stop")),
                target=_opt_float(setup.get("target")),
                rr_ratio=_opt_float(setup.get("rr_ratio")),
                trend_strength=trend.get("strength"),
                vwap_value=_opt_float(vwap_ctx.get("value")),
                volume=_opt_int(vol.get("current_bar")),
                market_condition=entry.get("market_condition") or context.get("market_condition"),
                pine_bracket_overridden="Pine bracket override" in notes,
                pine_bracket_ignored="Pine bracket ignored" in notes,
            ))

        return records

    def _trades_for_day(self, day: date) -> list[TradeRecord]:
        path = self.log_dir / f"journal_{day.isoformat()}.jsonl"
        if not path.exists():
            return []

        raw_entries = self._read_raw(path)
        # Pass 1: collect approved decision entries, and index standalone
        # OUTCOME entries by paper_order_id — the stable identity PaperBroker
        # mints once per position (see execution/paper_broker.py) and that
        # webhook/runner.py carries onto both the confirmed TRADE row and its
        # eventual OUTCOME row. Outcomes without an id (e.g. a non-PaperBroker
        # execution path) are never indexed, so they can never be guessed
        # onto an unrelated trade.
        decisions: list[dict] = []   # approved TRADE decisions, in order
        outcomes_by_order_id: dict[str, dict] = {}

        for entry in raw_entries:
            if entry.get("type") == "OUTCOME":
                outcome = entry.get("outcome") or {}
                order_id = outcome.get("paper_order_id")
                if order_id:
                    # First write for a given order id wins; a duplicate
                    # OUTCOME for the same position must not silently
                    # overwrite the original resolution.
                    outcomes_by_order_id.setdefault(order_id, outcome)
                continue
            if (
                entry.get("decision") == "TRADE"
                and (entry.get("risk_check") or {}).get("result") == "APPROVED"
            ):
                decisions.append(entry)

        # Pass 2: pair each decision with its outcome by exact paper_order_id
        # match. No FIFO fallback — an unmatched or identity-less TRADE row is
        # left unresolved/unjoinable rather than paired by position.
        records: list[TradeRecord] = []

        for entry in decisions:
            inline = entry.get("outcome") or {}
            inline_result = inline.get("result")
            unjoinable_legacy = False

            if inline_result in ("WIN", "LOSS", "BREAKEVEN"):
                result = inline_result
                pnl = float(inline.get("pnl_dollars") or 0.0)
            else:
                order_id = entry.get("paper_order_id")
                if order_id:
                    out = outcomes_by_order_id.get(order_id)
                    if out is not None:
                        result = out.get("result")
                        pnl = float(out.get("pnl_dollars") or 0.0) if result else None
                    else:
                        # Real identity, no match yet — genuinely still open.
                        result = None
                        pnl = None
                else:
                    # No stable identity to join on at all (legacy row, or a
                    # non-PaperBroker execution path that never minted one).
                    # Explicitly unjoinable — must not be FIFO-guessed.
                    result = None
                    pnl = None
                    unjoinable_legacy = True

            setup = entry.get("setup") or {}
            context = entry.get("context") or {}
            trend = context.get("trend") or {}
            vwap_ctx = context.get("vwap") or {}
            vol = context.get("volume") or {}
            confluence = entry.get("confluence") or {}
            notes = setup.get("notes") or ""

            records.append(TradeRecord(
                date=day.isoformat(),
                ts=entry.get("ts", ""),
                instrument=entry.get("instrument", ""),
                session=entry.get("session", ""),
                strategy=setup.get("strategy", "unknown"),
                direction=setup.get("direction", ""),
                contracts=int(setup.get("contracts") or 1),
                confluence_grade=confluence.get("grade"),
                entry=_opt_float(setup.get("entry")),
                stop=_opt_float(setup.get("stop")),
                target=_opt_float(setup.get("target")),
                rr_ratio=_opt_float(setup.get("rr_ratio")),
                result=result,
                pnl_dollars=pnl,
                trend_strength=trend.get("strength"),
                vwap_value=_opt_float(vwap_ctx.get("value")),
                volume=_opt_int(vol.get("current_bar")),
                pine_bracket_overridden="Pine bracket override" in notes,
                pine_bracket_ignored="Pine bracket ignored" in notes,
                unjoinable_legacy=unjoinable_legacy,
            ))

        return records

    @staticmethod
    def _read_raw(path: Path) -> list[dict]:
        entries: list[dict] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass
        return entries


def _opt_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _opt_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
