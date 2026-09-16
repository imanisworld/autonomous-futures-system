"""Asian-session winner/loser pre-signal feature audit.

Research-only. This module never creates strategy candidates and never labels an
outcome from price data. It accepts an explicit, already-classified cohort and
measures only information available before each signal timestamp.

Hard boundaries:
- cohort rows must explicitly provide WIN/LOSS labels;
- every comparison stays partitioned by instrument + strategy + session + direction;
- only the Asian session is accepted;
- 15/30/60/120 minute windows use closed 5m bars only;
- incomplete windows stay visible and are not padded;
- no execution, risk, broker, webhook, HTTP, environment, or deployment imports.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, time, timezone
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
WINDOWS_MINUTES = (15, 30, 60, 120)
_REQUIRED_CANDIDATE_FIELDS = (
    "candidate_id",
    "instrument",
    "strategy",
    "session",
    "direction",
    "signal_ts",
    "outcome_label",
)
_ALLOWED_DIRECTIONS = {"LONG", "SHORT"}
_ALLOWED_OUTCOMES = {"WIN", "LOSS"}
_STRUCTURE_KEYS = ("bos_direction", "mss_direction", "market_structure")
_PASSTHROUGH_CANDIDATE_KEYS = (
    "baseline_pnl_dollars",
    "baseline_stop_ticks",
    "target_r",
    "relative_volume",
    "source_variant",
    "source_file",
)


@dataclass(frozen=True)
class LoadedBars:
    rows: list[dict]
    timestamps: list[datetime]
    source_files: list[dict]


def parse_ts(value: object) -> datetime:
    """Parse an offset-aware timestamp. Numeric epoch seconds/ms are accepted."""
    if isinstance(value, bool) or value is None:
        raise ValueError("timestamp is required")
    if isinstance(value, (int, float)):
        n = float(value)
        if not math.isfinite(n):
            raise ValueError("timestamp must be finite")
        if abs(n) > 1_000_000_000_000:
            n /= 1000.0
        return datetime.fromtimestamp(n, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        raise ValueError("timestamp is required")
    if text.lstrip("-").isdigit():
        n = int(text)
        if abs(n) > 1_000_000_000_000:
            n //= 1000
        return datetime.fromtimestamp(n, tz=timezone.utc)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _number(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(out):
        raise ValueError(f"{field} must be finite")
    return out


def _optional_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _row_ts(row: dict) -> datetime:
    return parse_ts(row.get("timestamp", row.get("ts")))


def _bar_end(row: dict, *, bar_timestamp_mode: str) -> datetime:
    ts = _row_ts(row)
    if bar_timestamp_mode == "start":
        return ts + timedelta(minutes=5)
    if bar_timestamp_mode == "close":
        return ts
    raise ValueError("bar_timestamp_mode must be 'start' or 'close'")


def _is_asian_timestamp(ts: datetime) -> bool:
    t = ts.astimezone(_ET).time()
    return t >= time(18, 0) or t < time(3, 0)


def _asian_session_start(ts: datetime) -> datetime:
    et = ts.astimezone(_ET)
    if et.time() >= time(18, 0):
        local = et.replace(hour=18, minute=0, second=0, microsecond=0)
    elif et.time() < time(3, 0):
        local = (et - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    else:
        raise ValueError("timestamp is not inside Asian session")
    return local.astimezone(timezone.utc)


def validate_candidate(row: dict) -> dict:
    if not isinstance(row, dict):
        raise ValueError("candidate row must be an object")
    missing = [k for k in _REQUIRED_CANDIDATE_FIELDS if row.get(k) in (None, "")]
    if missing:
        raise ValueError(f"candidate missing required fields: {', '.join(missing)}")
    candidate_id = str(row["candidate_id"]).strip()
    instrument = str(row["instrument"]).strip().upper()
    strategy = str(row["strategy"]).strip()
    session = str(row["session"]).strip().lower()
    direction = str(row["direction"]).strip().upper()
    outcome = str(row["outcome_label"]).strip().upper()
    signal_ts = parse_ts(row["signal_ts"])
    if session != "asian":
        raise ValueError(f"{candidate_id}: session must be asian, got {session!r}")
    if not _is_asian_timestamp(signal_ts):
        raise ValueError(f"{candidate_id}: signal_ts is outside canonical Asian session")
    if direction not in _ALLOWED_DIRECTIONS:
        raise ValueError(f"{candidate_id}: direction must be LONG or SHORT")
    if outcome not in _ALLOWED_OUTCOMES:
        raise ValueError(f"{candidate_id}: outcome_label must be WIN or LOSS; labels are never inferred")
    out = {
        "candidate_id": candidate_id,
        "instrument": instrument,
        "strategy": strategy,
        "session": session,
        "direction": direction,
        "signal_ts": signal_ts.isoformat(),
        "outcome_label": outcome,
    }
    for key in _PASSTHROUGH_CANDIDATE_KEYS:
        if key in row:
            out[key] = row.get(key)
    return out


def load_candidates(path: str | Path) -> tuple[list[dict], dict]:
    path = Path(path)
    raw = path.read_text()
    rows: list[dict]
    if path.suffix.lower() == ".jsonl":
        rows = []
        for lineno, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: malformed JSON") from exc
            rows.append(value)
    else:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: malformed JSON") from exc
        if isinstance(value, list):
            rows = value
        elif isinstance(value, dict) and isinstance(value.get("candidates"), list):
            rows = value["candidates"]
        else:
            raise ValueError("candidate JSON must be an array or {'candidates': [...]} object")
    validated = [validate_candidate(row) for row in rows]
    seen: set[str] = set()
    for row in validated:
        cid = row["candidate_id"]
        if cid in seen:
            raise ValueError(f"duplicate candidate_id: {cid}")
        seen.add(cid)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return validated, {"path": str(path.resolve()), "sha256": digest, "rows": len(validated)}


def _iter_bar_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ValueError(f"bars path not found: {path}")
    files = sorted(p for p in path.rglob("*.jsonl") if p.is_file())
    if not files:
        raise ValueError(f"no .jsonl bar files found under {path}")
    return files


def load_bars(path: str | Path, *, instrument: str | None = None) -> LoadedBars:
    root = Path(path)
    files = _iter_bar_files(root)
    rows: list[dict] = []
    sources: list[dict] = []
    instrument = instrument.upper() if instrument else None
    for file in files:
        file_rows = 0
        for lineno, line in enumerate(file.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{file}:{lineno}: malformed JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{file}:{lineno}: bar row must be an object")
            _ = _row_ts(row)
            for key in ("open", "high", "low", "close"):
                _number(row.get(key), field=key)
            row_inst = row.get("instrument") or row.get("ticker")
            if row_inst and instrument:
                normalized = str(row_inst).upper().replace("1!", "")
                if not normalized.startswith(instrument):
                    raise ValueError(f"{file}:{lineno}: bar instrument {row_inst!r} does not match {instrument}")
            rows.append(dict(row))
            file_rows += 1
        sources.append({
            "path": str(file.resolve()),
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "rows": file_rows,
        })
    rows.sort(key=_row_ts)
    seen: set[datetime] = set()
    timestamps: list[datetime] = []
    for row in rows:
        ts = _row_ts(row)
        if ts in seen:
            raise ValueError(f"duplicate bar timestamp across corpus: {ts.isoformat()}")
        seen.add(ts)
        timestamps.append(ts)
    return LoadedBars(rows=rows, timestamps=timestamps, source_files=sources)


def _window_rows(loaded: LoadedBars, *, signal_ts: datetime, minutes: int, bar_timestamp_mode: str) -> tuple[list[dict], bool]:
    expected = minutes // 5
    start = signal_ts - timedelta(minutes=minutes)
    selected = [row for row in loaded.rows if start < _bar_end(row, bar_timestamp_mode=bar_timestamp_mode) <= signal_ts]
    if len(selected) != expected:
        return selected, False
    ends = [_bar_end(row, bar_timestamp_mode=bar_timestamp_mode) for row in selected]
    expected_ends = [start + timedelta(minutes=5 * (i + 1)) for i in range(expected)]
    return selected, ends == expected_ends


def _prior_equal_window(loaded: LoadedBars, *, signal_ts: datetime, minutes: int, bar_timestamp_mode: str) -> tuple[list[dict], bool]:
    return _window_rows(
        loaded,
        signal_ts=signal_ts - timedelta(minutes=minutes),
        minutes=minutes,
        bar_timestamp_mode=bar_timestamp_mode,
    )


def _true_ranges(rows: list[dict], previous_close: float | None = None) -> list[float]:
    out = []
    prev = previous_close
    for row in rows:
        high = _number(row["high"], field="high")
        low = _number(row["low"], field="low")
        tr = high - low if prev is None else max(high - low, abs(high - prev), abs(low - prev))
        out.append(tr)
        prev = _number(row["close"], field="close")
    return out


def _direction_sign(direction: str) -> int:
    return 1 if direction == "LONG" else -1


def _direction_token(direction: str) -> str:
    return "bullish" if direction == "LONG" else "bearish"


def _market_condition_features(rows: list[dict]) -> dict:
    vals = [str(r.get("market_condition") or "").strip().upper() for r in rows]
    vals = [v for v in vals if v]
    if not vals:
        return {
            "market_condition_first": None,
            "market_condition_last": None,
            "market_condition_trending_fraction": None,
            "market_condition_dead_fraction": None,
            "transition_to_trending": None,
            "transition_dead_to_trending": None,
        }
    first, last = vals[0], vals[-1]
    dead = {"DEAD", "RANGE_DEAD"}
    return {
        "market_condition_first": first,
        "market_condition_last": last,
        "market_condition_trending_fraction": sum(v == "TRENDING" for v in vals) / len(vals),
        "market_condition_dead_fraction": sum(v in dead for v in vals) / len(vals),
        "transition_to_trending": first != "TRENDING" and last == "TRENDING",
        "transition_dead_to_trending": first in dead and last == "TRENDING",
    }


def _trend_features(rows: list[dict], direction: str) -> dict:
    want = "UP" if direction == "LONG" else "DOWN"
    vals = [str(r.get("trend_direction") or "").strip().upper() for r in rows]
    vals = [v for v in vals if v]
    strengths = [str(r.get("trend_strength") or "").strip().upper() for r in rows]
    strengths = [v for v in strengths if v]
    result = {
        "trend_direction_last": vals[-1] if vals else None,
        "trend_alignment_fraction": sum(v == want for v in vals) / len(vals) if vals else None,
        "trend_strength_last": strengths[-1] if strengths else None,
    }
    if rows:
        last = rows[-1]
        for key in ("regime", "structural_market_condition", "structural_direction"):
            value = last.get(key)
            result[f"{key}_last"] = str(value) if value not in (None, "") else None
    last = rows[-1] if rows else {}
    e9 = _optional_number(last.get("ema_9"))
    e21 = _optional_number(last.get("ema_21"))
    e55 = _optional_number(last.get("ema_55"))
    close = _optional_number(last.get("close"))
    if None not in (e9, e21, e55, close):
        result["ema_stack_aligned_last"] = close > e9 > e21 > e55 if direction == "LONG" else close < e9 < e21 < e55
    else:
        result["ema_stack_aligned_last"] = None
    return result


def _structure_features(rows: list[dict], direction: str) -> dict:
    want = _direction_token(direction)
    oppose = "bearish" if want == "bullish" else "bullish"
    aligned = opposed = total = 0
    latest: str | None = None
    latest_aligned: bool | None = None
    for row in rows:
        bos = str(row.get("bos_direction") or "").strip().lower()
        mss = str(row.get("mss_direction") or "").strip().lower()
        combined = str(row.get("market_structure") or "").strip().lower()
        tokens = [v for v in (bos, mss) if v]
        if combined and combined != "none":
            latest = combined
        elif mss:
            latest = f"{mss}_mss"
        elif bos:
            latest = f"{bos}_bos"
        for token in tokens:
            total += 1
            if token == want:
                aligned += 1
            elif token == oppose:
                opposed += 1
        if latest:
            latest_aligned = want in latest
    return {
        "structure_event_count": total,
        "structure_aligned_count": aligned,
        "structure_opposed_count": opposed,
        "latest_market_structure": latest,
        "latest_structure_aligned": latest_aligned,
        "structure_data_present": any(
            any(row.get(key) not in (None, "", "none") for key in _STRUCTURE_KEYS) for row in rows
        ),
    }


def _level_features(rows: list[dict], direction: str) -> dict:
    if not rows:
        return {}
    close = _number(rows[-1]["close"], field="close")
    pdh = next((_optional_number(r.get("previous_day_high")) for r in reversed(rows) if _optional_number(r.get("previous_day_high")) is not None), None)
    pdl = next((_optional_number(r.get("previous_day_low")) for r in reversed(rows) if _optional_number(r.get("previous_day_low")) is not None), None)
    out = {
        "close_minus_pdh_points": close - pdh if pdh is not None else None,
        "close_minus_pdl_points": close - pdl if pdl is not None else None,
        "pdh_sweep_reclaim": None,
        "pdl_sweep_reclaim": None,
        "directional_sweep_reclaim": None,
    }
    if pdh is not None:
        out["pdh_sweep_reclaim"] = any(_number(r["high"], field="high") >= pdh for r in rows) and close < pdh
    if pdl is not None:
        out["pdl_sweep_reclaim"] = any(_number(r["low"], field="low") <= pdl for r in rows) and close > pdl
    out["directional_sweep_reclaim"] = out["pdl_sweep_reclaim"] if direction == "LONG" else out["pdh_sweep_reclaim"]
    return out


def _window_features(rows: list[dict], *, prior_rows: list[dict], complete: bool, prior_complete: bool, direction: str) -> dict:
    result: dict[str, Any] = {
        "bar_count": len(rows),
        "complete": complete,
        "prior_equal_window_complete": prior_complete,
    }
    if not complete or not rows:
        return result
    sign = _direction_sign(direction)
    first_open = _number(rows[0]["open"], field="open")
    last_close = _number(rows[-1]["close"], field="close")
    bodies = [_number(r["close"], field="close") - _number(r["open"], field="open") for r in rows]
    signed_bodies = [sign * v for v in bodies]
    high = max(_number(r["high"], field="high") for r in rows)
    low = min(_number(r["low"], field="low") for r in rows)
    path = sum(abs(v) for v in bodies)
    tr = _true_ranges(rows)
    volumes = [_optional_number(r.get("volume")) for r in rows]
    have_vol = all(v is not None for v in volumes)
    prior_volumes = [_optional_number(r.get("volume")) for r in prior_rows] if prior_complete else []
    have_prior_vol = prior_complete and prior_volumes and all(v is not None for v in prior_volumes)
    result.update({
        "signed_net_points": sign * (last_close - first_open),
        "directional_bar_fraction": sum(v > 0 for v in signed_bodies) / len(rows),
        "path_efficiency": abs(last_close - first_open) / path if path > 0 else 0.0,
        "range_points": high - low,
        "mean_true_range_points": statistics.fmean(tr),
        "mean_body_points": statistics.fmean(abs(v) for v in bodies),
        "mean_volume": statistics.fmean(volumes) if have_vol else None,
        "volume_ratio_vs_prior_equal_window": (
            statistics.fmean(volumes) / statistics.fmean(prior_volumes)
            if have_vol and have_prior_vol and statistics.fmean(prior_volumes) > 0 else None
        ),
    })
    result.update(_market_condition_features(rows))
    result.update(_trend_features(rows, direction))
    result.update(_structure_features(rows, direction))
    result.update(_level_features(rows, direction))
    return result


def _session_context(loaded: LoadedBars, *, signal_ts: datetime, direction: str, bar_timestamp_mode: str) -> dict:
    session_start = _asian_session_start(signal_ts)
    rows = [row for row in loaded.rows if session_start < _bar_end(row, bar_timestamp_mode=bar_timestamp_mode) <= signal_ts]
    if not rows:
        return {
            "asian_session_bars_before_signal": 0,
            "signed_close_vs_session_open_points": None,
            "signed_close_vs_session_vwap_points": None,
            "session_vwap": None,
        }
    sign = _direction_sign(direction)
    session_open = _number(rows[0]["open"], field="open")
    close = _number(rows[-1]["close"], field="close")
    vols = [_optional_number(r.get("volume")) for r in rows]
    if all(v is not None for v in vols) and sum(vols) > 0:
        typical = [(_number(r["high"], field="high") + _number(r["low"], field="low") + _number(r["close"], field="close")) / 3.0 for r in rows]
        vwap = sum(p * v for p, v in zip(typical, vols)) / sum(vols)
    else:
        vwap = None
    session_high = max(_number(r["high"], field="high") for r in rows)
    session_low = min(_number(r["low"], field="low") for r in rows)
    width = session_high - session_low
    position = (close - session_low) / width if width > 0 else 0.5
    source_vwap = next((_optional_number(r.get("vwap")) for r in reversed(rows) if _optional_number(r.get("vwap")) is not None), None)
    if direction == "LONG":
        session_open_reclaim = any(_number(r["low"], field="low") <= session_open for r in rows) and close > session_open
        room = session_high - close
        directional_position = position
    else:
        session_open_reclaim = any(_number(r["high"], field="high") >= session_open for r in rows) and close < session_open
        room = close - session_low
        directional_position = 1.0 - position
    return {
        "asian_session_bars_before_signal": len(rows),
        "signed_close_vs_session_open_points": sign * (close - session_open),
        "signed_close_vs_session_vwap_points": sign * (close - vwap) if vwap is not None else None,
        "signed_close_vs_source_vwap_points": sign * (close - source_vwap) if source_vwap is not None else None,
        "directional_session_range_position": directional_position,
        "directional_room_to_session_extreme_points": room,
        "session_open_sweep_reclaim": session_open_reclaim,
        "session_vwap": vwap,
    }


def _acceleration_features(windows: dict[str, dict]) -> dict:
    def ratio(a: object, b: object) -> float | None:
        aa = _optional_number(a)
        bb = _optional_number(b)
        if aa is None or bb in (None, 0.0):
            return None
        return aa / bb
    w15, w30, w60, w120 = (windows[str(v)] for v in WINDOWS_MINUTES)
    return {
        "true_range_ratio_15_to_60": ratio(w15.get("mean_true_range_points"), w60.get("mean_true_range_points")),
        "true_range_ratio_30_to_120": ratio(w30.get("mean_true_range_points"), w120.get("mean_true_range_points")),
        "volume_ratio_15_to_60": ratio(w15.get("mean_volume"), w60.get("mean_volume")),
        "volume_ratio_30_to_120": ratio(w30.get("mean_volume"), w120.get("mean_volume")),
    }


def build_feature_rows(candidates: Iterable[dict], loaded: LoadedBars, *, bar_timestamp_mode: str = "start") -> list[dict]:
    validated = [validate_candidate(row) for row in candidates]
    instruments = sorted({row["instrument"] for row in validated})
    if len(instruments) != 1:
        raise ValueError("one run must contain exactly one instrument; do not pool instruments")
    output: list[dict] = []
    for row in sorted(validated, key=lambda r: (parse_ts(r["signal_ts"]), r["candidate_id"])):
        signal_ts = parse_ts(row["signal_ts"])
        windows: dict[str, dict] = {}
        for minutes in WINDOWS_MINUTES:
            rows, complete = _window_rows(loaded, signal_ts=signal_ts, minutes=minutes, bar_timestamp_mode=bar_timestamp_mode)
            prior, prior_complete = _prior_equal_window(loaded, signal_ts=signal_ts, minutes=minutes, bar_timestamp_mode=bar_timestamp_mode)
            windows[str(minutes)] = _window_features(rows, prior_rows=prior, complete=complete, prior_complete=prior_complete, direction=row["direction"])
        feature = dict(row)
        feature["windows"] = windows
        feature["session_context"] = _session_context(loaded, signal_ts=signal_ts, direction=row["direction"], bar_timestamp_mode=bar_timestamp_mode)
        feature["acceleration"] = _acceleration_features(windows)
        output.append(feature)
    _assign_sample_halves(output)
    return output


def _assign_sample_halves(rows: list[dict]) -> None:
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["instrument"], row["strategy"], row["session"], row["direction"])].append(row)
    for values in groups.values():
        values.sort(key=lambda r: (parse_ts(r["signal_ts"]), r["candidate_id"]))
        split = (len(values) + 1) // 2
        for i, row in enumerate(values):
            row["sample_half"] = "H1" if i < split else "H2"


def _flatten_numeric(row: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    def add(prefix: str, obj: dict) -> None:
        for key, value in obj.items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(value, bool):
                out[name] = float(value)
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                out[name] = float(value)
    for minutes, obj in row.get("windows", {}).items():
        add(f"w{minutes}", obj)
    session_values = dict(row.get("session_context", {}))
    session_values.pop("session_vwap", None)
    add("session", session_values)
    add("accel", row.get("acceleration", {}))
    for key in ("baseline_pnl_dollars", "baseline_stop_ticks", "target_r", "relative_volume"):
        value = _optional_number(row.get(key))
        if value is not None:
            out[f"candidate.{key}"] = value
    return out


def _cliffs_delta(xs: list[float], ys: list[float]) -> float | None:
    if not xs or not ys:
        return None
    gt = lt = 0
    for x in xs:
        for y in ys:
            if x > y:
                gt += 1
            elif x < y:
                lt += 1
    return (gt - lt) / (len(xs) * len(ys))


def _numeric_comparison(rows: list[dict]) -> dict:
    feature_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"WIN": [], "LOSS": []})
    for row in rows:
        for name, value in _flatten_numeric(row).items():
            feature_values[name][row["outcome_label"]].append(value)
    result = {}
    for name in sorted(feature_values):
        wins = feature_values[name]["WIN"]
        losses = feature_values[name]["LOSS"]
        med_w = statistics.median(wins) if wins else None
        med_l = statistics.median(losses) if losses else None
        result[name] = {
            "n_win": len(wins),
            "n_loss": len(losses),
            "median_win": med_w,
            "median_loss": med_l,
            "median_delta_win_minus_loss": med_w - med_l if med_w is not None and med_l is not None else None,
            "mean_win": statistics.fmean(wins) if wins else None,
            "mean_loss": statistics.fmean(losses) if losses else None,
            "cliffs_delta_win_vs_loss": _cliffs_delta(wins, losses),
        }
    return result


def _categorical_values(row: dict) -> dict[str, object]:
    values: dict[str, object] = {"sample_half": row.get("sample_half")}
    for minutes in WINDOWS_MINUTES:
        w = row.get("windows", {}).get(str(minutes), {})
        for key in (
            "market_condition_first", "market_condition_last", "trend_direction_last", "trend_strength_last",
            "regime_last", "structural_market_condition_last", "structural_direction_last",
            "ema_stack_aligned_last", "transition_to_trending", "transition_dead_to_trending",
            "directional_sweep_reclaim", "latest_market_structure", "latest_structure_aligned",
            "structure_data_present", "complete",
        ):
            values[f"w{minutes}.{key}"] = w.get(key)
    return values


def _categorical_comparison(rows: list[dict]) -> dict:
    features: dict[str, dict[str, Counter]] = defaultdict(lambda: {"WIN": Counter(), "LOSS": Counter()})
    coverage: dict[str, dict[str, int]] = defaultdict(lambda: {"WIN": 0, "LOSS": 0})
    for row in rows:
        label = row["outcome_label"]
        for name, value in _categorical_values(row).items():
            if value is None:
                continue
            coverage[name][label] += 1
            features[name][label][str(value)] += 1
    return {
        name: {
            "coverage_win": coverage[name]["WIN"],
            "coverage_loss": coverage[name]["LOSS"],
            "values_win": dict(sorted(features[name]["WIN"].items())),
            "values_loss": dict(sorted(features[name]["LOSS"].items())),
        }
        for name in sorted(features)
    }


def _half_numeric(rows: list[dict]) -> dict:
    out = {}
    for half in ("H1", "H2"):
        half_rows = [r for r in rows if r.get("sample_half") == half]
        out[half] = {
            "n": len(half_rows),
            "wins": sum(r["outcome_label"] == "WIN" for r in half_rows),
            "losses": sum(r["outcome_label"] == "LOSS" for r in half_rows),
            "numeric_comparison": _numeric_comparison(half_rows),
        }
    return out


def summarize_feature_rows(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["instrument"], row["strategy"], row["session"], row["direction"])].append(row)
    report_groups = []
    for key in sorted(groups):
        values = sorted(groups[key], key=lambda r: (parse_ts(r["signal_ts"]), r["candidate_id"]))
        labels = Counter(r["outcome_label"] for r in values)
        report_groups.append({
            "instrument": key[0],
            "strategy": key[1],
            "session": key[2],
            "direction": key[3],
            "candidates": len(values),
            "wins": labels["WIN"],
            "losses": labels["LOSS"],
            "has_both_outcomes": labels["WIN"] > 0 and labels["LOSS"] > 0,
            "numeric_comparison": _numeric_comparison(values),
            "categorical_comparison": _categorical_comparison(values),
            "halves": _half_numeric(values),
        })
    return {
        "schema": "asian_precursor_audit_v1",
        "purpose": "descriptive pre-signal winner/loser comparison only",
        "labels_source": "input cohort; never inferred from bars",
        "partition_key": ["instrument", "strategy", "session", "direction"],
        "windows_minutes": list(WINDOWS_MINUTES),
        "groups": report_groups,
    }


def write_jsonl(path: str | Path, rows: Iterable[dict]) -> None:
    path = Path(path)
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str))
            fh.write("\n")


def write_json(path: str | Path, value: object) -> None:
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
