"""Pure prospective capture + Stage-A scoring for the 212 floor-outcome draft.

No provider call, file reader/writer, experiment adapter, deployment hook, or
real-data entry point lives here. The DRAFT study stays non-executable; these
primitives exist so the frozen contract can be validated with synthetic data
before any forward session is admitted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from alert_ranker.causal_bars import MINUTE_5, Bar
from alert_ranker.coverage_episodes import Episode, REDUCER_VERSION
from alert_ranker.coverage_observer import OBSERVED_TIMEFRAME, OBSERVER_VERSION
from alert_ranker.coverage_outcomes import gate_bucket
from alert_ranker.session_calendar import EXCHANGE_TIMEZONE, Session, nyse_session_for
from ops.options_212c_floor_outcome_monitor import (
    ACTIVATION_GATE,
    FAMILY,
    PATH_RECORD_VERSION,
    RECOGNIZED_GATES,
    TRIAL_ID,
    V1_UNIVERSE,
    canonical_seal_bytes,
    seal_sha256,
)

TARGET = "TARGET_FIRST"
STOP = "INVALIDATION_FIRST"
TIMEOUT = "UNRESOLVED_AT_CLOSE"
AMBIGUOUS = "AMBIGUOUS"
DATA_INVALID = "DATA_INVALID"
COMPLETED = frozenset({TARGET, STOP, TIMEOUT})
FIVE = MINUTE_5.delta

REQUIRED_METRICS = (
    "population_size",
    "setups_evaluated",
    "activation_count",
    "completed_trades",
    "wins",
    "losses",
    "timeouts",
    "ambiguous_count",
    "data_invalid_count",
    "win_rate",
    "loss_rate",
    "expectancy",
    "mean_r",
    "median_result",
    "average_winner",
    "average_loser",
    "payoff_ratio",
    "mae",
    "mfe",
    "mae_distribution",
    "mfe_distribution",
    "target_hit_rate",
    "stop_hit_rate",
    "timeout_rate",
    "concentration_by_ticker",
    "concentration_by_session_date",
    "concentration_by_clock_bucket",
    "outcome_concentration",
)


class StudyContractError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


@dataclass(frozen=True)
class SealedSessionArtifact:
    record: dict[str, Any]
    body: bytes
    sha256: str
    manifest: dict[str, Any]


def _dt(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise StudyContractError("timestamp_missing", field)
    try:
        out = datetime.fromisoformat(value)
    except ValueError as exc:
        raise StudyContractError("timestamp_invalid", field) from exc
    if out.tzinfo is None or out.utcoffset() is None:
        raise StudyContractError("timestamp_naive", field)
    return out.astimezone(timezone.utc)


def _num(value: Any, field: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise StudyContractError("numeric_invalid", field)
    out = float(value)
    if not math.isfinite(out):
        raise StudyContractError("numeric_invalid", field)
    return out


def _episode_id(ep: Episode) -> str:
    return "|".join(
        (ep.symbol, ep.session_date, ep.direction, ep.first_bar_start, ep.family)
    )


def _event_key(row: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    fields = ("symbol", "session_date", "direction", "bar_start", "family")
    values = tuple(row.get(field) for field in fields)
    if any(not isinstance(value, str) or not value for value in values):
        raise StudyContractError("event_identity_missing")
    return values  # type: ignore[return-value]


def _episode_key(ep: Episode) -> tuple[str, str, str, str, str]:
    return (
        ep.symbol,
        ep.session_date,
        ep.direction,
        ep.first_bar_start,
        ep.family,
    )


def expected_starts(session: Session, first_sight_at: str) -> list[datetime]:
    sight = _dt(first_sight_at, "first_sight_at")
    cursor = session.open.astimezone(timezone.utc)
    close = session.close.astimezone(timezone.utc)
    if sight >= close:
        return []
    while cursor < sight:
        cursor += FIVE
    out: list[datetime] = []
    while cursor + FIVE <= close:
        out.append(cursor)
        cursor += FIVE
    return out


def _bar_dict(bar: Bar) -> dict[str, Any]:
    o, h, l, c = (
        float(_num(bar.open, "open")),
        float(_num(bar.high, "high")),
        float(_num(bar.low, "low")),
        float(_num(bar.close, "close")),
    )
    if h < l or h < max(o, c) or l > min(o, c):
        raise StudyContractError("bar_ohlc_invalid", bar.start_utc.isoformat())
    return {
        "start": bar.start_utc.isoformat(),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
    }


def _sealed_bars(
    ep: Episode, session: Session, bars: Sequence[Bar]
) -> list[dict[str, Any]]:
    expected = expected_starts(session, ep.first_sight_at)
    by_start: dict[datetime, Bar] = {}
    for bar in bars:
        if bar.start_utc in by_start:
            raise StudyContractError("duplicate_bar", bar.start_utc.isoformat())
        by_start[bar.start_utc] = bar
    missing = [start for start in expected if start not in by_start]
    if missing:
        raise StudyContractError("missing_bar", missing[0].isoformat())
    return [_bar_dict(by_start[start]) for start in expected]


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-12)
    except (TypeError, ValueError):
        return a == b


def _crosscheck(ep: Episode, event: Mapping[str, Any]) -> None:
    exact = {
        "observer_version": OBSERVER_VERSION,
        "timeframe": OBSERVED_TIMEFRAME,
        "symbol": ep.symbol,
        "session_date": ep.session_date,
        "direction": ep.direction,
        "family": ep.family,
        "bar_start": ep.first_bar_start,
        "first_sight_at": ep.first_sight_at,
        "first_sight_after_close": ep.first_sight_after_close,
    }
    for field, wanted in exact.items():
        got = (
            bool(event.get(field))
            if field == "first_sight_after_close"
            else event.get(field)
        )
        wanted_cmp = bool(wanted) if field == "first_sight_after_close" else wanted
        if got != wanted_cmp:
            raise StudyContractError("episode_event_mismatch", field)
    for field, wanted in (
        ("entry_trigger", ep.entry_trigger),
        ("invalidation", ep.invalidation),
        ("risk", ep.risk),
        ("floor_target_1", ep.floor_target_1),
        ("first_sight_price", ep.first_sight_price),
    ):
        if not _same(event.get(field), wanted):
            raise StudyContractError("episode_event_mismatch", field)
    if "floor_target_2" not in event:
        raise StudyContractError("floor_target_2_missing")


def build_session_artifact(
    session: Session,
    episodes: Sequence[Episode],
    coverage_events: Sequence[Mapping[str, Any]],
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    source: Mapping[str, Any],
    captured_at: datetime,
) -> SealedSessionArtifact:
    """Create canonical bytes + manifest from already-causal in-memory inputs."""

    authority = nyse_session_for(session.date)
    if authority is None or authority.open != session.open or authority.close != session.close:
        raise StudyContractError("session_authority_mismatch")
    if captured_at.tzinfo is None or captured_at.astimezone(timezone.utc) < session.close:
        raise StudyContractError("captured_before_close")
    for field in (
        "provider",
        "request_start",
        "request_end",
        "observer_run_id",
        "observer_ran_at",
        "source_sha",
    ):
        if field not in source:
            raise StudyContractError("source_field_missing", field)

    event_index: dict[
        tuple[str, str, str, str, str], Mapping[str, Any]
    ] = {}
    for row in coverage_events:
        if (
            row.get("session_date") != session.date.isoformat()
            or row.get("family") != FAMILY
            or row.get("symbol") not in V1_UNIVERSE
        ):
            continue
        key = _event_key(row)
        if key in event_index:
            raise StudyContractError("duplicate_event_key", "|".join(key))
        event_index[key] = row

    snapshots: list[dict[str, Any]] = []
    seen: set[str] = set()
    selected = [
        ep
        for ep in episodes
        if ep.session_date == session.date.isoformat()
        and ep.family == FAMILY
        and ep.symbol in V1_UNIVERSE
    ]
    for ep in sorted(selected, key=lambda x: (x.symbol, x.first_bar_start, x.direction)):
        if ep.reducer_version != REDUCER_VERSION:
            raise StudyContractError("reducer_version_mismatch")
        eid = _episode_id(ep)
        if eid in seen:
            raise StudyContractError("duplicate_episode_id", eid)
        seen.add(eid)
        event = event_index.get(_episode_key(ep))
        if event is None:
            raise StudyContractError("first_event_missing", eid)
        _crosscheck(ep, event)
        gate = gate_bucket(ep, "floor")
        if gate not in RECOGNIZED_GATES:
            raise StudyContractError("gate_invalid", gate)
        risk = float(_num(ep.risk, "risk"))
        trigger = float(_num(ep.entry_trigger, "entry_trigger"))
        invalidation = float(_num(ep.invalidation, "invalidation"))
        if risk <= 0 or not math.isclose(
            abs(trigger - invalidation), risk, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise StudyContractError("risk_mismatch", eid)
        snapshots.append(
            {
                "episode_id": eid,
                "symbol": ep.symbol,
                "session_date": ep.session_date,
                "direction": ep.direction,
                "first_bar_start": ep.first_bar_start,
                "family": ep.family,
                "reducer_version": ep.reducer_version,
                "gate_bucket_floor": gate,
                "entry_trigger": trigger,
                "invalidation": invalidation,
                "structural_risk": risk,
                "first_sight_at": ep.first_sight_at,
                "first_sight_price": _num(
                    ep.first_sight_price, "first_sight_price", nullable=True
                ),
                "first_sight_after_close": bool(ep.first_sight_after_close),
                "floor_target_1": _num(
                    ep.floor_target_1, "floor_target_1", nullable=True
                ),
                # ep-v0.1 does not retain target_2; copy it from the
                # cross-checked first cov-v0.1 event.
                "floor_target_2": _num(
                    event.get("floor_target_2"),
                    "floor_target_2",
                    nullable=True,
                ),
                "bars": _sealed_bars(
                    ep, session, bars_by_symbol.get(ep.symbol, ())
                ),
            }
        )

    src = dict(source)
    src["request_start"] = _dt(src["request_start"], "request_start").isoformat()
    src["request_end"] = _dt(src["request_end"], "request_end").isoformat()
    src["observer_ran_at"] = _dt(
        src["observer_ran_at"], "observer_ran_at"
    ).isoformat()
    record = {
        "path_record_version": PATH_RECORD_VERSION,
        "trial_id": TRIAL_ID,
        "session_date": session.date.isoformat(),
        "session_open": session.open.astimezone(timezone.utc).isoformat(),
        "session_close": session.close.astimezone(timezone.utc).isoformat(),
        "source": src,
        "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "episodes": snapshots,
    }
    body = canonical_seal_bytes(record)
    digest = seal_sha256(record)
    return SealedSessionArtifact(
        record=record,
        body=body,
        sha256=digest,
        manifest={
            "session_date": session.date.isoformat(),
            "byte_length": len(body),
            "sha256": digest,
        },
    )


def _r(direction: str, price: float, entry: float, risk: float) -> float:
    return (
        (price - entry) / risk
        if direction == "LONG"
        else (entry - price) / risk
    )


def _invalid(snapshot: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "episode_id": snapshot.get("episode_id"),
        "ticker": snapshot.get("symbol"),
        "session_date": snapshot.get("session_date"),
        "clock_bucket": None,
        "outcome": DATA_INVALID,
        "realized_r": None,
        "mae_r": None,
        "mfe_r": None,
        "flags": [reason],
    }


def _validate_bar_rows(
    snapshot: Mapping[str, Any], session: Session
) -> list[Mapping[str, Any]]:
    raw = snapshot.get("bars")
    if not isinstance(raw, list):
        raise StudyContractError("bars_missing")
    expected = expected_starts(session, str(snapshot.get("first_sight_at")))
    if len(raw) != len(expected):
        raise StudyContractError("bar_grid_length")
    for row, wanted in zip(raw, expected):
        if (
            not isinstance(row, Mapping)
            or _dt(row.get("start"), "bar.start") != wanted
        ):
            raise StudyContractError("bar_grid_mismatch")
        o, h, l, c = (
            _num(row.get(k), f"bar.{k}")
            for k in ("open", "high", "low", "close")
        )
        assert None not in (o, h, l, c)
        if (
            float(h) < float(l)
            or float(h) < max(float(o), float(c))
            or float(l) > min(float(o), float(c))
        ):
            raise StudyContractError("bar_ohlc_invalid")
    return raw


def score_snapshot(
    snapshot: Mapping[str, Any], session: Session
) -> dict[str, Any]:
    """Frozen first-sight Stage-A scorer for one activated sealed snapshot."""

    if snapshot.get("gate_bucket_floor") != ACTIVATION_GATE:
        raise StudyContractError("not_activated")
    try:
        direction = snapshot.get("direction")
        if (
            direction not in {"LONG", "SHORT"}
            or snapshot.get("reducer_version") != REDUCER_VERSION
        ):
            raise StudyContractError("snapshot_identity_invalid")
        if snapshot.get("first_sight_after_close") is not False:
            raise StudyContractError("first_sight_after_close")
        entry = float(_num(snapshot.get("first_sight_price"), "first_sight_price"))
        trigger = float(_num(snapshot.get("entry_trigger"), "entry_trigger"))
        invalidation = float(_num(snapshot.get("invalidation"), "invalidation"))
        risk = float(_num(snapshot.get("structural_risk"), "structural_risk"))
        target1 = float(_num(snapshot.get("floor_target_1"), "floor_target_1"))
        target2_raw = snapshot.get("floor_target_2")
        target2 = (
            None
            if target2_raw is None
            else float(_num(target2_raw, "floor_target_2"))
        )
        if risk <= 0 or not math.isclose(
            abs(trigger - invalidation), risk, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise StudyContractError("risk_mismatch")
        if direction == "LONG":
            if (
                invalidation >= entry
                or target1 <= entry
                or (target2 is not None and target2 <= target1)
            ):
                raise StudyContractError("level_order_invalid")
        else:
            if (
                invalidation <= entry
                or target1 >= entry
                or (target2 is not None and target2 >= target1)
            ):
                raise StudyContractError("level_order_invalid")
        bars = _validate_bar_rows(snapshot, session)
        if not bars:
            raise StudyContractError("missing_forward_bars")
    except StudyContractError as exc:
        return _invalid(snapshot, exc.reason)

    t1r = _r(direction, target1, entry, risk)
    mfe = mae = 0.0
    outcome: str | None = None
    result: float | None = None
    flags: list[str] = []
    for bar in bars:
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        fav = h if direction == "LONG" else l
        adv = l if direction == "LONG" else h
        stop = adv <= invalidation if direction == "LONG" else adv >= invalidation
        hit = fav >= target1 if direction == "LONG" else fav <= target1
        if stop and hit:
            outcome, flags = AMBIGUOUS, [
                "gap_or_range_spans_stop_and_target"
            ]
            break
        if (
            o <= invalidation if direction == "LONG" else o >= invalidation
        ):
            outcome, result, flags = (
                STOP,
                _r(direction, o, entry, risk),
                ["gap_through_stop"],
            )
            break
        if o >= target1 if direction == "LONG" else o <= target1:
            outcome, result, flags = (
                TARGET,
                t1r,
                ["gap_through_target_1"],
            )
            break
        if stop:
            outcome, result = (
                STOP,
                _r(direction, invalidation, entry, risk),
            )
            break
        if hit:
            outcome, result = TARGET, t1r
            break
        mfe = max(mfe, _r(direction, fav, entry, risk))
        mae = min(mae, _r(direction, adv, entry, risk))

    if outcome is None:
        outcome = TIMEOUT
        result = _r(
            direction, float(bars[-1]["close"]), entry, risk
        )

    sight = _dt(
        snapshot.get("first_sight_at"), "first_sight_at"
    ).astimezone(ZoneInfo(EXCHANGE_TIMEZONE))
    return {
        "episode_id": snapshot.get("episode_id"),
        "ticker": snapshot.get("symbol"),
        "session_date": snapshot.get("session_date"),
        "clock_bucket": f"{sight.hour:02d}:00 ET",
        "outcome": outcome,
        "realized_r": None if result is None else round(result, 6),
        "mae_r": round(mae, 6),
        "mfe_r": round(mfe, 6),
        "flags": flags,
    }


def score_session_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Synthetic/in-memory only; no digest reader and no real one-look adapter."""

    if (
        record.get("path_record_version") != PATH_RECORD_VERSION
        or record.get("trial_id") != TRIAL_ID
    ):
        raise StudyContractError("record_identity_invalid")
    day = datetime.fromisoformat(str(record.get("session_date"))).date()
    session = nyse_session_for(day)
    if session is None:
        raise StudyContractError("session_invalid")
    episodes = record.get("episodes")
    if not isinstance(episodes, list):
        raise StudyContractError("episodes_missing")
    rows = []
    for snap in episodes:
        if (
            not isinstance(snap, Mapping)
            or snap.get("gate_bucket_floor") not in RECOGNIZED_GATES
        ):
            raise StudyContractError("gate_invalid")
        if snap.get("gate_bucket_floor") == ACTIVATION_GATE:
            rows.append(score_snapshot(snap, session))
    return {
        "session_date": record.get("session_date"),
        "population_size": len(episodes),
        "rows": rows,
    }


def _div(a: float, b: float) -> float | None:
    return a / b if b else None


def _dist(values: Sequence[float]) -> dict[str, Any]:
    vals = sorted(values)
    if not vals:
        return {"n": 0, "p25": None, "median": None, "p75": None}

    def q(p: float) -> float:
        k = (len(vals) - 1) * p
        lo, hi = math.floor(k), math.ceil(k)
        return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)

    return {
        "n": len(vals),
        "p25": round(q(0.25), 6),
        "median": round(median(vals), 6),
        "p75": round(q(0.75), 6),
    }


def _group(
    rows: Sequence[Mapping[str, Any]], key: str
) -> dict[str, Any]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = str(row.get(key) or "UNKNOWN")
        bucket = out.setdefault(
            label, {"count": 0, "completed": 0, "total_r": 0.0}
        )
        bucket["count"] += 1
        if (
            row.get("outcome") in COMPLETED
            and row.get("realized_r") is not None
        ):
            bucket["completed"] += 1
            bucket["total_r"] += float(row["realized_r"])
    for bucket in out.values():
        bucket["total_r"] = round(bucket["total_r"], 6)
    return out


def aggregate_metrics(
    session_scores: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    population = sum(
        int(x.get("population_size") or 0) for x in session_scores
    )
    rows = [
        r
        for x in session_scores
        for r in (x.get("rows") or [])
        if isinstance(r, Mapping)
    ]
    completed = [r for r in rows if r.get("outcome") in COMPLETED]
    wins = [r for r in completed if r.get("outcome") == TARGET]
    losses = [r for r in completed if r.get("outcome") == STOP]
    timeouts = [r for r in completed if r.get("outcome") == TIMEOUT]
    ambiguous = [r for r in rows if r.get("outcome") == AMBIGUOUS]
    invalid = [r for r in rows if r.get("outcome") == DATA_INVALID]
    results = [
        float(r["realized_r"])
        for r in completed
        if r.get("realized_r") is not None
    ]
    win_r = [
        float(r["realized_r"])
        for r in wins
        if r.get("realized_r") is not None
    ]
    loss_r = [
        float(r["realized_r"])
        for r in losses
        if r.get("realized_r") is not None
    ]
    mae = [
        float(r["mae_r"])
        for r in completed
        if r.get("mae_r") is not None
    ]
    mfe = [
        float(r["mfe_r"])
        for r in completed
        if r.get("mfe_r") is not None
    ]
    mean_r = sum(results) / len(results) if results else None
    aw = sum(win_r) / len(win_r) if win_r else None
    al = sum(loss_r) / len(loss_r) if loss_r else None
    payoff = (
        abs(aw / al)
        if aw is not None and al not in (None, 0)
        else None
    )

    ticker_r: dict[str, float] = {}
    for row in completed:
        if row.get("realized_r") is None:
            continue
        ticker = str(row.get("ticker") or "UNKNOWN")
        ticker_r[ticker] = ticker_r.get(ticker, 0.0) + float(
            row["realized_r"]
        )
    total_r = sum(results)
    largest = (
        max(
            completed,
            key=lambda r: float(
                r.get("realized_r")
                if r.get("realized_r") is not None
                else -math.inf
            ),
        )
        if completed
        else None
    )
    largest_ticker = (
        max(ticker_r, key=ticker_r.get) if ticker_r else None
    )
    concentration = {
        "total_r": round(total_r, 6),
        "largest_contributor_episode_id": (
            largest.get("episode_id") if largest else None
        ),
        "largest_contributor_r": (
            round(float(largest["realized_r"]), 6)
            if largest and largest.get("realized_r") is not None
            else None
        ),
        "largest_contributor_share_of_total_r": (
            round(float(largest["realized_r"]) / total_r, 6)
            if largest and total_r
            else None
        ),
        "largest_ticker": largest_ticker,
        "largest_ticker_r": (
            round(ticker_r[largest_ticker], 6)
            if largest_ticker
            else None
        ),
        "largest_ticker_share_of_total_r": (
            round(ticker_r[largest_ticker] / total_r, 6)
            if largest_ticker and total_r
            else None
        ),
    }
    metrics = {
        "population_size": {"count": population},
        "setups_evaluated": {
            "count": population,
            "of": population,
        },
        "activation_count": {
            "count": len(rows),
            "rate": _div(len(rows), population),
            "of": population,
        },
        "completed_trades": {
            "count": len(completed),
            "of": len(rows),
        },
        "wins": {"count": len(wins), "of": len(completed)},
        "losses": {"count": len(losses), "of": len(completed)},
        "timeouts": {
            "count": len(timeouts),
            "of": len(completed),
        },
        "ambiguous_count": {
            "count": len(ambiguous),
            "of": len(rows),
        },
        "data_invalid_count": {
            "count": len(invalid),
            "of": len(rows),
        },
        "win_rate": {
            "count": len(wins),
            "rate": _div(len(wins), len(completed)),
            "of": len(completed),
        },
        "loss_rate": {
            "count": len(losses),
            "rate": _div(len(losses), len(completed)),
            "of": len(completed),
        },
        "expectancy": {
            "value": round(mean_r, 6)
            if mean_r is not None
            else None,
            "of": len(results),
        },
        "mean_r": {
            "value": round(mean_r, 6)
            if mean_r is not None
            else None,
            "of": len(results),
        },
        "median_result": {
            "value": round(median(results), 6)
            if results
            else None,
            "of": len(results),
        },
        "average_winner": {
            "value": round(aw, 6) if aw is not None else None,
            "of": len(win_r),
        },
        "average_loser": {
            "value": round(al, 6) if al is not None else None,
            "of": len(loss_r),
        },
        "payoff_ratio": {
            "value": round(payoff, 6)
            if payoff is not None
            else None,
            "winners": len(win_r),
            "losers": len(loss_r),
        },
        "mae": {
            "mean": round(sum(mae) / len(mae), 6)
            if mae
            else None,
            "of": len(mae),
        },
        "mfe": {
            "mean": round(sum(mfe) / len(mfe), 6)
            if mfe
            else None,
            "of": len(mfe),
        },
        "mae_distribution": _dist(mae),
        "mfe_distribution": _dist(mfe),
        "target_hit_rate": {
            "count": len(wins),
            "rate": _div(len(wins), len(completed)),
            "of": len(completed),
        },
        "stop_hit_rate": {
            "count": len(losses),
            "rate": _div(len(losses), len(completed)),
            "of": len(completed),
        },
        "timeout_rate": {
            "count": len(timeouts),
            "rate": _div(len(timeouts), len(completed)),
            "of": len(completed),
        },
        "concentration_by_ticker": _group(rows, "ticker"),
        "concentration_by_session_date": _group(
            rows, "session_date"
        ),
        "concentration_by_clock_bucket": _group(
            rows, "clock_bucket"
        ),
        "outcome_concentration": concentration,
    }
    if set(REQUIRED_METRICS) - set(metrics):
        raise AssertionError("preregistered metric missing")
    return metrics
