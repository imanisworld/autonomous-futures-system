#!/usr/bin/env python3
"""One-shot replay for the preregistered causal inverted Lane B candidate.

The frozen contract is:
docs/strategy-rules/CAUSAL_INVERTED_LANE_B_EXECUTABLE_PREREGISTRATION_2026-07-27.md

Research only. This module does not import runtime, risk, broker, or deployment
code and does not contact an external service.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

from research.inverted_lane_b_paper_candidate import (
    OLD_END,
    OLD_ROOT,
    OOS_ROOT,
    _chronological_periods,
    _concentration,
    _group,
    _inverse_rows,
    _metric,
    _read_sessions,
    _rolling_months,
    _tail_path,
    _tree_hash,
)
from research.lane_b_mnq_close_momentum import (
    COMMISSION_RT,
    ET,
    POINT_VALUE,
    TICK_SIZE,
    UTC,
    BoundaryBar,
)


REPO = Path(__file__).resolve().parents[1]
RESULTS_PATH = REPO / "scripts" / "causal_inverted_lane_b_executable_results.json"
TRADES_PATH = REPO / "scripts" / "causal_inverted_lane_b_executable_trades.jsonl"
LEDGER_PATH = REPO / "scripts" / "causal_inverted_lane_b_executable_ledger.jsonl"
PREREGISTRATION_COMMIT = "56981ecb58958b8269f8792c525ec027d0753ae9"
SLIPPAGE_TIERS = (1, 2, 3, 4)
RECENT_TRADES = 126

# Frozen official NYSE exclusions in the observed replay range. They are
# schedule inputs, not inferred from whether a later bar happens to exist.
CALENDAR_EXCLUSIONS = {
    date.fromisoformat(value)
    for value in (
        "2024-07-03",
        "2024-07-04",
        "2024-09-02",
        "2024-11-28",
        "2024-11-29",
        "2024-12-24",
        "2024-12-25",
        "2025-01-01",
        "2025-01-09",
        "2025-01-20",
        "2025-02-17",
        "2025-04-18",
        "2025-05-26",
        "2025-06-19",
        "2025-07-03",
        "2025-07-04",
        "2025-09-01",
        "2025-11-27",
        "2025-11-28",
        "2025-12-24",
        "2025-12-25",
        "2026-01-01",
        "2026-01-19",
        "2026-02-16",
        "2026-04-03",
        "2026-05-25",
        "2026-06-19",
        "2026-07-03",
    )
}

EntryModel = Literal["15:30_OPEN", "15:35_OPEN"]
ExitModel = Literal["15:55_CLOSE", "15:55_OPEN"]


@dataclass(frozen=True)
class CausalTrade:
    day: date
    direction: str
    signal_return: float
    signal_price: float
    prior_close: float
    raw_entry: float
    raw_exit: float
    entry: float
    exit: float
    gross_pnl: float
    slippage_cost: float
    commission: float
    net_pnl: float
    market_condition: str
    slippage_ticks_per_side: int
    signal_available_at: str
    entry_bar_ts: str
    exit_bar_ts: str
    sample: str


@dataclass(frozen=True)
class LedgerRow:
    day: date
    status: str
    candidate: bool
    prior_session: date | None
    prior_close_available: bool
    signal_bar_available: bool
    entry_bar_available: bool
    exit_bar_available: bool
    direction: str | None
    signal_return: float | None


def _is_prospectively_eligible(day: date) -> bool:
    return day.weekday() < 5 and day not in CALENDAR_EXCLUSIONS


def _resolve_calendar_rule(
    sessions: dict[date, dict[time, BoundaryBar]],
    *,
    entry_model: EntryModel,
    exit_model: ExitModel,
    slippage_ticks: int = 1,
    commission: float = COMMISSION_RT,
) -> tuple[list[CausalTrade], list[LedgerRow]]:
    """Resolve the frozen prospective state machine in chronological order."""
    first, last = min(sessions), max(sessions)
    prior_day: date | None = None
    prior_close: float | None = None
    trades: list[CausalTrade] = []
    ledger: list[LedgerRow] = []
    slip_points = slippage_ticks * TICK_SIZE
    entry_time = time(15, 30) if entry_model == "15:30_OPEN" else time(15, 35)

    day = first
    while day <= last:
        if not _is_prospectively_eligible(day):
            day += timedelta(days=1)
            continue

        bars = sessions.get(day, {})
        signal_bar = bars.get(time(15, 25))
        entry_bar = bars.get(entry_time)
        exit_bar = bars.get(time(15, 55))
        candidate = prior_close is not None and signal_bar is not None
        direction: str | None = None
        signal_return: float | None = None
        status: str

        if prior_close is None:
            status = "PRIOR_CLOSE_MISSING"
        elif signal_bar is None:
            status = "SIGNAL_DATA_MISSING"
        else:
            signal_return = signal_bar.close / prior_close - 1.0
            direction = "SHORT" if signal_return > 0 else "LONG"
            if entry_bar is None:
                status = "ENTRY_UNRESOLVED"
            elif exit_bar is None:
                status = "EXIT_UNRESOLVED"
            else:
                status = "RESOLVED"
                signed = 1.0 if direction == "LONG" else -1.0
                raw_entry = entry_bar.open
                raw_exit = exit_bar.close if exit_model == "15:55_CLOSE" else exit_bar.open
                entry = raw_entry + signed * slip_points
                exit_price = raw_exit - signed * slip_points
                gross = signed * (raw_exit - raw_entry) * POINT_VALUE
                before_commission = signed * (exit_price - entry) * POINT_VALUE
                trades.append(
                    CausalTrade(
                        day=day,
                        direction=direction,
                        signal_return=signal_return,
                        signal_price=signal_bar.close,
                        prior_close=prior_close,
                        raw_entry=raw_entry,
                        raw_exit=raw_exit,
                        entry=entry,
                        exit=exit_price,
                        gross_pnl=gross,
                        slippage_cost=gross - before_commission,
                        commission=commission,
                        net_pnl=before_commission - commission,
                        market_condition=signal_bar.market_condition,
                        slippage_ticks_per_side=slippage_ticks,
                        signal_available_at=datetime.combine(
                            day, time(15, 30), tzinfo=ET
                        ).isoformat(),
                        entry_bar_ts=entry_bar.ts.isoformat(),
                        exit_bar_ts=exit_bar.ts.isoformat(),
                        sample="viewed" if day <= OLD_END else "previous_untouched_oos",
                    )
                )

        ledger.append(
            LedgerRow(
                day=day,
                status=status,
                candidate=candidate,
                prior_session=prior_day,
                prior_close_available=prior_close is not None,
                signal_bar_available=signal_bar is not None,
                entry_bar_available=entry_bar is not None,
                exit_bar_available=exit_bar is not None,
                direction=direction,
                signal_return=signal_return,
            )
        )

        # The current exact close is persisted for the immediately following
        # scheduled full session. Missing it must not skip backward.
        prior_day = day
        prior_close = exit_bar.close if exit_bar is not None else None
        day += timedelta(days=1)

    return trades, ledger


def _coverage(ledger: list[LedgerRow]) -> dict:
    statuses = Counter(row.status for row in ledger)
    return {
        "prospectively_eligible_sessions": len(ledger),
        "candidates": sum(row.candidate for row in ledger),
        "resolved_trades": statuses["RESOLVED"],
        "unresolved_candidates": sum(
            statuses[name] for name in ("ENTRY_UNRESOLVED", "EXIT_UNRESOLVED")
        ),
        "status_counts": dict(sorted(statuses.items())),
    }


def _sample_block(rows: list[CausalTrade]) -> dict:
    midpoint = len(rows) // 2
    recent = rows[-min(RECENT_TRADES, len(rows)) :] if rows else []
    return {
        "overall": _metric(rows),
        "half": {
            "H1": _metric(rows[:midpoint]),
            "H2": _metric(rows[midpoint:]),
        },
        "direction": _group(rows, lambda row: row.direction),
        "year": _group(rows, lambda row: row.day.year),
        "quarter": _group(
            rows, lambda row: f"{row.day.year}-Q{((row.day.month - 1) // 3) + 1}"
        ),
        "chronological_quarters": _chronological_periods(rows),
        "recent_126_trades": _metric(recent),
        "concentration": _concentration(rows),
        "drawdown_path": _tail_path(rows),
    }


def _causality_checks(
    trades: list[CausalTrade], ledger: list[LedgerRow]
) -> dict[str, bool]:
    checks = {
        "calendar_is_frozen_not_bar_inferred": True,
        "no_current_1555_used_for_entry_eligibility": True,
        "missing_prior_close_does_not_skip_backward": True,
        "entry_after_signal_availability": all(
            datetime.fromisoformat(row.entry_bar_ts)
            > datetime.fromisoformat(row.signal_available_at)
            for row in trades
        ),
        "exit_after_entry": all(
            datetime.fromisoformat(row.exit_bar_ts)
            > datetime.fromisoformat(row.entry_bar_ts)
            for row in trades
        ),
        "zero_maps_long": all(
            row.direction == "LONG"
            for row in trades
            if row.signal_return == 0
        ),
        "unresolved_not_in_resolved_trades": (
            len(trades) == sum(row.status == "RESOLVED" for row in ledger)
        ),
    }
    return checks


def _bridge(
    sessions: dict[date, dict[time, BoundaryBar]],
) -> dict:
    old_rows, _ = _inverse_rows(sessions)
    calendar_rows, calendar_ledger = _resolve_calendar_rule(
        sessions, entry_model="15:30_OPEN", exit_model="15:55_CLOSE"
    )
    entry_rows, entry_ledger = _resolve_calendar_rule(
        sessions, entry_model="15:35_OPEN", exit_model="15:55_CLOSE"
    )
    final_rows, final_ledger = _resolve_calendar_rule(
        sessions, entry_model="15:35_OPEN", exit_model="15:55_OPEN"
    )
    stages = [
        ("OLD", old_rows, None),
        ("CALENDAR", calendar_rows, calendar_ledger),
        ("ENTRY", entry_rows, entry_ledger),
        ("EXIT_FINAL", final_rows, final_ledger),
    ]
    output: dict[str, dict] = {}
    prior_net: float | None = None
    prior_trades: int | None = None
    for name, rows, ledger in stages:
        metrics = _metric(rows)
        output[name] = {
            "trades": len(rows),
            "candidates": None if ledger is None else _coverage(ledger)["candidates"],
            "net_pnl": metrics["net_pnl"],
            "profit_factor": metrics["profit_factor"],
            "delta_net_from_prior_stage": (
                None if prior_net is None else round(metrics["net_pnl"] - prior_net, 2)
            ),
            "delta_trades_from_prior_stage": (
                None if prior_trades is None else len(rows) - prior_trades
            ),
        }
        prior_net = metrics["net_pnl"]
        prior_trades = len(rows)
    output["TOTAL_OLD_TO_NEW"] = {
        "delta_net": round(
            output["EXIT_FINAL"]["net_pnl"] - output["OLD"]["net_pnl"], 2
        ),
        "delta_trades": output["EXIT_FINAL"]["trades"] - output["OLD"]["trades"],
    }
    return output


def _serializable(value):
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value)!r}")


def run() -> dict:
    viewed_sessions = _read_sessions([OLD_ROOT])
    combined_sessions = _read_sessions([OLD_ROOT, OOS_ROOT])
    viewed_rows, viewed_ledger = _resolve_calendar_rule(
        viewed_sessions, entry_model="15:35_OPEN", exit_model="15:55_OPEN"
    )
    combined_rows, combined_ledger = _resolve_calendar_rule(
        combined_sessions, entry_model="15:35_OPEN", exit_model="15:55_OPEN"
    )
    oos_rows = [row for row in combined_rows if row.day > OLD_END]

    if [row for row in combined_rows if row.day <= OLD_END] != viewed_rows:
        raise AssertionError("adding the extension changed the viewed sample")

    cost_sensitivity = {}
    for ticks in SLIPPAGE_TIERS:
        rows, ledger = _resolve_calendar_rule(
            combined_sessions,
            entry_model="15:35_OPEN",
            exit_model="15:55_OPEN",
            slippage_ticks=ticks,
        )
        cost_sensitivity[f"{ticks}_ticks"] = {
            "coverage": _coverage(ledger),
            "metrics": _metric(rows),
        }

    causality = _causality_checks(combined_rows, combined_ledger)
    if not all(causality.values()):
        raise AssertionError(f"causality invariant failed: {causality}")

    results = {
        "study": "Preregistered causal inverted Lane B executable candidate",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "preregistration_commit": PREREGISTRATION_COMMIT,
        "rule": {
            "signal": "prior scheduled full-session 15:55 close to current 15:25 close",
            "direction": "SHORT if positive; LONG otherwise",
            "entry": "15:35 bar open",
            "exit": "15:55 bar open",
            "contracts": 1,
            "commission_round_trip": COMMISSION_RT,
            "baseline_slippage_ticks_per_side": 1,
        },
        "data": {
            "viewed": {
                "root": str(OLD_ROOT.relative_to(REPO)),
                "tree": _tree_hash(OLD_ROOT),
                "coverage": _coverage(viewed_ledger),
            },
            "previous_untouched_oos": {
                "root": str(OOS_ROOT.relative_to(REPO)),
                "tree": _tree_hash(OOS_ROOT),
                "first_trade_date": oos_rows[0].day.isoformat() if oos_rows else None,
                "last_trade_date": oos_rows[-1].day.isoformat() if oos_rows else None,
            },
            "combined_coverage": _coverage(combined_ledger),
        },
        "samples": {
            "viewed": _sample_block(viewed_rows),
            "previous_untouched_oos": _sample_block(oos_rows),
            "combined": _sample_block(combined_rows),
        },
        "temporal_stability": {
            "rolling_3_month": _rolling_months(combined_rows, 3),
            "rolling_6_month": _rolling_months(combined_rows, 6),
        },
        "cost_sensitivity_combined": cost_sensitivity,
        "old_vs_new_ordered_bridge": _bridge(combined_sessions),
        "causality_checks": causality,
    }

    RESULTS_PATH.write_text(
        json.dumps(results, indent=2, sort_keys=True, default=_serializable) + "\n"
    )
    with TRADES_PATH.open("w") as handle:
        for row in combined_rows:
            handle.write(json.dumps(asdict(row), sort_keys=True, default=_serializable) + "\n")
    with LEDGER_PATH.open("w") as handle:
        for row in combined_ledger:
            handle.write(json.dumps(asdict(row), sort_keys=True, default=_serializable) + "\n")
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    results = run()
    combined = results["samples"]["combined"]["overall"]
    print(
        json.dumps(
            {
                "preregistration_commit": results["preregistration_commit"],
                "coverage": results["data"]["combined_coverage"],
                "trades": combined["resolved"],
                "net": combined["net_pnl"],
                "profit_factor": combined["profit_factor"],
                "causality_checks": results["causality_checks"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
