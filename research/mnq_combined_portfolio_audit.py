"""Pure shared-account arbitration for the preregistered MNQ portfolio audit.

Research-only. This module knows nothing about broker adapters, runtime config,
webhooks, or live state. Family adapters must first produce causal, independently
resolved fillable events. The core then answers only the shared-capacity question.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from math import inf, isfinite
from typing import Iterable, Optional


TIE_PRIORITY = (
    "4HR_RETRIGGER",
    "60M_322_FIRST_LIVE",
    "DAILY_22_COMPLETED_CLOSE",
    "12HR_MIYAGI",
    "ASIA_D_EMA",
    "SUSTAINED_TREND_V1",
)
_PRIORITY = {name: i for i, name in enumerate(TIE_PRIORITY)}
STARTING_BALANCE = 5_000.0
MAX_FILLS_PER_DAY = 3


def parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        out = value
    else:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if out.tzinfo is None:
        raise ValueError(f"naive timestamp not allowed: {value!r}")
    return out


@dataclass(frozen=True)
class PortfolioEvent:
    family: str
    source_id: str
    signal_ts: str
    eligible_fill_ts: str
    exit_ts: Optional[str]
    observation_day: str
    direction: str
    entry: float
    stop: float
    target: float
    result: str
    net_pnl: float
    session: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if self.family not in _PRIORITY:
            raise ValueError(f"unknown family {self.family!r}")
        fill = parse_ts(self.eligible_fill_ts)
        parse_ts(self.signal_ts)
        if self.exit_ts is not None and parse_ts(self.exit_ts) < fill:
            raise ValueError(
                f"{self.source_id}: exit before eligible fill "
                f"({self.exit_ts} < {self.eligible_fill_ts})"
            )
        if self.direction not in {"LONG", "SHORT"}:
            raise ValueError(f"{self.source_id}: invalid direction {self.direction!r}")
        if not all(isfinite(float(v)) for v in (self.entry, self.stop, self.target, self.net_pnl)):
            raise ValueError(f"{self.source_id}: non-finite economics")
        if self.direction == "LONG" and not self.stop < self.entry < self.target:
            raise ValueError(f"{self.source_id}: invalid LONG bracket")
        if self.direction == "SHORT" and not self.target < self.entry < self.stop:
            raise ValueError(f"{self.source_id}: invalid SHORT bracket")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioDecision:
    source_id: str
    family: str
    eligible_fill_ts: str
    observation_day: str
    disposition: str
    blocker_source_id: Optional[str] = None
    blocker_family: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PortfolioReplay:
    fills: list[PortfolioEvent]
    decisions: list[PortfolioDecision]
    exact_timestamp_collisions: int

    def to_dict(self) -> dict:
        return {
            "fills": [x.to_dict() for x in self.fills],
            "decisions": [x.to_dict() for x in self.decisions],
            "exact_timestamp_collisions": self.exact_timestamp_collisions,
        }


def _event_sort_key(event: PortfolioEvent):
    return (
        parse_ts(event.eligible_fill_ts),
        _PRIORITY[event.family],
        event.source_id,
    )


def replay_portfolio(
    events: Iterable[PortfolioEvent],
    *,
    excluded_families: Iterable[str] = (),
    max_fills_per_day: int = MAX_FILLS_PER_DAY,
) -> PortfolioReplay:
    """Replay fillable family events through one shared MNQ account.

    A prior position frees capacity only when its exit timestamp is strictly
    earlier than the candidate fill timestamp. Equal-timestamp entry/exit is
    treated as occupied because sub-bar ordering is unknown. This is the
    pessimistic ordering contract for heterogeneous source timeframes.
    """
    excluded = set(excluded_families)
    rows = [event for event in events if event.family not in excluded]
    rows.sort(key=_event_sort_key)

    ts_counts = Counter(event.eligible_fill_ts for event in rows)
    exact_collisions = sum(count - 1 for count in ts_counts.values() if count > 1)

    fills: list[PortfolioEvent] = []
    decisions: list[PortfolioDecision] = []
    active: Optional[PortfolioEvent] = None
    fills_by_day: dict[str, int] = defaultdict(int)

    for event in rows:
        now = parse_ts(event.eligible_fill_ts)
        if active is not None and active.exit_ts is not None:
            if parse_ts(active.exit_ts) < now:
                active = None

        if active is not None:
            decisions.append(
                PortfolioDecision(
                    source_id=event.source_id,
                    family=event.family,
                    eligible_fill_ts=event.eligible_fill_ts,
                    observation_day=event.observation_day,
                    disposition="SKIPPED_BUSY_PORTFOLIO",
                    blocker_source_id=active.source_id,
                    blocker_family=active.family,
                )
            )
            continue

        if fills_by_day[event.observation_day] >= max_fills_per_day:
            decisions.append(
                PortfolioDecision(
                    source_id=event.source_id,
                    family=event.family,
                    eligible_fill_ts=event.eligible_fill_ts,
                    observation_day=event.observation_day,
                    disposition="SKIPPED_MAX_TRADES_PORTFOLIO",
                )
            )
            continue

        fills.append(event)
        fills_by_day[event.observation_day] += 1
        decisions.append(
            PortfolioDecision(
                source_id=event.source_id,
                family=event.family,
                eligible_fill_ts=event.eligible_fill_ts,
                observation_day=event.observation_day,
                disposition="FILLED",
            )
        )
        # OPEN/unknown exit blocks the rest of the measured window.
        active = event

    return PortfolioReplay(
        fills=fills,
        decisions=decisions,
        exact_timestamp_collisions=exact_collisions,
    )


def profit_factor(pnls: list[float]) -> float | None:
    wins = sum(v for v in pnls if v > 0)
    losses = -sum(v for v in pnls if v < 0)
    if losses == 0:
        return inf if wins > 0 else None
    return wins / losses


def _max_drawdown(pnls: list[float], start: float = STARTING_BALANCE) -> tuple[float, float]:
    equity = peak = float(start)
    max_dd = 0.0
    max_dd_pct = 0.0
    for pnl in pnls:
        equity += float(pnl)
        peak = max(peak, equity)
        dd = peak - equity
        dd_pct = dd / peak if peak > 0 else 1.0
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd_pct)
    return round(max_dd, 2), round(max_dd_pct, 6)


def summarize_fills(fills: list[PortfolioEvent]) -> dict:
    ordered = sorted(fills, key=_event_sort_key)
    terminal = [x for x in ordered if x.result in {"WIN", "LOSS", "BREAKEVEN"}]
    pnls = [float(x.net_pnl) for x in terminal]
    p = profit_factor(pnls)
    split = (len(terminal) + 1) // 2
    h1 = pnls[:split]
    h2 = pnls[split:]
    max_dd, max_dd_pct = _max_drawdown(pnls)

    by_month: dict[str, list[float]] = defaultdict(list)
    by_day: dict[str, list[float]] = defaultdict(list)
    for event in terminal:
        by_month[event.eligible_fill_ts[:7]].append(float(event.net_pnl))
        by_day[event.observation_day].append(float(event.net_pnl))

    losing_streak = max_losing_streak = 0
    for pnl in pnls:
        if pnl < 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0

    def cell(values: list[float]) -> dict:
        pf = profit_factor(values)
        return {
            "n": len(values),
            "net": round(sum(values), 2),
            "pf": (
                "inf" if pf == inf else round(pf, 4) if pf is not None else None
            ),
        }

    return {
        "fills": len(ordered),
        "terminal": len(terminal),
        "wins": sum(x.result == "WIN" for x in terminal),
        "losses": sum(x.result == "LOSS" for x in terminal),
        "breakeven": sum(x.result == "BREAKEVEN" for x in terminal),
        "open_or_nonterminal": len(ordered) - len(terminal),
        "net": round(sum(pnls), 2),
        "expectancy": round(sum(pnls) / len(terminal), 2) if terminal else None,
        "pf": "inf" if p == inf else round(p, 4) if p is not None else None,
        "max_drawdown": max_dd,
        "max_drawdown_pct_of_5000": max_dd_pct,
        "distinct_filled_days": len({x.observation_day for x in ordered}),
        "h1": cell(h1),
        "h2": cell(h2),
        "monthly": {month: cell(vals) for month, vals in sorted(by_month.items())},
        "winning_days": sum(sum(vals) > 0 for vals in by_day.values()),
        "losing_days": sum(sum(vals) < 0 for vals in by_day.values()),
        "flat_days": sum(sum(vals) == 0 for vals in by_day.values()),
        "max_consecutive_losses": max_losing_streak,
        "fills_by_family": dict(Counter(x.family for x in ordered)),
    }


def summarize_replay(replay: PortfolioReplay) -> dict:
    out = summarize_fills(replay.fills)
    out["exact_timestamp_collisions"] = replay.exact_timestamp_collisions
    out["decisions_by_disposition"] = dict(
        Counter(x.disposition for x in replay.decisions)
    )
    out["busy_skips_by_family"] = dict(
        Counter(
            x.family
            for x in replay.decisions
            if x.disposition == "SKIPPED_BUSY_PORTFOLIO"
        )
    )
    out["max_trade_skips_by_family"] = dict(
        Counter(
            x.family
            for x in replay.decisions
            if x.disposition == "SKIPPED_MAX_TRADES_PORTFOLIO"
        )
    )
    return out


def leave_one_out(events: list[PortfolioEvent]) -> dict[str, dict]:
    full = replay_portfolio(events)
    full_summary = summarize_replay(full)
    out: dict[str, dict] = {}
    for family in TIE_PRIORITY:
        reduced = replay_portfolio(events, excluded_families={family})
        summary = summarize_replay(reduced)
        full_pf = full_summary["pf"]
        reduced_pf = summary["pf"]

        full_ids = {x.source_id for x in full.fills}
        reduced_ids = {x.source_id for x in reduced.fills}
        new_ids = reduced_ids - full_ids
        consumed = Counter(
            x.family for x in reduced.fills if x.source_id in new_ids
        )

        def _pf_num(value):
            if value == "inf":
                return inf
            return float(value) if value is not None else None

        a, b = _pf_num(full_pf), _pf_num(reduced_pf)
        delta_pf = None
        if a is not None and b is not None and isfinite(a) and isfinite(b):
            delta_pf = round(a - b, 4)

        out[family] = {
            "without_family": summary,
            "delta_full_minus_without": {
                "net": round(full_summary["net"] - summary["net"], 2),
                "pf": delta_pf,
                "max_drawdown": round(
                    full_summary["max_drawdown"] - summary["max_drawdown"], 2
                ),
                "fills": full_summary["fills"] - summary["fills"],
                "winning_days": full_summary["winning_days"] - summary["winning_days"],
                "losing_days": full_summary["losing_days"] - summary["losing_days"],
            },
            "newly_available_fill_count_without_family": len(new_ids),
            "families_consuming_freed_slots": dict(consumed),
            "newly_available_source_ids": sorted(new_ids),
        }
    return out


def deterministic_digest_payload(replay: PortfolioReplay) -> list[tuple]:
    """Compact stable representation used by tests and result provenance."""
    return [
        (
            event.source_id,
            event.family,
            event.eligible_fill_ts,
            event.exit_ts,
            event.result,
            round(float(event.net_pnl), 8),
        )
        for event in replay.fills
    ]
