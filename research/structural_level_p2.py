"""Structural-level prereg — P2 read-only tooling (P2-X extractor + P2-P parity).

Library behind ``scripts/structural_level_p2_extract.py`` (P2-X) and
``scripts/structural_level_p2_parity.py`` (P2-P). Prereg v1.3 §14, P2 spec
(docs/structural-level-p2-candidate-regeneration-spec-2026-09-17.md) §3.2–§3.3 and §4.

Hard rules encoded here:

* **Outcomes are never interpreted.** Every candidate is loaded with its ``outcome`` block
  split off at parse time. P2-P (parity) drops it. P2-X writes it, verbatim, to
  ``outcomes.sealed.jsonl`` keyed by ``candidate_key`` and records the file's sha256 — the
  file is meant to stay closed until the P1 feature table is frozen (spec §3.2).
* **Same identity as the resolver.** ``candidate_key`` is
  ``strategy.shadow_resolver._candidate_key("shadow_setups", …)`` — byte-identical to the
  live/replay resolver key so a later join can never mismatch.
* **Frozen parity gates** (spec §4): firing Jaccard ≥ 0.90; entry/stop/target each within
  one tick on ≥ 98 % of co-fired rows. Nothing else is a threshold.
* No import from webhook/, execution/, broker/. Pure functions over JSONL rows.
"""

from __future__ import annotations

import collections
import glob
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Iterator, Optional
from zoneinfo import ZoneInfo

from config.futures_contracts import optional_tick_size
from strategy.shadow_resolver import _candidate_key

TOOL_VERSION = "slp2-v1.5"
LANE = "shadow_setups"
_ET = ZoneInfo("America/New_York")

# Roll cut (prereg §2 / P3): first Z6 bar on the box. Exclusive on both sides.
ROLL_CUT = datetime(2026, 9, 14, 22, 0, tzinfo=timezone.utc)
INSTRUMENTS = ("MNQ", "MES")
# Prereg v1.5: M2K joins P-REPLAY + P-OOS-PROSPECTIVE. Its live candidates come from the
# cross-instrument observation lane (logs/cross_instrument_observation_v1.jsonl), not the
# runner journal — see iter_observation_rows.
OBSERVATION_INSTRUMENTS = ("M2K",)
# Lane-only canonical families written by execution.cross_instrument_observation
# (_strat_212_122_candidate via strategy.strat_212_122.advance_strat_212_122). They are NOT
# shadow_setups families, so replay never emits them (prereg v1.5 C21).
OBSERVATION_LANE_ONLY_FAMILIES = ("strat_212", "strat_122")

# Frozen parity gates (spec §4).
FIRING_JACCARD_MIN = 0.90
BRACKET_AGREE_MIN = 0.98

# Spec §2 family matrix — verdicts and declared input divergence (static, from the census).
# Families the regeneration manifest must never contain (spec §3.3 "family census vs matrix").
FORBIDDEN_IN_REPLAY = (
    "ovn_high_sweep_reclaim", "ovn_low_sweep_reclaim", "gap_fill",
    "vwap_hold_observed", "vwap_rejection_observed", "range_break_close",
)
DEAD_FAMILIES = ("ovn_high_sweep_reclaim", "ovn_low_sweep_reclaim", "gap_fill")
LIVE_ONLY_FAMILIES = ("vwap_hold_observed", "vwap_rejection_observed", "range_break_close")
NOT_TESTABLE_FAMILIES = ("transition_failed_breakdown_reclaim",)
NOT_TESTABLE_CELLS = (("strat_122_pullback", "MES"),)
# Prereg v1.4 Ruling 2: families that failed the frozen bracket gate on the parity corpus are
# analysed as REPLAY_ONLY + LIVE_ONLY strata, never pooled. The tool still reports the raw
# BRACKET_CONFLICT classification; the disposition is attached alongside, never in its place.
RULED_SPLIT_STRATA = {
    "ema_pullback_trend": "REPLAY_ONLY + LIVE_ONLY strata, never pooled (prereg v1.4 Ruling 2: "
                          "bracket 93.88% < 98% on the parity corpus; no tolerance/target/rounding change)",
}
# Families whose detector reads the 8-bar recent window: replay clears that deque at every
# day-file boundary (replay_engine.run → _research_bars.clear()), live's BarHistory does not.
RECENT_BAR_FAMILIES = (
    "impulse_first_pullback_observed", "trend_consolidation_break_observed",
    "transition_failed_breakdown_reclaim",
)
RESEARCH_BARS_WINDOW = 8
INPUT_DIVERGENCE = {
    "strat_22_continuation_observed": "bar-type classification source (Pine classify_bar vs corpus classify_htf_bar)",
    "strat_22_reversal_observed": "bar-type classification source",
    "strat_312_observed": "bar-type classification source",
    "strat_322_reversal_observed": "bar-type classification source",
    "strat_122_observed": "bar-type classification source",
    "strat_122_pullback": "bar-type classification source",
    "strat_4hr_retrigger_observed": "EMA source (Pine vs SMA-seeded corpus EMA) + avg-volume source",
    "orb_false_break_fade": "ORB source (Pine NY/London ORB via state_builder vs corpus orb_*/london_orb_*); live asian rows STALE_ORB (C1)",
    "ema_pullback_trend": "EMA source — ≤1-tick EMA touches decide firing",
    "impulse_first_pullback_observed": "trend source (EMA) + replay recent-bars reset at day-file boundary",
    "trend_consolidation_break_observed": "trend source (EMA) + replay recent-bars reset at day-file boundary",
    "transition_failed_breakdown_reclaim": "regime label source (Pine market_condition, MES blind C8) + recent-bars reset",
    "vwap_hold_observed": "FORWARD_EVIDENCE_CAMPAIGN env (live ON, replay unset) + VWAP holiday anchor (C14)",
    "vwap_rejection_observed": "as vwap_hold_observed",
    "ovn_high_sweep_reclaim": "payload never carries overnight_high/low",
    "ovn_low_sweep_reclaim": "payload never carries overnight_high/low",
    "gap_fill": "payload never carries rth_open/session_open",
    "range_break_close": "live-only range_signal lane (wall_context walls)",
    "strat_212": "observation-lane canonical detector (advance_strat_212_122), not a shadow_setups family (C21)",
    "strat_122": "observation-lane canonical detector (advance_strat_212_122), not a shadow_setups family (C21)",
}


# ── row model ─────────────────────────────────────────────────────────────────

@dataclass
class EvalRow:
    """One evaluated bar (a decision row) with its shadow candidates, outcome split off."""
    source: str                      # "live" | "replay"
    instrument: str
    bar_ts: datetime                 # bar OPEN time, UTC
    session: str
    day_file: str                    # journal file date (YYYY-MM-DD)
    idx_in_file: int                 # 0-based position among this instrument's rows in the file
    candidates: list[dict] = field(default_factory=list)   # outcome removed
    outcomes: list[Optional[dict]] = field(default_factory=list)  # parallel to candidates
    decision: Optional[str] = None
    row_ts: Optional[str] = None
    market_condition: Optional[str] = None


def parse_dt(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_decision_row(r: dict) -> bool:
    return isinstance(r, dict) and "decision" in r and not r.get("type")


def read_jsonl(path: str) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def split_outcome(cand: dict) -> tuple[dict, Optional[dict]]:
    """Return (candidate without 'outcome', outcome block or None). Never inspects the block."""
    if not isinstance(cand, dict):
        return {}, None
    c = dict(cand)
    outcome = c.pop("outcome", None)
    return c, outcome


def candidate_key(instrument: str, bar_ts: str, cand: dict) -> str:
    """Resolver identity (spec §3.2): ``shadow_setups|inst|bar_ts|strategy|direction|entry``."""
    return _candidate_key(
        LANE, instrument, bar_ts, str(cand.get("strategy") or "unknown"),
        str(cand.get("direction") or "").upper(), float(cand["entry"]),
    )


def _well_formed(cand: dict) -> bool:
    try:
        float(cand["entry"]); float(cand["stop"]); float(cand["target"])
    except (KeyError, TypeError, ValueError):
        return False
    return str(cand.get("direction") or "").upper() in ("LONG", "SHORT") and bool(cand.get("strategy"))


# ── loaders ───────────────────────────────────────────────────────────────────

def iter_replay_rows(log_dir: str, *, instruments: Iterable[str] = INSTRUMENTS,
                     end_ts_exclusive: Optional[datetime] = None) -> Iterator[EvalRow]:
    """Decision rows of a replay run (``journal_*.jsonl``); ``bar_ts`` is the candle open."""
    insts = set(instruments)
    for f in sorted(glob.glob(os.path.join(log_dir, "journal_*.jsonl"))):
        day = os.path.basename(f)[8:18]
        idx: collections.Counter = collections.Counter()
        for r in read_jsonl(f):
            if not is_decision_row(r) or r.get("instrument") not in insts:
                continue
            bar_ts = parse_dt(r.get("bar_ts"))
            if bar_ts is None:
                continue
            i = idx[r["instrument"]]
            idx[r["instrument"]] += 1
            if end_ts_exclusive is not None and bar_ts >= end_ts_exclusive:
                continue
            cands, outs = [], []
            for c in r.get("shadow_candidates") or []:
                c2, o = split_outcome(c)
                if _well_formed(c2):
                    cands.append(c2)
                    outs.append(o)
            yield EvalRow("replay", r["instrument"], bar_ts, str(r.get("session") or ""), day, i,
                          cands, outs, r.get("decision"), r.get("ts"), r.get("market_condition"))


def iter_live_rows(logs_root: str, *, start_date: str, end_ts_exclusive: datetime = ROLL_CUT,
                   instruments: Iterable[str] = INSTRUMENTS,
                   timeframe_minutes: int = 15) -> Iterator[EvalRow]:
    """15m decision rows of the live journal snapshot; ``bar_ts`` = ``context.timestamp``
    (the bar's own open time — the row's ``ts`` is the wall-clock delivery time)."""
    insts = set(instruments)
    for f in sorted(glob.glob(os.path.join(logs_root, "journal_*.jsonl"))):
        day = os.path.basename(f)[8:18]
        if day < start_date:
            continue
        idx: collections.Counter = collections.Counter()
        for r in read_jsonl(f):
            if not is_decision_row(r) or r.get("instrument") not in insts:
                continue
            tf = r.get("timeframe_minutes")
            if tf is not None and int(tf) != timeframe_minutes:
                continue
            ctx = r.get("context") if isinstance(r.get("context"), dict) else {}
            bar_ts = parse_dt(ctx.get("timestamp"))
            if bar_ts is None:
                continue
            i = idx[r["instrument"]]
            idx[r["instrument"]] += 1
            if bar_ts >= end_ts_exclusive:
                continue
            cands = []
            for c in r.get("shadow_candidates") or []:
                c2, _ = split_outcome(c)     # live rows carry no shadow outcome; drop if present
                if _well_formed(c2):
                    cands.append(c2)
            yield EvalRow("live", r["instrument"], bar_ts, str(r.get("session") or ""), day, i,
                          cands, [None] * len(cands), r.get("decision"), r.get("ts"),
                          r.get("market_condition"))


def iter_observation_rows(evidence_path: str, bars_root: str, *,
                          instruments: Iterable[str] = OBSERVATION_INSTRUMENTS,
                          end_ts_exclusive: Optional[datetime] = None,
                          timeframe_minutes: int = 15) -> Iterator[EvalRow]:
    """Live rows for a collection-only root from the cross-instrument observation lane.

    An evaluated bar = every 15m bar in ``bars_<INST>_<day>.jsonl`` under ``bars_root`` (those
    files are written by the observation transport itself, so a recorded bar is a bar the lane
    evaluated). Candidates = CANDIDATE and SIGNAL records whose ``signal_timestamp`` is that
    bar (SIGNAL rows carry a non-authoritative bracket and are still a firing; flagged
    ``bracket_authoritative=False``). OUTCOME records are skipped by ``record_type`` before
    anything else in the row is looked at — never read.
    """
    insts = set(instruments)
    cands: dict[tuple[str, datetime], list[dict]] = collections.defaultdict(list)
    sessions: dict[tuple[str, datetime], str] = {}
    for r in read_jsonl(evidence_path):
        if r.get("record_type") not in ("CANDIDATE", "SIGNAL"):
            continue
        inst = r.get("instrument")
        if inst not in insts:
            continue
        ts = parse_dt(r.get("signal_timestamp"))
        if ts is None:
            continue
        c = {"strategy": r.get("strategy"), "direction": r.get("direction"), "entry": r.get("entry"),
             "stop": r.get("stop"), "target": r.get("target"), "rr_ratio": r.get("reward_to_risk"),
             "record_type": r.get("record_type"), "collection_mode": r.get("collection_mode"),
             "bracket_authoritative": bool(r.get("bracket_authoritative")),
             "candidate_id": r.get("candidate_id")}
        if _well_formed(c):
            cands[(inst, ts)].append(c)
            sessions.setdefault((inst, ts), str(r.get("session") or ""))
    from research.structural_level_features import detect_session
    for inst in sorted(insts):
        for f in sorted(glob.glob(os.path.join(bars_root, f"bars_{inst}_*.jsonl"))):
            day = os.path.basename(f)[len(f"bars_{inst}_"):][:10]
            i = 0
            for b in read_jsonl(f):
                tf = b.get("timeframe")
                if tf is not None and int(str(tf).rstrip("m") or 0) != timeframe_minutes:
                    continue
                ts = parse_dt(b.get("ts"))
                if ts is None:
                    continue
                idx = i
                i += 1
                if end_ts_exclusive is not None and ts >= end_ts_exclusive:
                    continue
                k = (inst, ts)
                cl = cands.get(k, [])
                yield EvalRow("live", inst, ts, sessions.get(k) or detect_session(ts), day, idx,
                              cl, [None] * len(cl), "OBSERVATION_ONLY", None, None)


def load_corpus_bars(corpus_dir: str) -> dict[datetime, dict]:
    """``{bar_ts: candle}`` for one instrument's corpus directory (``<INST>_<day>.jsonl``)."""
    out: dict[datetime, dict] = {}
    for f in sorted(glob.glob(os.path.join(corpus_dir, "*.jsonl"))):
        for c in read_jsonl(f):
            ts = parse_dt(c.get("timestamp"))
            if ts is not None and ts not in out:
                out[ts] = c
    return out


def day_positions(corpus: dict[datetime, dict]) -> dict[datetime, int]:
    """Index of each bar within its UTC-date day file (the replay engine clears the 8-bar
    research deque at every ``run()`` = every day file, and fills it for every candle
    including skipped ones — so the warm-up is a corpus position, not a journal-row index)."""
    by_day: dict[str, list[datetime]] = collections.defaultdict(list)
    for ts in corpus:
        by_day[ts.date().isoformat()].append(ts)
    out: dict[datetime, int] = {}
    for day, tss in by_day.items():
        for i, ts in enumerate(sorted(tss)):
            out[ts] = i
    return out


# ── P2-P: firing / bracket parity (spec §4) ───────────────────────────────────

def firing_key(row: EvalRow, cand: dict) -> tuple[str, str, str, str]:
    return (row.instrument, row.bar_ts.isoformat(), str(cand["strategy"]),
            str(cand["direction"]).upper())


def is_stale_orb(row: EvalRow, cand: dict) -> bool:
    """Spec §6: live asian-session ``orb_false_break_fade`` rows are the pre-09-04 NY-ORB leak
    (C1). No ORB exists in the asian session by §3.6, so they are excluded and counted."""
    return (row.source == "live" and cand.get("strategy") == "orb_false_break_fade"
            and row.session == "asian")


def _within_tick(a: float, b: float, tick: float) -> bool:
    return abs(float(a) - float(b)) <= tick + 1e-9


def _pair_brackets(live: list[dict], replay: list[dict]) -> list[tuple[dict, dict]]:
    """Co-fired candidates for one firing key. Usually 1:1; if a side has several, pair in
    entry order (deterministic, never optimistic)."""
    l = sorted(live, key=lambda c: float(c["entry"]))
    r = sorted(replay, key=lambda c: float(c["entry"]))
    return list(zip(l, r))


def compute_parity(live_rows: Iterable[EvalRow], replay_rows: Iterable[EvalRow], *,
                   corpus: Optional[dict[str, dict[datetime, dict]]] = None,
                   example_limit: int = 5) -> dict:
    """Per-family firing Jaccard + bracket agreement over bars evaluated on BOTH sides.

    ``corpus`` (optional) = ``{inst: {bar_ts: candle}}`` of the parity corpus, used to split
    "replay did not evaluate this bar" into corpus-hole vs engine-skip and to locate the
    replay day-file warm-up (first 7 bars of each UTC day: the research deque holds < 8).
    """
    positions: dict[str, dict[datetime, int]] = {
        inst: day_positions(c) for inst, c in (corpus or {}).items()
    }

    def _warm(inst: str, ts: datetime, fallback_idx: int) -> bool:
        pos = positions.get(inst, {}).get(ts, fallback_idx)
        return pos < RESEARCH_BARS_WINDOW - 1
    live_by_bar: dict[tuple[str, datetime], EvalRow] = {}
    replay_by_bar: dict[tuple[str, datetime], EvalRow] = {}
    dup_live = dup_replay = 0
    for r in live_rows:
        k = (r.instrument, r.bar_ts)
        if k in live_by_bar:
            dup_live += 1          # first-seen wins (re-delivered bar); counted
            continue
        live_by_bar[k] = r
    for r in replay_rows:
        k = (r.instrument, r.bar_ts)
        if k in replay_by_bar:
            dup_replay += 1
            continue
        replay_by_bar[k] = r

    both = set(live_by_bar) & set(replay_by_bar)
    live_only_bars = set(live_by_bar) - set(replay_by_bar)
    replay_only_bars = set(replay_by_bar) - set(live_by_bar)
    bar_census: dict = {}
    for inst in sorted({k[0] for k in live_by_bar} | {k[0] for k in replay_by_bar}):
        lo = {k for k in live_only_bars if k[0] == inst}
        ro = {k for k in replay_only_bars if k[0] == inst}
        entry = {
            "live_bars": sum(1 for k in live_by_bar if k[0] == inst),
            "replay_bars": sum(1 for k in replay_by_bar if k[0] == inst),
            "both_evaluated": sum(1 for k in both if k[0] == inst),
            "live_only_bars": len(lo),
            "replay_only_bars": len(ro),
        }
        if corpus is not None and inst in corpus:
            cb = set(corpus[inst])
            entry["live_only_bars_absent_from_corpus"] = sum(1 for k in lo if k[1] not in cb)
            entry["live_only_bars_in_corpus_not_evaluated_by_replay"] = sum(1 for k in lo if k[1] in cb)
            entry["corpus_bars"] = len(cb)
        bar_census[inst] = entry

    stale_orb_excluded = 0
    fam_live_all: collections.Counter = collections.Counter()
    fam_replay_all: collections.Counter = collections.Counter()
    fam_live_by_inst: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    fam_replay_by_inst: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    # firing sets on both-evaluated bars
    live_fire: dict[str, dict[tuple, list[dict]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    replay_fire: dict[str, dict[tuple, list[dict]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    replay_warm: set[tuple] = set()   # firing keys on replay bars inside the day-file warm-up

    for k, r in live_by_bar.items():
        for c in r.candidates:
            fam = str(c["strategy"])
            if is_stale_orb(r, c):
                stale_orb_excluded += 1
                continue
            fam_live_all[fam] += 1
            fam_live_by_inst[fam][r.instrument] += 1
            if k in both:
                fk = firing_key(r, c)
                live_fire[fam][fk].append(c)
    for k, r in replay_by_bar.items():
        for c in r.candidates:
            fam = str(c["strategy"])
            fam_replay_all[fam] += 1
            fam_replay_by_inst[fam][r.instrument] += 1
            if k in both:
                fk = firing_key(r, c)
                replay_fire[fam][fk].append(c)
                if _warm(r.instrument, r.bar_ts, r.idx_in_file):
                    replay_warm.add(fk)

    families = sorted(set(fam_live_all) | set(fam_replay_all) | set(INPUT_DIVERGENCE))
    per_family: dict[str, dict] = {}
    gate_failures: list[str] = []
    for fam in families:
        L = set(live_fire[fam]); R = set(replay_fire[fam])
        inter, union = L & R, L | R
        jaccard = (len(inter) / len(union)) if union else None
        live_only_f = sorted(L - R); replay_only_f = sorted(R - L)
        warm_replay_only = sum(1 for fk in replay_only_f if fk in replay_warm)
        # live-only firings on replay bars still inside the day-file warm-up: replay could
        # not have seen the recent bars live had.
        warm_live_only = 0
        for fk in live_only_f:
            rr = replay_by_bar.get((fk[0], parse_dt(fk[1])))
            if rr is not None and _warm(rr.instrument, rr.bar_ts, rr.idx_in_file):
                warm_live_only += 1
        # bracket agreement on co-fired
        n_pairs = ok_pairs = 0
        ok_entry = ok_stop = ok_target = 0
        bad_examples: list[dict] = []
        for fk in sorted(inter):
            tick = optional_tick_size(fk[0]) or 0.25
            for lc, rc in _pair_brackets(live_fire[fam][fk], replay_fire[fam][fk]):
                n_pairs += 1
                e = _within_tick(lc["entry"], rc["entry"], tick)
                s = _within_tick(lc["stop"], rc["stop"], tick)
                t = _within_tick(lc["target"], rc["target"], tick)
                ok_entry += e; ok_stop += s; ok_target += t
                if e and s and t:
                    ok_pairs += 1
                elif len(bad_examples) < example_limit:
                    bad_examples.append({"key": list(fk),
                                         "live": {x: lc[x] for x in ("entry", "stop", "target")},
                                         "replay": {x: rc[x] for x in ("entry", "stop", "target")}})
        bracket_rate = (ok_pairs / n_pairs) if n_pairs else None
        entry_rate = (ok_entry / n_pairs) if n_pairs else None
        stop_rate = (ok_stop / n_pairs) if n_pairs else None
        target_rate = (ok_target / n_pairs) if n_pairs else None

        cls, reason = _classify(fam, len(L), len(R), jaccard, bracket_rate, fam_live_all[fam],
                                fam_replay_all[fam])
        if cls.startswith("MANIFEST_ERROR") or cls == "BOTH — BRACKET_CONFLICT":
            gate_failures.append(f"{fam}: {cls}")
        per_family[fam] = {
            "live_firings_all_live_bars": fam_live_all[fam],
            "replay_firings_all_replay_bars": fam_replay_all[fam],
            "live_by_instrument": dict(fam_live_by_inst[fam]),
            "replay_by_instrument": dict(fam_replay_by_inst[fam]),
            "on_both_evaluated_bars": {
                "live_firings": len(L), "replay_firings": len(R),
                "intersection": len(inter), "union": len(union),
                "firing_jaccard": None if jaccard is None else round(jaccard, 4),
                "live_only_firings": len(live_only_f),
                "live_only_on_replay_warmup_bars": warm_live_only,
                "replay_only_firings": len(replay_only_f),
                "replay_only_on_replay_warmup_bars": warm_replay_only,
            },
            "bracket_on_cofired": {
                "pairs": n_pairs,
                "all_three_within_one_tick_rate": None if bracket_rate is None else round(bracket_rate, 4),
                "entry_rate": None if entry_rate is None else round(entry_rate, 4),
                "stop_rate": None if stop_rate is None else round(stop_rate, 4),
                "target_rate": None if target_rate is None else round(target_rate, 4),
                "failing_examples": bad_examples,
            },
            "input_source_mismatch_reason": INPUT_DIVERGENCE.get(fam, "not in the P2 matrix"),
            "spec_verdict_reference": _spec_reference(fam),
            "classification": cls,
            "classification_reason": reason,
            "ruled_disposition": RULED_SPLIT_STRATA.get(fam),
            "live_only_examples": [list(x) for x in live_only_f[:example_limit]],
            "replay_only_examples": [list(x) for x in replay_only_f[:example_limit]],
        }

    return {
        "tool": TOOL_VERSION,
        "gates": {"firing_jaccard_min": FIRING_JACCARD_MIN, "bracket_agree_min": BRACKET_AGREE_MIN,
                  "bracket_tolerance": "one tick per leg (entry, stop, target)"},
        "bar_census": bar_census,
        "duplicate_bars_first_seen_wins": {"live": dup_live, "replay": dup_replay},
        "live_stale_orb_rows_excluded": stale_orb_excluded,
        "families": per_family,
        "gate_failures": gate_failures,
    }


def _spec_reference(fam: str) -> str:
    if fam in OBSERVATION_LANE_ONLY_FAMILIES:
        return "LANE_ONLY (prereg v1.5 C21)"
    if fam in DEAD_FAMILIES:
        return "DEAD (spec §2)"
    if fam in LIVE_ONLY_FAMILIES:
        return "LIVE_ONLY (spec §2/§6)"
    if fam in NOT_TESTABLE_FAMILIES:
        return "NOT_TESTABLE (spec §2/§6)"
    if fam in ("strat_4hr_retrigger_observed", "ema_pullback_trend", "impulse_first_pullback_observed",
               "trend_consolidation_break_observed"):
        return "BOTH — input-divergent (spec §2; resolved by §4 gates)"
    if fam == "orb_false_break_fade":
        return "BOTH after corpus rebuild (spec §2)"
    if fam.startswith("strat_"):
        return "BOTH (spec §2; bar-type parity measured here)"
    return "not in the P2 matrix"


def _classify(fam: str, live_n: int, replay_n: int, jaccard: Optional[float],
              bracket_rate: Optional[float], live_all: int, replay_all: int) -> tuple[str, str]:
    """Final classification per the operator's list: BOTH, BOTH — input-divergent, LIVE_ONLY,
    NOT_TESTABLE, DEAD (+ the fail-closed states the spec names)."""
    if fam in OBSERVATION_LANE_ONLY_FAMILIES:
        if replay_all:
            return "MANIFEST_ERROR — lane-only canonical family present in replay", f"replay={replay_all}"
        return "LANE_ONLY", f"observation-lane canonical detector; live={live_all}, replay=0 by construction (C21)"
    if fam in DEAD_FAMILIES:
        if live_all or replay_all:
            return "MANIFEST_ERROR — DEAD family fired", f"live={live_all} replay={replay_all}"
        return "DEAD", "never fires on either path (payload lacks the required field)"
    if fam in LIVE_ONLY_FAMILIES:
        if replay_all:
            return "MANIFEST_ERROR — LIVE_ONLY family present in replay", f"replay={replay_all}"
        return "LIVE_ONLY", f"live={live_all}, replay=0 by construction (env/lane)"
    if fam in NOT_TESTABLE_FAMILIES:
        return "NOT_TESTABLE", f"spec §6; live={live_all} replay={replay_all} (reported, not gated)"
    if live_n == 0 and replay_n == 0:
        if live_all == 0 and replay_all == 0:
            return "ABSENT", "no firing on either side"
        return "NOT_TESTABLE", "no firing on bars evaluated by both sides"
    if replay_n == 0:
        return "LIVE_ONLY", "observed: replay never fired on co-evaluated bars"
    if live_n == 0:
        return "REPLAY_ONLY", "observed: live never fired on co-evaluated bars"
    assert jaccard is not None
    if jaccard >= FIRING_JACCARD_MIN:
        if bracket_rate is not None and bracket_rate < BRACKET_AGREE_MIN:
            return "BOTH — BRACKET_CONFLICT", (f"jaccard {jaccard:.3f} ≥ {FIRING_JACCARD_MIN} but bracket "
                                               f"{bracket_rate:.3f} < {BRACKET_AGREE_MIN}: same formula, "
                                               "input miss — definition conflict, reported")
        return "BOTH", f"jaccard {jaccard:.3f} ≥ {FIRING_JACCARD_MIN}; bracket {bracket_rate}"
    return "BOTH — input-divergent", (f"jaccard {jaccard:.3f} < {FIRING_JACCARD_MIN}: split into "
                                      f"LIVE_ONLY + REPLAY_ONLY strata; bracket on co-fired {bracket_rate}")


# ── determinism (spec §3.3) ───────────────────────────────────────────────────

def candidates_digest(log_dir: str, *, instruments: Iterable[str] = INSTRUMENTS) -> dict[str, str]:
    """``{inst@bar_ts: sha256(canonical shadow_candidates JSON incl. outcome bytes)}``.
    Compares bytes only — the outcome block is hashed, never read."""
    out: dict[str, str] = {}
    insts = set(instruments)
    for f in sorted(glob.glob(os.path.join(log_dir, "journal_*.jsonl"))):
        for r in read_jsonl(f):
            if not is_decision_row(r) or r.get("instrument") not in insts:
                continue
            bar_ts = r.get("bar_ts")
            if not isinstance(bar_ts, str):
                continue
            blob = json.dumps(r.get("shadow_candidates") or [], sort_keys=True, separators=(",", ":"))
            out[f"{r['instrument']}@{bar_ts}"] = hashlib.sha256(blob.encode()).hexdigest()
    return out


def determinism_check(log_dir_a: str, log_dir_b: str) -> dict:
    a, b = candidates_digest(log_dir_a), candidates_digest(log_dir_b)
    keys = set(a) | set(b)
    differ = sorted(k for k in keys if a.get(k) != b.get(k))
    return {"rows_a": len(a), "rows_b": len(b), "rows_differ": len(differ),
            "identical": not differ and len(a) == len(b), "examples": differ[:10]}


# ── P2-X: extraction with sealed outcomes (spec §3.2 / §3.3) ──────────────────

def _contract_for(ts: datetime, segments: list[list]) -> tuple[Optional[str], Optional[str]]:
    d = ts.date().isoformat()
    for ticker, s, e in segments:
        if s <= d <= e:
            return ticker, s
    return None, None


def _orb_availability(corpus: dict[datetime, dict]) -> dict:
    """Session-days lacking the canonical ORB bar (09:30 ET NY / 03:00 ET London) — prereg
    §3.6 NOT_AVAILABLE days, counted from the corpus itself."""
    ny_days: set[str] = set(); ldn_days: set[str] = set()
    ny_has: set[str] = set(); ldn_has: set[str] = set()
    for ts in corpus:
        et = ts.astimezone(_ET)
        if et.weekday() >= 5:
            continue
        d = et.date().isoformat()
        hm = (et.hour, et.minute)
        if (9, 30) <= hm < (17, 0):
            ny_days.add(d)
            if hm == (9, 30):
                ny_has.add(d)
        elif (3, 0) <= hm < (9, 30):
            ldn_days.add(d)
            if hm == (3, 0):
                ldn_has.add(d)
    return {
        "ny_session_days": len(ny_days), "ny_orb_not_available_days": sorted(ny_days - ny_has),
        "london_session_days": len(ldn_days), "london_orb_not_available_days": sorted(ldn_days - ldn_has),
    }


def extract(log_dir: str, corpus_root: str, out_dir: str, *, instruments: Iterable[str] = INSTRUMENTS,
            integrity_only: bool = False, end_ts_exclusive: Optional[datetime] = None) -> dict:
    """Split replay journals into ``candidates.jsonl`` (no outcome) + ``outcomes.sealed.jsonl``
    and write ``manifest.json`` with hashes and the §3.3 integrity report.

    ``integrity_only=True`` runs every check and writes candidates + manifest but never writes
    (or opens) the outcomes file — used for smoke/parity runs that are not the regeneration."""
    os.makedirs(out_dir, exist_ok=True)
    insts = list(instruments)
    corpora: dict[str, dict[datetime, dict]] = {}
    corpus_manifests: dict[str, dict] = {}
    positions: dict[str, dict[datetime, int]] = {}
    for inst in insts:
        cdir = os.path.join(corpus_root, inst)
        corpora[inst] = load_corpus_bars(cdir)
        positions[inst] = day_positions(corpora[inst])
        mp = os.path.join(cdir, "MANIFEST.json")
        if os.path.exists(mp):
            with open(mp, encoding="utf-8") as fh:
                corpus_manifests[inst] = json.load(fh)

    cand_path = os.path.join(out_dir, "candidates.jsonl")
    out_path = os.path.join(out_dir, "outcomes.sealed.jsonl")
    seen: dict[str, str] = {}            # key → canonical candidate json
    conflicting: collections.Counter = collections.Counter()
    exact_dupes: collections.Counter = collections.Counter()
    census: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    session_census: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    evaluated_bars: collections.Counter = collections.Counter()
    n_rows = n_cands = 0
    forbidden_seen: collections.Counter = collections.Counter()
    asian_orb_fade = 0
    cand_fh = open(cand_path, "w", encoding="utf-8")
    out_fh = None if integrity_only else open(out_path, "w", encoding="utf-8")
    try:
        for row in iter_replay_rows(log_dir, instruments=insts, end_ts_exclusive=end_ts_exclusive):
            n_rows += 1
            evaluated_bars[row.instrument] += 1
            candle = corpora.get(row.instrument, {}).get(row.bar_ts)
            segs = (corpus_manifests.get(row.instrument, {}).get("source", {}) or {}).get("contract_segments") or []
            contract, roll_date = _contract_for(row.bar_ts, segs)
            for cand, outcome in zip(row.candidates, row.outcomes):
                n_cands += 1
                fam = str(cand["strategy"])
                census[fam][row.instrument] += 1
                session_census[fam][row.session] += 1
                if fam in FORBIDDEN_IN_REPLAY:
                    forbidden_seen[fam] += 1
                if fam == "orb_false_break_fade" and row.session == "asian":
                    asian_orb_fade += 1
                key = candidate_key(row.instrument, row.bar_ts.isoformat(), cand)
                rec = {
                    "candidate_key": key,
                    "instrument": row.instrument,
                    "bar_ts": row.bar_ts.isoformat(),
                    "session": row.session,
                    "family": fam,
                    "strategy": fam,
                    "direction": str(cand["direction"]).upper(),
                    "entry": float(cand["entry"]), "stop": float(cand["stop"]), "target": float(cand["target"]),
                    "rr_ratio": cand.get("rr_ratio"),
                    "risk_tier": cand.get("risk_tier"),
                    "size_multiplier": cand.get("size_multiplier"),
                    "market_condition_journal": row.market_condition,
                    "reconstructed_market_condition": (candle or {}).get("reconstructed_market_condition"),
                    "legacy_market_condition": (candle or {}).get("legacy_market_condition"),
                    "candle_present_in_corpus": candle is not None,
                    "contract": contract,
                    "contract_roll_utc_date": roll_date,
                    "is_roll_utc_day": bool(roll_date) and row.bar_ts.date().isoformat() == roll_date,
                    "journal_day_file": row.day_file,
                    "idx_in_day_file": row.idx_in_file,
                    "corpus_day_position": positions.get(row.instrument, {}).get(row.bar_ts),
                    "recent_bars_warmup": positions.get(row.instrument, {}).get(row.bar_ts, row.idx_in_file)
                    < RESEARCH_BARS_WINDOW - 1,
                }
                blob = json.dumps(rec, sort_keys=True, separators=(",", ":"))
                if key in seen:
                    if seen[key] == blob:
                        exact_dupes[fam] += 1
                    else:
                        conflicting[fam] += 1
                    continue
                seen[key] = blob
                cand_fh.write(json.dumps(rec, sort_keys=True) + "\n")
                if out_fh is not None:
                    out_fh.write(json.dumps({"candidate_key": key, "outcome": outcome}, sort_keys=True) + "\n")
    finally:
        cand_fh.close()
        if out_fh is not None:
            out_fh.close()

    orb_avail = {inst: _orb_availability(corpora[inst]) for inst in insts if corpora.get(inst)}
    blocked = sorted(conflicting)
    problems: list[str] = []
    if blocked:
        problems.append(f"CONFLICTING_DUPLICATE in families {blocked} → those population×family cells BLOCKED (prereg §9.1)")
    if asian_orb_fade:
        problems.append(f"orb_false_break_fade fired on {asian_orb_fade} asian-session replay rows — build defect (spec §3.3)")
    if forbidden_seen:
        problems.append(f"forbidden families present in replay: {dict(forbidden_seen)} — manifest/env error (spec §3.3)")
    journals = sorted(glob.glob(os.path.join(log_dir, "journal_*.jsonl")))
    manifest = {
        "tool": TOOL_VERSION,
        "mode": "integrity_only (outcomes NOT written)" if integrity_only else "full (outcomes sealed)",
        "replay_log_dir": os.path.abspath(log_dir),
        "corpus_root": os.path.abspath(corpus_root),
        "corpus_manifest_sha256": {inst: _sha256_path(os.path.join(corpus_root, inst, "MANIFEST.json"))
                                   for inst in insts if os.path.exists(os.path.join(corpus_root, inst, "MANIFEST.json"))},
        "journal_files": {os.path.basename(p): _sha256_path(p) for p in journals},
        "candidates_file": {"path": cand_path, "sha256": _sha256_path(cand_path), "rows": len(seen)},
        "outcomes_sealed_file": None if integrity_only else {"path": out_path, "sha256": _sha256_path(out_path),
                                                              "rows": len(seen), "status": "SEALED — do not open before the P1 feature table is frozen and hashed"},
        "end_ts_exclusive": end_ts_exclusive.isoformat() if end_ts_exclusive else None,
        "integrity": {
            "decision_rows": n_rows,
            "evaluated_bars_by_instrument": dict(evaluated_bars),
            "corpus_bars_by_instrument": {inst: len(corpora.get(inst, {})) for inst in insts},
            "bars_in_corpus_not_evaluated_by_replay": {
                inst: len(set(corpora.get(inst, {})) - {r for r in _evaluated_ts(log_dir, inst, end_ts_exclusive)})
                for inst in insts},
            "candidate_rows_seen": n_cands,
            "unique_candidate_keys": len(seen),
            "exact_duplicate_keys_collapsed": dict(exact_dupes),
            "conflicting_duplicate_keys": dict(conflicting),
            "families_blocked": blocked,
            "census_by_family_by_instrument": {f: dict(c) for f, c in sorted(census.items())},
            "census_by_family_by_session": {f: dict(c) for f, c in sorted(session_census.items())},
            "asian_orb_false_break_fade_rows": asian_orb_fade,
            "forbidden_families_present": dict(forbidden_seen),
            "orb_availability": orb_avail,
            "roll_ledger": {inst: (corpus_manifests.get(inst, {}).get("roll_ledger")) for inst in insts},
            "gap_ledger_cme_hours": {inst: (corpus_manifests.get(inst, {}).get("gap_ledger_cme_hours")) for inst in insts},
            "problems": problems,
            "status": "BLOCKED" if problems else "PASS",
        },
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=False)
        fh.write("\n")
    return manifest


def _evaluated_ts(log_dir: str, inst: str, end_ts_exclusive: Optional[datetime]) -> set[datetime]:
    return {r.bar_ts for r in iter_replay_rows(log_dir, instruments=(inst,), end_ts_exclusive=end_ts_exclusive)}


def _sha256_path(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
