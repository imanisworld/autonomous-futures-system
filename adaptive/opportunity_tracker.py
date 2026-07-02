"""
adaptive/opportunity_tracker.py

Read-only, evidence-first SHADOW opportunity tracker.

Purpose (counterfactual, never trades): if a valid regular-system setup was
blocked ONLY by a schedule/session gate, what would have happened afterward?

This module owns the data contracts, the deterministic outcome resolver, the
attribution classifier, and append-only JSONL persistence. It NEVER submits an
order and NEVER mutates configuration. The component that *generates* candidates
(an always-on counterfactual evaluation that re-runs strategy detection with the
schedule gates removed) lives in the decision/runner layer and feeds candidates
here; this keeps all trading logic out of the shadow layer.

Storage decision — JSONL (not SQLite):
  * Mirrors the existing append-only journal pattern (journal/journal_logger.py,
    logs/replay/*.jsonl) — one less storage paradigm in the repo.
  * Append-only is crash-safe and human-inspectable; no schema migrations.
  * Retention: per-UTC-day files under logs/opportunities/, pruned like journals.
  SQLite was rejected: it adds a migration surface and a second storage model for
  no benefit at this scale (thousands of rows/day, sequential append + batch read).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

# Mirrors execution/tradovate_broker.py tick maps (kept local to avoid importing
# the broker into the read-only shadow layer).
INSTRUMENT_SPECS: dict[str, tuple[float, float]] = {
    # instrument: (tick_size, tick_value_per_tick_usd)
    "MES": (0.25, 1.25), "ES": (0.25, 12.50),
    "MNQ": (0.25, 0.50), "NQ": (0.25, 5.00),
    "MGC": (0.10, 1.00), "MCL": (0.01, 1.00),
}
_DEFAULT_SPEC = (0.25, 0.50)

# Cost model (pessimistic, consistent with the range-fade backtest).
DEFAULT_SLIPPAGE_TICKS = 1.0       # adverse, on the market entry AND the stop
DEFAULT_COMMISSION_RT = 5.0        # USD round-trip per contract (stress cost)

# ── Block attribution ───────────────────────────────────────────────────────
# Pure TIME/SESSION-eligibility gates. A capacity gate (daily cap, per-session
# count) or a risk gate is NOT a schedule block — those are shared risk limits
# the spec requires us to keep, so a setup they stop is not a "missed schedule
# opportunity".
SCHEDULE_GATES: frozenset[str] = frozenset({
    # DecisionEngine failed_gates / markers
    "SESSION_NOT_ALLOWED", "SESSION_WINDOW", "NY_SESSION_WINDOW",
    # RiskEngine failed_rule values
    "session_not_allowed", "session_window", "session_cutoff",
})

# Block-type labels.
SETUP_BLOCKED = "SETUP_BLOCKED"     # valid setup, stopped ONLY by schedule gate(s)
RISK_REJECTED = "RISK_REJECTED"     # valid setup, stopped by a risk/capacity rule
QUALITY_BLOCKED = "QUALITY_BLOCKED"  # valid setup, stopped by a non-schedule quality gate
NO_SETUP = "NO_SETUP"               # no valid setup existed (not tracked)


def classify_block(
    *,
    has_valid_setup: bool,
    gate_ids: list[str] | None,
    risk_failed_rule: Optional[str],
) -> tuple[str, bool]:
    """Classify why a setup was not traded.

    Returns (block_type, multi_gate). `multi_gate` is True when more than one
    independent gate failed — we then NEVER attribute the miss to schedule alone.
    """
    if not has_valid_setup:
        return NO_SETUP, False
    blockers = [g for g in (gate_ids or []) if g]
    if risk_failed_rule:
        blockers.append(risk_failed_rule)
    blockers = list(dict.fromkeys(blockers))  # de-dupe, preserve order
    if not blockers:
        return NO_SETUP, False  # a valid setup with nothing blocking it isn't an "opportunity"

    schedule_blockers = [b for b in blockers if b in SCHEDULE_GATES]
    non_schedule = [b for b in blockers if b not in SCHEDULE_GATES]
    multi_gate = len(blockers) > 1

    if schedule_blockers and not non_schedule:
        return SETUP_BLOCKED, multi_gate  # schedule-only → a genuine missed opportunity
    # Something other than schedule also failed → do NOT call it a schedule miss.
    if risk_failed_rule and risk_failed_rule in non_schedule:
        return RISK_REJECTED, multi_gate
    return QUALITY_BLOCKED, multi_gate


# ── Data contracts ──────────────────────────────────────────────────────────

@dataclass
class OpportunityCandidate:
    candidate_id: str
    source_bar_id: str
    detected_at: str            # ISO8601 UTC
    instrument: str
    session: str
    timeframe: str
    strategy: str
    direction: str              # LONG | SHORT
    entry: float
    stop: float
    target: float
    failed_gates: list[str] = field(default_factory=list)
    risk_failed_rule: Optional[str] = None
    market_condition: Optional[str] = None
    block_type: str = NO_SETUP
    multi_gate: bool = False
    snapshots: dict = field(default_factory=dict)   # trend/vwap/volume/orb/htf/confluence/regime
    status: str = "PENDING"     # PENDING | RESOLVED | EXPIRED
    expires_at: Optional[str] = None
    direction_role: Optional[str] = None
    htf_primary_direction: Optional[str] = None
    daily_direction: Optional[str] = None
    four_hour_direction: Optional[str] = None
    direction_reason: Optional[str] = None
    selected: bool = False
    attempted: bool = False
    fallback_attempt: bool = False
    reject_code: Optional[str] = None
    reject_reason: Optional[str] = None
    broker_result: Optional[str] = None

    @staticmethod
    def make_id(instrument: str, source_bar_id: str, strategy: str, direction: str) -> str:
        return f"{instrument}:{source_bar_id}:{strategy}:{direction}"

    def has_valid_bracket(self) -> bool:
        if self.direction not in ("LONG", "SHORT"):
            return False
        if None in (self.entry, self.stop, self.target):
            return False
        if self.direction == "LONG":
            return self.stop < self.entry < self.target
        return self.target < self.entry < self.stop

    def to_dict(self) -> dict:
        d = asdict(self)
        d["_type"] = "candidate"
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "OpportunityCandidate":
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**d)


@dataclass
class OpportunityOutcome:
    candidate_id: str
    resolved_at: str            # ISO8601 UTC of the resolving bar
    result: str                 # TARGET_HIT | STOP_HIT | EXPIRED_OPEN
    exit_reason: str
    pnl_ticks: float
    pnl_dollars: float          # one-contract normalized, costs included
    contracts: int
    mfe_ticks: float
    mae_ticks: float
    bars_to_resolution: int
    est_commission: float
    est_slippage_ticks: float
    entry_touched: bool = False
    stop_touched: bool = False
    target_touched: bool = False
    pessimistic_same_bar: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["_type"] = "outcome"
        return d


# ── Deterministic outcome resolver ──────────────────────────────────────────

def resolve_outcome(
    candidate: OpportunityCandidate,
    future_bars: list[dict],
    *,
    slippage_ticks: float = DEFAULT_SLIPPAGE_TICKS,
    commission_rt: float = DEFAULT_COMMISSION_RT,
    pessimistic_both_hit: bool = True,
) -> OpportunityOutcome:
    """Resolve a candidate against causal FUTURE bars (chronological, strictly
    after the detection bar). Pessimistic same-bar rule: if a bar straddles both
    stop and target, assume the STOP (loss). Returns an EXPIRED_OPEN outcome when
    no bar resolves it (unresolved candidates are reported, never dropped)."""
    tick_size, tick_value = INSTRUMENT_SPECS.get(candidate.instrument, _DEFAULT_SPEC)
    long = candidate.direction == "LONG"
    entry, stop, target = candidate.entry, candidate.stop, candidate.target

    mfe_price = 0.0   # max favorable excursion from entry (>=0), after entry touch
    mae_price = 0.0   # max adverse excursion from entry (<=0), after entry touch
    entry_touched = False
    stop_touched = False
    target_touched = False
    pessimistic_same_bar = False

    def _finish(result: str, exit_price: float, idx: int, reason: str) -> OpportunityOutcome:
        # Adverse slippage on the market entry; target = clean limit, stop = slipped.
        slip = slippage_ticks * tick_size
        eff_entry = entry + slip if long else entry - slip
        if result == "STOP_HIT":
            eff_exit = exit_price - slip if long else exit_price + slip
        else:
            eff_exit = exit_price
        pnl_price = (eff_exit - eff_entry) if long else (eff_entry - eff_exit)
        pnl_ticks = pnl_price / tick_size if tick_size else 0.0
        pnl_dollars = pnl_ticks * tick_value - commission_rt
        return OpportunityOutcome(
            candidate_id=candidate.candidate_id,
            resolved_at=future_bars[idx].get("ts", ""),
            result=result,
            exit_reason=reason,
            pnl_ticks=round(pnl_ticks, 4),
            pnl_dollars=round(pnl_dollars, 2),
            contracts=1,
            mfe_ticks=round(mfe_price / tick_size, 4) if tick_size else 0.0,
            mae_ticks=round(mae_price / tick_size, 4) if tick_size else 0.0,
            bars_to_resolution=idx + 1,
            est_commission=commission_rt,
            est_slippage_ticks=slippage_ticks,
            entry_touched=entry_touched,
            stop_touched=stop_touched,
            target_touched=target_touched,
            pessimistic_same_bar=pessimistic_same_bar,
        )

    for i, bar in enumerate(future_bars):
        hi = float(bar["high"])
        lo = float(bar["low"])
        if not entry_touched:
            entry_touched = lo <= entry <= hi
            if not entry_touched:
                continue
        if long:
            mfe_price = max(mfe_price, hi - entry)
            mae_price = min(mae_price, lo - entry)
            hit_target = hi >= target
            hit_stop = lo <= stop
        else:
            mfe_price = max(mfe_price, entry - lo)
            mae_price = min(mae_price, entry - hi)
            hit_target = lo <= target
            hit_stop = hi >= stop

        stop_touched = stop_touched or hit_stop
        target_touched = target_touched or hit_target

        if hit_target and hit_stop:
            pessimistic_same_bar = pessimistic_both_hit
            if pessimistic_both_hit:
                return _finish("STOP_HIT", stop, i, "same-bar stop+target → pessimistic stop")
            return _finish("TARGET_HIT", target, i, "same-bar stop+target → optimistic target")
        if hit_stop:
            return _finish("STOP_HIT", stop, i, "stop hit")
        if hit_target:
            return _finish("TARGET_HIT", target, i, "target hit")

    # Unresolved — distinguish an unfilled resting entry from a filled/open one.
    unresolved_result = "EXPIRED_OPEN" if entry_touched else "ENTRY_NOT_TOUCHED"
    unresolved_reason = (
        "no stop/target reached within available bars"
        if entry_touched
        else "planned entry never traded within available bars"
    )
    return OpportunityOutcome(
        candidate_id=candidate.candidate_id,
        resolved_at="",
        result=unresolved_result,
        exit_reason=unresolved_reason,
        pnl_ticks=0.0,
        pnl_dollars=0.0,
        contracts=1,
        mfe_ticks=round(mfe_price / tick_size, 4) if tick_size else 0.0,
        mae_ticks=round(mae_price / tick_size, 4) if tick_size else 0.0,
        bars_to_resolution=len(future_bars),
        est_commission=commission_rt,
        est_slippage_ticks=slippage_ticks,
        entry_touched=entry_touched,
        stop_touched=stop_touched,
        target_touched=target_touched,
        pessimistic_same_bar=pessimistic_same_bar,
    )


# ── Append-only JSONL persistence ───────────────────────────────────────────

class OpportunityStore:
    """Append-only per-UTC-day JSONL store of candidates and outcomes."""

    def __init__(self, log_dir: str = "logs/opportunities") -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, for_date: Optional[date] = None) -> Path:
        d = for_date or datetime.now(timezone.utc).date()
        return self.log_dir / f"opportunities_{d.isoformat()}.jsonl"

    def _append(self, record: dict, for_date: Optional[date] = None) -> None:
        with self._path(for_date).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def record_candidate(self, c: OpportunityCandidate, for_date: Optional[date] = None) -> None:
        self._append(c.to_dict(), for_date)

    def record_outcome(self, o: OpportunityOutcome, for_date: Optional[date] = None) -> None:
        self._append(o.to_dict(), for_date)

    def record_lifecycle(
        self,
        candidate_id: str,
        stage: str,
        *,
        for_date: Optional[date] = None,
        **fields,
    ) -> None:
        self._append(
            {
                "_type": "lifecycle",
                "candidate_id": candidate_id,
                "stage": stage,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                **fields,
            },
            for_date,
        )

    def read_day(self, for_date: Optional[date] = None) -> list[dict]:
        path = self._path(for_date)
        if not path.exists():
            return []
        rows: list[dict] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return rows
