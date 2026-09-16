#!/usr/bin/env python3
"""Context-permission-layer study: reproducible review tooling. AUDIT / RESEARCH ONLY.

Executes the pre-registered analysis in
``docs/prereg-context-permission-layer-analysis-plan-2026-07-16.md`` against a read-only
copy of the VPS evidence directory.  First run: 2026-09-16
(``docs/context-permission-first-review-2026-09-16.md``, results JSON beside it) — that
document and its JSON are this tool's reproduction target.

Reads evidence only.  Imports nothing from broker, execution, journal, risk, runner or replay
paths; the only repo imports are pure helpers (timestamp parsing, the location collector's pure
``candidate_location`` / ``regime_persistence`` helpers and the contract economics table).  The
resolver's candidate-key and bracket parsers are mirrored here verbatim (importing
``strategy.shadow_resolver`` would drag in the journal writer and the risk engine); the test
module asserts byte parity against the real resolver.  Writes only into ``--out-dir``.  Never
touches the journal, collectors, config or the box.

Phases (``all`` runs them in order):
  gaps     feed-gap ledger from 15m bar history (expected 15m grid inside CME hours)
  join     candidates (shadow_candidates + range_signal) -> SHADOW_OUTCOME, S1 stratum;
           every unjoinable / excluded row is COUNTED, never silently dropped
  analyze  S2 PaperBroker join, feature encodings F1-F12, controls, permutation tests,
           Holm over the fixed 22-test family, walk-forward, winner removal, I1-I10,
           baselines B0/B1/B2, MAE conventions, analysis-layer repairs (F7r/F8r, reported
           separately and excluded from the confirmatory family)

Fixed conventions (pinned; change only via a plan amendment):
  * join key   = strategy.shadow_resolver._candidate_key (mirrored verbatim; parity-tested)
  * exclusions = non-terminal outcomes (NO_FILL/OPEN); rows with timeframe_minutes == 5
                 (5m bars that reached the 15m path, 2026-07-26..28); rows with no
                 context.location_context
  * costs      = 1 adverse tick per side + $1.48 round-turn at the analysis layer for S1;
                 S2 uses the journaled net_dollars (already costed by the lane)
  * R          = net dollars / (initial stop distance in ticks x tick value)
  * p-values   = permutation, labels shuffled within session x family strata, one-sided
                 in the PRE-REGISTERED direction for directional tests, two-sided otherwise;
                 numpy default_rng(seed) with seed pinned in the output
  * Holm       = over exactly 22 tests (12 univariate + 10 interactions); repaired
                 variants (suffix ``r``) are never in the family
  * MAE        = raw_bar_mae from bar history; execution_capped_mae caps STOP_HIT rows at
                 stop distance + one tick (plan MAE ruling 2026-07-16)

Usage:
    python3 scripts/context_permission_prereg_review.py all \
        --logs-root /path/to/logs-snapshot --out-dir /path/to/out
    python3 scripts/context_permission_prereg_review.py analyze --logs-root ... --out-dir ... \
        --nperm 10000 --nperm-interactions 2000 --seed 20260916
"""
from __future__ import annotations

import argparse
import bisect
import collections
import glob
import json
import math
import os
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

from config.futures_contracts import contract_economics  # noqa: E402  (pure table)
from context.bar_history import _parse_dt  # noqa: E402  (pure parser)
from context.location_context import candidate_location, regime_persistence  # noqa: E402

TOOL_VERSION = "cpr-review-v1"
_ET = ZoneInfo("America/New_York")
INSTRUMENTS = ("MNQ", "MES")
DEFAULT_START = "2026-07-16"          # location collector + observer go-live (#291)
DEFAULT_COMMISSION_RT = 1.48
DEFAULT_SLIP_TICKS_RT = 2.0           # one adverse tick per side
DEFAULT_SEED = 20260916
HOLM_FAMILY = 22
MIN_UNIVARIATE_TOTAL, MIN_UNIVARIATE_LEVEL = 40, 15
MIN_INTERACTION_TOTAL, MIN_INTERACTION_CELL = 60, 12
MIN_AVAILABILITY = 0.60
S2_SOURCES = (
    # (evidence file, lane label, instrument, family)
    ("mnq_strat_22_reversal_evidence.jsonl", "strat_22_reversal", "MNQ", "strat"),
    ("mes_trend_consolidation_break_evidence.jsonl", "trend_consolidation_break", "MES", "strat_other"),
)
STRAT_ADJACENT = {
    "trend_consolidation_break_observed", "impulse_first_pullback_observed",
    "ema_pullback_trend", "transition_failed_breakdown_reclaim",
}


# ────────────────────────────────────────────────────────────────────────────
# resolver identity — verbatim mirrors of strategy/shadow_resolver.py (parity-tested)
# ────────────────────────────────────────────────────────────────────────────
def _population_fields(*sources: Any) -> tuple[Optional[str], Optional[str]]:
    epoch: Optional[str] = None
    variant: Optional[str] = None
    for src in sources:
        if not isinstance(src, dict):
            continue
        if epoch is None and src.get("evidence_epoch"):
            epoch = str(src["evidence_epoch"])
        if variant is None and src.get("variant"):
            variant = str(src["variant"])
    return epoch, variant


def _candidate_key(lane: str, instrument: str, bar_ts: str, strategy: str, direction: str,
                   entry: float, evidence_epoch: Optional[str] = None, variant: Optional[str] = None) -> str:
    key = f"{lane}|{instrument}|{bar_ts}|{strategy}|{direction}|{entry}"
    if evidence_epoch is not None or variant is not None:
        key += f"|epoch={evidence_epoch}|variant={variant}"
    return key


def _bracket(cand: Any, strategy_field: str) -> Optional[dict]:
    if not isinstance(cand, dict):
        return None
    direction = str(cand.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return None
    try:
        entry = float(cand["entry"])
        stop = float(cand["stop"])
        target = float(cand["target"])
    except (KeyError, TypeError, ValueError):
        return None
    strategy = str(cand.get(strategy_field) or "unknown")
    return {"strategy": strategy, "direction": direction, "entry": entry, "stop": stop, "target": target}


def _range_bracket(signal: dict) -> Optional[dict]:
    direction = str(signal.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return None
    entry = signal.get("entry_candidate")
    stop = signal.get("stop_candidate")
    target = signal.get("target_candidate")
    if entry is None or stop is None or target is None:
        return None
    try:
        entry, stop, target = float(entry), float(stop), float(target)
    except (TypeError, ValueError):
        return None
    strategy = str(signal.get("signal_type") or "range_signal").lower()
    return {"strategy": strategy, "direction": direction, "entry": entry, "stop": stop, "target": target}


# ────────────────────────────────────────────────────────────────────────────
# shared helpers
# ────────────────────────────────────────────────────────────────────────────
def _read_jsonl(path: str | Path):
    with open(path) as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _journal_files(logs_root: str | Path) -> list[str]:
    return sorted(glob.glob(str(Path(logs_root) / "journal_*.jsonl")))


def _journal_day(path: str) -> str:
    return os.path.basename(path)[8:18]


def family_of(strategy: str | None) -> str:
    s = strategy or ""
    if s.startswith("strat_"):
        return "strat"
    if s in STRAT_ADJACENT:
        return "strat_other"
    if s.startswith("orb"):
        return "orb"
    if s.startswith("vwap"):
        return "vwap"
    if s.startswith("range"):
        return "range"
    return "other"


def _is_decision_row(r: dict) -> bool:
    return isinstance(r, dict) and "decision" in r and not r.get("type")


def _mean(x) -> float:
    x = list(x)
    return float(np.mean(x)) if x else float("nan")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True, default=str)


# ────────────────────────────────────────────────────────────────────────────
# phase: gaps
# ────────────────────────────────────────────────────────────────────────────
def in_cme_hours(t: datetime) -> bool:
    """Expected-bar grid: Sun 18:00 ET .. Fri 17:00 ET, minus the daily 17:00-18:00 ET break."""
    e = t.astimezone(_ET)
    wd, hm = e.weekday(), e.hour * 60 + e.minute
    if wd == 5:
        return False
    if wd == 6:
        return hm >= 18 * 60
    if wd == 4:
        return hm < 17 * 60
    return not (17 * 60 <= hm < 18 * 60)


def build_gap_ledger(logs_root: str | Path) -> dict:
    ledger: dict = {}
    bad = collections.Counter()
    for inst in INSTRUMENTS:
        ts: set = set()
        for f in sorted(glob.glob(str(Path(logs_root) / f"bars_{inst}_*.jsonl"))):
            for b in _read_jsonl(f):
                t = _parse_dt(str(b.get("ts") or ""))
                if t is None or str(b.get("timeframe") or "15") != "15":
                    bad[inst] += 1
                    continue
                ts.add(t)
        tss = sorted(ts)
        gaps, total = [], 0
        for a, b in zip(tss, tss[1:]):
            if (b - a).total_seconds() / 60 <= 15:
                continue
            missing, t = 0, a + timedelta(minutes=15)
            while t < b:
                if in_cme_hours(t):
                    missing += 1
                t += timedelta(minutes=15)
            if missing:
                gaps.append({"start": a.isoformat(), "end": b.isoformat(),
                             "missing_bars": missing, "minutes": missing * 15})
                total += missing * 15
        ledger[inst] = {
            "bars": len(tss),
            "first": tss[0].isoformat() if tss else None,
            "last": tss[-1].isoformat() if tss else None,
            "gap_count": len(gaps), "gap_minutes": total,
            "non_15m_or_unparseable_rows": bad[inst], "gaps": gaps,
        }
    return ledger


# ────────────────────────────────────────────────────────────────────────────
# phase: join (S1)
# ────────────────────────────────────────────────────────────────────────────
def resolved_economics(inst: str, entry: float, stop: float, pnl_ticks: float,
                       commission_rt: float, slip_ticks_rt: float) -> dict:
    """Net-after-cost dollars and R for a resolved bracket (pinned cost convention)."""
    tick, tv = contract_economics(inst)
    stop_ticks = abs(entry - stop) / tick
    gross = pnl_ticks * tv
    net = gross - slip_ticks_rt * tv - commission_rt
    risk = stop_ticks * tv
    return {
        "stop_ticks": stop_ticks, "risk_usd": risk, "gross_usd": gross, "net_usd": net,
        "gross_R": (pnl_ticks / stop_ticks) if stop_ticks else None,
        "net_R": (net / risk) if risk else None,
    }


def candidates_from_row(row: dict, inst: str) -> list[dict]:
    """Same extraction the live resolver performs, plus the per-candidate location block."""
    ts = row.get("ts") or row.get("timestamp")
    out: list[dict] = []
    for cand in row.get("shadow_candidates") or []:
        parsed = _bracket(cand, "strategy")
        if not parsed:
            continue
        epoch, variant = _population_fields(cand, row)
        out.append({"lane": "shadow_setups", **parsed, "location": cand.get("location"),
                    "key": _candidate_key("shadow_setups", inst, ts, parsed["strategy"],
                                          parsed["direction"], parsed["entry"], epoch, variant)})
    for field in ("range_signal", "shadow_range_signal"):
        rs = row.get(field)
        if isinstance(rs, dict):
            parsed = _range_bracket(rs)
            if parsed:
                epoch, variant = _population_fields(rs, row)
                out.append({"lane": "range_signal", **parsed, "location": None,
                            "key": _candidate_key("range_signal", inst, ts, parsed["strategy"],
                                                  parsed["direction"], parsed["entry"], epoch, variant)})
            break
    return out


OBSERVER_FIELDS = ("trend_persistence", "mnq_mes_agreement", "overnight_range_location",
                   "supply_demand_confluence", "key_level_confluence", "impulse_state",
                   "structural_regime", "market_condition", "gex", "vwap", "session", "timeframe")


def build_join(logs_root: str | Path, start: str, commission_rt: float,
               slip_ticks_rt: float) -> tuple[list[dict], dict]:
    stats: collections.Counter = collections.Counter({k: 0 for k in (
        "shadow_outcome_rows", "shadow_outcome_duplicate_keys", "observer_rows", "observer_duplicate_keys",
        "decision_rows", "decision_rows_other_instrument", "decision_rows_with_location_context",
        "decision_rows_with_observer_row", "candidates_total", "candidates_missing_location_context",
        "candidates_missing_candidate_location", "candidate_outcome_None")})
    # SHADOW_OUTCOME index (first row per key wins; duplicates counted)
    outcomes: dict[str, dict] = {}
    for f in _journal_files(logs_root):
        for r in _read_jsonl(f):
            if r.get("type") != "SHADOW_OUTCOME":
                continue
            stats["shadow_outcome_rows"] += 1
            k = r.get("candidate_key")
            if k in outcomes:
                stats["shadow_outcome_duplicate_keys"] += 1
                continue
            outcomes[k] = r
    # observer index by (instrument, timestamp, timeframe) — 5m rows share timestamps with 15m rows
    observer: dict[tuple, dict] = {}
    obs_path = Path(logs_root) / "strategy_context_observations.jsonl"
    if obs_path.exists():
        for r in _read_jsonl(obs_path):
            stats["observer_rows"] += 1
            k = (r.get("instrument"), r.get("timestamp"), str(r.get("timeframe") or ""))
            if k in observer:
                stats["observer_duplicate_keys"] += 1
            observer[k] = r
    rows: list[dict] = []
    for f in _journal_files(logs_root):
        day = _journal_day(f)
        if day < start:
            continue
        for r in _read_jsonl(f):
            if not _is_decision_row(r):
                continue
            inst = r.get("instrument")
            if inst not in INSTRUMENTS:
                stats["decision_rows_other_instrument"] += 1
                continue
            stats["decision_rows"] += 1
            ts = r.get("ts") or r.get("timestamp")
            ctx = r.get("context") if isinstance(r.get("context"), dict) else {}
            loc = ctx.get("location_context")
            tf = r.get("timeframe_minutes")
            if loc:
                stats["decision_rows_with_location_context"] += 1
            ob = observer.get((inst, ctx.get("timestamp"), "15" if tf in (15, None) else str(tf)))
            if ob:
                stats["decision_rows_with_observer_row"] += 1
            for c in candidates_from_row(r, inst):
                stats["candidates_total"] += 1
                o = outcomes.get(c["key"])
                result = (o.get("shadow_outcome") or {}).get("result") if o else None
                stats[f"candidate_outcome_{result}"] += 1
                if not loc:
                    stats["candidates_missing_location_context"] += 1
                if c["lane"] == "shadow_setups" and not c["location"]:
                    stats["candidates_missing_candidate_location"] += 1
                row = {
                    "day": day, "ts": ts, "bar_ts": ctx.get("timestamp"), "timeframe_minutes": tf,
                    "instrument": inst, "session": r.get("session"),
                    "market_condition": r.get("market_condition"), "regime": r.get("regime"),
                    "decision": r.get("decision"), "lane": c["lane"], "strategy": c["strategy"],
                    "direction": c["direction"], "entry": c["entry"], "stop": c["stop"],
                    "target": c["target"], "candidate_key": c["key"], "loc": loc,
                    "cand_loc": c["location"],
                    "observer": ({k: ob.get(k) for k in OBSERVER_FIELDS} if ob else None),
                    "context_market_condition": ctx.get("market_condition"),
                    "structural_mc": ctx.get("structural_market_condition"),
                    "gex": ctx.get("gex"), "signa": ctx.get("signa"),
                    "result": result, "outcome": (o or {}).get("shadow_outcome"),
                    "resolved_at": (o or {}).get("resolved_at_bar_ts"),
                    "forward_bars_used": (o or {}).get("forward_bars_used"),
                }
                if result in ("WIN", "LOSS"):
                    row.update(resolved_economics(inst, c["entry"], c["stop"],
                                                  float(row["outcome"]["pnl_ticks"]),
                                                  commission_rt, slip_ticks_rt))
                rows.append(row)
    return rows, dict(sorted(stats.items()))


# ────────────────────────────────────────────────────────────────────────────
# phase: analyze
# ────────────────────────────────────────────────────────────────────────────
def _context_index(logs_root: str | Path, start: str) -> dict[tuple, dict]:
    """(instrument, context.timestamp) -> 15m decision row with location context."""
    idx: dict[tuple, dict] = {}
    for f in _journal_files(logs_root):
        if _journal_day(f) < start:
            continue
        for r in _read_jsonl(f):
            if not _is_decision_row(r) or r.get("instrument") not in INSTRUMENTS:
                continue
            if r.get("timeframe_minutes") == 5:
                continue
            ctx = r.get("context") if isinstance(r.get("context"), dict) else {}
            if ctx.get("location_context"):
                idx[(r["instrument"], ctx.get("timestamp"))] = r
    return idx


def build_s2(logs_root: str | Path, start: str, ctx_index: dict[tuple, dict]) -> tuple[list[dict], dict]:
    """PaperBroker OUTCOME rows joined to the decision row's location context via entry_ts."""
    stats: collections.Counter = collections.Counter()
    rows: list[dict] = []
    for fn, lane, inst, fam in S2_SOURCES:
        path = Path(logs_root) / fn
        if not path.exists():
            stats[f"{lane}_file_missing"] += 1
            continue
        for o in _read_jsonl(path):
            if o.get("event") != "OUTCOME":
                continue
            stats[f"{lane}_outcomes"] += 1
            if o.get("result") not in ("WIN", "LOSS"):
                stats[f"{lane}_nonterminal_{o.get('result')}"] += 1
                continue
            if str(o.get("entry_ts") or "")[:10] < start:
                stats[f"{lane}_pre_start"] += 1
                continue
            j = ctx_index.get((inst, o.get("entry_ts")))
            if j is None:
                stats[f"{lane}_no_context_row"] += 1
                continue
            stop_raw = o.get("stop") or o.get("planned_stop")
            if stop_raw is None:
                stats[f"{lane}_no_stop_field"] += 1
                continue
            entry = float(o.get("actual_entry") or o.get("requested_entry"))
            stop = float(stop_raw)
            tick, tv = contract_economics(inst)
            stop_ticks = abs(entry - stop) / tick
            if stop_ticks <= 0:
                stats[f"{lane}_zero_stop"] += 1
                continue
            loc = j["context"]["location_context"]
            net, gross = float(o["net_dollars"]), float(o["gross_dollars"])
            rows.append({
                "day": str(o["entry_ts"])[:10], "ts": j["ts"], "bar_ts": o["entry_ts"],
                "timeframe_minutes": j.get("timeframe_minutes"), "instrument": inst,
                "session": j.get("session"), "market_condition": j.get("market_condition"),
                "regime": j.get("regime"), "decision": j.get("decision"), "lane": lane,
                "strategy": lane, "family": fam, "direction": o["direction"], "entry": entry,
                "stop": stop, "target": o.get("target") or o.get("planned_target"), "loc": loc,
                "cand_loc": candidate_location(loc, direction=o["direction"], entry=entry,
                                               target=o.get("target") or o.get("planned_target")),
                "observer": None, "context_market_condition": j["context"].get("market_condition"),
                "gex": j["context"].get("gex"), "signa": j["context"].get("signa"),
                "result": o["result"], "stop_ticks": stop_ticks, "risk_usd": stop_ticks * tv,
                "gross_usd": gross, "net_usd": net, "gross_R": gross / (stop_ticks * tv),
                "net_R": net / (stop_ticks * tv), "stratum": "S2",
                "resolved_at": o.get("exit_ts"), "mae_pts": o.get("maximum_adverse_excursion_points"),
                "candidate_key": o.get("candidate_key"),
            })
    return rows, dict(sorted(stats.items()))


def select_s1(joined: list[dict]) -> tuple[list[dict], dict]:
    """Apply the pinned exclusions; count every excluded row."""
    excl: collections.Counter = collections.Counter()
    out = []
    for r in joined:
        if r.get("result") not in ("WIN", "LOSS"):
            excl["unresolved_or_nofill_open"] += 1
            continue
        if r.get("timeframe_minutes") == 5:
            excl["timeframe_5m_rows"] += 1
            continue
        if not r.get("loc"):
            excl["missing_location_context"] += 1
            continue
        r = dict(r, stratum="S1", family=family_of(r["strategy"]))
        out.append(r)
    return out, dict(sorted(excl.items()))


def _attach_times(r: dict) -> None:
    r["bar_dt"] = _parse_dt(str(r.get("bar_ts") or r["ts"]))
    r["row_dt"] = _parse_dt(str(r["ts"]))
    r["resolved_dt"] = _parse_dt(str(r["resolved_at"])) if r.get("resolved_at") else None


def flag_gaps(rows: list[dict], ledger: dict) -> collections.Counter:
    gaps = {i: [(_parse_dt(g["start"]), _parse_dt(g["end"])) for g in ledger[i]["gaps"]] for i in ledger}

    def overlaps(inst, a, b):
        if a is None or b is None:
            return False
        return any(gs < b and ge > a for gs, ge in gaps.get(inst, []))

    counts: collections.Counter = collections.Counter()
    for r in rows:
        end = r["resolved_dt"] or (r["bar_dt"] + timedelta(hours=8))
        r["feed_gap_contaminated"] = overlaps(r["instrument"], r["bar_dt"], end)
        r["ctx_gap_4h"] = overlaps(r["instrument"], r["bar_dt"] - timedelta(hours=4), r["bar_dt"])
        e = r["bar_dt"].astimezone(_ET)
        reopen = e.replace(hour=18, minute=0, second=0, microsecond=0)
        if e < reopen:
            reopen -= timedelta(days=1)
        r["ctx_gap_overnight"] = overlaps(r["instrument"], reopen, r["bar_dt"])
        r["ctx_gap_zone1h"] = overlaps(r["instrument"], r["bar_dt"] - timedelta(days=5), r["bar_dt"])
        r["ctx_gap_zone4h"] = overlaps(r["instrument"], r["bar_dt"] - timedelta(days=10), r["bar_dt"])
        for k in ("feed_gap_contaminated", "ctx_gap_4h", "ctx_gap_overnight", "ctx_gap_zone1h", "ctx_gap_zone4h"):
            counts[(r["stratum"], k)] += int(r[k])
    return counts


def attach_mae(rows: list[dict], logs_root: str | Path) -> None:
    """raw_bar_mae from 15m bars after the decision bar up to resolution; execution-capped on LOSS."""
    bars: dict[str, list] = collections.defaultdict(list)
    for inst in INSTRUMENTS:
        for f in sorted(glob.glob(str(Path(logs_root) / f"bars_{inst}_*.jsonl"))):
            for b in _read_jsonl(f):
                t = _parse_dt(str(b.get("ts") or ""))
                if t and str(b.get("timeframe") or "15") == "15":
                    bars[inst].append((t, float(b["high"]), float(b["low"])))
        bars[inst].sort()
    keys = {inst: [x[0] for x in bars[inst]] for inst in bars}
    for r in rows:
        tick, _ = contract_economics(r["instrument"])
        if r.get("mae_pts") is not None:
            r["raw_bar_mae"] = float(r["mae_pts"])
        else:
            arr, ks = bars[r["instrument"]], keys[r["instrument"]]
            i = bisect.bisect_right(ks, r["bar_dt"])
            j = bisect.bisect_right(ks, r["resolved_dt"]) if r["resolved_dt"] else len(arr)
            seg = arr[i:j]
            if not seg:
                r["raw_bar_mae"] = None
            else:
                r["raw_bar_mae"] = max(0.0, max(
                    (r["entry"] - lo) if r["direction"] == "LONG" else (hi - r["entry"])
                    for _, hi, lo in seg))
        stop_pts = abs(r["entry"] - r["stop"])
        if r["raw_bar_mae"] is None:
            r["exec_capped_mae"] = None
        else:
            r["exec_capped_mae"] = (min(r["raw_bar_mae"], stop_pts + tick)
                                    if r["result"] == "LOSS" else r["raw_bar_mae"])


def attach_persistence(rows: list[dict], logs_root: str | Path) -> None:
    """F8 exactly as pre-registered: the repo's offline regime_persistence() on the journal."""
    cache: dict = {}
    for r in rows:
        if (r["loc"] or {}).get("regime_at_signal") != "TRENDING":
            r["F8"] = None
            continue
        key = (r["instrument"], r["ts"])
        if key not in cache:
            p = regime_persistence(Path(logs_root) / f"journal_{r['day']}.jsonl", r["instrument"], r["row_dt"])
            if p.get("+30m") is None:   # +30m may land in the next day's file around midnight UTC
                nd = (r["row_dt"] + timedelta(minutes=30)).date().isoformat()
                nd_path = Path(logs_root) / f"journal_{nd}.jsonl"
                if nd != r["day"] and nd_path.exists():
                    p["+30m"] = regime_persistence(nd_path, r["instrument"], r["row_dt"]).get("+30m")
            cache[key] = p
        p = cache[key]
        r["F8"] = None if p.get("+30m") is None else (p["+30m"] == "TRENDING")


def attach_repairs(rows: list[dict], ctx_index: dict[tuple, dict]) -> None:
    """Analysis-layer repair (plan §5): the same F7/F8 constructs re-derived from the payload-level
    context.market_condition, which survives the #376 universe short-circuit that blanks the
    top-level field for MES since 2026-07-28.  Reported separately; never confirmatory (§18)."""
    labels: dict[str, list] = collections.defaultdict(list)
    for (inst, _cts), j in ctx_index.items():
        lab = (j.get("context") or {}).get("market_condition")
        if lab:
            labels[inst].append((_parse_dt(str(j["ts"])), lab))
    for k in labels:
        labels[k].sort()

    def label_at(inst, when, tol_s=8 * 60):
        best = None
        for t, lab in labels[inst]:
            d = abs((t - when).total_seconds())
            if d <= tol_s and (best is None or d < best[0]):
                best = (d, lab)
        return best[1] if best else None

    def other_before(inst, when, max_age=1800):
        other = "MES" if inst == "MNQ" else "MNQ"
        best = None
        for t, lab in labels[other]:
            if t <= when and (best is None or t > best[0]):
                best = (t, lab)
        if best is None or (when - best[0]).total_seconds() > max_age:
            return None
        return best[1]

    for r in rows:
        sig = (r["loc"] or {}).get("regime_at_signal")
        ol = other_before(r["instrument"], r["row_dt"])
        r["F7r"] = None if ol is None or sig is None else bool(ol == "TRENDING" and ol == sig)
        if sig != "TRENDING":
            r["F8r"] = None
        else:
            l30 = label_at(r["instrument"], r["row_dt"] + timedelta(minutes=30))
            r["F8r"] = None if l30 is None else (l30 == "TRENDING")


# feature encodings: True = the pre-registered "hypothesised better / allowed" side, False = other,
# None = unavailable.  Contrasts are exactly plan §7.
def _L(r):
    return r["loc"] or {}


def _Z(r, tf):
    return (_L(r).get("zones") or {}).get(tf) or {}


def _nearest_4h(r):
    z = _Z(r, "4h")
    zs = [x for x in (z.get("supply"), z.get("demand")) if x]
    return min(zs, key=lambda x: x["distance_points"]) if zs else None


def _f5(r):
    nk, m = _L(r).get("nearest_key_level"), _L(r).get("mtr_15m_points")
    return None if not nk or not m else nk["distance_points"] <= 0.5 * m


def _f6(r):
    lv, m, px = _L(r).get("levels") or {}, _L(r).get("mtr_15m_points"), _L(r).get("observed_price")
    if lv.get("onh") is None or lv.get("onl") is None or not m or px is None:
        return None
    return px >= lv["onh"] - 0.5 * m or px <= lv["onl"] + 0.5 * m


def _f7(r):
    oi, ag = _L(r).get("other_instrument"), _L(r).get("regime_agreement")
    return None if not oi else bool(oi.get("market_condition") == "TRENDING" and ag is True)


FEATURES: dict[str, tuple[str, Callable[[dict], Optional[bool]], str]] = {
    "F1": ("middle_of_range: edge(False) vs middle(True) [H1 dir]",
           lambda r: None if _L(r).get("middle_of_range") is None else (not _L(r)["middle_of_range"]), "directional"),
    "F2": ("1H relation: inside/approaching vs middle [two-sided]",
           lambda r: None if not _Z(r, "1h").get("relation") else _Z(r, "1h")["relation"] != "middle", "two-sided"),
    "F3": ("4H relation: inside/approaching vs middle [two-sided]",
           lambda r: None if not _Z(r, "4h").get("relation") else _Z(r, "4h")["relation"] != "middle", "two-sided"),
    "F4": ("4H nearest-zone fresh(0 tests) vs tested [H6 dir]",
           lambda r: None if _nearest_4h(r) is None else _nearest_4h(r)["tests"] == 0, "directional"),
    "F5": ("key-level proximity <=0.5xMTR vs beyond [two-sided]", _f5, "two-sided"),
    "F6": ("overnight: at/beyond extreme vs inside [H7 dir]", _f6, "directional"),
    "F7": ("other instrument TRENDING & agreeing vs not [H2 dir]", _f7, "directional"),
    "F8": ("TRENDING persistent at +30m vs transient [H3 dir]", lambda r: r.get("F8"), "directional"),
    "F9": ("impulse pre/developing vs late_entry [H4 dir]",
           lambda r: None if not (_L(r).get("impulse") or {}).get("phase") else _L(r)["impulse"]["phase"] != "late_entry", "directional"),
    "F10": ("direction/zone aligned vs against (neutral excluded) [two-sided]",
            lambda r: {"aligned": True, "against": False}.get((r.get("cand_loc") or {}).get("direction_zone_alignment")), "two-sided"),
    "F11": ("target clear vs blocked by opposing zone [H5 dir]",
            lambda r: None if (r.get("cand_loc") or {}).get("target_blocked_by_opposing_zone") is None
            else (not r["cand_loc"]["target_blocked_by_opposing_zone"]), "directional"),
    "F7r": ("[analysis-layer repair] other instrument TRENDING & agreeing vs not, from context.market_condition",
            lambda r: r.get("F7r"), "directional"),
    "F8r": ("[analysis-layer repair] TRENDING persistent at +30m vs transient, from context.market_condition",
            lambda r: r.get("F8r"), "directional"),
    "F12": ("GEX regime positive vs negative [two-sided]",
            lambda r: None if not (r.get("gex") or {}).get("gex_regime")
            else str(r["gex"]["gex_regime"]).lower().startswith("pos"), "two-sided"),
}
INTERACTIONS = {
    "I1": ("F8", "session"), "I2": ("F7", "session"), "I3": ("F1", "F9"), "I4": ("F10", "F4"),
    "I5": ("F6", "direction"), "I6": ("F11", "F5"), "I7": ("F8", "F7"), "I8": ("F1", "session"),
    "I9": ("F9", "F8"), "I10": ("F2", "F3"),
    # repaired variants — reported, never in the confirmatory family
    "I1r": ("F8r", "session"), "I2r": ("F7r", "session"), "I7r": ("F8r", "F7r"), "I9r": ("F9", "F8r"),
}


def _strata(r):
    return (r["session"], r["family"])


def effect(rs, fid, metric="net_R"):
    a = [r[metric] for r in rs if r[fid] is True]
    b = [r[metric] for r in rs if r[fid] is False]
    return _mean(a) - _mean(b), len(a), len(b), _mean(a), _mean(b)


def perm_p(rs, fid, kind, rng, nperm, metric="net_R"):
    rs = [r for r in rs if r[fid] is not None]
    if not rs:
        return float("nan")
    obs = effect(rs, fid, metric)[0]
    groups = collections.defaultdict(list)
    for i, r in enumerate(rs):
        groups[_strata(r)].append(i)
    labels = np.array([r[fid] for r in rs], dtype=bool)
    vals = np.array([r[metric] for r in rs], dtype=float)
    idx_groups = [np.array(g) for g in groups.values()]
    count = 0
    for _ in range(nperm):
        lab = labels.copy()
        for g in idx_groups:
            lab[g] = lab[g][rng.permutation(len(g))]
        d = vals[lab].mean() - vals[~lab].mean() if lab.any() and (~lab).any() else 0.0
        if kind == "directional":
            count += d >= obs          # one-sided in the PRE-REGISTERED direction
        else:
            count += abs(d) >= abs(obs)
    return (count + 1) / (nperm + 1)


def walk_forward(rs, fid):
    rs = sorted([r for r in rs if r[fid] is not None], key=lambda r: r["bar_dt"])
    n, out = len(rs), {}
    if n < 2:
        return out
    h = n // 2
    e1, e2 = effect(rs[:h], fid)[0], effect(rs[h:], fid)[0]
    out["halves"] = (round(e1, 3), round(e2, 3))
    out["halves_sign_agree"] = bool(np.sign(e1) == np.sign(e2)) and not math.isnan(e1) and not math.isnan(e2)
    if n >= 90:
        k = n // 3
        out["folds"] = [round(effect(rs[i * k:(i + 1) * k if i < 2 else n], fid)[0], 3) for i in range(3)]
    return out


def winner_removal(rs, fid):
    rs = [r for r in rs if r[fid] is not None]
    srt = sorted(rs, key=lambda r: -r["net_usd"])
    return {"full": round(effect(rs, fid)[0], 3), "ex_top1": round(effect(srt[1:], fid)[0], 3),
            "ex_top5": round(effect(srt[5:], fid)[0], 3)}


def b1_partial(rs, fid):
    rs = [r for r in rs if r[fid] is not None]
    cell = collections.defaultdict(list)
    for r in rs:
        cell[_strata(r)].append(r["net_R"])
    cm = {k: _mean(v) for k, v in cell.items()}
    ra = [r["net_R"] - cm[_strata(r)] for r in rs if r[fid] is True]
    rb = [r["net_R"] - cm[_strata(r)] for r in rs if r[fid] is False]
    return _mean(ra) - _mean(rb)


def by(rs, fid, keyf):
    out = {}
    for k in sorted(set(keyf(r) for r in rs), key=str):
        sub = [r for r in rs if keyf(r) == k and r[fid] is not None]
        e, na, nb, _, _ = effect(sub, fid)
        out[str(k)] = (round(e, 3), na, nb)
    return out


def analyze_features(rows, rng, nperm):
    for fid, (_desc, fn, _kind) in FEATURES.items():
        for r in rows:
            r[fid] = fn(r)
    n_all = len(rows)
    trending = [r for r in rows if _L(r).get("regime_at_signal") == "TRENDING"]
    results = {}
    for fid, (desc, _fn, kind) in FEATURES.items():
        rs = [r for r in rows if r[fid] is not None]
        n_avail = len(rs)
        denom = len(trending) if fid in ("F8", "F8r") else n_all   # F8 is defined only on TRENDING-at-signal rows
        avail = n_avail / denom if denom else 0.0
        e, na, nb, ma, mb = effect(rs, fid)
        testable = avail >= MIN_AVAILABILITY and n_avail >= MIN_UNIVARIATE_TOTAL and min(na, nb) >= MIN_UNIVARIATE_LEVEL
        res = dict(desc=desc, kind=kind, n_avail=n_avail, availability=round(avail, 3), n_true=na, n_false=nb,
                   mean_true=round(ma, 3), mean_false=round(mb, 3), effect_netR=round(e, 3),
                   effect_grossR=round(effect(rs, fid, "gross_R")[0], 3), effect_usd=round(effect(rs, fid, "net_usd")[0], 2),
                   testable=testable)
        if testable:
            res["p_perm"] = perm_p(rs, fid, kind, rng, nperm)
            res["walk_forward"] = walk_forward(rs, fid)
            res["winner_removal"] = winner_removal(rs, fid)
            res["b1_partial"] = round(b1_partial(rs, fid), 3)
            res["by_stratum"] = by(rs, fid, lambda r: r["stratum"])
            res["by_session"] = by(rs, fid, lambda r: r["session"])
            res["by_family"] = by(rs, fid, lambda r: r["family"])
            res["by_inst_dir"] = by(rs, fid, lambda r: f"{r['instrument']}_{r['direction']}")
            res["B2_within_TRENDING"] = by([r for r in rs if r["market_condition"] == "TRENDING"], fid, lambda r: "TRENDING")
            clean = [r for r in rs if not r["feed_gap_contaminated"]]
            res["clean_effect"] = (round(effect(clean, fid)[0], 3), len(clean))
            for conv in ("raw_bar_mae", "exec_capped_mae"):
                res[conv] = (round(_mean([r[conv] for r in rs if r[fid] is True and r[conv] is not None]), 2),
                             round(_mean([r[conv] for r in rs if r[fid] is False and r[conv] is not None]), 2))
            res["winrate_true_false"] = (round(_mean([r["result"] == "WIN" for r in rs if r[fid] is True]), 3),
                                         round(_mean([r["result"] == "WIN" for r in rs if r[fid] is False]), 3))
        results[fid] = res
    return results


def analyze_interactions(rows, rng, nperm_inter):
    out = {}
    for iid, (a, b) in INTERACTIONS.items():
        bv = (lambda r, b=b: r[b])
        rs = [r for r in rows if r[a] is not None and bv(r) is not None]
        cells = collections.defaultdict(list)
        for r in rs:
            cells[(r[a], bv(r))].append(r["net_R"])
        table = {f"{a}={ka}|{b}={kb}": (round(_mean(v), 3), len(v)) for (ka, kb), v in sorted(cells.items(), key=str)}
        min_cell = min((len(v) for v in cells.values()), default=0)
        testable = len(rs) >= MIN_INTERACTION_TOTAL and min_cell >= MIN_INTERACTION_CELL and len(cells) >= 4
        res = dict(factors=(a, b), n=len(rs), min_cell=min_cell, table=table, testable=testable)
        if testable and b not in ("session", "direction"):
            def dod(vals_by_cell):
                m = {k: _mean(vals_by_cell.get(k, [])) for k in [(True, True), (True, False), (False, True), (False, False)]}
                return (m[(True, True)] - m[(False, True)]) - (m[(True, False)] - m[(False, False)])
            obs = dod(cells)
            groups = collections.defaultdict(list)
            for i, r in enumerate(rs):
                groups[_strata(r)].append(i)
            la = np.array([r[a] for r in rs], dtype=bool)
            lb = [bv(r) for r in rs]
            vals = [r["net_R"] for r in rs]
            count = 0
            for _ in range(nperm_inter):
                lab = la.copy()
                for g in groups.values():
                    g = np.array(g)
                    lab[g] = lab[g][rng.permutation(len(g))]
                pc = collections.defaultdict(list)
                for i in range(len(rs)):
                    pc[(bool(lab[i]), lb[i])].append(vals[i])
                if abs(dod(pc)) >= abs(obs):
                    count += 1
            res["interaction_contrast"] = round(obs, 3)
            res["p_perm_2000"] = (count + 1) / (nperm_inter + 1)
        elif testable:
            res["effect_by_level"] = by(rs, a, bv)
        out[iid] = res
    return out


def holm(pvals: dict[str, float], m: int = HOLM_FAMILY) -> dict[str, float]:
    ordered = sorted(pvals.items(), key=lambda kv: kv[1])
    adj: dict[str, float] = {}
    for i, (k, p) in enumerate(ordered):
        adj[k] = min(1.0, p * (m - i))
    for i in range(1, len(ordered)):
        adj[ordered[i][0]] = max(adj[ordered[i][0]], adj[ordered[i - 1][0]])
    return adj


def _jsonkey(k) -> str:
    """JSON-style key rendering so mixed None/bool/str keys sort and serialize deterministically."""
    return "null" if k is None else ("true" if k is True else ("false" if k is False else str(k)))


def _cnt(keyf, rs):
    return {_jsonkey(k): v for k, v in collections.Counter(keyf(r) for r in rs).most_common()}


def spot_check_indices(rows_n: int, review_date: str, k: int = 3) -> list[int]:
    """Plan §16: deterministic selection seeded by the review date string."""
    rnd = random.Random(review_date)
    return rnd.sample(range(rows_n), k) if rows_n >= k else list(range(rows_n))


def run_analyze(args) -> dict:
    out_dir = Path(args.out_dir)
    ledger = json.load(open(out_dir / "gap_ledger.json"))
    joined = list(_read_jsonl(out_dir / "joined.jsonl"))
    join_stats = json.load(open(out_dir / "join_stats.json"))
    s1, excl = select_s1(joined)
    ctx_index = _context_index(args.logs_root, args.start)
    s2, s2_stats = build_s2(args.logs_root, args.start, ctx_index)
    rows = s1 + s2
    for r in rows:
        _attach_times(r)
    gap_counts = flag_gaps(rows, ledger)
    attach_mae(rows, args.logs_root)
    attach_persistence(rows, args.logs_root)
    attach_repairs(rows, ctx_index)
    rng = np.random.default_rng(args.seed)
    features = analyze_features(rows, rng, args.nperm)
    interactions = analyze_interactions(rows, rng, args.nperm_interactions)
    family_p = {f: features[f]["p_perm"] for f in features if features[f].get("testable") and not f.endswith("r")}
    family_p.update({i: interactions[i]["p_perm_2000"] for i in interactions
                     if interactions[i].get("p_perm_2000") is not None and not i.endswith("r")})
    repaired_p = {k: (features[k].get("p_perm") if k in features else interactions[k].get("p_perm_2000"))
                  for k in ("F7r", "F8r", "I7r", "I9r")
                  if (k in features and features[k].get("testable")) or (k in interactions and interactions[k].get("p_perm_2000") is not None)}
    fam_of = lambda r: r["family"]  # noqa: E731
    trending = [r for r in rows if _L(r).get("regime_at_signal") == "TRENDING"]
    summary = dict(
        tool_version=TOOL_VERSION, n_S1=len(s1), n_S2=len(s2), S1_excl=excl, S2_stats=s2_stats, join_stats=join_stats,
        date_range=(min(r["day"] for r in rows), max(r["day"] for r in rows)) if rows else None,
        days=len(set(r["day"] for r in rows)),
        dist_session=_cnt(lambda r: r["session"], rows), dist_family=_cnt(fam_of, rows),
        dist_strategy=_cnt(lambda r: r["strategy"], rows), dist_inst=_cnt(lambda r: r["instrument"], rows),
        dist_dir=_cnt(lambda r: r["direction"], rows), dist_stratum=_cnt(lambda r: r["stratum"], rows),
        dist_market_condition=_cnt(lambda r: r["market_condition"], rows),
        unique_bars_S1=len(set((r["instrument"], r["ts"]) for r in s1)),
        unique_bar_dir_S1=len(set((r["instrument"], r["ts"], r["direction"]) for r in s1)),
        net_R_mean=round(_mean([r["net_R"] for r in rows]), 4),
        net_R_median=round(float(np.median([r["net_R"] for r in rows])), 4) if rows else None,
        net_usd_sum=round(sum(r["net_usd"] for r in rows), 2),
        winrate=round(_mean([r["result"] == "WIN" for r in rows]), 3),
        gap={f"{k[0]}:{k[1]}": v for k, v in sorted(gap_counts.items())},
        gap_ledger={i: {k: ledger[i][k] for k in ("bars", "first", "last", "gap_count", "gap_minutes")} for i in ledger},
        mae_by_stratum={st: (round(_mean([r["raw_bar_mae"] for r in rows if r["stratum"] == st and r["raw_bar_mae"] is not None]), 2),
                             round(_mean([r["exec_capped_mae"] for r in rows if r["stratum"] == st and r["exec_capped_mae"] is not None]), 2),
                             sum(1 for r in rows if r["stratum"] == st and r["raw_bar_mae"] is None)) for st in ("S1", "S2")},
        n_trending_at_signal=len(trending),
        F8_persistence_cells=_cnt(lambda r: r["F8"], trending), F8r_cells=_cnt(lambda r: r["F8r"], trending),
        F7r_cells=_cnt(lambda r: r["F7r"], rows),
        B0={f: (round(_mean([r["net_R"] for r in rows if r["family"] == f]), 3), sum(1 for r in rows if r["family"] == f))
            for f in sorted(set(r["family"] for r in rows))},
        B1={f"{s}|{f}": (round(_mean([r["net_R"] for r in rows if _strata(r) == (s, f)]), 3), sum(1 for r in rows if _strata(r) == (s, f)))
            for (s, f) in sorted(set(_strata(r) for r in rows), key=str)},
        B2={_jsonkey(mc): (round(_mean([r["net_R"] for r in rows if r["market_condition"] == mc]), 3), sum(1 for r in rows if r["market_condition"] == mc))
            for mc in sorted(set(r["market_condition"] for r in rows), key=str)},
        holm=holm(family_p), pvals=family_p, repaired_p=repaired_p,
        spot_check_indices=spot_check_indices(len(rows), args.review_date),
    )
    provenance = dict(
        review_date=args.review_date, tool_version=TOOL_VERSION, start=args.start, seed=args.seed,
        permutations={"univariate": args.nperm, "interaction": args.nperm_interactions},
        cost_model=f"{args.slip_ticks_rt / 2:g} adverse tick(s) per side + {args.commission_rt} USD round-turn, "
                   "applied at the analysis layer to S1; S2 net_dollars as journaled",
        holm_family=HOLM_FAMILY, repaired_variants_excluded_from_family=["F7r", "F8r", "I1r", "I2r", "I7r", "I9r"],
        plan="docs/prereg-context-permission-layer-analysis-plan-2026-07-16.md",
        reproduction_target="docs/context-permission-first-review-2026-09-16-results.json",
        logs_root_basename=os.path.basename(os.path.normpath(str(args.logs_root))),
    )
    results = {"_provenance": provenance, "summary": summary, "features": features, "interactions": interactions}
    _write_json(out_dir / "prereg_results.json", results)
    with open(out_dir / "allrows.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps({k: v for k, v in r.items()
                                 if k not in ("loc", "cand_loc", "observer", "gex", "signa", "outcome")}, default=str, sort_keys=True) + "\n")
    return results


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────
def run_gaps(args) -> dict:
    ledger = build_gap_ledger(args.logs_root)
    _write_json(Path(args.out_dir) / "gap_ledger.json", ledger)
    for inst, l in ledger.items():
        print(f"{inst}: bars {l['bars']} {l['first']} .. {l['last']} | gaps {l['gap_count']} / {l['gap_minutes']} min")
    return ledger


def run_join(args) -> dict:
    rows, stats = build_join(args.logs_root, args.start, args.commission_rt, args.slip_ticks_rt)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "joined.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str, sort_keys=True) + "\n")
    _write_json(out_dir / "join_stats.json", stats)
    for k, v in stats.items():
        print(f"{k:45s}{v}")
    return stats


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("phase", choices=("gaps", "join", "analyze", "all"))
    p.add_argument("--logs-root", required=True, help="read-only copy of the VPS logs directory")
    p.add_argument("--out-dir", required=True, help="output directory (created); nothing else is written")
    p.add_argument("--start", default=DEFAULT_START, help="first journal day to include (collector go-live)")
    p.add_argument("--review-date", default=date.today().isoformat(), help="seeds the §16 spot-check selection")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--nperm", type=int, default=10000)
    p.add_argument("--nperm-interactions", type=int, default=2000)
    p.add_argument("--commission-rt", type=float, default=DEFAULT_COMMISSION_RT)
    p.add_argument("--slip-ticks-rt", type=float, default=DEFAULT_SLIP_TICKS_RT)
    args = p.parse_args(argv)
    if args.phase in ("gaps", "all"):
        run_gaps(args)
    if args.phase in ("join", "all"):
        run_join(args)
    if args.phase in ("analyze", "all"):
        res = run_analyze(args)
        s = res["summary"]
        print(f"S1 {s['n_S1']}  S2 {s['n_S2']}  excl {s['S1_excl']}  holm {{{', '.join(f'{k}:{v:.3f}' for k, v in s['holm'].items())}}}")
        print(f"spot-check indices ({args.review_date}): {s['spot_check_indices']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
