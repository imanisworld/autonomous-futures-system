"""R7 independent re-derivation (outcome-blind). Session a3488d14 — NOT the R6 author.

Imports NOTHING from the repo (no research/, context/, strategy/, scripts/). Everything is
re-implemented from the prereg text (§3 levels, §4.1 LC_ZONE, §5 events, §5.1 reduction, §6
hypotheses, §9.4 gap rule) and from the bracket arithmetic quoted in the attestation.
Reads only: data/replay_polygon_v2/{MNQ,MES}/*.jsonl (source bars) and the three frozen
candidate/feature rows extracted to frozen_rows.json. Never touches outcomes.sealed.jsonl.
"""
import json, glob, statistics, sys
from datetime import datetime, timedelta, time, date
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")
TICK = {"MNQ": 0.25, "MES": 0.25}           # config/futures_contracts: both 0.25 (verified)
TAU, PROX, K, R, N_ACC, DMAX, MTRW, WIN = 0.25, 0.5, 4, 8, 2, 1.0, 64, 960
GAP_MIN = 45
import os
S = os.path.dirname(os.path.abspath(__file__))  # published copy: inputs/outputs live beside this file; run from the repo root
FROZEN = json.load(open(f"{S}/frozen_rows.json"))

def load_bars(inst):
    bars = []
    for f in sorted(glob.glob(f"data/replay_polygon_v2/{inst}/{inst}_*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            bars.append({"ts": datetime.fromisoformat(r["timestamp"]), "open": float(r["open"]), "high": float(r["high"]),
                         "low": float(r["low"]), "close": float(r["close"]), "volume": r.get("volume"), "raw": r})
    bars.sort(key=lambda b: b["ts"])
    return bars

def et(ts): return ts.astimezone(ET)
def tday(ts): return (et(ts) + timedelta(hours=6)).date()          # CME day rolls 18:00 ET
def tweek(ts): d = tday(ts); i = d.isocalendar(); return (i[0], i[1])
def session(ts):
    t = et(ts).time()
    if t >= time(18) or t < time(3): return "asian"
    if t < time(9, 30): return "london"
    if t < time(17): return "new_york"
    return "off_hours"
def mtr(bars):
    trs = [max(c["high"]-c["low"], abs(c["high"]-p["close"]), abs(c["low"]-p["close"])) for p, c in zip(bars, bars[1:])]
    return statistics.median(trs) if trs else None
def slot_open(t):
    e = et(t); wd, hr = e.weekday(), e.hour
    return not (hr == 17 or wd == 5 or (wd == 4 and hr >= 17) or (wd == 6 and hr < 18))
def gap_minutes(bars, t0, t1):
    present = {b["ts"] for b in bars if t0 <= b["ts"] < t1}
    miss, t = 0, t0
    while t < t1:
        if slot_open(t) and t not in present: miss += 1
        t += timedelta(minutes=15)
    return miss * 15
def cme_hours(t0, t1):
    if t1 <= t0: return 0.0
    cur, end, h = et(t0), et(t1), 0.0
    while cur < end:
        nxt = min(end, (cur + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0))
        if slot_open(cur): h += (nxt - cur).total_seconds() / 3600
        cur = nxt
    return round(h, 2)

# ── §4.1 LC_ZONE re-implementation ──────────────────────────────────────────
def aggregate(bars, minutes):
    out = {}
    for b in bars:
        k = int(b["ts"].timestamp()) // (minutes * 60)
        if k not in out: out[k] = {"ts": b["ts"], "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]}
        else:
            o = out[k]; o["high"] = max(o["high"], b["high"]); o["low"] = min(o["low"], b["low"]); o["close"] = b["close"]
    return [out[k] for k in sorted(out)]
def zones(agg):
    if len(agg) < 3: return []
    m = mtr(agg)
    if not m: return []
    zs = []
    for i in range(1, len(agg)):
        body = agg[i]["close"] - agg[i]["open"]
        if abs(body) < 1.2 * m: continue
        base = agg[i-1]
        kind = "demand" if body > 0 else "supply"
        tests, broken = 0, False
        for b in agg[i+1:]:                          # skip the impulse bar itself
            if kind == "supply" and b["close"] > base["high"]: broken = True; break
            if kind == "demand" and b["close"] < base["low"]: broken = True; break
            if b["low"] <= base["high"] and b["high"] >= base["low"]: tests += 1
        zs.append({"kind": kind, "top": base["high"], "bottom": base["low"], "formed_ts": base["ts"], "tests": tests, "broken": broken})
    return zs
def nearest(zs, price):
    sup = dem = None
    for z in zs:
        if z["broken"]: continue
        if z["kind"] == "supply" and z["top"] >= price:
            d = max(0.0, z["bottom"] - price)
            if sup is None or d < sup[0]: sup = (d, z)
        if z["kind"] == "demand" and z["bottom"] <= price:
            d = max(0.0, price - z["top"])
            if dem is None or d < dem[0]: dem = (d, z)
    return (sup[1] if sup else None), (dem[1] if dem else None)

# ── §3 levels ────────────────────────────────────────────────────────────────
def L(name, **kw):
    d = {"name": name, "value": None, "status": "NOT_AVAILABLE", "kind": "point", "top": None, "bottom": None,
         "formed_ts": None, "vfrom": None, "tests": None, "broken": None, "exploratory": False, "reason": None,
         "gap_minutes": None, "gap_contaminated": None}
    d.update(kw); return d
def flag_gap(lv, bars, t0, t1):
    if t0 is None or t1 is None or t1 <= t0: return
    g = gap_minutes(bars, t0, t1); lv["gap_minutes"] = g; lv["gap_contaminated"] = g >= GAP_MIN
def build(all_bars, inst, b0_ts):
    clean = [b for b in all_bars if b["ts"] <= b0_ts]
    past = clean[-WIN:]; b0 = past[-1]; now = b0["ts"]; assert now == b0_ts, "B0 not in corpus"
    sess = session(now); close = b0["close"]; m = mtr(past[-MTRW:]); lv = {}; warn = []
    today = tday(now)
    byday = {}
    for b in past: byday.setdefault(tday(b["ts"]), []).append(b)
    today_bars = byday.get(today, []); first_today = len(past) - len(today_bars)
    prev = sorted(k for k in byday if k < today)
    if prev:
        pk = prev[-1]; pb = byday[pk]
        if not (pk == today - timedelta(days=1) or (today.weekday() == 0 and pk == today - timedelta(days=3))):
            warn.append(f"previous trading day in window is {pk}, not the calendar-previous day")
        formed = pb[-1]["ts"] + timedelta(minutes=15)
        lv["PDH"] = L("PDH", value=max(b["high"] for b in pb), status="AVAILABLE", formed_ts=formed, vfrom=first_today)
        lv["PDL"] = L("PDL", value=min(b["low"] for b in pb), status="AVAILABLE", formed_ts=formed, vfrom=first_today)
        lv["PDC_BAR"] = L("PDC_BAR", value=pb[-1]["close"], status="AVAILABLE", formed_ts=formed, vfrom=first_today)
        e0 = et(pb[0]["ts"]); pd0 = e0.replace(hour=18, minute=0, second=0, microsecond=0) - (timedelta(days=1) if e0.hour < 18 else timedelta(0))
        for n in ("PDH", "PDL", "PDC_BAR"): flag_gap(lv[n], past, pd0, pd0 + timedelta(hours=23))
    else:
        for n in ("PDH", "PDL", "PDC_BAR"): lv[n] = L(n, reason="no previous trading day in window")
    tw = tweek(now); pwbars = [b for b in clean if tweek(b["ts"]) < tw]
    if pwbars:
        pw = tweek(pwbars[-1]["ts"]); pwb = [b for b in pwbars if tweek(b["ts"]) == pw]
        days = {tday(b["ts"]) for b in pwb}; mon = min(days) - timedelta(days=min(days).weekday())
        exp = {mon + timedelta(days=i) for i in range(5)}
        if not exp <= days:
            for n in ("PWH", "PWL"): lv[n] = L(n, reason=f"previous week incomplete in window: missing {sorted(exp-days)}")
        else:
            formed = pwb[-1]["ts"] + timedelta(minutes=15)
            wsi = next((i for i, b in enumerate(past) if tweek(b["ts"]) == tw), len(past) - 1)
            lv["PWH"] = L("PWH", value=max(b["high"] for b in pwb), status="AVAILABLE", formed_ts=formed, vfrom=wsi)
            lv["PWL"] = L("PWL", value=min(b["low"] for b in pwb), status="AVAILABLE", formed_ts=formed, vfrom=wsi)
            w0 = datetime.combine(mon - timedelta(days=1), time(18), tzinfo=ET); w1 = datetime.combine(mon + timedelta(days=4), time(17), tzinfo=ET)
            for n in ("PWH", "PWL"): flag_gap(lv[n], clean, w0, w1)
    else:
        for n in ("PWH", "PWL"): lv[n] = L(n, reason="no previous trading week in window")
    rth = datetime.combine(today, time(9, 30), tzinfo=ET); pm0 = datetime.combine(today, time(4), tzinfo=ET)
    on = [b for b in today_bars if et(b["ts"]) < rth]; pm = [b for b in on if et(b["ts"]) >= pm0]
    if on:
        frozen = sess == "new_york"; st = "AVAILABLE" if frozen else "NOT_IN_WINDOW"
        rs = None if frozen else "overnight range still forming; events are RTH-only"
        f = on[-1]["ts"] + timedelta(minutes=15); ri = first_today + len(on)
        lv["ONH"] = L("ONH", value=max(b["high"] for b in on), status=st, formed_ts=f, vfrom=ri, reason=rs)
        lv["ONL"] = L("ONL", value=min(b["low"] for b in on), status=st, formed_ts=f, vfrom=ri, reason=rs)
        o0 = datetime.combine(today - timedelta(days=1), time(18), tzinfo=ET); o1 = min(rth, now + timedelta(minutes=15))
        for n in ("ONH", "ONL"): flag_gap(lv[n], past, o0, o1)
    else:
        for n in ("ONH", "ONL"): lv[n] = L(n, reason="no overnight bars for the current trading day in window")
    if pm:
        st = "AVAILABLE" if sess == "new_york" else "NOT_IN_WINDOW"
        lv["PMH"] = L("PMH", value=max(b["high"] for b in pm), status=st, exploratory=True, vfrom=first_today + len(on))
        lv["PML"] = L("PML", value=min(b["low"] for b in pm), status=st, exploratory=True, vfrom=first_today + len(on))
    else:
        for n in ("PMH", "PML"): lv[n] = L(n, exploratory=True, reason="no premarket bars")
    if today_bars:
        lv["HOD"] = L("HOD", value=max(b["high"] for b in today_bars), status="AVAILABLE", exploratory=True, vfrom=first_today)
        lv["LOD"] = L("LOD", value=min(b["low"] for b in today_bars), status="AVAILABLE", exploratory=True, vfrom=first_today)
        for n in ("HOD", "LOD"): flag_gap(lv[n], past, datetime.combine(today - timedelta(days=1), time(18), tzinfo=ET), now + timedelta(minutes=15))
    else:
        for n in ("HOD", "LOD"): lv[n] = L(n, exploratory=True, reason="no bars for the current trading day")
    def orb(prefix, ot, vf, vt):
        bar = next((b for b in today_bars if et(b["ts"]).time() == ot), None)
        t0 = et(now).time(); inw = vf <= t0 < vt
        if bar is None:
            rs = f"canonical {ot.strftime('%H:%M')} ET bar absent for this trading day" if inw else f"outside validity window and no {ot.strftime('%H:%M')} bar"
            for s in "HL": lv[f"{prefix}_{s}"] = L(f"{prefix}_{s}", reason=rs)
            return
        st = "AVAILABLE" if inw else "NOT_IN_WINDOW"; rs = None if inw else "outside validity window"
        idx = past.index(bar) + 1; f = bar["ts"] + timedelta(minutes=15)
        lv[f"{prefix}_H"] = L(f"{prefix}_H", value=bar["high"], status=st, formed_ts=f, vfrom=idx, reason=rs)
        lv[f"{prefix}_L"] = L(f"{prefix}_L", value=bar["low"], status=st, formed_ts=f, vfrom=idx, reason=rs)
    orb("NY_ORB", time(9, 30), time(9, 45), time(17)); orb("LDN_ORB", time(3), time(3, 15), time(9, 30))
    num = den = 0.0; ok = True
    for b in today_bars:
        v = b["volume"]
        if v is None: ok = False; break
        num += (b["high"] + b["low"] + b["close"]) / 3 * float(v); den += float(v)
    if today_bars and ok and den > 0:
        lv["VWAP"] = L("VWAP", value=num / den, status="AVAILABLE", exploratory=True, vfrom=first_today, reason="NOT_ADMITTED tranche 1 (v1.3): diagnostic only")
        flag_gap(lv["VWAP"], past, datetime.combine(today - timedelta(days=1), time(18), tzinfo=ET), now + timedelta(minutes=15))
    else:
        lv["VWAP"] = L("VWAP", exploratory=True, reason="no volume on a current-day bar" if today_bars else "no bars for the current trading day")
    for lab, mins, lb in (("1h", 60, 120), ("4h", 240, 60)):
        agg = aggregate(past, mins)[-lb:]; sup, dem = nearest(zones(agg), close)
        for kind, z in (("supply", sup), ("demand", dem)):
            n = f"LC_ZONE_{lab.upper()}_{kind.upper()}"
            if z:
                lv[n] = L(n, kind="zone", status="AVAILABLE", top=z["top"], bottom=z["bottom"], formed_ts=z["formed_ts"], tests=z["tests"], broken=z["broken"])
                flag_gap(lv[n], past, agg[0]["ts"], now + timedelta(minutes=15))
            else:
                lv[n] = L(n, kind="zone", reason=f"no unbroken {kind} zone on {lab}")
    return {"past": past, "b0": b0, "session": sess, "close": close, "mtr": m, "levels": lv, "warnings": warn, "tick": TICK[inst]}

# ── §5 events / §5.1 reduction / §6 labels ───────────────────────────────────
MAJOR = ("PWH", "PWL", "PDH", "PDL", "ONH", "ONL", "NY_ORB_H", "NY_ORB_L", "LDN_ORB_H", "LDN_ORB_L")
ZN = ("LC_ZONE_4H_SUPPLY", "LC_ZONE_4H_DEMAND", "LC_ZONE_1H_SUPPLY", "LC_ZONE_1H_DEMAND")
SETS = {"H1": MAJOR, "H2": MAJOR, "H3": MAJOR + ZN, "H4": MAJOR + ZN, "H5": MAJOR + ZN + ("PDC_BAR",), "H6": MAJOR + ZN}
PREC = {"PWH": 0, "PWL": 0, "PDH": 1, "PDL": 1, "ONH": 2, "ONL": 2, "NY_ORB_H": 3, "NY_ORB_L": 3, "LDN_ORB_H": 4, "LDN_ORB_L": 4,
        "LC_ZONE_4H_SUPPLY": 5, "LC_ZONE_4H_DEMAND": 5, "LC_ZONE_1H_SUPPLY": 6, "LC_ZONE_1H_DEMAND": 6, "PDC_BAR": 7}
OWN = {"orb": ("NY_ORB_H", "NY_ORB_L", "LDN_ORB_H", "LDN_ORB_L"), "vwap": ("VWAP",), "pdh_pdl": ("PDH", "PDL")}
def family(s):
    s = s.lower()
    if s.startswith("orb_"): return "orb"
    if s.startswith("vwap_"): return "vwap"
    if s in ("pdh_reclaim", "pdl_reclaim"): return "pdh_pdl"
    if s.startswith("range_"): return "range_signal"
    if "strat" in s: return "strat"
    return "other"
def edge(lv, price):
    if lv["kind"] != "zone": return lv["value"]
    return lv["top"] if price > lv["top"] else lv["bottom"] if price < lv["bottom"] else price
def admitted(ls, names, fam):
    out = []
    for n in names:
        if n in OWN.get(fam, ()): continue
        lv = ls["levels"][n]
        if lv["status"] != "AVAILABLE" or lv["exploratory"] or n == "VWAP": continue
        out.append(lv)
    return out
def supportive(lv, entry, sg): return (lv["top"] <= entry if sg > 0 else lv["bottom"] >= entry) if lv["kind"] == "zone" else (lv["value"] <= entry if sg > 0 else lv["value"] >= entry)
def opposing(lv, entry, sg): return (lv["bottom"] >= entry if sg > 0 else lv["top"] <= entry) if lv["kind"] == "zone" else (lv["value"] >= entry if sg > 0 else lv["value"] <= entry)
def dist(lv, entry, tick): return round(abs(entry - edge(lv, entry)) / tick) * tick
def anchor(ls, names, fam, entry, sg, side="supportive"):
    pred = supportive if side == "supportive" else opposing
    c = [lv for lv in admitted(ls, names, fam) if pred(lv, entry, sg)]
    if not c: return None
    return min(c, key=lambda lv: (dist(lv, entry, ls["tick"]), PREC[lv["name"]], edge(lv, entry) * sg))
def touch(b0, lv): return (b0["low"] <= lv["top"] and b0["high"] >= lv["bottom"]) if lv["kind"] == "zone" else (b0["low"] <= lv["value"] <= b0["high"])
def wick_reject(b0, lv, sg, tick, m):
    l = edge(lv, b0["close"])
    return (b0["low"] <= l - tick and b0["close"] > l and l - b0["low"] <= DMAX * m) if sg > 0 else (b0["high"] >= l + tick and b0["close"] < l and b0["high"] - l <= DMAX * m)
def sweep_reclaim(past, lv, sg, m):
    """E3b: ∃k∈[1,K]: B−k.close strictly far side, closes B−k..B−1 all ≤ ℓ (far/at), B0 close near side,
    entered from the near side, excursion ≤ D_max over B−k..B0."""
    b0 = past[-1]; l = edge(lv, b0["close"])
    if not (b0["close"] - l) * sg > 0: return False
    for k in range(1, K + 1):
        if len(past) < k + 2: break
        seg = past[-1 - k:-1]                                   # B−k..B−1
        if not all((b["close"] - l) * sg <= 0 for b in seg): break
        if not (seg[0]["close"] - l) * sg < 0:                  # B−k must be a strict far-side close
            continue
        if not (past[-2 - k]["close"] - l) * sg > 0:            # came from the near side
            continue
        exc = max(((l - b["low"]) if sg > 0 else (b["high"] - l)) for b in seg + [b0])
        return exc <= DMAX * m
    return False
def beyond(p, l, sg, tick=0.0): return (p - l) * sg >= tick if tick else (p - l) * sg > 0
def find_break(past, lv, sg, tick):
    l = lv["value"]; n = len(past); start = lv["vfrom"] or 0
    for j in range(0, R + 1):
        i = n - 1 - j
        if i <= start or i < 1: break
        if beyond(past[i]["close"], l, sg, tick) and not beyond(past[i-1]["close"], l, sg, tick):
            return j if all(beyond(b["close"], l, sg) for b in past[i+1:-1]) else None
    return None
def retest_state(past, lv, sg, tick, m):
    j = find_break(past, lv, sg, tick)
    if j is None: return None, None
    if j < 2: return "IMMEDIATE", j
    l, tau, n = lv["value"], TAU * m, len(past)
    within = (lambda b: b["low"] <= l + tau) if sg > 0 else (lambda b: b["high"] >= l - tau)
    if any(within(b) for b in past[n-j:n-1]): return "RETESTED_EARLIER", j
    b0 = past[-1]
    if within(b0): return ("BREAK_RETEST_HOLD" if beyond(b0["close"], l, sg) else "BREAK_RETEST_REJECT"), j
    return ("ACCEPT_NO_RETEST" if beyond(b0["close"], l, sg) else "BACK_THROUGH_NO_RETEST"), j
def prox_only(b0, lv, sg, m):
    if touch(b0, lv): return False
    d = (b0["close"] - edge(lv, b0["close"])) * sg
    return 0 < d <= PROX * m
def test_count(past, lv):
    if lv["kind"] == "zone": return lv["tests"]
    return sum(1 for b in past[lv["vfrom"]:-1] if b["low"] <= lv["value"] <= b["high"])
def label(ls, direction, entry, stop, target, strategy):
    past, b0, m, tick = ls["past"], ls["b0"], ls["mtr"], ls["tick"]; sg = 1 if direction == "LONG" else -1; fam = family(strategy); H = {}
    a = anchor(ls, SETS["H1"], fam, entry, sg)
    if a is None: H["H1"] = {"label": "NOT_APPLICABLE", "reason": "no admitted supportive MAJOR level"}
    elif wick_reject(b0, a, sg, tick, m) or sweep_reclaim(past, a, sg, m): H["H1"] = {"label": "T", "anchor": a["name"]}
    elif touch(b0, a): H["H1"] = {"label": "F", "anchor": a["name"]}
    else: H["H1"] = {"label": "NOT_APPLICABLE", "reason": "anchor neither swept/reclaimed nor touched", "anchor": a["name"]}
    best = None
    for lv in admitted(ls, MAJOR, fam):
        if not supportive(lv, entry, sg): continue
        st, j = retest_state(past, lv, sg, tick, m)
        if st in ("BREAK_RETEST_HOLD", "BREAK_RETEST_REJECT", "ACCEPT_NO_RETEST", "RETESTED_EARLIER", "BACK_THROUGH_NO_RETEST"):
            k = (dist(lv, entry, tick), PREC[lv["name"]], lv["value"] * sg)
            if best is None or k < best[0]: best = (k, lv, st, j)
    if best is None: H["H2"] = {"label": "NOT_APPLICABLE", "reason": "no eligible broken MAJOR level (age 2..R, closes held)"}
    else:
        _, lv, st, j = best
        H["H2"] = {"label": "T", "anchor": lv["name"], "break_age": j} if st == "BREAK_RETEST_HOLD" else \
                  {"label": "F", "anchor": lv["name"], "break_age": j} if st == "ACCEPT_NO_RETEST" else \
                  {"label": "NOT_APPLICABLE", "reason": st.lower(), "anchor": lv["name"], "break_age": j}
    a = anchor(ls, SETS["H3"], fam, entry, sg)
    if a is None: H["H3"] = {"label": "NOT_APPLICABLE", "reason": "no admitted supportive level"}
    elif wick_reject(b0, a, sg, tick, m): H["H3"] = {"label": "T", "anchor": a["name"]}
    elif prox_only(b0, a, sg, m): H["H3"] = {"label": "F", "anchor": a["name"]}
    elif touch(b0, a): H["H3"] = {"label": "NOT_APPLICABLE", "reason": "touch_and_close_through_or_plain_touch (descriptive third group)", "anchor": a["name"]}
    else: H["H3"] = {"label": "NOT_APPLICABLE", "reason": "anchor beyond proximity band", "anchor": a["name"]}
    a = anchor(ls, SETS["H4"], fam, entry, sg)
    if a is None: H["H4"] = {"label": "NOT_APPLICABLE", "reason": "no admitted supportive level"}
    elif touch(b0, a) or prox_only(b0, a, sg, m):
        H["H4"] = {"label": "APPLICABLE", "anchor": a["name"], "test_count": test_count(past, a),
                   "age_hours": cme_hours(a["formed_ts"], b0["ts"] + timedelta(minutes=15)) if a["formed_ts"] else None}
    else: H["H4"] = {"label": "NOT_APPLICABLE", "reason": "anchor neither touched nor within proximity band", "anchor": a["name"]}
    a = anchor(ls, SETS["H5"], fam, entry, sg)
    if a is None: H["H5"] = {"label": "NOT_APPLICABLE", "reason": "no admitted supportive level"}
    elif dist(a, entry, tick) > PROX * m: H["H5"] = {"label": "NOT_APPLICABLE", "reason": "anchor beyond relevance band", "anchor": a["name"], "dist_mtr": round(dist(a, entry, tick) / m, 4)}
    else:
        ae = edge(a, ls["close"]); band = PROX * m; c = 0
        for lv in admitted(ls, SETS["H5"], fam):
            if lv["kind"] == "zone": c += lv["bottom"] <= ae + band and lv["top"] >= ae - band
            else: c += abs(lv["value"] - ae) <= band
        H["H5"] = {"label": "T" if c >= 2 else "F", "anchor": a["name"], "cluster": c}
    o = anchor(ls, SETS["H6"], fam, entry, sg, "opposing")
    if o is None: H["H6"] = {"label": "NOT_APPLICABLE", "reason": "no admitted opposing level"}
    else:
        room = (edge(o, entry) - entry) * sg; risk = abs(entry - stop); tau = TAU * m; td = (target - entry) * sg
        rel = "before" if td < room - tau else "beyond" if td > room + tau else "inside"
        r = {"opposing": o["name"], "room_points": round(room, 4), "room_R": round(room / risk, 4), "target_rel": rel}
        H["H6"] = {"label": "NOT_APPLICABLE", "reason": "target inside ± tau of the opposing level", **r} if rel == "inside" else {"label": "T" if rel == "before" else "F", **r}
    return H

# ── bracket re-derivation from source bars ───────────────────────────────────
def ema_series(closes, n):
    out = [None] * len(closes)
    if len(closes) < n: return out
    e = sum(closes[:n]) / n; out[n-1] = e; k = 2 / (n + 1)
    for i in range(n, len(closes)):
        e = closes[i] * k + e * (1 - k); out[i] = e
    return out
def bracket(all_bars, inst, cand):
    tick = TICK[inst]; b0_ts = datetime.fromisoformat(cand["bar_ts"]); idx = next(i for i, b in enumerate(all_bars) if b["ts"] == b0_ts)
    b0, b1 = all_bars[idx], all_bars[idx-1]; s = cand["strategy"]; out = {"strategy": s, "b0": b0["ts"].isoformat()}
    if s == "impulse_first_pullback_observed":
        seq = all_bars[idx-3:idx+1]; c = [b["close"] for b in seq]
        out["firing"] = {"impulse_up": c[0] < c[1] < c[2], "pullback_down": c[3] < c[2], "corpus_trend_direction": b0["raw"].get("trend_direction")}
        entry = b0["high"] + tick; stop = min(b["low"] for b in seq[-2:]) - tick; target = entry + 2.0 * (entry - stop); direction = "LONG"
    elif s == "strat_22_reversal_observed":
        b2 = all_bars[idx-2]
        def btype(b, p):
            if b["high"] > p["high"] and b["low"] < p["low"]: return "3"
            if b["high"] <= p["high"] and b["low"] >= p["low"]: return "1"
            return "2U" if b["high"] > p["high"] else "2D"
        out["firing"] = {"prev_bar_type": btype(b1, b2), "cur_bar_type": btype(b0, b1), "corpus_prev_high": b0["raw"].get("previous_bar_high"), "corpus_prev_low": b0["raw"].get("previous_bar_low"),
                         "b1_high": b1["high"], "b1_low": b1["low"], "prev_fields_match_b1": b0["raw"].get("previous_bar_high") == b1["high"] and b0["raw"].get("previous_bar_low") == b1["low"]}
        entry = b1["low"] - tick; stop = b1["high"] + tick; target = entry - 2 * (stop - entry); direction = "SHORT"
    elif s == "ema_pullback_trend":
        closes = [b["close"] for b in all_bars[:idx+1]]
        e9, e21, e55 = (ema_series(closes, n)[-1] for n in (9, 21, 55))
        out["ema_recomputed"] = {"ema9": e9, "ema21": e21, "ema55": e55}
        out["ema_corpus"] = {k: b0["raw"].get(k) for k in ("ema_9", "ema_21", "ema_55")}
        out["ema_max_abs_diff"] = max(abs(e9 - b0["raw"]["ema_9"]), abs(e21 - b0["raw"]["ema_21"]), abs(e55 - b0["raw"]["ema_55"]))
        out["firing"] = {"stack_bull": e9 > e21 > e55, "touched_zone": b0["low"] <= e9 and b0["low"] >= e21 - 4 * tick, "resumed": b0["close"] > e9}
        entry = b0["close"]; stop = min(b0["low"] - 2 * tick, e21 - 4 * tick); target = entry + 2.2 * (entry - stop); direction = "LONG"
    out.update({"direction": direction, "entry": round(entry, 4), "stop": round(stop, 4), "target": round(target, 4)})
    risk = (entry - stop) * (1 if direction == "LONG" else -1); rew = (target - entry) * (1 if direction == "LONG" else -1)
    out["rr"] = round(rew / risk, 4)
    return out

# ── run + compare ────────────────────────────────────────────────────────────
def ser(lv):
    return {"value": lv["value"], "status": lv["status"], "top": lv["top"], "bottom": lv["bottom"], "tests": lv["tests"], "broken": lv["broken"],
            "exploratory": lv["exploratory"], "reason": lv["reason"], "gap_minutes": lv["gap_minutes"], "gap_contaminated": lv["gap_contaminated"],
            "formed_ts": lv["formed_ts"].isoformat() if lv["formed_ts"] else None}
def feq(a, b):
    if isinstance(a, float) and isinstance(b, (int, float)): return abs(a - b) < 1e-9
    return a == b
report = {}; ok_all = True
bars_cache = {}
for key, fr in FROZEN.items():
    cand, feat = fr["cand"], fr["feat"]; inst = cand["instrument"]
    if inst not in bars_cache: bars_cache[inst] = load_bars(inst)
    ab = bars_cache[inst]; b0_ts = datetime.fromisoformat(cand["bar_ts"])
    rep = {"instrument": inst, "diffs": []}
    D = rep["diffs"]
    # identity
    parts = key.split("|")
    if parts != ["shadow_setups", inst, cand["bar_ts"], cand["strategy"], cand["direction"], str(cand["entry"]) if cand["entry"] != int(cand["entry"]) else f"{cand['entry']:.1f}"] and parts[:5] != ["shadow_setups", inst, cand["bar_ts"], cand["strategy"], cand["direction"]]:
        D.append(("identity", "key segments", parts))
    if float(parts[5]) != cand["entry"]: D.append(("identity", "key entry", parts[5], cand["entry"]))
    if feat["candidate_key"] != key or feat["b0_ts"] != cand["bar_ts"] or feat["bar_ts"] != cand["bar_ts"]: D.append(("identity", "feature key/b0", feat["candidate_key"], feat["b0_ts"]))
    for f in ("instrument", "strategy", "family", "direction", "entry", "stop", "target", "contract", "contract_roll_utc_date", "is_roll_utc_day", "corpus_day_position", "recent_bars_warmup", "candle_present_in_corpus"):
        if not feq(cand[f], feat[f]): D.append(("identity", f, cand[f], feat[f]))
    if feat["session_candidate"] != cand["session"]: D.append(("identity", "session_candidate", cand["session"], feat["session_candidate"]))
    b0_in = any(b["ts"] == b0_ts for b in ab); rep["b0_in_corpus"] = b0_in
    if not b0_in: D.append(("identity", "B0 missing from corpus"))
    # corpus_day_position / journal_day_file from bars
    dayfile = b0_ts.strftime("%Y-%m-%d"); pos = sum(1 for b in ab if b["ts"].strftime("%Y-%m-%d") == dayfile and b["ts"] < b0_ts)
    rep["day_position_rederived"] = pos
    if pos != cand["corpus_day_position"] or dayfile != cand["journal_day_file"]: D.append(("identity", "corpus_day_position/journal_day_file", pos, dayfile, cand["corpus_day_position"], cand["journal_day_file"]))
    # bracket
    br = bracket(ab, inst, cand); rep["bracket"] = br
    for f in ("direction", "entry", "stop", "target"):
        if not feq(br[f], cand[f]): D.append(("bracket", f, br[f], cand[f]))
    if not feq(br["rr"], cand["rr_ratio"]): D.append(("bracket", "rr_ratio", br["rr"], cand["rr_ratio"]))
    # levels
    ls = build(ab, inst, b0_ts)
    rep["mtr15"] = ls["mtr"]; rep["close_b0"] = ls["close"]; rep["session_p1"] = ls["session"]; rep["n_bars"] = len(ls["past"])
    if not feq(ls["mtr"], feat["mtr15"]): D.append(("p1", "mtr15", ls["mtr"], feat["mtr15"]))
    if not feq(ls["close"], feat["close_b0"]): D.append(("p1", "close_b0", ls["close"], feat["close_b0"]))
    if ls["session"] != feat["session_p1"]: D.append(("p1", "session_p1", ls["session"], feat["session_p1"]))
    if len(ls["past"]) != feat["n_bars_in_window"]: D.append(("p1", "n_bars_in_window", len(ls["past"]), feat["n_bars_in_window"]))
    if ls["warnings"] != feat["level_warnings"]: D.append(("p1", "level_warnings", ls["warnings"], feat["level_warnings"]))
    if family(cand["strategy"]) != feat["p1_family"]: D.append(("p1", "p1_family", family(cand["strategy"]), feat["p1_family"]))
    mine = {n: ser(lv) for n, lv in ls["levels"].items()}; rep["levels"] = mine
    if set(mine) != set(feat["levels"]): D.append(("levels", "level name set", sorted(set(mine) ^ set(feat["levels"]))))
    for n in mine:
        for f in mine[n]:
            if not feq(mine[n][f], feat["levels"][n].get(f)): D.append(("levels", n, f, mine[n][f], feat["levels"][n].get(f)))
    adm = [n for n in MAJOR + ZN + ("PDC_BAR",)]
    for fld, pred in (("admitted_levels_not_available", lambda l: l["status"] == "NOT_AVAILABLE"), ("admitted_levels_not_in_window", lambda l: l["status"] == "NOT_IN_WINDOW"),
                      ("admitted_levels_gap_contaminated", lambda l: l["gap_contaminated"] is True)):
        m = sorted(n for n in adm if pred(ls["levels"][n]))
        if m != sorted(feat[fld]): D.append(("levels", fld, m, feat[fld]))
    # hypotheses
    H = label(ls, cand["direction"], cand["entry"], cand["stop"], cand["target"], cand["strategy"]); rep["hypotheses"] = H
    for h in ("H1", "H2", "H3", "H4", "H5", "H6"):
        a, b = H[h], feat["hypotheses"][h]
        if set(a) != set(b): D.append(("hyp", h, "field set", sorted(a), sorted(b)))
        for f in a:
            if not feq(a[f], b.get(f)): D.append(("hyp", h, f, a[f], b.get(f)))
    rep["verdict"] = "AGREE" if not D else "DISAGREE"; ok_all &= not D
    report[key] = rep
    print("=====", key); print(" bracket:", json.dumps(br, default=str)); print(" mtr15", ls["mtr"], "close", ls["close"], "session", ls["session"], "n", len(ls["past"]))
    for h, v in H.items(): print("  ", h, v)
    print(" DIFFS:", D if D else "none"); print(" ROW VERDICT:", rep["verdict"])
json.dump(report, open(f"{S}/rederivation_report.rerun.json", "w"), indent=1, default=str)
print("\nALL ROWS AGREE:", ok_all)
