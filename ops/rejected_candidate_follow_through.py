"""Prospective follow-through for rejected / blocked futures candidates (U6).

Read-only, offline. It never edits journals, never turns a rejected candidate
into a trade, never touches a broker, and never changes strategy selection.

Sources (all read from existing journal decision rows):

* ``blocked_candidate_audit`` — candidates blocked by an early gate (e.g. the
  TRENDING-only gate). Previously never resolved anywhere.
* ``candidate_audit`` rows carrying a ``reject_code`` that were not the
  selected/attempted candidate.
* ``RISK_REJECTED`` decisions (the setup the risk engine refused).

Each candidate keeps its original decision and rejection verbatim and is
resolved prospectively — only bars strictly after its signal bar — through the
real ``execution.paper_broker.PaperBroker`` under ONE declared, frozen execution
model (the U3 ``execution_assumptions`` vocabulary). Bars are read across day
and session boundaries until a declared horizon (in bars) is reached. Terminal
observations:

``WIN`` / ``LOSS`` / ``BREAKEVEN`` (from the broker fill), ``NO_FILL`` (entry
never established), ``EXPIRED`` (horizon reached unresolved), and
``UNAVAILABLE_BY_RULE`` (unsupported instrument, invalid geometry, unknown
timeframe, missing signal bar). A candidate without enough subsequent bars yet
stays ``PENDING`` — reported, never written as terminal — and is re-evaluated on
the next run, so follow-through carries across session boundaries.

Terminal observations are appended once (idempotent by ``candidate_id``) to an
append-only ledger. Economics are per one contract, counterfactual only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops import evidence_row as evidence_contract  # noqa: E402

SCHEMA_VERSION = "1.0.0"
LEDGER_FILENAME = "rejected_candidate_outcomes.jsonl"

SOURCE_BLOCKED_AUDIT = "blocked_candidate_audit"
SOURCE_CANDIDATE_AUDIT = "candidate_audit"
SOURCE_RISK_REJECTED = "risk_rejected"

WIN, LOSS, BREAKEVEN = "WIN", "LOSS", "BREAKEVEN"
NO_FILL, EXPIRED = "NO_FILL", "EXPIRED"
UNAVAILABLE = "UNAVAILABLE_BY_RULE"
PENDING = "PENDING"
TERMINAL_STATES = frozenset({WIN, LOSS, BREAKEVEN, NO_FILL, EXPIRED, UNAVAILABLE})
COUNTERFACTUAL_CONTRACTS = 1


class FollowThroughError(RuntimeError):
    """The follow-through run cannot proceed safely."""


# ─── Candidate extraction ───────────────────────────────────────────────────


@dataclass(frozen=True)
class RejectedCandidate:
    candidate_id: str
    source: str
    instrument: str
    strategy: str
    direction: str
    entry: Any
    stop: Any
    target: Any
    signal_bar_ts: str
    timeframe_minutes: Optional[int]
    original_decision: Optional[str]
    rejection: dict


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def candidate_identity(
    *, source: str, instrument: str, signal_bar_ts: str, strategy: str,
    direction: str, entry: Any, stop: Any, target: Any,
) -> str:
    material = "|".join(
        str(part) for part in (source, instrument, signal_bar_ts, strategy, direction, entry, stop, target)
    )
    return "rc-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _timeframe(row: Mapping[str, Any]) -> Optional[int]:
    raw = row.get("timeframe_minutes")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, str) and raw.strip().isdigit() and int(raw) > 0:
        return int(raw)
    return None


def _make(
    row: Mapping[str, Any], *, source: str, cand: Mapping[str, Any], rejection: dict
) -> Optional[RejectedCandidate]:
    signal = _parse_ts(row.get("bar_ts") or row.get("ts"))
    instrument = str(cand.get("instrument") or row.get("instrument") or "").strip().upper()
    strategy = str(cand.get("strategy") or "").strip()
    direction = str(cand.get("direction") or "").strip().upper()
    if signal is None or not instrument or not strategy:
        return None
    signal_ts = _iso(signal)
    return RejectedCandidate(
        candidate_id=candidate_identity(
            source=source, instrument=instrument, signal_bar_ts=signal_ts, strategy=strategy,
            direction=direction, entry=cand.get("entry"), stop=cand.get("stop"),
            target=cand.get("target"),
        ),
        source=source,
        instrument=instrument,
        strategy=strategy,
        direction=direction,
        entry=cand.get("entry"),
        stop=cand.get("stop"),
        target=cand.get("target"),
        signal_bar_ts=signal_ts,
        timeframe_minutes=_timeframe(row),
        original_decision=row.get("decision"),
        rejection=rejection,
    )


def extract_rejected_candidates(rows: Iterable[Mapping[str, Any]]) -> list[RejectedCandidate]:
    """Pull every rejected / blocked candidate out of journal decision rows."""
    found: dict[str, RejectedCandidate] = {}

    def add(candidate: Optional[RejectedCandidate]) -> None:
        if candidate is not None and candidate.candidate_id not in found:
            found[candidate.candidate_id] = candidate

    for row in rows:
        if not isinstance(row, Mapping) or row.get("type") == "OUTCOME":
            continue
        blocked = row.get("blocked_candidate_audit")
        if isinstance(blocked, Mapping):
            for cand in blocked.get("candidates") or []:
                if isinstance(cand, Mapping):
                    add(
                        _make(
                            row,
                            source=SOURCE_BLOCKED_AUDIT,
                            cand=cand,
                            rejection={
                                "blocking_gate": cand.get("blocking_gate") or blocked.get("blocking_gate"),
                                "failed_gates": list(row.get("failed_gates") or []),
                                "reason": row.get("reason"),
                            },
                        )
                    )
        for cand in row.get("candidate_audit") or []:
            if (
                isinstance(cand, Mapping)
                and cand.get("reject_code")
                and not cand.get("selected")
                and not cand.get("attempted")
            ):
                add(
                    _make(
                        row,
                        source=SOURCE_CANDIDATE_AUDIT,
                        cand=cand,
                        rejection={
                            "reject_code": cand.get("reject_code"),
                            "reject_reason": cand.get("reject_reason"),
                            "failed_gates": list(cand.get("failed_gates") or []),
                        },
                    )
                )
        if row.get("decision") == "RISK_REJECTED" and isinstance(row.get("setup"), Mapping):
            risk = row.get("risk_check") or {}
            add(
                _make(
                    row,
                    source=SOURCE_RISK_REJECTED,
                    cand=row["setup"],
                    rejection={
                        "failed_rule": risk.get("failed_rule"),
                        "reason": risk.get("reason") or row.get("reason"),
                        "failed_gates": list(row.get("failed_gates") or []),
                    },
                )
            )
    return sorted(found.values(), key=lambda c: (c.signal_bar_ts, c.candidate_id))


# ─── Resolution ─────────────────────────────────────────────────────────────


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _bar_minutes(bar: Mapping[str, Any]) -> Optional[int]:
    from webhook.runner import normalize_timeframe_minutes

    minutes = normalize_timeframe_minutes(bar.get("timeframe"))
    return int(minutes) if minutes and minutes > 0 else None


def _observation(
    candidate: RejectedCandidate, state: str, *, model_id: str, horizon_bars: int, **extra: Any
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "counterfactual": True,
        "candidate_id": candidate.candidate_id,
        "source": candidate.source,
        "instrument": candidate.instrument,
        "strategy": candidate.strategy,
        "direction": candidate.direction,
        "entry": candidate.entry,
        "stop": candidate.stop,
        "target": candidate.target,
        "signal_bar_ts": candidate.signal_bar_ts,
        "original_decision": candidate.original_decision,
        "rejection": dict(candidate.rejection),
        "terminal_state": state,
        "execution_model_id": model_id,
        "horizon_bars": horizon_bars,
        "contracts": COUNTERFACTUAL_CONTRACTS,
        **extra,
    }


def resolve_candidate(
    candidate: RejectedCandidate,
    bars: list[Mapping[str, Any]],
    *,
    model: Any,
    model_id: str,
    horizon_bars: int,
) -> dict[str, Any]:
    """Resolve one candidate against its instrument's ordered bars.

    ``bars`` must include the signal bar and any later bars available.
    """
    from config.futures_contracts import UnsupportedContractError, contract_economics
    from execution.broker_interface import BracketOrder
    from execution.day_only_exit import (
        is_after_eod_close,
        is_exact_eod_bar,
        resolve_paper_eod,
        strategy_is_day_only,
    )
    from execution.paper_broker import NextBarOHLC, PaperBroker

    def done(state: str, **extra: Any) -> dict[str, Any]:
        return _observation(candidate, state, model_id=model_id, horizon_bars=horizon_bars, **extra)

    try:
        tick, tick_value = contract_economics(candidate.instrument)
    except UnsupportedContractError:
        return done(UNAVAILABLE, unavailable_reason="UNSUPPORTED_INSTRUMENT")
    entry, stop, target = (_finite(candidate.entry), _finite(candidate.stop), _finite(candidate.target))
    if None in (entry, stop, target) or candidate.direction not in ("LONG", "SHORT"):
        return done(UNAVAILABLE, unavailable_reason="INCOMPLETE_GEOMETRY")
    if candidate.direction == "LONG" and not (stop < entry < target):
        return done(UNAVAILABLE, unavailable_reason="INVALID_GEOMETRY")
    if candidate.direction == "SHORT" and not (target < entry < stop):
        return done(UNAVAILABLE, unavailable_reason="INVALID_GEOMETRY")

    signal_dt = _parse_ts(candidate.signal_bar_ts)
    ordered = sorted(
        ((dt, bar) for bar in bars if (dt := _parse_ts(bar.get("ts"))) is not None),
        key=lambda item: item[0],
    )
    signal_bar = next((bar for dt, bar in ordered if dt == signal_dt), None)
    if signal_bar is None:
        return done(UNAVAILABLE, unavailable_reason="SIGNAL_BAR_MISSING")
    minutes = candidate.timeframe_minutes or _bar_minutes(signal_bar)
    if not minutes:
        return done(UNAVAILABLE, unavailable_reason="TIMEFRAME_UNKNOWN")
    later = [(dt, bar) for dt, bar in ordered if dt > signal_dt][:horizon_bars]

    broker = PaperBroker(
        starting_balance=1_000_000.0,
        slippage_ticks=model.slippage_ticks,
        pessimistic_both_hit=True,
        breakeven_at_1r=model.breakeven_at_1r,
        runner_mode=False,
        entry_fill_model=model.entry_fill_model,
    )
    order = BracketOrder(
        instrument=candidate.instrument,
        direction=candidate.direction,
        entry=entry,
        stop=stop,
        target=target,
        rr_ratio=abs(target - entry) / abs(entry - stop),
        strategy=candidate.strategy,
        contracts=COUNTERFACTUAL_CONTRACTS,
        min_rr_ratio=0.0,
    )
    entry_fill = broker._execute_bracket_impl(order, float(signal_bar["close"]))
    if entry_fill.result == "CANCELLED":
        return done(NO_FILL, no_fill_reason=entry_fill.exit_reason or "CANCELLED")

    day_only = strategy_is_day_only(candidate.strategy)
    signal_day = signal_dt.date()
    filled_at: Optional[datetime] = signal_dt + timedelta(minutes=minutes) if entry_fill.result == "OPEN" else None
    fill = None
    resolved_at: Optional[datetime] = None
    used = 0
    for dt, bar in later:
        used += 1
        if day_only and (dt.date() != signal_day or is_after_eod_close(bar["ts"])):
            return done(EXPIRED, expiry_reason="EOD_BAR_MISSING", bars_used=used - 1)
        pending_before = broker.has_pending_entry()
        fill = broker.resolve_position(
            NextBarOHLC(open=float(bar["open"]), high=float(bar["high"]), low=float(bar["low"]))
        )
        if filled_at is None and pending_before and not broker.has_pending_entry() and (
            fill is None or fill.result != "CANCELLED"
        ):
            filled_at = dt
        if fill is not None:
            resolved_at = dt
            break
        if day_only and is_exact_eod_bar(bar["ts"], bar.get("timeframe")):
            position = broker.get_position()
            fill = resolve_paper_eod(
                broker,
                {
                    "instrument": candidate.instrument,
                    "direction": candidate.direction,
                    "entry": position.entry_price if position else entry,
                    "contracts": COUNTERFACTUAL_CONTRACTS,
                    "strategy": candidate.strategy,
                },
                timestamp=bar["ts"],
                timeframe=bar.get("timeframe"),
                close=float(bar["close"]),
            )
            resolved_at = dt
            break

    if fill is None:
        if len(later) < horizon_bars:
            return done(PENDING, bars_available=len(later))
        return done(
            EXPIRED,
            expiry_reason="HORIZON_REACHED",
            position_state="OPEN" if broker.get_position() is not None else "NOT_FILLED",
            bars_used=len(later),
        )
    if fill.result == "CANCELLED":
        return done(NO_FILL, no_fill_reason=fill.exit_reason or "CANCELLED", bars_used=used)
    if fill.result not in (WIN, LOSS, BREAKEVEN):
        return done(UNAVAILABLE, unavailable_reason=f"UNSUPPORTED_RESULT_{fill.result}")
    gross = float(fill.pnl_dollars or 0.0)
    costs = round(
        (model.commission_per_contract_per_side + model.fees_per_contract_per_side)
        * 2 * COUNTERFACTUAL_CONTRACTS,
        2,
    )
    risk = abs(float(fill.entry_price) - stop) / tick * tick_value * COUNTERFACTUAL_CONTRACTS
    net = round(gross - costs, 2)
    return done(
        fill.result,
        fill_price=fill.entry_price,
        fill_ts=_iso(filled_at) if filled_at else None,
        exit_price=fill.exit_price,
        exit_reason=fill.exit_reason,
        resolved_at_bar_ts=_iso(resolved_at) if resolved_at else None,
        bars_used=used,
        gross_pnl=round(gross, 2),
        costs_fees=costs,
        net_pnl=net,
        r_multiple=round(net / risk, 6) if risk > 0 else None,
    )


# ─── Bars, journals, ledger ─────────────────────────────────────────────────


def bars_from_history(bars_dir: Path) -> Callable[[str, datetime, int], list[dict]]:
    """Bar provider over ``bars_<INSTRUMENT>_<date>.jsonl`` files across days."""

    def provider(instrument: str, signal_dt: datetime, horizon_bars: int) -> list[dict]:
        out: list[dict] = []
        files = sorted(bars_dir.glob(f"bars_{instrument}_*.jsonl"))
        for path in files:
            stem_date = path.stem.rsplit("_", 1)[-1]
            try:
                file_day = date.fromisoformat(stem_date)
            except ValueError:
                continue
            if file_day < signal_dt.date() - timedelta(days=1):
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
        return out

    return provider


def read_journal_rows(journal_dir: Path, *, since: date, until: date) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(journal_dir.glob("journal_*.jsonl")):
        try:
            day = date.fromisoformat(path.stem.split("_", 1)[1])
        except ValueError:
            continue
        if not (since <= day <= until):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise FollowThroughError(f"{path.name}:{lineno} unreadable journal row") from exc
    return rows


def ledger_ids(
    ledger: Path,
    *,
    expected_model_id: str,
    expected_horizon_bars: int,
) -> set[str]:
    """Load terminal IDs only from a ledger with one frozen evidence identity.

    A counterfactual terminal result is meaningful only under the execution
    model and follow-through horizon that produced it. Reusing the same ledger
    under different assumptions would silently relabel stale outcomes.
    """
    if not ledger.is_file():
        return set()
    ids: set[str] = set()
    for lineno, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            candidate_id = str(row["candidate_id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise FollowThroughError(f"{ledger.name}:{lineno} corrupt ledger row") from exc
        if not isinstance(row, Mapping):
            raise FollowThroughError(f"{ledger.name}:{lineno} corrupt ledger row")
        if row.get("schema_version") != SCHEMA_VERSION:
            raise FollowThroughError(
                f"{ledger.name}:{lineno} schema_version {row.get('schema_version')!r} "
                f"!= current {SCHEMA_VERSION!r}"
            )
        if row.get("execution_model_id") != expected_model_id:
            raise FollowThroughError(
                f"{ledger.name}:{lineno} execution_model_id does not match this run"
            )
        if row.get("horizon_bars") != expected_horizon_bars:
            raise FollowThroughError(
                f"{ledger.name}:{lineno} horizon_bars {row.get('horizon_bars')!r} "
                f"!= this run {expected_horizon_bars!r}"
            )
        if candidate_id in ids:
            raise FollowThroughError(
                f"{ledger.name}:{lineno} duplicate candidate_id {candidate_id!r}"
            )
        ids.add(candidate_id)
    return ids


def run_follow_through(
    *,
    journal_rows: list[dict],
    bar_provider: Callable[[str, datetime, int], list[dict]],
    assumptions: Mapping[str, Any],
    horizon_bars: int,
    ledger: Path,
) -> dict[str, Any]:
    """Resolve every rejected candidate; append new terminal observations."""
    from execution import paper_mirror_hook
    from ops.research_experiment_adapters.futures_replay import (
        FuturesReplayAdapterError,
        parse_execution_assumptions,
    )

    if isinstance(horizon_bars, bool) or not isinstance(horizon_bars, int) or horizon_bars <= 0:
        raise FollowThroughError("horizon_bars must be a declared positive integer")
    if paper_mirror_hook.mirror_enabled():
        raise FollowThroughError("paper broker mirror is enabled; refusing counterfactual resolution")
    try:
        model = parse_execution_assumptions(assumptions)
    except FuturesReplayAdapterError as exc:
        raise FollowThroughError(f"execution assumptions: {exc}") from exc
    model_id = evidence_contract.execution_model_id(assumptions)

    already = ledger_ids(
        ledger,
        expected_model_id=model_id,
        expected_horizon_bars=horizon_bars,
    )
    candidates = extract_rejected_candidates(journal_rows)
    written: list[dict] = []
    pending: list[dict] = []
    skipped = 0
    for candidate in candidates:
        if candidate.candidate_id in already:
            skipped += 1
            continue
        signal_dt = _parse_ts(candidate.signal_bar_ts)
        bars = bar_provider(candidate.instrument, signal_dt, horizon_bars) if signal_dt else []
        observation = resolve_candidate(
            candidate, bars, model=model, model_id=model_id, horizon_bars=horizon_bars
        )
        if observation["terminal_state"] == PENDING:
            pending.append(observation)
            continue
        written.append(observation)

    if written:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle:
            for row in written:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    counts: dict[str, int] = {}
    for row in written:
        counts[row["terminal_state"]] = counts.get(row["terminal_state"], 0) + 1
    return {
        "candidates_seen": len(candidates),
        "already_terminal": skipped,
        "written": len(written),
        "pending": len(pending),
        "terminal_counts": counts,
        "pending_candidates": [
            {"candidate_id": p["candidate_id"], "signal_bar_ts": p["signal_bar_ts"],
             "bars_available": p.get("bars_available")}
            for p in pending
        ],
        "execution_model": asdict(model),
        "execution_model_id": model_id,
        "horizon_bars": horizon_bars,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--journal-dir", type=Path, required=True)
    parser.add_argument("--bars-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--execution-assumptions", type=Path, required=True,
                        help="JSON file with the frozen execution_assumptions bundle")
    parser.add_argument("--horizon-bars", type=int, required=True)
    parser.add_argument("--since", type=date.fromisoformat, required=True)
    parser.add_argument("--until", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    try:
        assumptions = json.loads(args.execution_assumptions.read_text(encoding="utf-8"))
        summary = run_follow_through(
            journal_rows=read_journal_rows(args.journal_dir, since=args.since, until=args.until),
            bar_provider=bars_from_history(args.bars_dir),
            assumptions=assumptions,
            horizon_bars=args.horizon_bars,
            ledger=args.out_dir / LEDGER_FILENAME,
        )
    except (FollowThroughError, OSError, ValueError) as exc:
        print(f"REJECTED-CANDIDATE FOLLOW-THROUGH BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
