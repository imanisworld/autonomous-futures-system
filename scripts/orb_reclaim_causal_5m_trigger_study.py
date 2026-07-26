#!/usr/bin/env python3
"""ORB Reclaim causal sub-15m trigger study (evidence orchestration only).

Question (operator-scoped follow-up to PR #355): does ORB Reclaim's apparent
at-level edge survive when using ONLY information causally available intrabar
— without hindsight from the completed 15m confirmation bar?

PR #355's LEVEL variant entered at the plan level on bars that later produced
a confirmed 15m signal: its population cannot contain the false triggers a
real faster-than-15m engine would take.  This pass builds the FULL causal
trigger population from the 5m data tier and includes every trigger,
including ones the completed 15m bar never confirmed.

The frozen strategy rule being reconstructed (strategy/signal_engine.py
``_try_orb_reclaim`` + scripts/csv_to_replay.py ``derive_orb_status``):
``reclaimed_high`` is a close-cross UP through the frozen ORB high
(previous close ≤ orb_high < close), LONG-only, VWAP-above gate, GEX gate,
TRENDING-only market-condition gate; plan entry = orb_high + 2 ticks,
stop = max(orb_low − 4 ticks, entry − 80 ticks), target = entry + 2.5R.

Causal 5m architecture simulated (implementable resting stop, no hindsight):
- ORB levels: the generator's own frozen levels (NY open range, 15 minutes,
  frozen until the next NY session start — london/asian bars trade against
  the prior day's frozen ORB).  Tier parity is PROVEN per day before use;
  days where the 5m and 15m tiers disagree on the frozen ORB are EXCLUDED
  and reported.
- A stop-buy works at L = orb_high + 2 ticks whenever, as of the last
  COMPLETED 5m bar: the lane is flat, prev close ≤ orb_high (a cross lies
  ahead), L is above the last known VWAP (the frozen vwap-above gate,
  evaluated at order-working time), the last COMPLETED 15m corpus bar's
  market_condition is TRENDING (frozen gate; the corpus is the canonical
  post-#338 source), and its GEX regime is not positive-gamma.
- The working stop fills causally on the next 5m bar: gap through the level
  → fill at that bar's open ± adverse slippage; intrabar touch → fill at
  L ± adverse slippage.  No completed-15m-bar information is used anywhere
  at or before the fill.
- Fill-bar pessimism (same rule as PR #355 LEVEL): if the fill bar's range
  reaches the ordered stop, the trigger books an immediate LOSS at stop ±
  slip (intrabar order unknowable); a fill-bar range reaching the target is
  NEVER awarded — the position must prove out on later bars via the
  production ``PaperBroker.resolve_position`` walk (pessimistic both-hit,
  stop exits pay adverse slippage, target fills clean), carried across day
  files.  Sequential single-position lane per instrument (no overlapping
  triggers), 1 contract, NO drawdown breaker (population evidence, not the
  frozen account path — #346 owns that).
- Every fill is a row.  ``confirmed_15m_parent`` labels a trigger whose
  instrument/day has a frozen-15m-engine orb_reclaim signal (TRADE setup or
  candidate/blocked-candidate row in the preserved #346 journals) within
  ±20 minutes of the fill; ``confirmed_15m_day`` is the day-level version.
  Failed-later triggers are the false-trigger population the operator asked
  to include.

Proof gates (abort the run if any fails — "no proof, no run"):
1. Bracket reconstruction: every #346 orb_reclaim attempt's journaled
   entry/stop/target must be EXACTLY reproduced from its decision bar's
   corpus orb_high/orb_low via the frozen formula.
2. Tier ORB parity: per-day frozen NY ORB equal across the 5m and 15m
   tiers; unequal days are excluded and enumerated (fail if >5% of days).
3. 15m recall: fraction of #346 orb_reclaim attempts (on included days)
   whose day the causal 5m machine also triggers at or before the 15m
   decision-bar close — reported as a parity metric (not an abort).

Commission ($1.48 RT) at the analysis layer; slippage sensitivity 1/2/3/4
ticks (1 tick = primary).  H1 (≤2026-01-23) is the only window comparable
to #346/#354/#355 (their populations are H1-censored by the #346 breaker);
H2 coverage ends at the 5m tier's last day and carries NO breaker
censoring — both limitations are kept visible.

Pre-registered decision rule (operator's, fixed before results were seen),
evaluated on the FULL causal population, all sessions, net after commission:
- Confirmed-parent subset positive (net > 0, PF > 1) while the full
  population is not material → LOOKAHEAD ARTIFACT / REJECT.
- Full population material — net > 0 AND PF > 1.10 at BOTH 1 and 2 ticks
  AND net > 0 at 3 ticks → PROMISING BUT UNPROVEN, eligible for a separate
  architecture research lane.
- Otherwise (negative or near-breakeven) → REJECT the faster-entry
  hypothesis.
No implementation recommendation is made unless the material branch fires.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from execution.paper_broker import (  # noqa: E402
    NextBarOHLC,
    PaperBroker,
    TICK_SIZE,
    TICK_VALUE,
)

STRATEGY = "orb_reclaim"
DIRECTION = "LONG"  # the frozen strategy is LONG-only
INSTRUMENTS = ("MNQ", "MES")
WINDOW_START = "2025-07-24"
HALVES = {
    "H1": ("2025-07-24", "2026-01-23"),
    "H2": ("2026-01-24", "2026-07-23"),
}
QUARTERS = {
    "Q1": ("2025-07-24", "2025-10-23"),
    "Q2": ("2025-10-24", "2026-01-23"),
    "Q3": ("2026-01-24", "2026-04-23"),
    "Q4": ("2026-04-24", "2026-07-23"),
}
COMMISSION_ROUND_TRIP = 1.48
SLIPPAGE_TIERS = (1.0, 2.0, 3.0, 4.0)
PRIMARY_SLIPPAGE = 1.0
CONTRACTS = 1
ENTRY_OFFSET_TICKS = 2.0
STOP_BELOW_ORB_LOW_TICKS = 4.0
# SignalEngine.MAX_ORB_STOP_TICKS (frozen, per instrument)
MAX_ORB_STOP_TICKS = {"MNQ": 80.0, "MES": 40.0}
# config.min_target_points — SignalEngine._enforce_min_target_distance
# expands any target closer than this to entry ± min_points (frozen values
# asserted against the loaded config in main()).
MIN_TARGET_POINTS = {"MNQ": 15.0, "MES": 15.0}
TARGET_R = 2.5
ORB_BARS_5M = 3  # 15-minute opening range at 5m granularity
CONFIRM_WINDOW_MIN = 20.0
EXCLUDED_DAY_ABORT_FRACTION = 0.05
PR346_CORPUS_TREE_SHA256 = (
    "4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4"
)
BASELINES = {
    "pr346_ioc_orb_reclaim": {
        "attempts": 131,
        "fills": 86,
        "win_rate": 0.291,
        "net_after_commission": -588.28,
        "pf": 0.803,
    },
    "pr354_market_cf_orb_reclaim_all": {"net_after_commission": -243.76, "pf": 0.943},
    "pr354_market_cf_orb_reclaim_nofill": {"net_after_commission": 350.02, "pf": 1.268},
    "pr355_level_orb_reclaim_all": {
        "triggered": 131,
        "net_after_commission": 724.37,
        "pf": 1.187,
    },
    "pr355_level_orb_reclaim_nofill": {"net_after_commission": 1265.78, "pf": 2.138},
}


# ── generic helpers ──────────────────────────────────────────────────────────


def _sha256_tree(root: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*.jsonl"))
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return len(paths), digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def _json_lines(path: Path) -> Iterable[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _period_label(value: str, periods: dict[str, tuple[str, str]]) -> str:
    for label, (start, end) in periods.items():
        if start <= value <= end:
            return label
    return "OUT_OF_RANGE"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and math.isinf(value):
        return "inf"
    return value


def _profit_factor(values: list[float]) -> Optional[float]:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses:
        return round(wins / losses, 6)
    return math.inf if wins else None


def _gex_allows(gex_regime: Optional[str]) -> bool:
    """Mirror of SignalEngine._gex_allows_orb on a raw regime string."""
    if not gex_regime:
        return True
    regime = str(gex_regime).lower()
    return not any(
        key in regime for key in ("positive", "pos_gamma", "long_gamma", "compressed")
    )


# ── data loading ─────────────────────────────────────────────────────────────


def _load_day(path: Path) -> list[dict]:
    return list(_json_lines(path))


class DayIndex:
    """Sorted (day → path) for one instrument in one data tier."""

    def __init__(self, root: Path, instrument: str) -> None:
        self.by_day: dict[str, Path] = {}
        for path in sorted((root / instrument).glob("*.jsonl")):
            self.by_day[path.stem.rsplit("_", 1)[-1]] = path
        self.days = sorted(self.by_day)


def _frozen_ny_orb(bars: list[dict]) -> Optional[tuple[float, float]]:
    """The day's frozen NY ORB from a bar list (last NY bar's orb fields)."""
    for bar in reversed(bars):
        if bar.get("session") == "new_york" and bar.get("orb_high") is not None:
            return float(bar["orb_high"]), float(bar["orb_low"])
    return None


def _frozen_london_orb_15m(bars: list[dict]) -> Optional[tuple[float, float]]:
    """The day's frozen london ORB from 15m corpus bars (last london bar)."""
    for bar in reversed(bars):
        if bar.get("session") == "london" and bar.get("london_orb_high") is not None:
            return float(bar["london_orb_high"]), float(bar["london_orb_low"])
    return None


def _derive_london_orb(
    stream: list[tuple[str, dict]],
) -> list[Optional[tuple[float, float, bool]]]:
    """Causal per-bar london ORB derived from the 5m stream.

    The 5m tier predates london-ORB support and has no london fields; the
    frozen system (replay_engine.py:1064) maps ``state.orb`` to the LONDON
    ORB for london-session bars, so it must be reconstructed here with the
    generator's exact rule (polygon_to_replay.py): initialize on the first
    bar of each london session, accumulate ``ORB_BARS_5M`` bars while the
    session is london, then freeze.  Entry i holds the values known AT BAR
    i's CLOSE: (high, low, done).  Non-london bars carry the last values
    (the state mapping only consumes them during london).
    """
    out: list[Optional[tuple[float, float, bool]]] = []
    high = low = None
    count = 0
    done = False
    prev_session: Optional[str] = None
    for _, bar in stream:
        session = bar.get("session")
        if session == "london" and prev_session != "london":
            high, low = float(bar["high"]), float(bar["low"])
            count = 1
            done = count >= ORB_BARS_5M
        elif high is not None and not done and session == "london":
            high = max(high, float(bar["high"]))
            low = min(low, float(bar["low"]))
            count += 1
            done = count >= ORB_BARS_5M
        prev_session = session
        out.append(None if high is None else (high, low, done))
    return out


# ── phase 0: proofs ──────────────────────────────────────────────────────────


def _proof_bracket_reconstruction(
    corpus_idx: dict[str, DayIndex], logs_root: Path
) -> dict:
    """Reproduce every #346 orb_reclaim journaled plan from corpus ORB fields."""
    checked = 0
    for instrument in INSTRUMENTS:
        tick = TICK_SIZE[instrument]
        for path in sorted((logs_root / instrument).glob("journal_*.jsonl")):
            day = path.stem.removeprefix("journal_")
            bars: Optional[dict[str, dict]] = None
            for entry in _json_lines(path):
                setup = entry.get("setup") or {}
                if entry.get("decision") != "TRADE" or setup.get("strategy") != STRATEGY:
                    continue
                if bars is None:
                    day_path = corpus_idx[instrument].by_day.get(day)
                    if day_path is None:
                        raise RuntimeError(f"corpus day missing for {instrument}/{day}")
                    bars = {
                        _ts(bar["timestamp"]).isoformat(): bar
                        for bar in _load_day(day_path)
                    }
                bar = bars.get(_ts(entry["bar_ts"]).isoformat())
                if bar is None:
                    raise RuntimeError(
                        f"decision bar missing {instrument}/{day} {entry['bar_ts']}"
                    )
                # Session-aware ORB source — the frozen replay state maps
                # london-session bars to the LONDON ORB (replay_engine.py:1064).
                if bar.get("session") == "london":
                    status = bar.get("london_orb_status")
                    src_high = bar.get("london_orb_high")
                    src_low = bar.get("london_orb_low")
                else:
                    status = bar.get("orb_status")
                    src_high = bar.get("orb_high")
                    src_low = bar.get("orb_low")
                if status != "reclaimed_high":
                    raise RuntimeError(
                        f"decision bar not reclaimed_high {instrument}/{day} "
                        f"({bar.get('session')}: {status})"
                    )
                entry_px = float(src_high) + tick * ENTRY_OFFSET_TICKS
                stop_px = max(
                    float(src_low) - tick * STOP_BELOW_ORB_LOW_TICKS,
                    entry_px - tick * MAX_ORB_STOP_TICKS[instrument],
                )
                target_px = entry_px + max(
                    (entry_px - stop_px) * TARGET_R,
                    MIN_TARGET_POINTS[instrument],
                )
                for name, mine, journaled in (
                    ("entry", entry_px, float(setup["entry"])),
                    ("stop", stop_px, float(setup["stop"])),
                    ("target", target_px, float(setup["target"])),
                ):
                    if abs(mine - journaled) > 1e-4:
                        raise RuntimeError(
                            f"bracket reconstruction FAILED {instrument}/{day} "
                            f"{name}: reconstructed {mine} vs journaled {journaled}"
                        )
                checked += 1
    if checked == 0:
        raise RuntimeError("no #346 orb_reclaim attempts found to verify against")
    return {"attempts_verified": checked}


def _proof_tier_orb_parity(
    corpus_idx: dict[str, DayIndex],
    m5_idx: dict[str, DayIndex],
    window_days: dict,
    london_by_day: dict[str, dict[str, tuple[float, float]]],
) -> dict:
    """Per-day frozen ORBs must match across tiers; deviants are excluded.

    NY ORB: the 5m tier's own frozen fields vs the 15m corpus.
    London ORB: this script's causal derivation from 5m OHLC vs the 15m
    corpus's london fields (the 5m tier itself has none).
    """
    excluded: dict[str, list[str]] = {ins: [] for ins in INSTRUMENTS}
    compared = 0
    london_compared = 0
    for instrument in INSTRUMENTS:
        for day in window_days[instrument]:
            bars15 = _load_day(corpus_idx[instrument].by_day[day])
            orb15 = _frozen_ny_orb(bars15)
            orb5 = _frozen_ny_orb(_load_day(m5_idx[instrument].by_day[day]))
            if orb15 is None and orb5 is None:
                # no NY session in either tier (Sunday/holiday) — not drift
                continue
            if orb15 is None or orb5 is None:
                excluded[instrument].append(day)
                continue
            compared += 1
            if abs(orb15[0] - orb5[0]) > 1e-6 or abs(orb15[1] - orb5[1]) > 1e-6:
                excluded[instrument].append(day)
                continue
            london15 = _frozen_london_orb_15m(bars15)
            london5 = london_by_day[instrument].get(day)
            if london15 is None and london5 is None:
                continue
            london_compared += 1
            if (
                london15 is None
                or london5 is None
                or abs(london15[0] - london5[0]) > 1e-6
                or abs(london15[1] - london5[1]) > 1e-6
            ):
                excluded[instrument].append(day)
    total_days = sum(len(window_days[ins]) for ins in INSTRUMENTS)
    total_excluded = sum(len(v) for v in excluded.values())
    if total_days and total_excluded / total_days > EXCLUDED_DAY_ABORT_FRACTION:
        raise RuntimeError(
            f"tier ORB parity failure rate {total_excluded}/{total_days} exceeds "
            f"{EXCLUDED_DAY_ABORT_FRACTION:.0%} — refusing to run on drifted data"
        )
    return {
        "days_compared": compared,
        "london_days_compared": london_compared,
        "excluded_days": excluded,
        "excluded_total": total_excluded,
    }


# ── 15m gate + confirmation lookups ─────────────────────────────────────────


class GateLookup:
    """Last-completed-15m-bar market_condition / gex_regime, causal at time T."""

    def __init__(self, corpus_idx: DayIndex) -> None:
        self._times: list[datetime] = []
        self._rows: list[tuple[Optional[str], Optional[str]]] = []
        for day in corpus_idx.days:
            for bar in _load_day(corpus_idx.by_day[day]):
                self._times.append(_ts(bar["timestamp"]))
                self._rows.append((bar.get("market_condition"), bar.get("gex_regime")))

    def at(self, moment: datetime) -> tuple[Optional[str], Optional[str]]:
        """Fields of the last 15m bar COMPLETED at `moment` (start ≤ moment−15m)."""
        idx = bisect.bisect_right(self._times, moment - timedelta(minutes=15)) - 1
        if idx < 0:
            return None, None
        return self._rows[idx]


class ConfirmLookup:
    """Frozen-15m-engine orb_reclaim signal timestamps per instrument/day."""

    def __init__(self, logs_root: Path, instrument: str) -> None:
        self.by_day: dict[str, list[datetime]] = {}
        for path in sorted((logs_root / instrument).glob("journal_*.jsonl")):
            day = path.stem.removeprefix("journal_")
            stamps: list[datetime] = []
            for entry in _json_lines(path):
                if entry.get("type") == "OUTCOME":
                    continue
                found = (entry.get("setup") or {}).get("strategy") == STRATEGY or any(
                    c.get("strategy") == STRATEGY
                    for c in (
                        (entry.get("candidate_audit") or [])
                        + (
                            (entry.get("blocked_candidate_audit") or {}).get(
                                "candidates"
                            )
                            or []
                        )
                    )
                )
                if found and entry.get("bar_ts"):
                    stamps.append(_ts(entry["bar_ts"]))
            if stamps:
                self.by_day[day] = sorted(stamps)

    def label(self, day: str, fill_ts: datetime) -> tuple[bool, bool]:
        stamps = self.by_day.get(day, [])
        day_hit = bool(stamps)
        parent_hit = any(
            abs((s - fill_ts).total_seconds()) <= CONFIRM_WINDOW_MIN * 60.0
            for s in stamps
        )
        return parent_hit, day_hit


# ── phase 1: causal lane simulation ─────────────────────────────────────────


def _simulate_lane(
    instrument: str,
    stream: list[tuple[str, dict]],
    london: list[Optional[tuple[float, float, bool]]],
    full_day_order: dict[str, int],
    gates: GateLookup,
    confirms: ConfirmLookup,
    *,
    slippage_ticks: float,
) -> tuple[list[dict], dict]:
    tick = TICK_SIZE[instrument]
    tick_value = TICK_VALUE[instrument]
    slip = slippage_ticks * tick
    rows: list[dict] = []
    audit = Counter()

    open_trigger: Optional[dict] = None
    broker: Optional[PaperBroker] = None
    prev_bar: Optional[dict] = None
    prev_session: Optional[str] = None
    developing_left = 0
    prev_day: Optional[str] = None

    def _close_open(reason: str) -> None:
        nonlocal open_trigger, broker
        if open_trigger is not None:
            open_trigger["resolved"] = 0
            open_trigger["open_unresolved"] = 1
            open_trigger["censor_reason"] = reason
            rows.append(open_trigger)
            audit[f"censored_{reason}"] += 1
        open_trigger = None
        broker = None

    for i, (day, bar) in enumerate(stream):
        bar_ts = _ts(bar["timestamp"])
        session = bar.get("session")
        if prev_day is not None and day != prev_day:
            if full_day_order.get(day, 0) - full_day_order.get(prev_day, 0) != 1:
                # an excluded/missing day sits between → causality break
                _close_open("DAY_GAP")
                prev_bar = None
        prev_day = day

        if session == "new_york" and prev_session != "new_york":
            developing_left = ORB_BARS_5M  # ORB resets; skip the opening window
        prev_session = session

        in_developing = developing_left > 0
        if developing_left > 0:
            developing_left -= 1

        # 1) resolve any open position on this bar (production broker walk)
        if open_trigger is not None and broker is not None:
            fill = broker.resolve_position(
                NextBarOHLC(open=bar["open"], high=bar["high"], low=bar["low"])
            )
            open_trigger["bars_after_fill"] += 1
            if fill is not None and fill.result in {"WIN", "LOSS", "BREAKEVEN"}:
                open_trigger["resolved"] = 1
                open_trigger["result"] = fill.result
                open_trigger["exit_reason"] = fill.exit_reason
                open_trigger["exit_price"] = float(fill.exit_price)
                open_trigger["pnl_before_commission"] = round(
                    float(fill.pnl_dollars or 0.0), 2
                )
                open_trigger["pnl_after_commission"] = round(
                    float(fill.pnl_dollars or 0.0) - COMMISSION_ROUND_TRIP, 2
                )
                open_trigger["crossed_day"] = int(
                    open_trigger["date"] != day
                )
                rows.append(open_trigger)
                open_trigger = None
                broker = None
            prev_bar = bar
            continue

        # 2) flat: decide whether a stop order was WORKING during this bar,
        #    using only information completed at this bar's open.
        if prev_bar is None or in_developing:
            prev_bar = bar
            continue
        if session == "london":
            # Frozen state mapping uses the LONDON ORB for london bars
            # (replay_engine.py:1064); the 5m tier lacks the fields, so use
            # this script's causal derivation as of the PREVIOUS bar's close.
            lon_prev = london[i - 1] if i > 0 else None
            if lon_prev is None or not lon_prev[2]:
                audit["skipped_london_orb_developing"] += 1
                prev_bar = bar
                continue
            orb_high, orb_low = lon_prev[0], lon_prev[1]
        else:
            raw_high = bar.get("orb_high")
            raw_low = bar.get("orb_low")
            if (
                raw_high is None
                or raw_low is None
                or prev_bar.get("orb_high") is None
                or abs(float(prev_bar["orb_high"]) - float(raw_high)) > 1e-9
            ):
                prev_bar = bar
                continue  # level unknown or resetting between bars
            orb_high = float(raw_high)
            orb_low = float(raw_low)
        level = orb_high + tick * ENTRY_OFFSET_TICKS
        if float(prev_bar["close"]) > orb_high:
            audit["not_armed_prev_close_above_level"] += 1
            prev_bar = bar
            continue
        vwap_prev = prev_bar.get("vwap")
        if vwap_prev is None or level <= float(vwap_prev):
            audit["gate_blocked_vwap"] += 1
            prev_bar = bar
            continue
        condition, gex_regime = gates.at(bar_ts)
        if condition != "TRENDING":
            audit["gate_blocked_market_condition"] += 1
            prev_bar = bar
            continue
        if not _gex_allows(gex_regime):
            audit["gate_blocked_gex"] += 1
            prev_bar = bar
            continue

        # 3) working stop: does this bar fill it?
        gap_fill = float(bar["open"]) > level
        touched = gap_fill or float(bar["high"]) >= level
        if not touched:
            audit["armed_no_cross"] += 1
            prev_bar = bar
            continue
        fill_entry = (float(bar["open"]) + slip) if gap_fill else (level + slip)
        stop_px = max(
            orb_low - tick * STOP_BELOW_ORB_LOW_TICKS,
            level - tick * MAX_ORB_STOP_TICKS[instrument],
        )
        target_px = level + max(
            (level - stop_px) * TARGET_R, MIN_TARGET_POINTS[instrument]
        )
        if not (stop_px < fill_entry < target_px):
            audit["skipped_bracket_invalid_at_fill"] += 1
            prev_bar = bar
            continue

        parent_hit, day_hit = confirms.label(day, bar_ts)
        trigger = {
            "instrument": instrument,
            "strategy": STRATEGY,
            "direction": DIRECTION,
            "date": day,
            "fill_bar_ts": bar_ts.isoformat(),
            "session": session,
            "half": _period_label(day, HALVES),
            "quarter": _period_label(day, QUARTERS),
            "level": round(level, 4),
            "orb_high": orb_high,
            "orb_low": orb_low,
            "cf_entry_price": round(fill_entry, 4),
            "gap_fill": int(gap_fill),
            "stop": round(stop_px, 4),
            "target": round(target_px, 4),
            "contracts": CONTRACTS,
            "confirmed_15m_parent": int(parent_hit),
            "confirmed_15m_day": int(day_hit),
            "bars_after_fill": 0,
            "crossed_day": 0,
            "censor_reason": None,
            "resolved": 0,
            "open_unresolved": 0,
            "result": None,
            "exit_reason": None,
            "exit_price": None,
            "pnl_before_commission": 0.0,
            "pnl_after_commission": 0.0,
            "decision_bar_immediate_stop": 0,
        }
        audit["triggers_filled"] += 1

        # fill-bar pessimism (identical rule to PR #355 LEVEL)
        if float(bar["low"]) <= stop_px:
            exit_px = stop_px - slip
            ticks = (exit_px - fill_entry) / tick
            trigger.update(
                {
                    "resolved": 1,
                    "result": "LOSS",
                    "exit_reason": "STOP_HIT",
                    "exit_price": round(exit_px, 4),
                    "decision_bar_immediate_stop": 1,
                    "pnl_before_commission": round(ticks * tick_value * CONTRACTS, 2),
                    "pnl_after_commission": round(
                        ticks * tick_value * CONTRACTS - COMMISSION_ROUND_TRIP, 2
                    ),
                }
            )
            rows.append(trigger)
            prev_bar = bar
            continue

        broker = PaperBroker(
            starting_balance=1500.0,
            slippage_ticks=slippage_ticks,
            pessimistic_both_hit=True,
            breakeven_at_1r=False,
            runner_mode=False,
            entry_fill_model="market",
        )
        broker.restore_position(
            instrument=instrument,
            direction=DIRECTION,
            entry=fill_entry,
            stop=stop_px,
            target=target_px,
            contracts=CONTRACTS,
        )
        open_trigger = trigger
        prev_bar = bar

    _close_open("END_OF_DATA")
    return rows, dict(audit)


# ── stats / report ───────────────────────────────────────────────────────────


def _stats(rows: list[dict]) -> dict:
    resolved_rows = [row for row in rows if row["resolved"]]
    gross = [row["pnl_before_commission"] for row in resolved_rows]
    net = [row["pnl_after_commission"] for row in resolved_rows]
    wins = sum(row["result"] == "WIN" for row in resolved_rows)
    return {
        "triggers": len(rows),
        "resolved": len(resolved_rows),
        "open_unresolved": sum(row["open_unresolved"] for row in rows),
        "wins": wins,
        "losses": sum(row["result"] == "LOSS" for row in resolved_rows),
        "breakeven": sum(row["result"] == "BREAKEVEN" for row in resolved_rows),
        "immediate_stops": sum(
            row["decision_bar_immediate_stop"] for row in rows
        ),
        "gap_fills": sum(row["gap_fill"] for row in rows),
        "win_rate": round(wins / len(resolved_rows), 6) if resolved_rows else None,
        "net_before_commission": round(sum(gross), 2),
        "net_after_commission": round(sum(net), 2),
        "expectancy_after_commission": (
            round(statistics.fmean(net), 4) if net else None
        ),
        "profit_factor_after_commission": _profit_factor(net),
        "largest_win_after_commission": round(max(net), 2) if net else None,
        "largest_loss_after_commission": round(min(net), 2) if net else None,
        "crossed_day_resolutions": sum(row["crossed_day"] for row in resolved_rows),
    }


def _group(rows: list[dict], field: str, labels: Iterable[str] | None = None) -> dict:
    keys = list(labels or sorted({str(row[field]) for row in rows}))
    return {
        key: _stats([row for row in rows if str(row[field]) == key]) for key in keys
    }


def _verify_independently(rows: list[dict], stats: dict) -> None:
    resolved = [r for r in rows if r["result"] in {"WIN", "LOSS", "BREAKEVEN"}]
    total = 0.0
    win_count = 0
    for r in resolved:
        tick = TICK_SIZE[r["instrument"]]
        tick_value = TICK_VALUE[r["instrument"]]
        ticks = (r["exit_price"] - r["cf_entry_price"]) / tick  # LONG-only
        dollars = ticks * tick_value * r["contracts"]
        if abs(dollars - r["pnl_before_commission"]) > 0.011:
            raise RuntimeError(
                f"independent price-arithmetic mismatch at {r['fill_bar_ts']}: "
                f"{dollars:.2f} vs {r['pnl_before_commission']:.2f}"
            )
        total += r["pnl_before_commission"] - COMMISSION_ROUND_TRIP
        if r["result"] == "WIN":
            win_count += 1
    if abs(round(total, 2) - stats["net_after_commission"]) > 0.011:
        raise RuntimeError("independent net mismatch")
    if resolved and abs(win_count / len(resolved) - (stats["win_rate"] or 0.0)) > 1e-6:
        raise RuntimeError("independent win-rate mismatch")


def _fmt_money(value: Any) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _fmt_rate(value: Any) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _fmt_pf(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    return "∞" if math.isinf(value) else f"{value:.3f}"


def _table(title: str, blocks: dict[str, dict]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| Scope | Triggers | Resolved | Open | Imm. stop | Gap fills | WR | Net gross | Net after $1.48 RT | Exp net | PF net |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, row in blocks.items():
        lines.append(
            f"| {label} | {row['triggers']} | {row['resolved']} | "
            f"{row['open_unresolved']} | {row['immediate_stops']} | "
            f"{row['gap_fills']} | {_fmt_rate(row['win_rate'])} | "
            f"{_fmt_money(row['net_before_commission'])} | "
            f"{_fmt_money(row['net_after_commission'])} | "
            f"{_fmt_money(row['expectancy_after_commission'])} | "
            f"{_fmt_pf(row['profit_factor_after_commission'])} |"
        )
    lines.append("")
    return lines


def _render_report(results: dict) -> str:
    primary = results["tiers"]["slippage_1"]
    full = primary["cohorts"]["FULL_POPULATION"]
    confirmed = primary["cohorts"]["CONFIRMED_15M_PARENT"]
    failed = primary["cohorts"]["FAILED_LATER_15M"]
    h1 = primary["breakdowns"]["half"].get("H1", {})
    proofs = results["proofs"]
    lines = [
        "# ORB Reclaim causal sub-15m trigger study",
        "",
        f"**Verdict: {results['verdict']}**",
        "",
        f"Pinned code: `{results['meta']['main_sha']}`",
        f"Corpus (gates + proofs): `{results['meta']['corpus_tree_sha256']}` "
        "(byte-identical to PR #346's corpus)",
        f"5m tier: `{results['meta']['m5_tree_sha256']}` "
        f"({results['meta']['m5_files']} files)",
        f"Window: {results['meta']['window'][0]} → {results['meta']['window'][1]} "
        "(corpus ∩ 5m availability, parity-excluded days removed)",
        "",
        "## Question and posture",
        "",
        "- PR #355's at-level edge was measured on 15m-confirmed signals only — "
        "a population a real sub-15m engine cannot know in advance. This pass "
        "builds the FULL causal trigger population at 5m granularity, "
        "including every false trigger the completed 15m bar never confirmed.",
        "- Architecture simulated: a resting stop-buy at the frozen plan level "
        "(ORB high + 2 ticks), working only while causally-known gates pass "
        "(prev 5m close ≤ ORB high; level above last known VWAP; last "
        "COMPLETED 15m corpus bar TRENDING; GEX not positive-gamma), filling "
        "causally (gap → open ± slip, touch → level ± slip). No completed-15m "
        "information is used at or before any fill.",
        "- Frozen bracket (entry/stop/target formulas), $1.48 RT commission, "
        "pessimistic fill-bar handling identical to PR #355 LEVEL, production "
        "`resolve_position` walk, sequential single-position lane per "
        "instrument, 1 contract, no breaker. Evidence orchestration only.",
        "",
        "## Proof gates (all passed before simulation)",
        "",
        f"- Bracket reconstruction: {proofs['bracket']['attempts_verified']}/"
        f"{proofs['bracket']['attempts_verified']} #346 orb_reclaim journaled "
        "plans reproduced exactly from corpus ORB fields via the frozen formula.",
        f"- Tier ORB parity: {proofs['parity']['days_compared']} days compared; "
        f"{proofs['parity']['excluded_total']} excluded for tier disagreement "
        f"({json.dumps(proofs['parity']['excluded_days'], sort_keys=True)}).",
        f"- 15m recall: {proofs['recall']['matched']}/{proofs['recall']['total']} "
        f"({proofs['recall']['rate']:.1%}) of #346 orb_reclaim attempts on "
        "included days have a causal 5m trigger at/before their 15m decision "
        "bar close (parity metric; differences are real gate-timing effects).",
        "",
        "## Pre-registered decision rule (operator's)",
        "",
        "- Confirmed-subset positive while full population not material → "
        "lookahead artifact / reject.",
        "- Full population material (net > 0 AND PF > 1.10 at both 1 and 2 "
        "ticks AND net > 0 at 3 ticks) → PROMISING BUT UNPROVEN, eligible for "
        "a separate architecture research lane.",
        "- Otherwise → reject the faster-entry hypothesis.",
        "",
    ]
    lines += _table(
        "The discriminating split (1-tick slippage)",
        {
            "FULL causal population": full,
            "Confirmed by 15m (±20 min)": confirmed,
            "FAILED later 15m (false triggers)": failed,
        },
    )
    lines += _table("By instrument (1 tick)", primary["breakdowns"]["instrument"])
    lines += _table("By session (1 tick)", primary["breakdowns"]["session"])
    lines += _table("By half (1 tick)", primary["breakdowns"]["half"])
    lines += _table("By quarter (1 tick)", primary["breakdowns"]["quarter"])
    lines += [
        "## Slippage sensitivity (net after commission / PF)",
        "",
        "| Slippage | Full population | Confirmed ±20min | Failed-later |",
        "|---|---:|---:|---:|",
    ]
    for tier in SLIPPAGE_TIERS:
        block = results["tiers"][f"slippage_{tier:g}"]["cohorts"]
        lines.append(
            f"| {tier:g} tick | "
            f"{_fmt_money(block['FULL_POPULATION']['net_after_commission'])} / "
            f"{_fmt_pf(block['FULL_POPULATION']['profit_factor_after_commission'])} | "
            f"{_fmt_money(block['CONFIRMED_15M_PARENT']['net_after_commission'])} / "
            f"{_fmt_pf(block['CONFIRMED_15M_PARENT']['profit_factor_after_commission'])} | "
            f"{_fmt_money(block['FAILED_LATER_15M']['net_after_commission'])} / "
            f"{_fmt_pf(block['FAILED_LATER_15M']['profit_factor_after_commission'])} |"
        )
    lines += [
        "",
        "## Comparison ladder (orb_reclaim slices, net after commission)",
        "",
        "| Pass | Population | Net | PF |",
        "|---|---|---:|---:|",
        "| #346 IOC (system) | 131 attempts, 86 fills | $-588.28 | 0.803 |",
        "| #354 market-at-close | same 131 | $-243.76 | 0.943 |",
        "| #355 LEVEL (15m-confirmed only) | same 131 | $+724.37 | 1.187 |",
        (
            f"| THIS PASS (full causal population) | {full['triggers']} triggers | "
            f"{_fmt_money(full['net_after_commission'])} | "
            f"{_fmt_pf(full['profit_factor_after_commission'])} |"
        ),
        (
            f"| — H1 subset (comparable window) | {h1.get('triggers', 0)} triggers | "
            f"{_fmt_money(h1.get('net_after_commission'))} | "
            f"{_fmt_pf(h1.get('profit_factor_after_commission'))} |"
        ),
        "",
        "## Audit and limitations",
        "",
        f"- Gate/arming audit (1 tick): "
        f"`{json.dumps(results['lane_audit'], sort_keys=True)}`.",
        "- H1 (≤2026-01-23) is the only window comparable to #346/#354/#355 — "
        "their populations are censored by the #346 breaker halting both "
        "instruments in H1. This pass's H2 rows carry NO breaker censoring "
        "and end at the 5m tier's last day; treat cross-pass comparisons as "
        "H1-only.",
        "- Trigger-level evidence: the full 15m system's admission machinery "
        "(confluence ranking against other strategies, session budgets, "
        "one-position-per-account, risk sizing, breaker) is deliberately NOT "
        "reproduced beyond a sequential one-position lane per instrument. "
        "Population evidence, not account-path P&L.",
        "- The market-condition gate uses the last COMPLETED 15m corpus bar — "
        "a real sub-15m engine pays exactly this staleness; the frozen 15m "
        "engine instead sees the (not-yet-known) decision bar's own value.",
        "- Confirmation labels derive from the preserved #346 journals "
        "(TRADE setups + candidate + blocked-candidate rows, ±20 min).",
        "- 1 contract, replay-scale dollars, historical evidence, not live-fill "
        "proof. Nothing here is an implementation recommendation unless the "
        "pre-registered material branch fired.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "python scripts/orb_reclaim_causal_5m_trigger_study.py \\",
        "  --corpus data/replay_corpus_v1_market_condition_fixed \\",
        "  --m5 data/replay_polygon_5m \\",
        "  --logs /private/tmp/corrected_ioc_corpus_logs \\",
        "  --out scripts/orb_reclaim_causal_5m_trigger_results.json \\",
        "  --raw scripts/orb_reclaim_causal_5m_trigger_raw.jsonl \\",
        "  --report docs/orb-reclaim-causal-5m-trigger-study-2026-07-26.md",
        "```",
        "",
    ]
    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--m5", required=True, type=Path)
    parser.add_argument("--logs", required=True, type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "scripts/orb_reclaim_causal_5m_trigger_results.json",
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=REPO / "scripts/orb_reclaim_causal_5m_trigger_raw.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO / "docs/orb-reclaim-causal-5m-trigger-study-2026-07-26.md",
    )
    args = parser.parse_args()

    config = load_config()
    if config.fill_slippage_ticks != PRIMARY_SLIPPAGE:
        raise RuntimeError("canonical fill_slippage_ticks is not 1.0")
    if not config.fill_pessimistic_both_hit:
        raise RuntimeError("canonical pessimistic same-bar handling is disabled")
    for ins in INSTRUMENTS:
        if float(config.min_target_points.get(ins, 0) or 0) != MIN_TARGET_POINTS[ins]:
            raise RuntimeError(
                f"config.min_target_points[{ins}] differs from frozen constant"
            )
    if float(config.orb_stop_ticks.get("MNQ", 0)) != 48.0:
        # Documented divergence: the frozen orb_reclaim formula uses the
        # ORB-low-anchored stop (max(orb_low − 4t, entry − 80t)), NOT
        # orb_stop_ticks (that config drives orb_breakout).  The bracket
        # reconstruction proof below is the arbiter — if the journaled #346
        # plans match the formula, the reconstruction is right.
        pass

    corpus_files, corpus_hash = _sha256_tree(args.corpus)
    if corpus_hash != PR346_CORPUS_TREE_SHA256:
        raise RuntimeError("corpus tree hash differs from PR #346's documented corpus")
    m5_files, m5_hash = _sha256_tree(args.m5)

    corpus_idx = {ins: DayIndex(args.corpus, ins) for ins in INSTRUMENTS}
    m5_idx = {ins: DayIndex(args.m5, ins) for ins in INSTRUMENTS}

    window_days = {
        ins: [
            day
            for day in corpus_idx[ins].days
            if day >= WINDOW_START and day in m5_idx[ins].by_day
        ]
        for ins in INSTRUMENTS
    }
    window_end = max(max(days) for days in window_days.values())

    def _build_stream(ins: str, days: list[str]) -> list[tuple[str, dict]]:
        stream: list[tuple[str, dict]] = []
        for day in days:
            for bar in _load_day(m5_idx[ins].by_day[day]):
                stream.append((day, bar))
        return stream

    # Pre-exclusion streams → causal london ORB derivation → per-day frozen
    # values for the parity proof.
    window_streams = {ins: _build_stream(ins, window_days[ins]) for ins in INSTRUMENTS}
    london_by_day: dict[str, dict[str, tuple[float, float]]] = {}
    for ins in INSTRUMENTS:
        derived = _derive_london_orb(window_streams[ins])
        per_day: dict[str, tuple[float, float]] = {}
        for (day, bar), values in zip(window_streams[ins], derived):
            if bar.get("session") == "london" and values is not None and values[2]:
                per_day[day] = (values[0], values[1])
        london_by_day[ins] = per_day

    proofs: dict[str, Any] = {}
    proofs["bracket"] = _proof_bracket_reconstruction(corpus_idx, args.logs)
    proofs["parity"] = _proof_tier_orb_parity(
        corpus_idx, m5_idx, window_days, london_by_day
    )
    included_days = {
        ins: [
            day
            for day in window_days[ins]
            if day not in set(proofs["parity"]["excluded_days"][ins])
        ]
        for ins in INSTRUMENTS
    }

    gates = {ins: GateLookup(corpus_idx[ins]) for ins in INSTRUMENTS}
    confirms = {ins: ConfirmLookup(args.logs, ins) for ins in INSTRUMENTS}
    lane_streams = {ins: _build_stream(ins, included_days[ins]) for ins in INSTRUMENTS}
    lane_london = {ins: _derive_london_orb(lane_streams[ins]) for ins in INSTRUMENTS}
    full_day_order = {
        ins: {day: idx for idx, day in enumerate(m5_idx[ins].days)}
        for ins in INSTRUMENTS
    }

    tiers: dict[str, dict] = {}
    lane_audit_primary: dict[str, Any] = {}
    primary_rows: list[dict] = []
    for tier in SLIPPAGE_TIERS:
        rows: list[dict] = []
        audits: dict[str, dict] = {}
        for ins in INSTRUMENTS:
            lane_rows, audit = _simulate_lane(
                ins,
                lane_streams[ins],
                lane_london[ins],
                full_day_order[ins],
                gates[ins],
                confirms[ins],
                slippage_ticks=tier,
            )
            rows.extend(lane_rows)
            audits[ins] = audit
        all_stats = _stats(rows)
        _verify_independently(rows, all_stats)
        confirmed_rows = [r for r in rows if r["confirmed_15m_parent"]]
        failed_rows = [r for r in rows if not r["confirmed_15m_parent"]]
        tiers[f"slippage_{tier:g}"] = {
            "cohorts": {
                "FULL_POPULATION": all_stats,
                "CONFIRMED_15M_PARENT": _stats(confirmed_rows),
                "CONFIRMED_15M_DAY": _stats(
                    [r for r in rows if r["confirmed_15m_day"]]
                ),
                "FAILED_LATER_15M": _stats(failed_rows),
            },
            "breakdowns": {
                "strategy": _group(rows, "strategy"),
                "instrument": _group(rows, "instrument", INSTRUMENTS),
                "direction": _group(rows, "direction", (DIRECTION,)),
                "session": _group(rows, "session"),
                "half": _group(rows, "half", HALVES),
                "quarter": _group(rows, "quarter", QUARTERS),
                "confirmed_by_instrument": {
                    ins: _stats(
                        [
                            r
                            for r in confirmed_rows
                            if r["instrument"] == ins
                        ]
                    )
                    for ins in INSTRUMENTS
                },
                "failed_by_instrument": {
                    ins: _stats(
                        [r for r in failed_rows if r["instrument"] == ins]
                    )
                    for ins in INSTRUMENTS
                },
            },
        }
        if tier == PRIMARY_SLIPPAGE:
            primary_rows = rows
            lane_audit_primary = audits

    # Proof 3 (report-only): 15m recall — attempts on included days whose day
    # has a causal 5m trigger at/before the 15m decision-bar close (1-tick).
    recall_total = 0
    recall_matched = 0
    trigger_index: dict[tuple[str, str], list[datetime]] = {}
    for row in primary_rows:
        trigger_index.setdefault((row["instrument"], row["date"]), []).append(
            _ts(row["fill_bar_ts"])
        )
    for ins in INSTRUMENTS:
        for path in sorted((args.logs / ins).glob("journal_*.jsonl")):
            day = path.stem.removeprefix("journal_")
            if day not in set(included_days[ins]):
                continue
            for entry in _json_lines(path):
                setup = entry.get("setup") or {}
                if entry.get("decision") != "TRADE" or setup.get("strategy") != STRATEGY:
                    continue
                recall_total += 1
                decision_close = _ts(entry["bar_ts"]) + timedelta(minutes=15)
                if any(
                    ts <= decision_close
                    for ts in trigger_index.get((ins, day), [])
                ):
                    recall_matched += 1
    proofs["recall"] = {
        "total": recall_total,
        "matched": recall_matched,
        "rate": (recall_matched / recall_total) if recall_total else 0.0,
    }

    def _block(tier: str, cohort: str) -> dict:
        return tiers[f"slippage_{tier}"]["cohorts"][cohort]

    def _material() -> bool:
        t1 = _block("1", "FULL_POPULATION")
        t2 = _block("2", "FULL_POPULATION")
        t3 = _block("3", "FULL_POPULATION")

        def pos(block: dict, pf_floor: float) -> bool:
            pf = block["profit_factor_after_commission"]
            return (
                (block["net_after_commission"] or 0) > 0
                and pf is not None
                and not isinstance(pf, str)
                and pf > pf_floor
            )

        return pos(t1, 1.10) and pos(t2, 1.10) and (t3["net_after_commission"] or 0) > 0

    confirmed_1 = _block("1", "CONFIRMED_15M_PARENT")
    confirmed_positive = (
        (confirmed_1["net_after_commission"] or 0) > 0
        and confirmed_1["profit_factor_after_commission"] is not None
        and not isinstance(confirmed_1["profit_factor_after_commission"], str)
        and confirmed_1["profit_factor_after_commission"] > 1
    )
    if _material():
        verdict = (
            "PROMISING BUT UNPROVEN — FULL CAUSAL SUB-15M POPULATION MATERIALLY "
            "POSITIVE ACROSS REALISTIC SLIPPAGE; ELIGIBLE FOR A SEPARATE "
            "ARCHITECTURE RESEARCH LANE"
        )
    elif confirmed_positive:
        verdict = (
            "LOOKAHEAD ARTIFACT / REJECT — EDGE EXISTS ONLY IN THE 15M-CONFIRMED "
            "SUBSET; THE FULL CAUSAL TRIGGER POPULATION IS NOT MATERIALLY POSITIVE"
        )
    else:
        verdict = (
            "REJECT FASTER-ENTRY HYPOTHESIS — FULL CAUSAL TRIGGER POPULATION "
            "NEGATIVE OR NEAR BREAKEVEN"
        )

    results = {
        "meta": {
            "main_sha": _git("rev-parse", "HEAD"),
            "window": [WINDOW_START, window_end],
            "corpus": str(args.corpus),
            "corpus_files": corpus_files,
            "corpus_tree_sha256": corpus_hash,
            "m5": str(args.m5),
            "m5_files": m5_files,
            "m5_tree_sha256": m5_hash,
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "slippage_tiers": list(SLIPPAGE_TIERS),
            "primary_slippage": PRIMARY_SLIPPAGE,
            "contracts": CONTRACTS,
            "confirm_window_minutes": CONFIRM_WINDOW_MIN,
            "frozen_formula": {
                "entry": "orb_high + 2 ticks",
                "stop": "max(orb_low - 4 ticks, entry - MAX_ORB_STOP_TICKS[ins]) (MNQ 80 / MES 40)",
                "target": "entry + max(2.5 * risk, min_target_points[ins]=15)",
                "direction": DIRECTION,
                "gates": [
                    "prev 5m close <= orb_high (working-stop arming)",
                    "level > last known VWAP",
                    "last completed 15m market_condition == TRENDING",
                    "GEX regime not positive-gamma",
                ],
            },
        },
        "verdict": verdict,
        "proofs": proofs,
        "lane_audit": lane_audit_primary,
        "baselines": BASELINES,
        "tiers": tiers,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(_json_safe(results), indent=2, allow_nan=False) + "\n")
    with args.raw.open("w", encoding="utf-8") as handle:
        for row in sorted(
            primary_rows, key=lambda item: (item["date"], item["fill_bar_ts"])
        ):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    args.report.write_text(_render_report(results).rstrip() + "\n")
    print(
        json.dumps(
            _json_safe(
                {
                    "verdict": verdict,
                    "proofs": proofs,
                    "full_population_1tick": _block("1", "FULL_POPULATION"),
                    "confirmed_parent_1tick": confirmed_1,
                    "failed_later_1tick": _block("1", "FAILED_LATER_15M"),
                }
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
