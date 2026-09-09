"""Read-only bankroll, mark-to-market, drawdown, and swing analysis for Options Paper V1.

This module does not change setup admission, contract selection, stops, targets,
or broker behavior.  It replays the already-recorded ACTIVE ``OPTIONS_PAPER_V1``
rows through cash-only long-option account scenarios so the same evidence can
answer a separate question: how much starting capital was actually needed and
what did the equity/drawdown path look like, including overnight swings.

Default scenario balances are the operator's current evaluation ladder:
$1,500, $2,500, and $5,000.  The $5,000 value is an allocation ceiling for this
study, not an acceptable drawdown.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .contract_marks import ensure_schema
from .storage import ScanStorage

POLICY_ID = "OPTIONS_PAPER_V1"
COUNTERFACTUAL_LANE = "COUNTERFACTUAL"
DEFAULT_STARTING_BALANCES = (1500.0, 2500.0, 5000.0)
DEFAULT_CAPITAL_CEILING = 5000.0
CONTRACT_MULTIPLIER = 100.0
EXCHANGE_TIMEZONE = "America/New_York"
_EPS = 1e-9


@dataclass(frozen=True)
class Mark:
    timestamp: datetime
    bid: float


@dataclass(frozen=True)
class TradeEvidence:
    shadow_id: int
    ticker: str
    status: str
    entry_time: datetime
    exit_time: datetime | None
    entry_ask: float
    entry_bid: float | None
    exit_bid: float | None
    quantity: int
    planned_risk: float
    capital_required: float
    marks: tuple[Mark, ...]

    @property
    def latest_time(self) -> datetime:
        if self.exit_time is not None:
            return self.exit_time
        if self.marks:
            return self.marks[-1].timestamp
        return self.entry_time

    @property
    def is_resolved(self) -> bool:
        return self.status != "OPEN"


@dataclass(frozen=True)
class ReplayEvent:
    timestamp: datetime
    priority: int
    kind: str
    trade_id: int
    bid: float | None = None


@dataclass
class OpenPosition:
    trade: TradeEvidence
    mark_bid: float

    @property
    def market_value(self) -> float:
        return self.mark_bid * CONTRACT_MULTIPLIER * self.trade.quantity


@dataclass(frozen=True)
class ScenarioResult:
    starting_balance: float
    ending_equity: float
    total_return_percent: float
    realized_pnl: float
    unrealized_pnl: float
    lowest_equity: float
    peak_equity: float
    max_drawdown_dollars: float
    max_drawdown_percent: float
    longest_underwater_minutes: float
    trades_considered: int
    trades_funded: int
    trades_blocked_capital: int
    funding_block_rate_percent: float
    max_open_positions: int
    max_capital_deployed: float
    max_capital_deployed_percent_of_start: float
    max_planned_risk_open: float
    max_planned_risk_percent_of_start: float
    overnight_holds: int
    overnight_position_nights: int
    max_overnight_positions: int
    max_overnight_gap_loss_dollars: float
    open_positions_at_end: int
    available_cash_at_end: float

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _num(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _positive(value: Any) -> float | None:
    parsed = _num(value)
    return parsed if parsed is not None and parsed > 0 else None


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _active_v1_rows(storage: ScanStorage) -> list[dict[str, Any]]:
    with storage._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, timestamp, ticker, direction, status,
                   selected_contract_json, outcome_json
            FROM options_shadow_journal
            WHERE selected_contract_json LIKE ?
            ORDER BY timestamp ASC, id ASC
            """,
            (f"%{POLICY_ID}%",),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        selected = json.loads(row["selected_contract_json"] or "{}")
        if selected.get("paper_policy_id") != POLICY_ID:
            continue
        if (
            selected.get("paper_evidence_lane") == COUNTERFACTUAL_LANE
            or selected.get("risk_budget_consumed") is False
        ):
            continue
        result.append(
            {
                "id": int(row["id"]),
                "timestamp": row["timestamp"],
                "ticker": row["ticker"],
                "direction": row["direction"],
                "status": row["status"],
                "selected": selected,
                "outcome": json.loads(row["outcome_json"] or "{}"),
            }
        )
    return result


def _marks_by_shadow(storage: ScanStorage) -> dict[int, list[Mark]]:
    ensure_schema(storage)
    with storage._connect() as conn:
        rows = conn.execute(
            """
            SELECT shadow_id, timestamp, bid
            FROM options_contract_marks
            WHERE error = '' AND bid IS NOT NULL AND bid > 0
            ORDER BY timestamp ASC, id ASC
            """
        ).fetchall()
    grouped: dict[int, list[Mark]] = {}
    for row in rows:
        stamp = _parse_dt(row["timestamp"])
        bid = _positive(row["bid"])
        if stamp is None or bid is None:
            continue
        grouped.setdefault(int(row["shadow_id"]), []).append(Mark(stamp, bid))
    return grouped


def _build_trade(row: dict[str, Any], marks: list[Mark]) -> tuple[TradeEvidence | None, str | None]:
    selected = row["selected"]
    outcome = row["outcome"]
    entry_time = _parse_dt(row["timestamp"])
    entry_ask = _positive(
        selected.get("option_mark")
        or selected.get("entry_quote")
        or selected.get("option_ask")
    )
    quantity_raw = _positive(selected.get("contracts") or selected.get("quantity") or 1)
    planned_risk = _num(selected.get("planned_risk_dollars"))
    if entry_time is None:
        return None, "entry_timestamp_missing"
    if entry_ask is None:
        return None, "entry_ask_missing"
    if quantity_raw is None:
        return None, "quantity_missing"
    quantity = int(quantity_raw)
    if quantity <= 0:
        return None, "quantity_invalid"
    if planned_risk is None or planned_risk < 0:
        return None, "planned_risk_missing"

    status = str(row.get("status") or "OPEN").upper()
    exit_time = _parse_dt(outcome.get("resolved_at"))
    exit_bid = _positive(
        outcome.get("exit_mark")
        or outcome.get("option_bid_at_resolution")
        or outcome.get("exit_premium")
    )
    if status != "OPEN" and exit_time is None and marks:
        exit_time = marks[-1].timestamp
    if status != "OPEN" and exit_bid is None and marks:
        exit_bid = marks[-1].bid
    if status != "OPEN" and (exit_time is None or exit_bid is None):
        return None, "resolved_exit_quote_missing"

    entry_bid = _positive(selected.get("option_bid"))
    if entry_bid is None:
        same_time = [mark.bid for mark in marks if mark.timestamp == entry_time]
        entry_bid = same_time[-1] if same_time else None

    return (
        TradeEvidence(
            shadow_id=int(row["id"]),
            ticker=str(row.get("ticker") or ""),
            status=status,
            entry_time=entry_time,
            exit_time=exit_time,
            entry_ask=entry_ask,
            entry_bid=entry_bid,
            exit_bid=exit_bid,
            quantity=quantity,
            planned_risk=planned_risk,
            capital_required=round(entry_ask * CONTRACT_MULTIPLIER * quantity, 2),
            marks=tuple(marks),
        ),
        None,
    )


def load_trade_evidence(storage: ScanStorage) -> tuple[list[TradeEvidence], dict[str, int]]:
    """Load complete ACTIVE V1 rows and report why incomplete rows were excluded."""
    marks = _marks_by_shadow(storage)
    rows = _active_v1_rows(storage)
    trades: list[TradeEvidence] = []
    issues: dict[str, int] = {}
    for row in rows:
        trade, issue = _build_trade(row, marks.get(int(row["id"]), []))
        if trade is not None:
            trades.append(trade)
        else:
            issues[issue or "unknown"] = issues.get(issue or "unknown", 0) + 1
    trades.sort(key=lambda trade: (trade.entry_time, trade.shadow_id))
    return trades, issues


def _events(trades: Iterable[TradeEvidence]) -> list[ReplayEvent]:
    events: list[ReplayEvent] = []
    for trade in trades:
        # Entry before exit at an identical timestamp is deliberately conservative
        # for cash sufficiency: a same-cycle exit cannot be assumed to fund a new
        # entry unless the recorded timestamps prove that ordering.
        events.append(ReplayEvent(trade.entry_time, 0, "ENTRY", trade.shadow_id))
        for mark in trade.marks:
            if mark.timestamp < trade.entry_time:
                continue
            if trade.exit_time is not None and mark.timestamp > trade.exit_time:
                continue
            events.append(ReplayEvent(mark.timestamp, 1, "MARK", trade.shadow_id, mark.bid))
        if trade.exit_time is not None:
            events.append(ReplayEvent(trade.exit_time, 2, "EXIT", trade.shadow_id, trade.exit_bid))
    return sorted(events, key=lambda event: (event.timestamp, event.priority, event.trade_id))


def _equity(cash: float, open_positions: dict[int, OpenPosition]) -> float:
    return cash + sum(position.market_value for position in open_positions.values())


def _capital_deployed(open_positions: dict[int, OpenPosition]) -> float:
    return sum(position.trade.capital_required for position in open_positions.values())


def _planned_risk(open_positions: dict[int, OpenPosition]) -> float:
    return sum(position.trade.planned_risk for position in open_positions.values())


def _overnight_metrics(
    funded_trades: Iterable[TradeEvidence],
    *,
    end_time: datetime,
) -> tuple[int, int, int, float]:
    tz = ZoneInfo(EXCHANGE_TIMEZONE)
    overnight_holds = 0
    position_nights = 0
    overnight_counts: dict[date, int] = {}
    worst_gap_loss = 0.0

    for trade in funded_trades:
        stop_time = trade.exit_time or max(trade.latest_time, end_time)
        start_day = trade.entry_time.astimezone(tz).date()
        end_day = stop_time.astimezone(tz).date()
        if end_day > start_day:
            overnight_holds += 1
            day = start_day
            while day < end_day:
                overnight_counts[day] = overnight_counts.get(day, 0) + 1
                position_nights += 1
                day += timedelta(days=1)

        by_day: dict[date, list[Mark]] = {}
        for mark in trade.marks:
            if mark.timestamp < trade.entry_time:
                continue
            if trade.exit_time is not None and mark.timestamp > trade.exit_time:
                continue
            by_day.setdefault(mark.timestamp.astimezone(tz).date(), []).append(mark)
        ordered_days = sorted(by_day)
        for prior_day, next_day in zip(ordered_days, ordered_days[1:]):
            prior_marks = by_day[prior_day]
            next_marks = by_day[next_day]
            if not prior_marks or not next_marks:
                continue
            loss = max(0.0, prior_marks[-1].bid - next_marks[0].bid)
            loss *= CONTRACT_MULTIPLIER * trade.quantity
            worst_gap_loss = max(worst_gap_loss, loss)

    return (
        overnight_holds,
        position_nights,
        max(overnight_counts.values(), default=0),
        round(worst_gap_loss, 2),
    )


def simulate_account(
    trades: Iterable[TradeEvidence],
    *,
    starting_balance: float,
    as_of: datetime | None = None,
) -> ScenarioResult:
    """Replay complete ACTIVE V1 evidence through one cash-only account size."""
    if starting_balance <= 0:
        raise ValueError("starting_balance must be > 0")
    trade_list = list(trades)
    by_id = {trade.shadow_id: trade for trade in trade_list}
    events = _events(trade_list)
    fallback_as_of = max((event.timestamp for event in events), default=datetime.now(timezone.utc))
    end_time = (as_of or fallback_as_of).astimezone(timezone.utc)

    cash = float(starting_balance)
    open_positions: dict[int, OpenPosition] = {}
    funded_ids: set[int] = set()
    blocked_ids: set[int] = set()
    realized_pnl = 0.0

    peak_equity = float(starting_balance)
    lowest_equity = float(starting_balance)
    max_drawdown = 0.0
    max_drawdown_percent = 0.0
    underwater_started: datetime | None = None
    longest_underwater = timedelta(0)
    max_open_positions = 0
    max_capital_deployed = 0.0
    max_planned_risk = 0.0
    last_event_time = end_time

    for event in events:
        if event.timestamp > end_time:
            break
        last_event_time = event.timestamp
        trade = by_id[event.trade_id]

        if event.kind == "ENTRY":
            if trade.capital_required <= cash + _EPS:
                funded_ids.add(trade.shadow_id)
                cash -= trade.capital_required
                initial_bid = trade.entry_bid or trade.entry_ask
                open_positions[trade.shadow_id] = OpenPosition(trade, initial_bid)
            else:
                blocked_ids.add(trade.shadow_id)
        elif event.kind == "MARK":
            position = open_positions.get(trade.shadow_id)
            if position is not None and event.bid is not None and event.bid > 0:
                position.mark_bid = event.bid
        elif event.kind == "EXIT":
            position = open_positions.get(trade.shadow_id)
            if position is not None and event.bid is not None and event.bid > 0:
                proceeds = event.bid * CONTRACT_MULTIPLIER * trade.quantity
                cash += proceeds
                realized_pnl += proceeds - trade.capital_required
                del open_positions[trade.shadow_id]

        equity = _equity(cash, open_positions)
        lowest_equity = min(lowest_equity, equity)
        capital_deployed = _capital_deployed(open_positions)
        planned_risk = _planned_risk(open_positions)
        max_open_positions = max(max_open_positions, len(open_positions))
        max_capital_deployed = max(max_capital_deployed, capital_deployed)
        max_planned_risk = max(max_planned_risk, planned_risk)

        if equity > peak_equity + _EPS:
            peak_equity = equity
            if underwater_started is not None:
                longest_underwater = max(longest_underwater, event.timestamp - underwater_started)
                underwater_started = None
        elif equity < peak_equity - _EPS:
            if underwater_started is None:
                underwater_started = event.timestamp
            drawdown = peak_equity - equity
            drawdown_percent = (drawdown / peak_equity) * 100.0 if peak_equity > 0 else 0.0
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_percent = drawdown_percent

    if underwater_started is not None:
        longest_underwater = max(longest_underwater, last_event_time - underwater_started)

    ending_equity = _equity(cash, open_positions)
    unrealized_pnl = sum(
        position.market_value - position.trade.capital_required
        for position in open_positions.values()
    )
    funded_trades = [by_id[trade_id] for trade_id in sorted(funded_ids)]
    overnight_holds, position_nights, max_overnight, worst_gap_loss = _overnight_metrics(
        funded_trades, end_time=end_time
    )
    considered = len(trade_list)
    blocked = len(blocked_ids)

    return ScenarioResult(
        starting_balance=round(starting_balance, 2),
        ending_equity=round(ending_equity, 2),
        total_return_percent=round(((ending_equity - starting_balance) / starting_balance) * 100.0, 2),
        realized_pnl=round(realized_pnl, 2),
        unrealized_pnl=round(unrealized_pnl, 2),
        lowest_equity=round(lowest_equity, 2),
        peak_equity=round(peak_equity, 2),
        max_drawdown_dollars=round(max_drawdown, 2),
        max_drawdown_percent=round(max_drawdown_percent, 2),
        longest_underwater_minutes=round(longest_underwater.total_seconds() / 60.0, 2),
        trades_considered=considered,
        trades_funded=len(funded_ids),
        trades_blocked_capital=blocked,
        funding_block_rate_percent=round((blocked / considered) * 100.0, 2) if considered else 0.0,
        max_open_positions=max_open_positions,
        max_capital_deployed=round(max_capital_deployed, 2),
        max_capital_deployed_percent_of_start=round((max_capital_deployed / starting_balance) * 100.0, 2),
        max_planned_risk_open=round(max_planned_risk, 2),
        max_planned_risk_percent_of_start=round((max_planned_risk / starting_balance) * 100.0, 2),
        overnight_holds=overnight_holds,
        overnight_position_nights=position_nights,
        max_overnight_positions=max_overnight,
        max_overnight_gap_loss_dollars=worst_gap_loss,
        open_positions_at_end=len(open_positions),
        available_cash_at_end=round(cash, 2),
    )


def minimum_starting_cash_to_fund_all(trades: Iterable[TradeEvidence]) -> float:
    """Minimum initial cash that would have funded every observed entry.

    This is a cash-flow requirement, not a recommendation. Same-timestamp entries
    are processed before exits, matching the conservative scenario replay.
    """
    trade_list = list(trades)
    by_id = {trade.shadow_id: trade for trade in trade_list}
    cash_delta = 0.0
    minimum_delta = 0.0
    for event in _events(trade_list):
        trade = by_id[event.trade_id]
        if event.kind == "ENTRY":
            cash_delta -= trade.capital_required
            minimum_delta = min(minimum_delta, cash_delta)
        elif event.kind == "EXIT" and event.bid is not None and event.bid > 0:
            cash_delta += event.bid * CONTRACT_MULTIPLIER * trade.quantity
    return round(max(0.0, -minimum_delta), 2)


def build_account_equity_report(
    storage: ScanStorage,
    *,
    starting_balances: Iterable[float] = DEFAULT_STARTING_BALANCES,
    capital_ceiling: float = DEFAULT_CAPITAL_CEILING,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    trades, issues = load_trade_evidence(storage)
    balances = tuple(float(balance) for balance in starting_balances)
    if not balances or any(balance <= 0 for balance in balances):
        raise ValueError("starting_balances must contain positive values")
    if capital_ceiling <= 0:
        raise ValueError("capital_ceiling must be > 0")

    minimum_cash = minimum_starting_cash_to_fund_all(trades)
    scenarios = [
        simulate_account(trades, starting_balance=balance, as_of=as_of).to_dict()
        for balance in balances
    ]
    report_time = as_of or max(
        (trade.latest_time for trade in trades),
        default=datetime.now(timezone.utc),
    )
    return {
        "paper_policy_id": POLICY_ID,
        "advisory_only": True,
        "analysis_only": True,
        "report_as_of": report_time.astimezone(timezone.utc).isoformat(),
        "capital_ceiling": round(float(capital_ceiling), 2),
        "capital_ceiling_is_allocation_not_drawdown": True,
        "minimum_starting_cash_to_fund_all_observed_entries": minimum_cash,
        "capital_ceiling_funded_all_observed_entries": minimum_cash <= capital_ceiling + _EPS,
        "active_v1_trades_loaded": len(trades),
        "incomplete_active_v1_rows": sum(issues.values()),
        "incomplete_reason_counts": issues,
        "scenarios": scenarios,
        "methodology": {
            "entry_fill": "ASK",
            "mark_to_market": "BID",
            "exit_fill": "BID",
            "position_type": "LONG_CALL_OR_PUT_CASH_ONLY",
            "counterfactual_rows": "EXCLUDED",
            "starting_balances": list(balances),
            "same_timestamp_cash_order": "ENTRY_BEFORE_EXIT",
            "commission_model": "V1_RECORDED_NO_COMMISSION",
            "unrealized_pnl_in_equity": True,
            "overnight_gap_basis": "PRIOR_SESSION_LAST_BID_TO_NEXT_SESSION_FIRST_BID",
            "notes": "Scenario sizing does not change frozen V1 setup/risk admission rules.",
        },
    }


def _parse_balances(raw: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in raw.split(",") if item.strip())
    if not values or any(value <= 0 for value in values):
        raise ValueError("balances must be comma-separated positive numbers")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description="Options Paper V1 bankroll/drawdown report")
    parser.add_argument("--db", default="logs/options_scanner.sqlite", help="scanner SQLite path")
    parser.add_argument(
        "--balances",
        default=",".join(str(int(value)) for value in DEFAULT_STARTING_BALANCES),
        help="comma-separated starting balances, default: 1500,2500,5000",
    )
    parser.add_argument(
        "--capital-ceiling",
        type=float,
        default=DEFAULT_CAPITAL_CEILING,
        help="maximum allocation to evaluate; not a drawdown limit",
    )
    args = parser.parse_args()
    storage = ScanStorage(Path(args.db))
    report = build_account_equity_report(
        storage,
        starting_balances=_parse_balances(args.balances),
        capital_ceiling=args.capital_ceiling,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
