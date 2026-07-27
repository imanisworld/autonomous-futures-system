#!/usr/bin/env python3
"""ORB Reclaim strategy-logic rework — Pass 1: rule anatomy diagnosis.

Evidence orchestration ONLY. No strategy/replay/broker/risk/config/deployment/
Pine changes. Operator opened the strategy-logic rework after the execution
arc closed (#346→#354→#355→#356→#358: no modeled execution mode makes the
frozen system profitable; strategy logic is the primary suspect).

This pass diagnoses WHICH COMPONENTS of the frozen orb_reclaim rule carry or
destroy expectancy, using PR #352's isolated honest-fill populations (input
pinned from branch `claude/orb-reclaim-isolated-honest-fill` @ 3d0220a97,
copied verbatim to scripts/orb_reclaim_pr352_raw_trades_input.jsonl):

- DIAGNOSTIC substrate (feature separation): the breaker-OFF runs
  (MNQ/MES_ioc_1tick_breaker_off_DIAGNOSTIC, n=531 attempts) — uncensored
  full-year populations. Diagnostic ONLY, per the operator's #352 ruling
  that breaker-off rows never ground claims.
- CANONICAL substrate (variant scoring): the breaker-ON populations —
  all-session accounts (MNQ/MES_ioc_1tick) and the session-isolated lanes
  (*_london/new_york_ioc_1tick, the #352 HOLD-amendment authoritative test)
  — with their 2-4 tick rows for slippage sensitivity of any passing variant.

The frozen rule under diagnosis (signal_engine._try_orb_reclaim +
csv_to_replay.derive_orb_status, both PROVEN by #356's 131/131 bracket
reconstruction):

    trigger  = 15m close-cross UP through the session ORB high
               ("reclaimed_high" is ANY cross — the docstring's
               "rejected above, pulled back, now reclaiming" pattern is
               NOT what the state machine requires)
    gates    = VWAP-above, TRENDING, GEX-not-positive; LONG only
    bracket  = entry ORBhigh+2t; stop max(ORBlow-4t, entry-80/40t);
               target entry + max(2.5R, 15pt)

Anatomy features computed per attempt from the corpus day file (session-aware
ORB source exactly as replay maps it — london bars use the london ORB):

  true_reclaim        — an EARLIER same-day bar (same ORB source+level)
                        CLOSED above the ORB high before this trigger: the
                        docstring's intended pullback-reclaim shape.
                        first_cross = no such prior bar (the trigger is the
                        day's first close above the level).
  prior_rejected_high — an earlier same-day bar carried orb_status
                        rejected_high (an explicit failed-break preceded).
  attempt_index       — 1st/2nd/3rd… orb_reclaim attempt of the day
                        (re-fire anatomy).
  chase_ticks         — decision close minus plan entry, in ticks (how far
                        the market ran past the level by decision time).
  vwap_dist_ticks     — close minus VWAP in ticks.
  orb_width_ticks     — ORB high−low in ticks (stop-geometry driver).
  trend_strength      — corpus trend_strength on the trigger bar.
  hour_et / session / half / instrument.

Loser anatomy (5m MFE): for canonical-1t FILLED LOSS rows, the maximum
favorable excursion in R units before the stop, measured on the 5m tier
(parity-proven in #356) — did losers pay for a nearer target?

Pre-registered variant scoring (filters ON TOP of the frozen rule; scored on
the CANONICAL populations; house acceptance standard: n≥30 resolved, net>0
after $1.48 RT, both halves positive, PF>1; slippage from the 2-4t rows):

  V1  new_york-only                (restates #352's authoritative lanes)
  V2  true_reclaim-only            (all sessions)
  V3  first-attempt-of-day-only    (all sessions)
  V4  new_york AND true_reclaim
  V5  new_york AND first-attempt

CAVEAT stated on every variant: post-hoc filters of replayed accounts inherit
the unfiltered account's position-blocking/breaker path; any variant that
passes here still needs its own isolated filtered replay before promotion.
No engine change is made or recommended by this pass.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PR352_BRANCH_SHA = "3d0220a970d12875c75226c16ef781d4d7ddf60a"
COMMISSION_ROUND_TRIP = 1.48
TICK = 0.25
ENTRY_OFFSET_TICKS = 2.0
STOP_BELOW_ORB_LOW_TICKS = 4.0
MAX_ORB_STOP_TICKS = {"MNQ": 80.0, "MES": 40.0}
MIN_TARGET_POINTS = {"MNQ": 15.0, "MES": 15.0}
IOC_TOL_TICKS = {"MNQ": 32.0, "MES": 16.0}
INSTRUMENTS = ("MNQ", "MES")
HALVES = {"H1": ("2025-07-24", "2026-01-23"), "H2": ("2026-01-24", "2026-07-23")}

DIAGNOSTIC_TAGS = (
    "MNQ_ioc_1tick_breaker_off_DIAGNOSTIC",
    "MES_ioc_1tick_breaker_off_DIAGNOSTIC",
)
CANONICAL_1T_TAGS = ("MNQ_ioc_1tick", "MES_ioc_1tick")
LANE_1T_TAGS = (
    "MNQ_london_ioc_1tick",
    "MNQ_new_york_ioc_1tick",
    "MES_london_ioc_1tick",
    "MES_new_york_ioc_1tick",
)
SLIPPAGE_LANE_PREFIXES = (
    "MNQ_ioc_", "MES_ioc_",
    "MNQ_london_ioc_", "MNQ_new_york_ioc_",
    "MES_london_ioc_", "MES_new_york_ioc_",
)

VARIANTS = {
    "V1_new_york_only": lambda f: f["session"] == "new_york",
    "V2_true_reclaim_only": lambda f: f["true_reclaim"] == 1,
    "V3_first_attempt_only": lambda f: f["attempt_index"] == 1,
    "V4_ny_and_true_reclaim": lambda f: f["session"] == "new_york" and f["true_reclaim"] == 1,
    "V5_ny_and_first_attempt": lambda f: f["session"] == "new_york" and f["attempt_index"] == 1,
}


def _json_lines(path: Path) -> Iterable[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _half(day: str) -> str:
    for label, (start, end) in HALVES.items():
        if start <= day <= end:
            return label
    return "OUT_OF_RANGE"


def _profit_factor(values: list[float]) -> Optional[float]:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses:
        return round(wins / losses, 6)
    return math.inf if wins else None


class DayBars:
    """Corpus 15m day files with per-bar session-aware ORB mapping."""

    def __init__(self, corpus: Path) -> None:
        self._corpus = corpus
        self._cache: dict[tuple[str, str], list[dict]] = {}

    def bars(self, instrument: str, day: str) -> list[dict]:
        key = (instrument, day)
        if key not in self._cache:
            path = self._corpus / instrument / f"{instrument}_{day}.jsonl"
            self._cache[key] = list(_json_lines(path))
            if len(self._cache) > 32:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[key]

    @staticmethod
    def orb_of(bar: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
        """(orb_high, orb_low, orb_status) exactly as replay maps state.orb
        (replay_engine.py:1064): london-session bars use the london ORB."""
        if bar.get("session") == "london":
            return (
                bar.get("london_orb_high"),
                bar.get("london_orb_low"),
                bar.get("london_orb_status"),
            )
        return bar.get("orb_high"), bar.get("orb_low"), bar.get("orb_status")


def _features_for(
    daybars: DayBars, instrument: str, day: str, bar_ts: str, attempt_index: int
) -> Optional[dict]:
    bars = daybars.bars(instrument, day)
    target_iso = _ts(bar_ts).isoformat()
    idx = next(
        (
            i
            for i, b in enumerate(bars)
            if _ts(b["timestamp"]).isoformat() == target_iso
        ),
        None,
    )
    if idx is None:
        return None
    bar = bars[idx]
    orb_high, orb_low, orb_status = DayBars.orb_of(bar)
    if orb_high is None or orb_low is None:
        return None
    plan_entry = float(orb_high) + TICK * ENTRY_OFFSET_TICKS
    stop = max(
        float(orb_low) - TICK * STOP_BELOW_ORB_LOW_TICKS,
        plan_entry - TICK * MAX_ORB_STOP_TICKS[instrument],
    )
    risk = plan_entry - stop
    target = plan_entry + max(risk * 2.5, MIN_TARGET_POINTS[instrument])

    true_reclaim = 0
    prior_rejected = 0
    for prior in bars[:idx]:
        p_high, _p_low, p_status = DayBars.orb_of(prior)
        if p_high is None or abs(float(p_high) - float(orb_high)) > 1e-9:
            continue  # different ORB source/level (session change / reset)
        if float(prior["close"]) > float(orb_high):
            true_reclaim = 1
        if p_status == "rejected_high":
            prior_rejected = 1

    close = float(bar["close"])
    vwap = bar.get("vwap")
    hour_et = _ts(bar["timestamp"]).astimezone(ZoneInfo("America/New_York")).hour
    return {
        "instrument": instrument,
        "date": day,
        "bar_ts": target_iso,
        "session": bar.get("session"),
        "half": _half(day),
        "hour_et": hour_et,
        "attempt_index": attempt_index,
        "true_reclaim": true_reclaim,
        "prior_rejected_high": prior_rejected,
        "chase_ticks": round((close - plan_entry) / TICK, 2),
        "vwap_dist_ticks": (
            round((close - float(vwap)) / TICK, 2) if vwap is not None else None
        ),
        "orb_width_ticks": round((float(orb_high) - float(orb_low)) / TICK, 2),
        "trend_strength": bar.get("trend_strength"),
        "trend_direction": bar.get("trend_direction"),
        "plan_entry": round(plan_entry, 4),
        "plan_stop": round(stop, 4),
        "plan_target": round(target, 4),
        "risk_points": round(risk, 4),
        "decision_close": close,
    }


def _loser_mfe_r(
    m5_root: Path, feat: dict, slippage_ticks: float = 1.0
) -> Optional[float]:
    """Max favorable excursion (R units) before the stop prints, on 5m bars
    from the trigger bar forward. Diagnostic approximation: entry = the IOC
    fill (decision close + slip, capped at plan entry + tolerance), exit at
    the first 5m bar whose low ≤ stop; MFE = max(high) before/at that bar."""
    path = m5_root / feat["instrument"] / f"{feat['instrument']}_{feat['date']}.jsonl"
    if not path.exists():
        return None
    slip = slippage_ticks * TICK
    tol = IOC_TOL_TICKS[feat["instrument"]] * TICK
    fill = min(feat["decision_close"] + slip, feat["plan_entry"] + tol)
    risk = fill - feat["plan_stop"]
    if risk <= 0:
        return None
    trigger = _ts(feat["bar_ts"])
    best = None
    started = False
    for bar in _json_lines(path):
        ts = _ts(bar["timestamp"])
        if ts < trigger:
            continue
        started = True
        high = float(bar["high"])
        best = high if best is None else max(best, high)
        if float(bar["low"]) <= feat["plan_stop"]:
            break
    if not started or best is None:
        return None
    return round((best - fill) / risk, 3)


def _score(rows: list[dict]) -> dict:
    resolved = [r for r in rows if r["resolved"]]
    net = [r["pnl_after_commission"] for r in resolved]
    wins = sum(r["result"] == "WIN" for r in resolved)
    halves = {
        h: round(
            sum(
                r["pnl_after_commission"]
                for r in resolved
                if r["half"] == h
            ),
            2,
        )
        for h in HALVES
    }
    return {
        "attempts": len(rows),
        "fills": sum(r["filled"] for r in rows),
        "resolved": len(resolved),
        "win_rate": round(wins / len(resolved), 4) if resolved else None,
        "net_after_commission": round(sum(net), 2),
        "expectancy_per_fill": round(statistics.fmean(net), 4) if net else None,
        "profit_factor": _profit_factor(net),
        "h1_net": halves["H1"],
        "h2_net": halves["H2"],
        "both_halves_positive": (
            bool(halves["H1"] > 0 and halves["H2"] > 0) if resolved else None
        ),
        "n_ge_30": len(resolved) >= 30,
    }


def _verdict(score: dict) -> str:
    if not score["resolved"]:
        return "NO DATA"
    ok = (
        score["n_ge_30"]
        and (score["net_after_commission"] or 0) > 0
        and score["profit_factor"] is not None
        and not isinstance(score["profit_factor"], str)
        and score["profit_factor"] > 1
        and score["both_halves_positive"]
    )
    return "PASSES PRE-REGISTERED SLICE CRITERIA" if ok else "FAILS"


def _fmt_pf(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and math.isinf(v):
        return "∞"
    return f"{v:.3f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--m5", required=True, type=Path)
    parser.add_argument(
        "--raw-input",
        type=Path,
        default=REPO / "scripts/orb_reclaim_pr352_raw_trades_input.jsonl",
    )
    parser.add_argument(
        "--out", type=Path, default=REPO / "scripts/orb_reclaim_rule_anatomy_results.json"
    )
    parser.add_argument(
        "--raw", type=Path, default=REPO / "scripts/orb_reclaim_rule_anatomy_raw.jsonl"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO / "docs/orb-reclaim-strategy-rework-diagnosis-2026-07-27.md",
    )
    args = parser.parse_args()

    rows = list(_json_lines(args.raw_input))
    daybars = DayBars(args.corpus)

    # attempt_index: per (run_tag, instrument, date) chronological ordering.
    by_run_day: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_run_day[(r["run_tag"], r["instrument"], r["date"])].append(r)
    for group in by_run_day.values():
        group.sort(key=lambda r: r["bar_ts"])
        for i, r in enumerate(group, 1):
            r["attempt_index"] = i

    feature_cache: dict[tuple, Optional[dict]] = {}
    enriched: list[dict] = []
    missing = 0
    for r in rows:
        key = (r["instrument"], r["date"], r["bar_ts"], r["attempt_index"])
        fkey = key[:3]
        if fkey not in feature_cache:
            feature_cache[fkey] = _features_for(
                daybars, r["instrument"], r["date"], r["bar_ts"], r["attempt_index"]
            )
        feat = feature_cache[fkey]
        if feat is None:
            missing += 1
            continue
        merged = {**r, **{k: v for k, v in feat.items() if k not in r}}
        merged["attempt_index"] = r["attempt_index"]
        enriched.append(merged)
    if missing:
        print(f"WARNING: {missing} rows had no matching corpus bar", flush=True)

    diag = [r for r in enriched if r["run_tag"] in DIAGNOSTIC_TAGS]
    canonical_1t = [
        r for r in enriched if r["run_tag"] in CANONICAL_1T_TAGS + LANE_1T_TAGS
    ]
    # Lane rows duplicate all-session rows at the attempt level; for variant
    # scoring use all-session canonical rows (V1 uses #352's own lane runs,
    # restated from the lane rows directly).
    canonical_all = [r for r in enriched if r["run_tag"] in CANONICAL_1T_TAGS]
    lanes = {tag: [r for r in enriched if r["run_tag"] == tag] for tag in LANE_1T_TAGS}

    # ── Anatomy: feature separation on the DIAGNOSTIC substrate ────────────
    def split(rows_, keyfn) -> dict:
        buckets: dict[str, list[dict]] = defaultdict(list)
        for r in rows_:
            buckets[str(keyfn(r))].append(r)
        return {k: _score(v) for k, v in sorted(buckets.items())}

    anatomy = {
        "true_reclaim_vs_first_cross": split(
            diag, lambda r: "true_reclaim" if r["true_reclaim"] else "first_cross"
        ),
        "prior_rejected_high": split(diag, lambda r: bool(r["prior_rejected_high"])),
        "attempt_index": split(diag, lambda r: min(r["attempt_index"], 3)),
        "session": split(diag, lambda r: r["session"]),
        "instrument": split(diag, lambda r: r["instrument"]),
        "chase_quartile": {},
        "orb_width_tercile": {},
        "trend_strength": split(diag, lambda r: r["trend_strength"]),
        "hour_et_band": split(
            diag,
            lambda r: (
                "03-08" if 3 <= r["hour_et"] < 9 else
                "09-11" if 9 <= r["hour_et"] < 12 else
                "12-15" if 12 <= r["hour_et"] < 16 else "other"
            ),
        ),
    }
    chases = sorted(r["chase_ticks"] for r in diag)
    if chases:
        qs = [chases[int(len(chases) * q)] for q in (0.25, 0.5, 0.75)]
        def chase_bucket(r):
            c = r["chase_ticks"]
            if c <= qs[0]:
                return f"Q1(<= {qs[0]:g}t)"
            if c <= qs[1]:
                return f"Q2(<= {qs[1]:g}t)"
            if c <= qs[2]:
                return f"Q3(<= {qs[2]:g}t)"
            return f"Q4(> {qs[2]:g}t)"
        anatomy["chase_quartile"] = split(diag, chase_bucket)
    widths = sorted(r["orb_width_ticks"] for r in diag)
    if widths:
        ts_ = [widths[int(len(widths) * q)] for q in (1 / 3, 2 / 3)]
        def width_bucket(r):
            w = r["orb_width_ticks"]
            if w <= ts_[0]:
                return f"narrow(<= {ts_[0]:g}t)"
            if w <= ts_[1]:
                return f"mid(<= {ts_[1]:g}t)"
            return f"wide(> {ts_[1]:g}t)"
        anatomy["orb_width_tercile"] = split(diag, width_bucket)

    # ── Loser MFE anatomy (canonical 1t all-session fills) ──────────────────
    mfe_values: list[float] = []
    mfe_ge_half = 0
    mfe_ge_1r = 0
    losers = [
        r for r in canonical_all if r["resolved"] and r["result"] == "LOSS"
    ]
    for r in losers:
        mfe = _loser_mfe_r(args.m5, r)
        r["loser_mfe_r"] = mfe
        if mfe is not None:
            mfe_values.append(mfe)
            mfe_ge_half += mfe >= 0.5
            mfe_ge_1r += mfe >= 1.0
    loser_anatomy = {
        "losers_measured": len(mfe_values),
        "median_mfe_r": round(statistics.median(mfe_values), 3) if mfe_values else None,
        "share_mfe_ge_0.5R": (
            round(mfe_ge_half / len(mfe_values), 3) if mfe_values else None
        ),
        "share_mfe_ge_1.0R": (
            round(mfe_ge_1r / len(mfe_values), 3) if mfe_values else None
        ),
        "note": (
            "5m-granularity approximation (entry=IOC fill est., exit=first 5m "
            "bar through the stop); diagnostic for target-geometry only"
        ),
    }

    # ── Variant scoring (canonical substrates) ──────────────────────────────
    variants: dict[str, dict] = {}
    for name, pred in VARIANTS.items():
        scored = _score([r for r in canonical_all if pred(r)])
        scored["verdict"] = _verdict(scored)
        # slippage sensitivity from matching 2-4t canonical rows
        slip = {}
        for t in (2, 3, 4):
            tier_rows = [
                r
                for r in enriched
                if r["run_tag"] in (f"MNQ_ioc_{t}tick", f"MES_ioc_{t}tick")
                and pred(r)
            ]
            s = _score(tier_rows)
            slip[f"{t}t"] = {
                "net": s["net_after_commission"],
                "pf": s["profit_factor"],
                "resolved": s["resolved"],
            }
        scored["slippage"] = slip
        # diagnostic (uncensored) view of the same filter, context only
        scored["diagnostic_view"] = _score([r for r in diag if pred(r)])
        variants[name] = scored
    lane_restate = {tag: _score(rows_) for tag, rows_ in lanes.items()}

    passing = [k for k, v in variants.items() if v["verdict"].startswith("PASSES")]
    # Candidate-flagging on the DIAGNOSTIC substrate (explicitly non-claim):
    # same acceptance shape, applied to the uncensored view.
    diag_candidates = [
        k
        for k, v in variants.items()
        if v["diagnostic_view"]["resolved"] >= 20
        and (v["diagnostic_view"]["net_after_commission"] or 0) > 0
        and v["diagnostic_view"]["both_halves_positive"]
    ]
    if passing:
        verdict = (
            f"SLICE-LEVEL CANDIDATES: {', '.join(passing)} — each still requires "
            "its own isolated filtered replay before any promotion decision"
        )
    elif diag_candidates:
        verdict = (
            "NO VARIANT PASSES ON THE CANONICAL SUBSTRATE (breaker censoring "
            "makes both-halves unreachable there); DIAGNOSTIC-SUBSTRATE "
            f"CANDIDATE(S): {', '.join(diag_candidates)} — candidate-flag only, "
            "requires an isolated filtered replay for any claim"
        )
    else:
        verdict = (
            "NO PRE-REGISTERED VARIANT PASSES SLICE CRITERIA ON EITHER "
            "SUBSTRATE — rule-level rework must go deeper than these filters"
        )

    results = {
        "meta": {
            "main_sha": _git("rev-parse", "HEAD"),
            "pr352_branch_sha": PR352_BRANCH_SHA,
            "raw_input": str(args.raw_input),
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "frozen_rule": (
                "reclaimed_high = ANY 15m close-cross up through session ORB "
                "high (no pullback requirement despite docstring); VWAP-above; "
                "TRENDING; LONG-only; bracket entry+2t / max(ORBlow-4t, "
                "entry-80/40t) / entry+max(2.5R,15pt)"
            ),
            "substrates": {
                "diagnostic": list(DIAGNOSTIC_TAGS),
                "canonical": list(CANONICAL_1T_TAGS),
                "lanes": list(LANE_1T_TAGS),
            },
            "caveat": (
                "variant scores are post-hoc filters of replayed accounts and "
                "inherit the unfiltered account's position/breaker path; a "
                "passing variant requires its own isolated filtered replay"
            ),
        },
        "verdict": verdict,
        "anatomy_diagnostic_substrate": anatomy,
        "loser_mfe_anatomy": loser_anatomy,
        "variants_canonical_substrate": variants,
        "session_lanes_restated_pr352": lane_restate,
    }

    def _json_safe(v):
        if isinstance(v, dict):
            return {k: _json_safe(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_json_safe(x) for x in v]
        if isinstance(v, float) and math.isinf(v):
            return "inf"
        return v

    args.out.write_text(json.dumps(_json_safe(results), indent=2) + "\n")
    with args.raw.open("w", encoding="utf-8") as handle:
        for r in sorted(
            enriched, key=lambda x: (x["run_tag"], x["date"], x["bar_ts"])
        ):
            handle.write(json.dumps(r, sort_keys=True) + "\n")

    # ── report ──────────────────────────────────────────────────────────────
    def table(block: dict, title: str) -> list[str]:
        lines = [
            f"### {title}",
            "",
            "| Split | Attempts | Fills | Resolved | WR | Net after RT | Exp/fill | PF | H1 | H2 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for key, s in block.items():
            wr = f"{100 * s['win_rate']:.1f}%" if s["win_rate"] is not None else "—"
            exp = f"${s['expectancy_per_fill']:.2f}" if s["expectancy_per_fill"] is not None else "—"
            lines.append(
                f"| {key} | {s['attempts']} | {s['fills']} | {s['resolved']} | {wr} | "
                f"${s['net_after_commission']:,.2f} | {exp} | {_fmt_pf(s['profit_factor'])} | "
                f"${s['h1_net']:,.2f} | ${s['h2_net']:,.2f} |"
            )
        lines.append("")
        return lines

    lines = [
        "# ORB Reclaim strategy-logic rework — Pass 1: rule anatomy diagnosis",
        "",
        f"**Verdict: {results['verdict']}**",
        "",
        f"Pinned code: `{results['meta']['main_sha']}`; input: PR #352 raw trades "
        f"(branch @ `{PR352_BRANCH_SHA[:12]}`), pinned verbatim in-repo.",
        "",
        "## The frozen rule, as PROVEN (not as documented)",
        "",
        "- `reclaimed_high` is ANY 15m close-cross up through the session ORB",
        "  high — the docstring's \"rejected above, pulled back, now",
        "  reclaiming\" pattern is NOT required by the state machine",
        "  (csv_to_replay.derive_orb_status; proven exact by #356's 131/131",
        "  bracket reconstruction). The `true_reclaim` feature below measures",
        "  the documented pattern explicitly.",
        "- Gates: VWAP-above, TRENDING, GEX-not-positive-gamma; LONG-only.",
        "- Bracket: entry ORBhigh+2t; stop max(ORBlow−4t, entry−80t/40t);",
        "  target entry+max(2.5R, 15pt).",
        "",
        "## Anatomy — DIAGNOSTIC substrate (breaker-off, uncensored; never a claim)",
        "",
    ]
    for key, title in (
        ("true_reclaim_vs_first_cross", "True reclaim (documented pattern) vs first cross (implemented pattern)"),
        ("prior_rejected_high", "Prior explicit rejected_high earlier same day"),
        ("attempt_index", "Attempt index within day (3 = 3rd or later)"),
        ("session", "Session"),
        ("instrument", "Instrument"),
        ("chase_quartile", "Chase distance at decision (quartiles, ticks past plan)"),
        ("orb_width_tercile", "ORB width (stop-geometry driver, terciles)"),
        ("trend_strength", "Trend strength on trigger bar"),
        ("hour_et_band", "Hour of day (ET bands)"),
    ):
        lines += table(anatomy[key], title)
    lines += [
        "## Loser MFE anatomy (canonical 1t fills, 5m approximation)",
        "",
        f"- Losers measured: {loser_anatomy['losers_measured']}",
        f"- Median MFE before stop: {loser_anatomy['median_mfe_r']}R",
        f"- Losers reaching ≥0.5R favorable first: {loser_anatomy['share_mfe_ge_0.5R']}",
        f"- Losers reaching ≥1.0R favorable first: {loser_anatomy['share_mfe_ge_1.0R']}",
        f"- {loser_anatomy['note']}.",
        "",
        "## Pre-registered variants — CANONICAL substrate (breaker-on)",
        "",
        "| Variant | Resolved | WR | Net after RT | PF | Both halves + | 2t net | 3t net | 4t net | Verdict |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---|",
    ]
    for name, s in variants.items():
        wr = f"{100 * s['win_rate']:.1f}%" if s["win_rate"] is not None else "—"
        lines.append(
            f"| {name} | {s['resolved']} | {wr} | ${s['net_after_commission']:,.2f} | "
            f"{_fmt_pf(s['profit_factor'])} | {s['both_halves_positive']} | "
            f"${s['slippage']['2t']['net']:,.2f} | ${s['slippage']['3t']['net']:,.2f} | "
            f"${s['slippage']['4t']['net']:,.2f} | {s['verdict']} |"
        )
    lines += [
        "",
        "Diagnostic (uncensored) views of the same filters are in the results",
        "JSON (`variants_canonical_substrate.*.diagnostic_view`).",
        "",
        "## #352 session-isolated lanes (authoritative, restated)",
        "",
    ]
    lines += table(lane_restate, "Session lanes (independent accounts, breaker on)")
    lines += [
        "## Caveats",
        "",
        "- Variant scores are post-hoc filters of replayed accounts: they",
        "  inherit the unfiltered account's one-position blocking and breaker",
        "  path. A passing variant is a CANDIDATE for its own isolated",
        "  filtered replay — never directly promotable from this pass.",
        "- Diagnostic substrate is breaker-off by construction and is used",
        "  only for feature separation, per the operator's #352 ruling.",
        "- Loser MFE is a 5m-granularity approximation.",
        "- No engine, config, or Pine change is made or recommended here.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "python scripts/orb_reclaim_rule_anatomy.py \\",
        "  --corpus data/replay_corpus_v1_market_condition_fixed \\",
        "  --m5 data/replay_polygon_5m \\",
        "  --raw-input scripts/orb_reclaim_pr352_raw_trades_input.jsonl \\",
        "  --out scripts/orb_reclaim_rule_anatomy_results.json \\",
        "  --raw scripts/orb_reclaim_rule_anatomy_raw.jsonl \\",
        "  --report docs/orb-reclaim-strategy-rework-diagnosis-2026-07-27.md",
        "```",
        "",
    ]
    args.report.write_text("\n".join(lines).rstrip() + "\n")
    print(
        json.dumps(
            _json_safe(
                {
                    "verdict": verdict,
                    "variants": {
                        k: {
                            "resolved": v["resolved"],
                            "net": v["net_after_commission"],
                            "pf": v["profit_factor"],
                            "both_halves": v["both_halves_positive"],
                            "verdict": v["verdict"],
                        }
                        for k, v in variants.items()
                    },
                    "loser_mfe": loser_anatomy,
                }
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
