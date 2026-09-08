"""Read-only reconstruction of the frozen inverse-ORB IOC contract.

PREREQUISITE: this needs the local 5-minute bar corpus at data/replay_polygon_5m/MNQ,
which is gitignored (.gitignore:25) and therefore NOT part of a fresh clone. Without
it the sweep cannot run; the committed JSON artifact is the durable record of the
result. This is the same constraint that left PR #491's proof without a generator.

Nothing here imports or mutates runtime code. It replays the frozen 63-arm
population against data/replay_polygon_5m/MNQ under a parameterised IOC
tolerance. Every other contract term is held fixed.
"""
import json, glob, os
from datetime import datetime, timezone

TICK = 0.25
MULT = 2.0          # MNQ: $2 per index point
COMMISSION = 1.48   # round trip
DATA = "data/replay_polygon_5m/MNQ"
PROOF = "scripts/inverse_orb_canonical_ioc_proof_2026-09-07.json"

_bars_cache = {}
def bars_for(date):
    if date not in _bars_cache:
        p = f"{DATA}/MNQ_{date}.jsonl"
        rows = []
        if os.path.exists(p):
            with open(p) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        rows.sort(key=lambda b: b["timestamp"])
        _bars_cache[date] = rows
    return _bars_cache[date]

def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00").replace(" ", "T"))

def adverse_ticks(row):
    """Ticks of adverse arrival slippage vs the planned (source) entry."""
    e, m, d = row["source_entry"], row["fill_market"], row["inverse_direction"]
    return ((m - e) if d == "LONG" else (e - m)) / TICK

def resolve(row, forward_days=1):
    """Walk bars from fill_ts; pessimistic same-bar (stop before target)."""
    d = row["inverse_direction"]
    entry = row["fill_market"] + (TICK if d == "LONG" else -TICK)   # 1 adverse tick, market leg
    stop, target = row["inverse_stop"], row["inverse_target"]
    start = ts(row["fill_ts"])
    dates = [row["date"]]
    if forward_days > 1:                                  # allow spill to later sessions
        alldates = sorted(os.path.basename(p)[4:-6] for p in glob.glob(f"{DATA}/MNQ_*.jsonl"))
        if not alldates:
            raise RuntimeError(
                f"no bar files under {DATA} — that path is gitignored and is not in a "
                "fresh clone. Restore the local replay corpus to re-run this sweep.")
        if row["date"] not in alldates:
            raise RuntimeError(f"no bar file for {row['date']} under {DATA}")
        i = alldates.index(row["date"])
        dates = alldates[i:i + forward_days]
    for dt in dates:
        for b in bars_for(dt):
            if ts(b["timestamp"]) < start:
                continue
            hi, lo = b["high"], b["low"]
            hit_stop = (lo <= stop) if d == "LONG" else (hi >= stop)
            hit_tgt  = (hi >= target) if d == "LONG" else (lo <= target)
            if hit_stop:                                   # pessimistic: stop wins ties
                px = stop - TICK if d == "LONG" else stop + TICK   # market leg slippage
                gross = (px - entry) * MULT if d == "LONG" else (entry - px) * MULT
                return "STOP_HIT", b["timestamp"], round(gross, 2)
            if hit_tgt:                                    # limit exit, no slippage
                gross = (target - entry) * MULT if d == "LONG" else (entry - target) * MULT
                return "TARGET_HIT", b["timestamp"], round(gross, 2)
    return None, None, None

def load_rows():
    return json.load(open(PROOF))["rows"]


# ─── Sweep driver ─────────────────────────────────────────────────────────────
# Read-only. Replays the frozen 63-arm population at a range of IOC tolerances,
# holding every other contract term fixed. Writes the JSON artifact next to the
# canonical proof. Run: PYTHONPATH=. python3 scripts/inverse_orb_ioc_tolerance_sweep.py

TOLERANCES = [4, 8, 12, 16, 24, 32]
CONTROL = {"attempts": 63, "fills": 57, "no_fills": 6, "net": 1026.64,
           "H1_net": 546.08, "H2_net": 480.56}


def outcome(row, tol):
    if adverse_ticks(row) > tol:
        return None
    if row["status"] == "FILLED":                 # frozen arms replay identically
        return dict(net=row["net"], gross=row["gross"], reason=row["exit_reason"], ts=row["fill_ts"])
    reason, _et, gross = resolve(row, forward_days=5)
    if reason is None:
        return dict(net=None, gross=None, reason="UNRESOLVED", ts=row["fill_ts"])
    return dict(net=round(gross - COMMISSION, 2), gross=gross, reason=reason, ts=row["fill_ts"])


def metrics(items):
    fills = [i for i in items if i is not None]
    res = [i for i in fills if i["net"] is not None]
    nets = [i["net"] for i in res]
    gp = sum(n for n in nets if n > 0)
    gl = -sum(n for n in nets if n < 0)
    eq = peak = dd = 0.0
    for i in sorted(res, key=lambda x: ts(x["ts"])):
        eq += i["net"]; peak = max(peak, eq); dd = max(dd, peak - eq)
    return dict(
        attempts=len(items), fills=len(fills), no_fills=len(items) - len(fills),
        wins=sum(1 for i in res if i["reason"] == "TARGET_HIT"),
        losses=sum(1 for i in res if i["reason"] == "STOP_HIT"),
        gross=round(sum(i["gross"] for i in res), 2),
        commission=round(len(res) * COMMISSION, 2),
        net=round(sum(nets), 2),
        pf=round(gp / gl, 4) if gl else None,
        expectancy_per_attempt=round(sum(nets) / len(items), 4) if items else None,
        expectancy_per_fill=round(sum(nets) / len(res), 4) if res else None,
        max_dd=round(dd, 2),
    )


def main():
    proof = json.load(open(PROOF))
    rows = proof["rows"]
    mid = proof["halves"]["midpoint_day"]
    h1 = [r for r in rows if r["date"] < mid]
    h2 = [r for r in rows if r["date"] >= mid]

    out = {"manifest": dict(proof["manifest"], sweep_note="IOC tolerance sensitivity; all other terms frozen"),
           "control_expected": CONTROL, "tolerances": {}}
    for t in TOLERANCES:
        m = metrics([outcome(r, t) for r in rows])
        m["H1"] = metrics([outcome(r, t) for r in h1])
        m["H2"] = metrics([outcome(r, t) for r in h2])
        m["sessions"] = {s: metrics([outcome(r, t) for r in rows if r["session"] == s])
                         for s in ("asian", "london", "new_york")}
        out["tolerances"][str(t)] = m

    c = out["tolerances"]["8"]
    out["control_reproduced"] = (
        c["attempts"] == CONTROL["attempts"] and c["fills"] == CONTROL["fills"]
        and c["no_fills"] == CONTROL["no_fills"] and abs(c["net"] - CONTROL["net"]) < 0.005
        and abs(c["H1"]["net"] - CONTROL["H1_net"]) < 0.005
        and abs(c["H2"]["net"] - CONTROL["H2_net"]) < 0.005)

    marginal = []
    for r in sorted(rows, key=adverse_ticks):
        adv = adverse_ticks(r)
        if adv <= 8 or adv > max(TOLERANCES):
            continue
        reason, _et, gross = resolve(r, forward_days=5)
        px = r["fill_market"] + (TICK if r["inverse_direction"] == "LONG" else -TICK)
        marginal.append(dict(bar_ts=r["bar_ts"], direction=r["inverse_direction"],
                             planned_entry=r["source_entry"], arrival_price=r["fill_market"],
                             adverse_ticks=round(adv, 1),
                             first_tolerance_permitting_fill=next(t for t in TOLERANCES if adv <= t),
                             fill_price=px, stop=r["inverse_stop"], target=r["inverse_target"],
                             outcome=reason, net=round(gross - COMMISSION, 2), session=r["session"]))
    out["marginal_fills"] = marginal
    dest = "scripts/inverse_orb_ioc_tolerance_sensitivity_2026-09-08.json"
    json.dump(out, open(dest, "w"), indent=2)
    print(("PASS" if out["control_reproduced"] else "FAIL"), "->", dest)


if __name__ == "__main__":
    main()
