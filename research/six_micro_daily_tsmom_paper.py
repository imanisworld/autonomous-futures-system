"""Paper ledger for the six-micro daily time-series momentum study.

This module records hypothetical round-turns. It does not import a broker,
submit a demo order, or write the single-look evidence directory. Demo
routing is not implemented and is not authorized.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from config.futures_contracts import tick_size, tick_value
from sources.polygon_client import PolygonError, front_contract

TRIAL_ID = "T-2026-10-10-prereg-six-micro-daily-tsmom-forward-2026-10-10-01"
ROOTS = ("MNQ", "MES", "M2K", "MGC", "MCL", "MBT")
SCHEDULED_ROOTS = ("MNQ", "MES", "M2K")
LOOKBACK_SESSIONS = 60
MAX_HOLD_SESSIONS = 20
COMMISSION_PER_SIDE = 1.48
SLIPPAGE_TICKS = 2
FIRST_ELIGIBLE = date(2026, 10, 12)
PRE_REGISTRATION_THROUGH = date(2026, 10, 9)
MNQ_SEAL_START = date(2026, 6, 29)
MNQ_SEAL_END = date(2027, 1, 29)
LOOK_DEADLINE = date(2028, 10, 12)

FROZEN_PARAMETERS = {
    "trial_id": TRIAL_ID,
    "roots": list(ROOTS),
    "lookback_sessions": LOOKBACK_SESSIONS,
    "max_hold_sessions": MAX_HOLD_SESSIONS,
    "commission_per_side": COMMISSION_PER_SIDE,
    "slippage_ticks": SLIPPAGE_TICKS,
    "first_eligible": FIRST_ELIGIBLE.isoformat(),
    "mnq_seal_start": MNQ_SEAL_START.isoformat(),
    "mnq_seal_end": MNQ_SEAL_END.isoformat(),
    "look_deadline": LOOK_DEADLINE.isoformat(),
}


def frozen_rule_sha256() -> str:
    payload = json.dumps(FROZEN_PARAMETERS, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reject_rule_edit(_proposed: dict) -> None:
    """The seal has no edit path. MNQ seal bars cannot retune this rule."""
    raise PermissionError(
        "six-micro daily tsmom rules are frozen; "
        f"MNQ {MNQ_SEAL_START.isoformat()}..{MNQ_SEAL_END.isoformat()} "
        "is scoring input only"
    )


def close_prices(closes: list) -> list[float]:
    prices: list[float] = []
    for item in closes:
        if isinstance(item, dict):
            prices.append(float(item["close"]))
        else:
            prices.append(float(item))
    return prices


def signal_from_closes(closes: list) -> int:
    values = close_prices(closes)
    if len(values) < LOOKBACK_SESSIONS + 1:
        return 0
    change = values[-1] / values[-1 - LOOKBACK_SESSIONS] - 1.0
    if change > 0:
        return 1
    if change < 0:
        return -1
    return 0


def paper_pnl(root: str, side: int, entry_open: float, exit_open: float) -> float:
    tick = tick_size(root)
    value = tick_value(root)
    slip = SLIPPAGE_TICKS * tick
    if side > 0:
        entry_fill = entry_open + slip
        exit_fill = exit_open - slip
        gross_ticks = (exit_fill - entry_fill) / tick
    else:
        entry_fill = entry_open - slip
        exit_fill = exit_open + slip
        gross_ticks = (entry_fill - exit_fill) / tick
    return gross_ticks * value - 2.0 * COMMISSION_PER_SIDE


@dataclass(frozen=True)
class SessionPrint:
    session: date
    root: str
    contract: str
    rth_open: float
    session_close: float
    roll_exit_open: Optional[float] = None


class PaperLedger:
    def __init__(self, journal_dir: Path):
        path = Path(journal_dir)
        if "research-evidence" in path.resolve().parts:
            raise ValueError("paper journal is not the single-look evidence artifact")
        path.mkdir(parents=True, exist_ok=True)
        self.journal_path = path / "round_turns.jsonl"
        self.state_path = path / "state.json"
        self.state = self._load()

    def on_session(self, bar: SessionPrint) -> list[dict]:
        if self.state["rule_sha256"] != frozen_rule_sha256():
            raise RuntimeError("paper ledger rule hash does not match the frozen rule")
        root = bar.root.strip().upper()
        if root not in ROOTS:
            raise ValueError(f"{root} is not one of the six frozen micros")
        if not bar.contract.strip():
            raise ValueError("dated contract ticker is required")
        events: list[dict] = []
        closes: list = self.state["closes"].setdefault(root, [])
        if bar.session in self.stored_sessions(root):
            return []
        if root in SCHEDULED_ROOTS and not self._scheduled_front_ok(bar, root):
            self._record_close(closes, bar)
            event = {
                "trial_id": TRIAL_ID,
                "kind": "FRONT_MISMATCH",
                "root": root,
                "session": bar.session.isoformat(),
                "contract": bar.contract,
            }
            events.append(event)
            self._save(events)
            return events
        if bar.session <= PRE_REGISTRATION_THROUGH:
            self._record_close(closes, bar)
            event = {
                "trial_id": TRIAL_ID,
                "kind": "PRE_REGISTRATION",
                "root": root,
                "session": bar.session.isoformat(),
                "contract": bar.contract,
                "counts_toward_forty": False,
            }
            events.append(event)
            self._save(events)
            return events
        position = self.state["positions"].get(root)
        exited_today = False
        if isinstance(position, dict) and (
            position["contract"] != bar.contract or position.get("roll_price_missing")
        ):
            if bar.roll_exit_open is None and not position.get("roll_price_missing"):
                position["roll_price_missing"] = True
                self.state["positions"][root] = position
                event = {
                    "trial_id": TRIAL_ID,
                    "kind": "ROLL_PRICE_MISSING",
                    "root": root,
                    "session": bar.session.isoformat(),
                    "contract": position["contract"],
                }
                events.append(event)
                self._record_close(closes, bar)
                self._save(events)
                return events
            if bar.roll_exit_open is None:
                exit_open = float(bar.rth_open)
                exit_price_source = "next_session_open"
            else:
                exit_open = float(bar.roll_exit_open)
                exit_price_source = "old_contract_open"
            events.append(
                self._exit(root, position, bar.session, exit_open, exit_price_source)
            )
            position = None
            exited_today = True
        elif isinstance(position, dict):
            sessions_after = int(position["sessions_after"]) + 1
            signal = signal_from_closes(closes)
            if sessions_after >= MAX_HOLD_SESSIONS or signal != int(position["side"]):
                events.append(self._exit(root, position, bar.session, bar.rth_open))
                position = None
                exited_today = True
            else:
                position["sessions_after"] = sessions_after
                self.state["positions"][root] = position

        signal = signal_from_closes(closes)
        if (
            position is None
            and not exited_today
            and bar.session >= FIRST_ELIGIBLE
            and signal != 0
        ):
            side = signal
            slip = SLIPPAGE_TICKS * tick_size(root)
            entry_fill = bar.rth_open + slip if side > 0 else bar.rth_open - slip
            self.state["positions"][root] = {
                "side": side,
                "entry_fill": entry_fill,
                "entry_open": bar.rth_open,
                "entry_session": bar.session.isoformat(),
                "contract": bar.contract,
                "sessions_after": 0,
            }
            events.append({
                "trial_id": TRIAL_ID,
                "kind": "OPEN",
                "root": root,
                "session": bar.session.isoformat(),
                "contract": bar.contract,
                "side": side,
            })

        self._record_close(closes, bar)
        self._save(events)
        return events

    def _record_close(self, closes: list, bar: SessionPrint) -> None:
        pre_registration = bar.session <= PRE_REGISTRATION_THROUGH
        closes.append({
            "session": bar.session.isoformat(),
            "close": float(bar.session_close),
            "pre_registration": pre_registration,
            "counts_toward_forty": not pre_registration,
        })

    def stored_sessions(self, root: str) -> set[date]:
        found: set[date] = set()
        for item in self.state["closes"].get(root, []):
            if isinstance(item, dict) and item.get("session"):
                found.add(date.fromisoformat(str(item["session"])))
        return found

    def bare_close_count(self, root: str) -> int:
        return sum(
            1 for item in self.state["closes"].get(root, [])
            if not isinstance(item, dict)
        )

    def mark_preregistration(self, root: str, prints: list[SessionPrint]) -> None:
        """Attach dates to the backfill and keep those days out of the 40."""
        bare = [
            float(item) for item in self.state["closes"].get(root, [])
            if not isinstance(item, dict)
        ]
        if not bare:
            return
        prereg = [item for item in prints if item.session <= PRE_REGISTRATION_THROUGH]
        fetched = [float(item.session_close) for item in prereg]
        if len(bare) != len(fetched) or any(
            abs(old - new) > 1e-4 for old, new in zip(bare, fetched)
        ):
            raise RuntimeError(
                f"{root} backfill has {len(bare)} prices and the refetch has {len(fetched)}"
            )
        self.state["closes"][root] = [
            {
                "session": item.session.isoformat(),
                "close": float(item.session_close),
                "pre_registration": True,
                "counts_toward_forty": False,
            }
            for item in prereg
        ]
        self._save()

    @property
    def round_turns(self) -> int:
        return int(self.state["round_turns"])

    @property
    def scoring_round_turns(self) -> int:
        return self.round_turns

    def _scheduled_front_ok(self, bar: SessionPrint, root: str) -> bool:
        try:
            expected = front_contract(root, bar.session)
        except PolygonError:
            return False
        return bar.contract.strip().upper() == expected

    def _exit(
        self,
        root: str,
        position: dict,
        session: date,
        exit_open: float,
        exit_price_source: str | None = None,
    ) -> dict:
        side = int(position["side"])
        pnl = paper_pnl(root, side, float(position["entry_open"]), exit_open)
        self.state["positions"][root] = None
        entry_session = date.fromisoformat(str(position["entry_session"]))
        counts = session >= FIRST_ELIGIBLE and entry_session >= FIRST_ELIGIBLE
        if counts:
            self.state["round_turns"] = int(self.state["round_turns"]) + 1
        event = {
            "trial_id": TRIAL_ID,
            "kind": "ROUND_TURN",
            "root": root,
            "session": session.isoformat(),
            "contract": position["contract"],
            "side": side,
            "pnl": pnl,
            "pre_registration": not counts,
            "counts_toward_forty": counts,
            "round_turns": self.state["round_turns"],
        }
        if exit_price_source:
            event["exit_price_source"] = exit_price_source
        self._append(event)
        return event

    def _append(self, event: dict) -> None:
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

    def _save(self, events: Optional[list[dict]] = None) -> None:
        if events:
            for event in events:
                if event.get("kind") != "ROUND_TURN":
                    self._append(event)
        self.state_path.write_text(
            json.dumps(self.state, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def _load(self) -> dict:
        if not self.state_path.exists():
            return {
                "trial_id": TRIAL_ID,
                "rule_sha256": frozen_rule_sha256(),
                "closes": {},
                "positions": {},
                "round_turns": 0,
            }
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        if state.get("rule_sha256") != frozen_rule_sha256():
            raise RuntimeError("stored paper ledger was written under a different rule")
        return state
