#!/usr/bin/env python3
"""MNQ entry-refresh comparison study (read-only, Phase A).

Extends the validated harness from scripts/orb_market_entry_study.py (PR #143)
and scripts/orb_breakout_entry_study.py (PR #261): same arms
(logs/retest_baseline_off/MNQ), same 5m bars (data/replay_polygon_5m/MNQ),
same PaperBroker resolve (pessimistic both-hit), zero new fill assumptions.

Question: when a qualified MNQ setup is ENTRY_DETACHED at evaluation time
(price already ran past the entry in the trade direction), which refresh
policy — if any — recovers positive expectancy without turning misses into
chases? Broken out per strategy (orb_breakout / vwap_hold / orb_reclaim),
per exit mode (runner / static), walk-forward halves per strategy.

Policies (predeclared, causal, no lookahead):
  static_reject      — live baseline: detached (gap>0) => NO_TRADE;
                       else resting order at level, 20-min window.
  translate_capN     — detached => market fill (first 5m open +1 tick slip),
                       stop/target TRANSLATED by the fill offset (original R
                       and R:R preserved). Reject if chase distance > cap.
                       Caps: 8t, 16t, 32t, 0.25R, 0.5R, 1.0R, unbounded.
  structural_minrr   — detached => market fill, KEEP original structural
                       stop/target, recompute geometry. Reject if new RR<1.5,
                       new risk>1.5x original, or reward<=0.
  confirm5m_16t      — detached => wait (<=20 min) for first 5m CLOSE beyond
                       the level in trade direction, enter next 5m open
                       +1 tick, translated bracket, 16-tick chase cap
                       measured from the level at that open.

Costs (predeclared): 1 tick adverse slip on every market fill (in px),
$1.48 round-trip commission per resolved trade (Tradovate micro approx).
Cells with resolved n<30 are INCONCLUSIVE by rule — no directional claims.

Also evaluates the 35 strategy-attributed LIVE detached incidents pulled from
the box journals 2026-06-01..2026-07-13 (no outcome resolution — local 5m
data ends 2026-06-26), incl. the named 2026-07-13T02:15 vwap_hold incident.
Fixture (REDACTED to derived metrics; live entry/stop/target levels are
strategy internals, kept out of the public repo):
scripts/fixtures/mnq_detached_incidents_2026-07-13.json

Reproduce: python3 scripts/mnq_entry_refresh_study.py
Full write-up: docs/mnq-entry-refresh-study-2026-07-13.md
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from context.bar_history import _parse_dt  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402

STRATS = {"orb_breakout", "vwap_hold", "orb_reclaim"}
TICK = 0.25
COMMISSION_RT = 1.48
JOURNALS = REPO / "logs/retest_baseline_off/MNQ"
FINE_ROOT = REPO / "data/replay_polygon_5m/MNQ"
FIXTURES = REPO / "scripts/fixtures/mnq_detached_incidents_2026-07-13.json"
RESULTS = REPO / "scripts/mnq_entry_refresh_results.json"


def load_arms() -> list[dict]:
    arms = []
    for path in sorted(JOURNALS.glob("journal_*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            setup = row.get("setup") or {}
            if (
                row.get("decision") != "TRADE"
                or (row.get("risk_check") or {}).get("result") != "APPROVED"
                or setup.get("strategy") not in STRATS
            ):
                continue
            ts = _parse_dt(str(row.get("bar_ts") or ""))
            if ts is None:
                continue
            arms.append(
                {
                    "armed_at": ts + timedelta(minutes=15),
                    "direction": str(setup["direction"]).upper(),
                    "entry": float(setup["entry"]),
                    "stop": float(setup["stop"]),
                    "target": float(setup["target"]),
                    "strategy": str(setup["strategy"]),
                }
            )
    arms.sort(key=lambda a: a["armed_at"])
    return arms


def load_bars(day: str) -> list[dict]:
    path = FINE_ROOT / f"MNQ_{day}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        ts = _parse_dt(str(row.get("timestamp") or ""))
        if ts is not None:
            out.append(
                {
                    "ts": ts,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            )
    out.sort(key=lambda b: b["ts"])
    return out


def resting_fill(arm: dict, after: list[dict]) -> tuple[str, float, object]:
    """Shared non-detached branch: resting order at the level, 20-min window."""
    long = arm["direction"] == "LONG"
    level = arm["entry"]
    deadline = arm["armed_at"] + timedelta(minutes=20)
    for b in after:
        if b["ts"] > deadline:
            break
        hit = (b["high"] >= level) if long else (b["low"] <= level)
        if hit:
            px = level + TICK if long else level - TICK
            return ("FILLED", px, b["ts"])
    return ("NO_FILL", 0.0, None)


def apply_policy(arm: dict, bars: list[dict], policy: str, cap_ticks: float | None,
                 cap_r: float | None) -> dict:
    """Returns dict: status, fill px, executed stop/target, fill_ts, gap_ticks."""
    after = [b for b in bars if b["ts"] >= arm["armed_at"]]
    if not after:
        return {"status": "NO_DATA", "gap": 0.0}
    long = arm["direction"] == "LONG"
    level, stop, target = arm["entry"], arm["stop"], arm["target"]
    mkt = after[0]["open"]
    gap = (mkt - level) / TICK if long else (level - mkt) / TICK  # >0 = ran past entry
    risk_ticks = abs(level - stop) / TICK

    base = {"gap": gap, "detached": gap > 0}
    if gap <= 0:
        st, px, ts = resting_fill(arm, after)
        if st != "FILLED":
            return {**base, "status": st}
        return {**base, "status": "FILLED", "px": px, "stop": stop, "target": target,
                "fill_ts": ts}

    # --- detached branch: policy-specific ---
    if policy == "static_reject":
        return {**base, "status": "DETACH_REJECTED"}

    if policy == "translate":
        cap = cap_ticks if cap_ticks is not None else cap_r * risk_ticks
        if gap > cap:
            return {**base, "status": "CAP_REJECTED"}
        px = mkt + TICK if long else mkt - TICK
        off = px - level
        return {**base, "status": "FILLED", "px": px, "stop": stop + off,
                "target": target + off, "fill_ts": after[0]["ts"]}

    if policy == "structural_minrr":
        px = mkt + TICK if long else mkt - TICK
        risk = abs(px - stop)
        reward = (target - px) if long else (px - target)
        if reward <= 0:
            return {**base, "status": "REJECTED_TARGET_PASSED"}
        if risk > 1.5 * abs(level - stop):
            return {**base, "status": "REJECTED_STOP_TOO_WIDE"}
        if reward / risk < 1.5:
            return {**base, "status": "REJECTED_BAD_RR"}
        return {**base, "status": "FILLED", "px": px, "stop": stop, "target": target,
                "fill_ts": after[0]["ts"]}

    if policy == "confirm5m_16t":
        deadline = arm["armed_at"] + timedelta(minutes=20)
        for i, b in enumerate(after):
            if b["ts"] > deadline:
                break
            confirmed = (b["close"] > level) if long else (b["close"] < level)
            if confirmed and i + 1 < len(after):
                nb = after[i + 1]
                d = (nb["open"] - level) / TICK if long else (level - nb["open"]) / TICK
                if d > 16:
                    return {**base, "status": "CAP_REJECTED"}
                px = nb["open"] + TICK if long else nb["open"] - TICK
                off = px - level
                return {**base, "status": "FILLED", "px": px, "stop": stop + off,
                        "target": target + off, "fill_ts": nb["ts"]}
        return {**base, "status": "NO_CONFIRM"}

    raise ValueError(policy)


def resolve(arm: dict, fill: dict, bars: list[dict], *, runner: bool) -> tuple[str, float]:
    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=0.0,
        pessimistic_both_hit=True,
        runner_mode=runner,
        runner_activation_r=1.0,
        runner_trail_r=0.5,
    )
    broker.execute_bracket(
        BracketOrder(
            instrument="MNQ",
            direction=arm["direction"],
            entry=fill["px"],
            stop=fill["stop"],
            target=fill["target"],
            rr_ratio=2.0,
            strategy=arm["strategy"],
            contracts=1,
        )
    )
    for b in bars:
        if b["ts"] <= fill["fill_ts"]:
            continue
        out = broker.resolve_position(NextBarOHLC(high=b["high"], low=b["low"]))
        if out is not None:
            return out.result, float(out.pnl_dollars or 0.0) - COMMISSION_RT
    return ("OPEN", 0.0)


def summarize(rows: list[dict]) -> dict:
    resolved = [r for r in rows if r["outcome"] in {"WIN", "LOSS", "BREAKEVEN"}]
    pnls = [r["pnl"] for r in resolved]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return {
        "arms": len(rows),
        "filled": sum(1 for r in rows if r["status"] == "FILLED"),
        "detached_arms": sum(1 for r in rows if r.get("detached")),
        "detached_filled": sum(1 for r in rows if r.get("detached") and r["status"] == "FILLED"),
        "resolved": len(resolved),
        "win_rate": round(len(wins) / len(resolved), 3) if resolved else None,
        "net_pnl": round(sum(pnls), 2),
        "expectancy": round(statistics.fmean(pnls), 2) if pnls else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if wins and losses else None,
    }


POLICIES = [
    ("static_reject", None, None),
    ("translate_cap8t", 8, None),
    ("translate_cap16t", 16, None),
    ("translate_cap32t", 32, None),
    ("translate_cap025R", None, 0.25),
    ("translate_cap05R", None, 0.5),
    ("translate_cap10R", None, 1.0),
    ("translate_unbounded", 999999, None),
    ("structural_minrr", None, None),
    ("confirm5m_16t", None, None),
]


def run_policy(arms: list[dict], policy: str, cap_t, cap_r, *, runner: bool,
               cache: dict) -> list[dict]:
    rows = []
    base_policy = "translate" if policy.startswith("translate") else policy
    for arm in arms:
        day = arm["armed_at"].date().isoformat()
        bars = cache.setdefault(day, load_bars(day))
        fill = apply_policy(arm, bars, base_policy, cap_t, cap_r)
        if fill["status"] != "FILLED":
            rows.append({**fill, "outcome": "NO_FILL", "pnl": 0.0,
                         "strategy": arm["strategy"]})
            continue
        outcome, pnl = resolve(arm, fill, bars, runner=runner)
        rows.append({**fill, "outcome": outcome, "pnl": pnl, "strategy": arm["strategy"]})
    return rows


def eval_fixtures() -> list[dict]:
    """Policy decisions for the live box incidents (no outcome data).

    The public fixture is REDACTED to derived metrics (detachment in ticks
    and R, precomputed structural verdict) — the live system's absolute
    entry/stop/target levels are strategy internals and stay out of the
    public repo. Cap decisions derive from the gap metrics directly; the
    structural verdict was computed from the full geometry at redaction
    time (all 35 incidents: REJECTED_TARGET_PASSED).
    """
    incidents = [r for r in json.loads(FIXTURES.read_text()) if r.get("strategy")]
    out = []
    for inc in incidents:
        gap_t, gap_r = inc.get("gap_ticks"), inc.get("gap_R")
        if gap_t is None:
            out.append({"ts": inc["ts"], "strategy": inc["strategy"], "skip": "missing fields"})
            continue
        decisions = {
            "translate_cap8t": "TRADE" if gap_t <= 8 else "CAP_REJECTED",
            "translate_cap05R": "TRADE" if gap_r is not None and gap_r <= 0.5 else "CAP_REJECTED",
            "translate_cap10R": "TRADE" if gap_r is not None and gap_r <= 1.0 else "CAP_REJECTED",
            "structural_minrr": inc.get("structural_verdict"),
            "translate_unbounded": "TRADE",
        }
        out.append({
            "ts": inc["ts"], "strategy": inc["strategy"], "direction": inc["direction"],
            "gap_ticks": gap_t, "gap_R": gap_r,
            "decisions": decisions,
        })
    return out


def main() -> None:
    arms = load_arms()
    print(f"MNQ arms: {len(arms)} "
          f"({ {s: sum(1 for a in arms if a['strategy']==s) for s in sorted(STRATS)} })")
    cache: dict = {}
    results: dict = {}
    for pol, cap_t, cap_r in POLICIES:
        for exit_name, runner in (("runner", True), ("static", False)):
            rows = run_policy(arms, pol, cap_t, cap_r, runner=runner, cache=cache)
            key = f"{pol}|{exit_name}"
            by_strat = {}
            for s in sorted(STRATS):
                idx = [i for i, a in enumerate(arms) if a["strategy"] == s]
                srows = [rows[i] for i in idx]
                mid = len(idx) // 2
                det = [r for r in srows if r.get("detached")]
                by_strat[s] = {
                    "all": summarize(srows),
                    "first_half": summarize(srows[:mid]),
                    "second_half": summarize(srows[mid:]),
                    "detached_only": summarize(det),
                }
            results[key] = by_strat
    fixtures = eval_fixtures()

    RESULTS.write_text(json.dumps({"cells": results, "fixtures": fixtures}, indent=1))

    # -- compact report --
    for s in sorted(STRATS):
        print(f"\n{'='*100}\n{s}\n{'='*100}")
        print(f"{'policy|exit':28s} {'n':>4} {'detN':>5} {'net$':>10} {'exp':>7} "
              f"{'WR':>5} {'PF':>6}  halves(exp)      detached-only: n / net$ / exp")
        for pol, _, _ in POLICIES:
            for ex in ("runner", "static"):
                c = results[f"{pol}|{ex}"][s]
                a, f, sh, d = c["all"], c["first_half"], c["second_half"], c["detached_only"]
                print(f"{pol+'|'+ex:28s} {a['resolved']:>4} {a['detached_filled']:>5} "
                      f"{a['net_pnl']:>10.2f} {str(a['expectancy']):>7} "
                      f"{str(a['win_rate']):>5} {str(a['profit_factor']):>6}  "
                      f"({f['expectancy']}, {sh['expectancy']})   "
                      f"{d['resolved']} / {d['net_pnl']} / {d['expectancy']}")

    print(f"\n{'='*100}\nLIVE FIXTURES (geometry-only, {len(fixtures)} incidents)\n{'='*100}")
    for fx in fixtures:
        if "skip" in fx:
            continue
        d = fx["decisions"]
        print(f"{fx['ts'][:16]} {fx['strategy']:13s} {fx['direction']:5s} "
              f"gap={fx['gap_ticks']:>7.1f}t ({fx['gap_R']}R)  "
              f"cap8t={d['translate_cap8t'][:4]} cap0.5R={d['translate_cap05R'][:4]} "
              f"cap1R={d['translate_cap10R'][:4]} structural={d['structural_minrr']}")


if __name__ == "__main__":
    main()
