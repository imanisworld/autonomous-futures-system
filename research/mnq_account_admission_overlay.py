"""Research-only account-admission overlay for an already-built MNQ fill stream.

This module does not replace ``replay_portfolio`` and does not edit the pinned
#915 or prereg #929 files. Capacity rules stay those of the frozen replay.
The daily-loss day key follows the live journal, not the CME observation day.

Live ``process_alert`` is called without ``for_date``. The runner sets
``today = date.today()`` and the $150 gate reads
``journal.get_daily_state(today).realized_pnl_dollars``. A resolved position
is logged with ``for_date=open_position_date``, the calendar day of the open,
so a later calendar day does not see that outcome in its own journal file.

``date.today()`` is the process-local calendar date. This repo does not set
``TZ`` on futures-bot. Historical bars have no process wall clock, so the
replay stand-in is the UTC calendar date of the event timestamp. That is not
the CME 18:00 ET observation day. Confirm the box timezone before any scored
run; until then this is the journal-day rule, not a claim that the host zone
was re-read.

Drawdown stays ``DRAWDOWN_GATE_NOT_EVALUATED`` unless the caller supplies a
provenance-backed starting balance and starting peak. Those two numbers then
move only when an accepted fill resolves. An external balance snapshot is not
accepted: once this overlay skips or admits a different trade, that snapshot
is no longer the counterfactual account.

No execution authority. No scored portfolio result.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Iterable, Optional

from research.mnq_combined_portfolio_audit import (
    MAX_FILLS_PER_DAY,
    TIE_PRIORITY,
    PortfolioEvent,
    parse_ts,
)

# Current production value at one contract. Copied so a later yaml edit cannot
# silently rescore this overlay. RiskEngine._check_daily_loss_limit rejects a
# new entry when the journal day's realized P&L is at or below this loss.
MAX_DAILY_LOSS_DOLLARS = 150.0
MAX_DRAWDOWN_FRACTION = 0.30

SKIPPED_DAILY_LOSS = "SKIPPED_DAILY_LOSS"
SKIPPED_DRAWDOWN_FLOOR = "SKIPPED_DRAWDOWN_FLOOR"
DRAWDOWN_GATE_NOT_EVALUATED = "DRAWDOWN_GATE_NOT_EVALUATED"
DRAWDOWN_NOT_APPLIED = "DRAWDOWN_NOT_APPLIED"
DRAWDOWN_WITHIN_FLOOR = "WITHIN_FLOOR"
DRAWDOWN_AT_OR_BEYOND_FLOOR = "AT_OR_BEYOND_FLOOR"

_TERMINAL_RESULTS = {"WIN", "LOSS", "BREAKEVEN"}
_PRIORITY = {name: i for i, name in enumerate(TIE_PRIORITY)}


def journal_day(ts: datetime | str) -> str:
    """UTC calendar date stand-in for live ``date.today()``.

    Not ``observation_day``. The CME session rolls at 18:00 ET; this does not.
    """

    return parse_ts(ts).astimezone(timezone.utc).date().isoformat()


@dataclass(frozen=True)
class AccountSeed:
    """Provenance-backed start of a counterfactual balance path.

    ``source`` names where the two numbers came from. This type has no default
    balance and does not read the position-sizing ladder.
    """

    starting_balance: float
    starting_peak: float
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("account seed requires a non-empty provenance source")
        if not isfinite(float(self.starting_balance)) or not isfinite(float(self.starting_peak)):
            raise ValueError("account seed balance and peak must be finite")
        if float(self.starting_peak) <= 0:
            raise ValueError("account seed peak must be > 0")
        if float(self.starting_peak) < float(self.starting_balance):
            raise ValueError("account seed peak must be >= starting balance")


@dataclass(frozen=True)
class AdmissionDecision:
    source_id: str
    family: str
    eligible_fill_ts: str
    observation_day: str
    disposition: str
    drawdown_gate: str
    journal_day: str
    blocker_source_id: Optional[str] = None
    blocker_family: Optional[str] = None


@dataclass
class AdmissionReplay:
    fills: list[PortfolioEvent]
    decisions: list[AdmissionDecision]
    exact_timestamp_collisions: int
    drawdown_gate: str


def _event_sort_key(event: PortfolioEvent):
    return (
        parse_ts(event.eligible_fill_ts),
        _PRIORITY[event.family],
        event.source_id,
    )


@dataclass
class _PendingRealization:
    exit_at: datetime
    journal_day: str
    net_pnl: float
    applied: bool = False


@dataclass
class _AccountPath:
    balance: float
    peak: float


def _realize_strictly_before(
    pending: list[_PendingRealization],
    now: datetime,
    realized_by_day: dict[str, float],
    path: Optional[_AccountPath],
) -> None:
    for item in pending:
        if item.applied or item.exit_at >= now:
            continue
        realized_by_day[item.journal_day] += item.net_pnl
        if path is not None:
            path.balance += item.net_pnl
            path.peak = max(path.peak, path.balance)
        item.applied = True


def apply_account_admission(
    events: Iterable[PortfolioEvent],
    *,
    account_seed: Optional[AccountSeed] = None,
) -> AdmissionReplay:
    """Admit fillable events under the frozen capacity rules plus daily loss.

    Omit ``account_seed`` and the drawdown gate is
    ``DRAWDOWN_GATE_NOT_EVALUATED``. Pass a seed and balance/peak then move
    only with accepted, resolved fills.
    """

    if account_seed is not None and not isinstance(account_seed, AccountSeed):
        raise TypeError("account_seed must be an AccountSeed or omitted")

    rows = list(events)
    rows.sort(key=_event_sort_key)
    summary_gate = (
        DRAWDOWN_GATE_NOT_EVALUATED if account_seed is None else "EVALUATED"
    )
    path = (
        None
        if account_seed is None
        else _AccountPath(
            balance=float(account_seed.starting_balance),
            peak=float(account_seed.starting_peak),
        )
    )

    ts_counts: dict[str, int] = defaultdict(int)
    for event in rows:
        ts_counts[event.eligible_fill_ts] += 1
    exact_collisions = sum(count - 1 for count in ts_counts.values() if count > 1)

    fills: list[PortfolioEvent] = []
    decisions: list[AdmissionDecision] = []
    active: Optional[PortfolioEvent] = None
    fills_by_day: dict[str, int] = defaultdict(int)
    realized_by_day: dict[str, float] = defaultdict(float)
    pending: list[_PendingRealization] = []

    for event in rows:
        now = parse_ts(event.eligible_fill_ts)
        _realize_strictly_before(pending, now, realized_by_day, path)
        if active is not None and active.exit_ts is not None:
            if parse_ts(active.exit_ts) < now:
                active = None

        if active is not None:
            decisions.append(
                _decision(
                    event,
                    "SKIPPED_BUSY_PORTFOLIO",
                    DRAWDOWN_NOT_APPLIED if path is not None else summary_gate,
                    blocker_source_id=active.source_id,
                    blocker_family=active.family,
                )
            )
            continue

        if fills_by_day[event.observation_day] >= MAX_FILLS_PER_DAY:
            decisions.append(
                _decision(
                    event,
                    "SKIPPED_MAX_TRADES_PORTFOLIO",
                    DRAWDOWN_NOT_APPLIED if path is not None else summary_gate,
                )
            )
            continue

        drawdown_gate = summary_gate
        if path is not None:
            fraction = (path.peak - path.balance) / path.peak
            if fraction >= MAX_DRAWDOWN_FRACTION:
                decisions.append(
                    _decision(event, SKIPPED_DRAWDOWN_FLOOR, DRAWDOWN_AT_OR_BEYOND_FLOOR)
                )
                continue
            drawdown_gate = DRAWDOWN_WITHIN_FLOOR

        if realized_by_day[journal_day(now)] <= -MAX_DAILY_LOSS_DOLLARS:
            decisions.append(_decision(event, SKIPPED_DAILY_LOSS, drawdown_gate))
            continue

        fills.append(event)
        fills_by_day[event.observation_day] += 1
        decisions.append(_decision(event, "FILLED", drawdown_gate))
        active = event
        if event.exit_ts is not None and event.result in _TERMINAL_RESULTS:
            pending.append(
                _PendingRealization(
                    exit_at=parse_ts(event.exit_ts),
                    journal_day=journal_day(event.eligible_fill_ts),
                    net_pnl=float(event.net_pnl),
                )
            )

    return AdmissionReplay(
        fills=fills,
        decisions=decisions,
        exact_timestamp_collisions=exact_collisions,
        drawdown_gate=summary_gate,
    )


def _decision(
    event: PortfolioEvent,
    disposition: str,
    drawdown_gate: str,
    *,
    blocker_source_id: Optional[str] = None,
    blocker_family: Optional[str] = None,
) -> AdmissionDecision:
    return AdmissionDecision(
        source_id=event.source_id,
        family=event.family,
        eligible_fill_ts=event.eligible_fill_ts,
        observation_day=event.observation_day,
        disposition=disposition,
        drawdown_gate=drawdown_gate,
        journal_day=journal_day(event.eligible_fill_ts),
        blocker_source_id=blocker_source_id,
        blocker_family=blocker_family,
    )
